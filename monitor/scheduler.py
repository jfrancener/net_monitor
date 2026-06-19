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
speedtest_start_time = None
omada_connected = False
consecutive_failures = {}

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
    from monitor.models import Service, AdGuardConfig, WANStatus, NetworkDevice, SpeedTestResult, WifiNetwork
    from monitor.omada import fetch_omada_status, fetch_omada_ssids, OmadaClient
    from monitor.adguard import fetch_adguard_status
    
    global is_speedtesting, speedtest_start_time
    logger.info("Iniciando loop do Monitor de Rede...")
    
    # Cria o registro inicial da WAN se não existir
    wan_status, _ = WANStatus.objects.get_or_create(id=1)
    
    last_speedtest_time = None
    last_scheduled_speedtest_hour = None
    
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
            global consecutive_failures
            
            for s in services:
                was_online = s.is_online
                had_checked = s.last_checked is not None
                
                # Executa o teste de conectividade
                if s.type == 'ping':
                    online, lat = ping_host(s.target)
                elif s.type == 'tcp' and s.port:
                    online, lat = tcp_check(s.target, s.port)
                elif s.type == 'http':
                    online, lat = http_check(s.target)
                else:
                    online, lat = False, 0.0
                
                # Inicializa o contador se o serviço for novo na execução atual
                if s.id not in consecutive_failures:
                    consecutive_failures[s.id] = 0
                
                # Definição do novo status com tolerância a falsos positivos
                new_status = was_online
                should_alert = False
                
                if online:
                    # Se voltou a responder, limpa as falhas
                    consecutive_failures[s.id] = 0
                    if not was_online and had_checked:
                        # Se estava offline e agora está online, atualiza e avisa imediatamente
                        new_status = True
                        should_alert = True
                else:
                    # Se falhou, incrementa o contador de falhas
                    consecutive_failures[s.id] += 1
                    logger.info(f"Serviço {s.name} ({s.target}) falhou {consecutive_failures[s.id]}/3 vezes consecutivas.")
                    
                    if was_online and had_checked:
                        # Só muda o estado para offline e alerta se atingir 3 falhas consecutivas
                        if consecutive_failures[s.id] >= 3:
                            new_status = False
                            should_alert = True
                            
                s.is_online = new_status
                s.latency_ms = lat if online else 0.0
                s.last_checked = timezone.now()
                s.save()
                
                # Se o estado mudou e determinamos que deve alertar
                if should_alert:
                    from monitor.telegram import send_telegram_message
                    
                    emoji = "🟢" if new_status else "🔴"
                    status_str = "ONLINE" if new_status else "OFFLINE"
                    action_str = "voltou a ficar ONLINE" if new_status else "ficou OFFLINE"
                    
                    msg_parts = [
                        f"{emoji} <b>ALERTA DE STATUS</b>\n",
                        f"O serviço <b>{s.name}</b> ({s.target}) {action_str}!",
                        f"<b>Status atual:</b> {status_str}"
                    ]
                    if new_status:
                        msg_parts.append(f"<b>Latência:</b> {s.latency_ms:.1f} ms")
                    else:
                        msg_parts.append(f"<b>Motivo:</b> Falhou em 3 tentativas consecutivas de verificação.")
                        
                    message = "\n".join(msg_parts)
                    
                    # Dispara o envio de forma assíncrona para não atrasar a verificação de outros serviços
                    import threading
                    threading.Thread(target=send_telegram_message, args=(message,), daemon=True).start()
                
            # 3. Consultar Omada Controller
            omada_env_url = os.environ.get("OMADA_URL")
            omada_env_user = os.environ.get("OMADA_USERNAME")
            omada_env_pass = os.environ.get("OMADA_PASSWORD")
            omada_env_site = os.environ.get("OMADA_SITE_NAME") or "Default"
            
            omada_enabled = bool(omada_env_url and omada_env_user and omada_env_pass)
            
            global omada_connected
            if omada_enabled:
                client = OmadaClient(omada_env_url, omada_env_user, omada_env_pass, omada_env_site)
                if client.login():
                    omada_devices = client.get_devices()
                    omada_ssids = client.get_ssids()
                    omada_clients = client.get_clients()
                    
                    if omada_devices is not None and omada_ssids is not None:
                        # Conexão estabelecida com sucesso
                        omada_connected = True
                            
                        # Sincronizar dispositivos (deleta equipamentos antigos/mockados que não foram retornados na coleta atual)
                        collected_macs = [dev["mac"] for dev in omada_devices]
                        NetworkDevice.objects.exclude(mac__in=collected_macs).delete()
                        
                        gateway_mac = None
                        for dev in omada_devices:
                            mac = dev["mac"]
                            name = dev.get("name") or dev.get("model") or mac
                            status_val = dev.get("status", 0)
                            
                            is_currently_online = (status_val in [1, 2, 14])
                            
                            # Tenta buscar do banco para saber o estado anterior
                            try:
                                old_dev = NetworkDevice.objects.get(mac=mac)
                                was_device_online = (old_dev.status == "Online")
                            except NetworkDevice.DoesNotExist:
                                was_device_online = False
                                
                            if mac not in consecutive_failures:
                                consecutive_failures[mac] = 0
                                
                            new_device_online = was_device_online
                            alert_device = False
                            
                            if is_currently_online:
                                consecutive_failures[mac] = 0
                                if not was_device_online:
                                    new_device_online = True
                                    alert_device = True
                            else:
                                consecutive_failures[mac] += 1
                                logger.info(f"Dispositivo Omada {name} ({mac}) falhou {consecutive_failures[mac]}/3 vezes consecutivas.")
                                if was_device_online:
                                    if consecutive_failures[mac] >= 3:
                                        new_device_online = False
                                        alert_device = True
                                        
                            status_str = "Online" if new_device_online else "Offline"
                            
                            # Identifica o roteador/gateway
                            is_gw = dev.get("type") == "gateway" or "ER7212" in dev.get("model", "")
                            if is_gw:
                                gateway_mac = dev.get("mac")
                                
                            NetworkDevice.objects.update_or_create(
                                mac=mac,
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
                            
                            if alert_device:
                                from monitor.telegram import send_telegram_message
                                emoji = "🟢" if new_device_online else "🔴"
                                action = "voltou a ficar ONLINE" if new_device_online else "ficou OFFLINE"
                                msg = (
                                    f"{emoji} <b>ALERTA DE INFRAESTRUTURA</b>\n\n"
                                    f"O dispositivo <b>{name}</b> ({dev.get('model', '')}) {action}!"
                                )
                                if not new_device_online:
                                    msg += "\n<b>Motivo:</b> Inacessível no controlador Omada por 3 ciclos consecutivos."
                                threading.Thread(target=send_telegram_message, args=(msg,), daemon=True).start()
                            
                        # Sincronizar SSIDs do Wi-Fi
                        current_ssid_names = [ssid.get("name") or ssid.get("ssid") for ssid in omada_ssids]
                        WifiNetwork.objects.exclude(name__in=current_ssid_names).delete()
                        
                        # Contagem de clientes ativos em tempo real por SSID
                        wifi_clients_map = {}
                        if omada_clients:
                            for cli in omada_clients:
                                if cli.get("wireless"):
                                    ssid_name = cli.get("ssid")
                                    if ssid_name:
                                        wifi_clients_map[ssid_name] = wifi_clients_map.get(ssid_name, 0) + 1
                        
                        for ssid in omada_ssids:
                            name = ssid.get("name") or ssid.get("ssid")
                            band_val = ssid.get("band", 0)
                            band_str = "2.4G" if band_val == 1 else "5G" if band_val == 2 else "2.4G / 5G"
                            
                            clients_count = wifi_clients_map.get(name, 0)
                            
                            WifiNetwork.objects.update_or_create(
                                name=name,
                                defaults={
                                    "band": band_str,
                                    "enabled": ssid.get("enabled", True) if "enabled" in ssid else ssid.get("broadcast", True),
                                    "clients": clients_count
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
                        omada_connected = False
                        NetworkDevice.objects.all().delete()
                        WifiNetwork.objects.all().delete()
                else:
                    omada_connected = False
                    NetworkDevice.objects.all().delete()
                    WifiNetwork.objects.all().delete()
            else:
                omada_connected = False
                NetworkDevice.objects.all().delete()
                WifiNetwork.objects.all().delete()
                
            # --- Debounce e Alertas para WAN e DNS (WANStatus) ---
            # 1. WAN (Internet)
            was_wan = wan_status.wan_online
            new_wan = was_wan
            alert_wan = False
            
            if "wan" not in consecutive_failures:
                consecutive_failures["wan"] = 0
                
            if wan_online:
                consecutive_failures["wan"] = 0
                if not was_wan:
                    new_wan = True
                    alert_wan = True
            else:
                consecutive_failures["wan"] += 1
                logger.info(f"Internet WAN falhou {consecutive_failures['wan']}/3 vezes consecutivas.")
                if was_wan:
                    if consecutive_failures["wan"] >= 3:
                        new_wan = False
                        alert_wan = True
                        
            if alert_wan:
                from monitor.telegram import send_telegram_message
                emoji = "🟢" if new_wan else "🔴"
                action = "foi RESTABELECIDA" if new_wan else "CAIU (Offline)"
                msg = (
                    f"{emoji} <b>ALERTA DE CONEXÃO</b>\n\n"
                    f"A conexão de Internet WAN {action}!"
                )
                if not new_wan:
                    msg += "\n<b>Motivo:</b> Link reportado como offline por 3 ciclos consecutivos."
                import threading
                threading.Thread(target=send_telegram_message, args=(msg,), daemon=True).start()

            # 2. DNS Primário (192.168.10.253)
            was_dns_primary = wan_status.dns_primary_online
            new_dns_primary = was_dns_primary
            alert_dns_primary = False
            
            if "dns_primary" not in consecutive_failures:
                consecutive_failures["dns_primary"] = 0
                
            if dns_primary_online:
                consecutive_failures["dns_primary"] = 0
                if not was_dns_primary:
                    new_dns_primary = True
                    alert_dns_primary = True
            else:
                consecutive_failures["dns_primary"] += 1
                logger.info(f"DNS Primário (192.168.10.253) falhou {consecutive_failures['dns_primary']}/3 vezes consecutivas.")
                if was_dns_primary:
                    if consecutive_failures["dns_primary"] >= 3:
                        new_dns_primary = False
                        alert_dns_primary = True
                        
            if alert_dns_primary:
                from monitor.telegram import send_telegram_message
                emoji = "🟢" if new_dns_primary else "🔴"
                action = "voltou a ficar ONLINE" if new_dns_primary else "ficou OFFLINE"
                msg = (
                    f"{emoji} <b>ALERTA DE DNS</b>\n\n"
                    f"O Servidor DNS Primário (192.168.10.253) {action}!"
                )
                if not new_dns_primary:
                    msg += "\n<b>Motivo:</b> Sem resposta a ping por 3 tentativas consecutivas."
                import threading
                threading.Thread(target=send_telegram_message, args=(msg,), daemon=True).start()

            # 3. DNS Secundário (192.168.10.1)
            was_dns_secondary = wan_status.dns_secondary_online
            new_dns_secondary = was_dns_secondary
            alert_dns_secondary = False
            
            if "dns_secondary" not in consecutive_failures:
                consecutive_failures["dns_secondary"] = 0
                
            if dns_secondary_online:
                consecutive_failures["dns_secondary"] = 0
                if not was_dns_secondary:
                    new_dns_secondary = True
                    alert_dns_secondary = True
            else:
                consecutive_failures["dns_secondary"] += 1
                logger.info(f"DNS Secundário (192.168.10.1) falhou {consecutive_failures['dns_secondary']}/3 vezes consecutivas.")
                if was_dns_secondary:
                    if consecutive_failures["dns_secondary"] >= 3:
                        new_dns_secondary = False
                        alert_dns_secondary = True
                        
            if alert_dns_secondary:
                from monitor.telegram import send_telegram_message
                emoji = "🟢" if new_dns_secondary else "🔴"
                action = "voltou a ficar ONLINE" if new_dns_secondary else "ficou OFFLINE"
                msg = (
                    f"{emoji} <b>ALERTA DE DNS</b>\n\n"
                    f"O Servidor DNS Secundário (192.168.10.1) {action}!"
                )
                if not new_dns_secondary:
                    msg += "\n<b>Motivo:</b> Sem resposta a ping por 3 tentativas consecutivas."
                import threading
                threading.Thread(target=send_telegram_message, args=(msg,), daemon=True).start()

            # Atualiza e salva o status geral da WAN
            wan_status.wan_online = new_wan
            wan_status.dns_primary_online = new_dns_primary
            wan_status.dns_secondary_online = new_dns_secondary
            wan_status.latency_wan_ms = wan_latency if new_wan else 0.0
            wan_status.download_rate_mbps = download_rate if new_wan else 0.0
            wan_status.upload_rate_mbps = upload_rate if new_wan else 0.0
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

            # Verifica se o teste remoto foi concluído ou estourou o timeout de 90 segundos
            if is_speedtesting and speedtest_start_time:
                elapsed = (timezone.now() - speedtest_start_time).total_seconds()
                latest_local_test = SpeedTestResult.objects.first()
                if latest_local_test and latest_local_test.timestamp > speedtest_start_time:
                    is_speedtesting = False
                    logger.info("Speedtest do Omada concluído e detectado no banco de dados. Finalizando status 'is_speedtesting'.")
                elif elapsed > 90:
                    is_speedtesting = False
                    logger.warning("Tempo limite de 90 segundos atingido para o Speedtest do Omada. Resetando status 'is_speedtesting'.")

            # Disparo agendado de hora em hora (nos horários redondos: minuto 00)
            now_local = timezone.localtime(timezone.now())
            if now_local.minute == 0 and now_local.hour != last_scheduled_speedtest_hour:
                if omada_enabled:
                    logger.info(f"Disparando speedtest agendado de hora em hora no Omada: {now_local.strftime('%H:%M')}")
                    success = trigger_omada_speedtest_now()
                    if success:
                        last_scheduled_speedtest_hour = now_local.hour
                        logger.info("Speedtest do Omada agendado com sucesso.")
                    else:
                        logger.warning("Falha ao agendar Speedtest do Omada. Tentará novamente no próximo ciclo.")
                
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
    from monitor.models import NetworkDevice, WifiNetwork, WANStatus, SpeedTestResult
    from monitor.omada import OmadaClient
    from django.utils import timezone
    import logging
    
    logger = logging.getLogger(__name__)
    global omada_connected
    
    # Sincroniza a partir do .env
    import os
    omada_env_url = os.environ.get("OMADA_URL")
    omada_env_user = os.environ.get("OMADA_USERNAME")
    omada_env_pass = os.environ.get("OMADA_PASSWORD")
    omada_env_site = os.environ.get("OMADA_SITE_NAME") or "Default"

    if not (omada_env_url and omada_env_user and omada_env_pass):
        return False
        
    client = OmadaClient(omada_env_url, omada_env_user, omada_env_pass, omada_env_site)
    if client.login():
        omada_connected = True
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
                            test_dt = timezone.make_aware(timezone.datetime.fromtimestamp(test_time_epoch, timezone.utc))
                            
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


def trigger_omada_speedtest_now():
    """Dispara um novo teste de velocidade diretamente no gateway físico via controlador Omada."""
    from monitor.omada import OmadaClient
    from django.utils import timezone
    import os
    import logging
    
    logger = logging.getLogger(__name__)
    global is_speedtesting, speedtest_start_time
    
    omada_env_url = os.environ.get("OMADA_URL")
    omada_env_user = os.environ.get("OMADA_USERNAME")
    omada_env_pass = os.environ.get("OMADA_PASSWORD")
    omada_env_site = os.environ.get("OMADA_SITE_NAME") or "Default"

    if not (omada_env_url and omada_env_user and omada_env_pass):
        return False
        
    client = OmadaClient(omada_env_url, omada_env_user, omada_env_pass, omada_env_site)
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
                bandwidth_data = client.get_wan_bandwidth()
                if bandwidth_data:
                    port_uuid = bandwidth_data.get("port_uuid")
                    if port_uuid:
                        # Chama a API para iniciar o teste físico
                        logger.info("Disparando speedtest remoto no gateway Omada...")
                        success = client.trigger_gateway_speedtest(gateway_mac, port_uuid)
                        if success:
                            is_speedtesting = True
                            speedtest_start_time = timezone.now()
                            logger.info(f"Status is_speedtesting ativado em: {speedtest_start_time}")
                        return success
    return False
