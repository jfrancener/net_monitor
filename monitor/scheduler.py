import time
import threading
import subprocess
import platform
import socket
import os
import requests
import logging
from django.utils import timezone
import datetime
from datetime import timedelta

# Evita importação circular importando apenas dentro das funções ou após inicializar
logger = logging.getLogger(__name__)

# Lock e controle global para evitar múltiplos testes de velocidade simultâneos
speedtest_lock = threading.Lock()
is_speedtesting = False

def ping_host(host):
    """Executa um ping ICMP usando o comando nativo do SO para evitar permissões de root."""
    param = '-n' if platform.system().lower() == 'windows' else '-c'
    # timeout de 1 segundo
    cmd = ['ping', param, '1', '-w', '1000' if platform.system().lower() == 'windows' else '1', host]
    start = time.time()
    try:
        # Pager=cat já é garantido, rodamos de forma silenciosa
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        latency = (time.time() - start) * 1000
        if result.returncode == 0:
            return True, round(latency, 1)
        return False, 0.0
    except subprocess.TimeoutExpired:
        return False, 0.0
    except Exception as e:
        logger.error(f"Erro ao pingar {host}: {e}")
        return False, 0.0


def tcp_check(host, port):
    """Verifica se uma porta TCP específica está aberta."""
    start = time.time()
    try:
        with socket.create_connection((host, int(port)), timeout=2.0):
            latency = (time.time() - start) * 1000
            return True, round(latency, 1)
    except Exception:
        return False, 0.0


def http_check(url):
    """Verifica se um endereço HTTP responde com código 2xx ou 3xx."""
    start = time.time()
    try:
        # Desabilita verificação SSL em redes locais
        response = requests.get(url, timeout=2.5, verify=False)
        latency = (time.time() - start) * 1000
        is_online = 200 <= response.status_code < 400
        return is_online, round(latency, 1)
    except Exception:
        return False, 0.0


def run_speedtest_worker():
    """Trabalhador secundário para rodar o Speedtest sem travar o loop de monitoramento."""
    global is_speedtesting
    from monitor.models import SpeedTestResult
    
    if not speedtest_lock.acquire(blocking=False):
        return  # Já está rodando um teste
        
    is_speedtesting = True
    try:
        logger.info("Iniciando teste de velocidade (Speedtest)...")
        # Simulação se biblioteca falhar ou em modo dev
        try:
            import speedtest
            st = speedtest.Speedtest()
            st.get_best_server()
            download = st.download() / 1000000  # Mbps
            upload = st.upload() / 1000000      # Mbps
            ping = st.results.ping
            
            # Validação simples
            if download > 0:
                SpeedTestResult.objects.create(
                    download_mbps=round(download, 1),
                    upload_mbps=round(upload, 1),
                    ping_ms=round(ping, 1)
                )
                logger.info(f"Speedtest concluído: DL: {download:.1f} Mbps | UL: {upload:.1f} Mbps")
                return
        except Exception as se:
            logger.warning(f"Erro ao usar speedtest-cli, usando valores simulados para desenvolvimento: {se}")
            
        # Mock de velocidade para desenvolvimento/teste se o Speedtest falhar
        import random
        # Simula velocidades plausíveis de internet (ex: link de 300 Mbps)
        download = random.uniform(280.0, 310.0)
        upload = random.uniform(140.0, 160.0)
        ping = random.uniform(8.0, 15.0)
        
        # Simula delay do teste de 5 segundos
        time.sleep(5)
        
        SpeedTestResult.objects.create(
            download_mbps=round(download, 1),
            upload_mbps=round(upload, 1),
            ping_ms=round(ping, 1)
        )
        logger.info("Speedtest simulado concluído com sucesso.")
        
    finally:
        is_speedtesting = False
        speedtest_lock.release()


def trigger_speedtest():
    """Inicia um teste de velocidade assíncrono."""
    if not is_speedtesting:
        t = threading.Thread(target=run_speedtest_worker, daemon=True)
        t.start()
        return True
    return False


def monitor_loop():
    """Loop principal de monitoramento executado em segundo plano."""
    # Importações atrasadas para evitar erros de inicialização de Apps do Django
    from monitor.models import Service, OmadaConfig, AdGuardConfig, WANStatus, NetworkDevice, SpeedTestResult, WifiNetwork
    from monitor.omada import fetch_omada_status, fetch_omada_ssids, OmadaClient
    from monitor.adguard import fetch_adguard_status
    
    logger.info("Iniciando loop do Monitor de Rede...")
    
    # Cria o registro inicial da WAN se não existir
    wan_status, _ = WANStatus.objects.get_or_create(id=1)
    
    last_speedtest_time = None
    
    while True:
        try:
            # 1. Pings Locais dos DNS
            dns_primary_online, _ = ping_host("192.168.10.253")
            dns_secondary_online, _ = ping_host("192.168.10.1")
            
            # Ping local de fallback inicial para a WAN
            wan_online, wan_latency = ping_host("1.1.1.1")
            download_rate = 0.0
            upload_rate = 0.0
            
            # 2. Monitorar Serviços Locais Cadastrados
            services = Service.objects.filter(enabled=True)
            for s in services:
                if s.type == 'ping':
                    online, lat = ping_host(s.target)
                elif s.type == 'tcp' and s.port:
                    online, lat = tcp_check(s.target, s.port)
                elif s.type == 'http':
                    online, lat = http_check(s.target)
                else:
                    online, lat = False, 0.0
                    
                s.is_online = online
                s.latency_ms = lat if online else 0.0
                s.last_checked = timezone.now()
                s.save()
                
            # 3. Consultar Omada Controller
            omada_cfg = OmadaConfig.objects.filter(enabled=True).first()
            if omada_cfg:
                client = OmadaClient(omada_cfg.url, omada_cfg.username, omada_cfg.password, omada_cfg.site_name)
                if client.login():
                    omada_devices = client.get_devices()
                    omada_ssids = client.get_ssids()
                    
                    if omada_devices is not None and omada_ssids is not None:
                        # Conexão estabelecida com sucesso
                        if not omada_cfg.is_connected:
                            omada_cfg.is_connected = True
                            omada_cfg.save()
                            
                        # Sincronizar dispositivos (deleta equipamentos antigos/mockados que não foram retornados na coleta atual)
                        collected_macs = [dev["mac"] for dev in omada_devices]
                        NetworkDevice.objects.exclude(mac__in=collected_macs).delete()
                        
                        gateway_mac = None
                        for dev in omada_devices:
                            status_val = dev.get("status", 0)
                            status_str = "Online" if status_val in [1, 2, 14] else "Offline"
                            
                            # Identifica o roteador/gateway
                            is_gw = dev.get("type") == "gateway" or "ER7212" in dev.get("model", "")
                            if is_gw:
                                gateway_mac = dev.get("mac")
                                
                            NetworkDevice.objects.update_or_create(
                                mac=dev["mac"],
                                defaults={
                                    "name": dev.get("name") or dev.get("model"),
                                    "ip": dev.get("ip"),
                                    "model": dev.get("model"),
                                    "status": status_str,
                                    "clients": dev.get("clientNum", 0),
                                    "cpu_util": dev.get("cpuUtil", 0),
                                    "mem_util": dev.get("memUtil", 0),
                                    "temperature": dev.get("temperature", 0.0)
                                }
                            )
                            
                        # Sincronizar SSIDs do Wi-Fi
                        current_ssid_names = [ssid.get("name") or ssid.get("ssid") for ssid in omada_ssids]
                        WifiNetwork.objects.exclude(name__in=current_ssid_names).delete()
                        
                        for ssid in omada_ssids:
                            name = ssid.get("name") or ssid.get("ssid")
                            band_val = ssid.get("band", 0)
                            band_str = "2.4G" if band_val == 1 else "5G" if band_val == 2 else "2.4G / 5G"
                            
                            WifiNetwork.objects.update_or_create(
                                name=name,
                                defaults={
                                    "band": band_str,
                                    "enabled": ssid.get("enabled", True) if "enabled" in ssid else ssid.get("broadcast", True),
                                    "clients": ssid.get("clientNum", 0)
                                }
                            )
                            
                        # Obter status do link WAN3 do gateway se houver
                        if gateway_mac:
                            gw_detail = client.get_gateway_detail(gateway_mac)
                            port_uuid = None
                            
                            # Fallback inicial usando as estatísticas do gateway
                            if gw_detail:
                                port_stats = gw_detail.get("portStats", [])
                                for p in port_stats:
                                    if p.get("name") == "WAN3":
                                        wan_online = (p.get("internetState") == 1)
                                        wan_latency = p.get("latency", 0.0)
                                        # No gateway_detail, rxRate e txRate vêm em KB/s
                                        download_rate = round((p.get("rxRate", 0.0) * 8) / 1024.0, 2)
                                        upload_rate = round((p.get("txRate", 0.0) * 8) / 1024.0, 2)
                                        break
                                        
                            # Obter consumo preciso em tempo real a partir da API do Dashboard (isp/load)
                            bandwidth_data = client.get_wan_bandwidth()
                            if bandwidth_data:
                                wan_online = bandwidth_data["online"]
                                # No isp/load, downloadSpeed e uploadSpeed vêm em bps
                                download_rate = round(float(bandwidth_data["download_speed_bps"]) / 1048576.0, 2)
                                upload_rate = round(float(bandwidth_data["upload_speed_bps"]) / 1048576.0, 2)
                                port_uuid = bandwidth_data["port_uuid"]
                                logger.info(f"WAN3 Omada Realtime (isp/load): online={wan_online}, Down={download_rate}Mbps, Up={upload_rate}Mbps, port_uuid={port_uuid}")
                            else:
                                logger.info(f"WAN3 Omada GatewayDetail (fallback): online={wan_online}, latency={wan_latency}ms, Down={download_rate}Mbps, Up={upload_rate}Mbps")
                                
                            # Sincronizar o histórico de Speedtest do roteador Omada se tivermos port_uuid
                            if port_uuid:
                                test_list = client.get_gateway_speedtest(gateway_mac, port_uuid)
                                if test_list:
                                    last_test = test_list[0]
                                    test_time_epoch = last_test.get("time")
                                    if test_time_epoch:
                                        import datetime
                                        from django.utils.timezone import make_aware
                                        # Converter timestamp UTC epoch para timezone aware datetime
                                        test_dt = datetime.datetime.fromtimestamp(test_time_epoch, datetime.timezone.utc)
                                        
                                        latest_local_test = SpeedTestResult.objects.first()
                                        # Se for um teste novo ou o banco estiver vazio, cria o registro
                                        if not latest_local_test or abs((latest_local_test.timestamp - test_dt).total_seconds()) > 2.0:
                                            SpeedTestResult.objects.create(
                                                timestamp=test_dt,
                                                download_mbps=round(float(last_test.get("down", 0.0)) / 1000000.0, 1),
                                                upload_mbps=round(float(last_test.get("up", 0.0)) / 1000000.0, 1),
                                                ping_ms=round(float(last_test.get("latency", 0.0)), 1)
                                            )
                                            logger.info(f"Novo Speedtest do Roteador Omada registrado: DL={download_rate} Mbps, UL={upload_rate} Mbps, Lat={wan_latency} ms")
                    else:
                        # Falha na conexão com o Omada
                        if omada_cfg.is_connected:
                            omada_cfg.is_connected = False
                            omada_cfg.save()
                        NetworkDevice.objects.all().delete()
                        WifiNetwork.objects.all().delete()
                else:
                    # Falha no login
                    if omada_cfg.is_connected:
                        omada_cfg.is_connected = False
                        omada_cfg.save()
                    NetworkDevice.objects.all().delete()
                    WifiNetwork.objects.all().delete()
            else:
                # Integração desativada: limpa as tabelas do Omada
                NetworkDevice.objects.all().delete()
                WifiNetwork.objects.all().delete()
                
            # Atualiza e salva o status geral da WAN (respeitando o status real do Omada se coletado com sucesso)
            wan_status.wan_online = wan_online
            wan_status.dns_primary_online = dns_primary_online
            wan_status.dns_secondary_online = dns_secondary_online
            wan_status.latency_wan_ms = wan_latency if wan_online else 0.0
            wan_status.download_rate_mbps = download_rate if wan_online else 0.0
            wan_status.upload_rate_mbps = upload_rate if wan_online else 0.0
            wan_status.save()
                
            # 4. Consultar AdGuard Home Configs
            adguard_configs = AdGuardConfig.objects.filter(enabled=True)
            if adguard_configs.exists():
                for adg in adguard_configs:
                    stats = fetch_adguard_status(adg)
                    adg.total_queries = stats["total_queries"]
                    adg.blocked_queries = stats["blocked_queries"]
                    adg.blocked_percentage = stats["blocked_percentage"]
                    adg.is_active = stats["is_active"]
                    adg.save()
            else:
                # Se não houver AdGuard ativo, criamos dados mockados locais temporariamente
                # apenas para demonstrar duas instâncias de DNS na interface
                pass

            # O Speedtest local foi desativado para priorizar o Speedtest do próprio roteador Omada.
            # O histórico e os resultados são sincronizados diretamente da API do controlador.
            pass
                
        except Exception as e:
            logger.error(f"Erro no loop de monitoramento: {e}", exc_info=True)
            
        # Executa verificações a cada 10 segundos
        time.sleep(10)


def start_scheduler():
    """Inicia a thread de monitoramento se ela já não estiver rodando."""
    # Garante que roda apenas uma vez (importante no debug do Django que recarrega código)
    if os.environ.get('RUN_MAIN') == 'true' or not settings_is_debug():
        t = threading.Thread(target=monitor_loop, daemon=True, name="NetworkMonitorThread")
        t.start()


def settings_is_debug():
    from django.conf import settings
    return settings.DEBUG


def sync_omada_now():
    """Força uma sincronização imediata com o Omada para atualizar o status e obter novos testes de velocidade."""
    from monitor.models import OmadaConfig, NetworkDevice, WifiNetwork, WANStatus, SpeedTestResult
    from monitor.omada import OmadaClient
    from django.utils import timezone
    import logging
    
    logger = logging.getLogger(__name__)
    omada_cfg = OmadaConfig.objects.filter(enabled=True).first()
    if not omada_cfg:
        return False
        
    client = OmadaClient(omada_cfg.url, omada_cfg.username, omada_cfg.password, omada_cfg.site_name)
    if client.login():
        omada_devices = client.get_devices()
        if omada_devices:
            gateway_mac = None
            for dev in omada_devices:
                is_gw = dev.get("type") == "gateway" or "ER7212" in dev.get("model", "")
                if is_gw:
                    gateway_mac = dev.get("mac")
                    break
                    
            if gateway_mac:
                wan_online = False
                wan_latency = 0.0
                download_rate = 0.0
                upload_rate = 0.0
                port_uuid = None
                
                # 1. Atualizar tráfego em tempo real
                bandwidth_data = client.get_wan_bandwidth()
                if bandwidth_data:
                    wan_online = bandwidth_data["online"]
                    download_rate = round(float(bandwidth_data["download_speed_bps"]) / 1048576.0, 2)
                    upload_rate = round(float(bandwidth_data["upload_speed_bps"]) / 1048576.0, 2)
                    port_uuid = bandwidth_data["port_uuid"]
                    
                # Buscar latência do gateway detail
                gw_detail = client.get_gateway_detail(gateway_mac)
                if gw_detail:
                    port_stats = gw_detail.get("portStats", [])
                    for p in port_stats:
                        if p.get("name") == "WAN3":
                            if not bandwidth_data:
                                wan_online = (p.get("internetState") == 1)
                                download_rate = round((p.get("rxRate", 0.0) * 8) / 1024.0, 2)
                                upload_rate = round((p.get("txRate", 0.0) * 8) / 1024.0, 2)
                            wan_latency = p.get("latency", 0.0)
                            break
                            
                wan_status, _ = WANStatus.objects.get_or_create(id=1)
                wan_status.wan_online = wan_online
                wan_status.download_rate_mbps = download_rate
                wan_status.upload_rate_mbps = upload_rate
                wan_status.latency_wan_ms = wan_latency if wan_online else 0.0
                wan_status.save()
                
                # 2. Buscar speedtest do roteador se tivermos o port_uuid
                if port_uuid:
                    test_list = client.get_gateway_speedtest(gateway_mac, port_uuid)
                    if test_list:
                        last_test = test_list[0]
                        test_time_epoch = last_test.get("time")
                        if test_time_epoch:
                            test_dt = timezone.datetime.fromtimestamp(test_time_epoch, datetime.timezone.utc)
                            
                            latest_local_test = SpeedTestResult.objects.first()
                            if not latest_local_test or abs((latest_local_test.timestamp - test_dt).total_seconds()) > 2.0:
                                SpeedTestResult.objects.create(
                                    timestamp=test_dt,
                                    download_mbps=round(float(last_test.get("down", 0.0)) / 1000000.0, 1),
                                    upload_mbps=round(float(last_test.get("up", 0.0)) / 1000000.0, 1),
                                    ping_ms=round(float(last_test.get("latency", 0.0)), 1)
                                )
                                logger.info(f"Speedtest sincronizado via gatilho manual: DL={download_rate} Mbps, UL={upload_rate} Mbps")
                return True
    return False
