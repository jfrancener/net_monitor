import os
import time
import random
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.utils import timezone
from .models import Service, AdGuardConfig, WANStatus, NetworkDevice, SpeedTestResult, WifiNetwork, SystemConfig
from .scheduler import trigger_speedtest, is_speedtesting

logger = logging.getLogger(__name__)

def get_cpu_temp():
    """Lê a temperatura real da CPU no MKS Pi (Linux) ou simula se estiver no Windows."""
    try:
        if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp = float(f.read().strip()) / 1000.0
                return round(temp, 1)
    except Exception as e:
        logger.debug(f"Não foi possível ler temp da CPU: {e}")
    # Simulado para desenvolvimento
    return round(random.uniform(39.0, 43.5), 1)


def get_ram_usage():
    """Lê o uso real de RAM no Linux (/proc/meminfo) ou simula no desenvolvimento."""
    try:
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
                mem_total = 0
                mem_available = 0
                for line in lines:
                    if "MemTotal" in line:
                        mem_total = int(line.split()[1])
                    elif "MemAvailable" in line:
                        mem_available = int(line.split()[1])
                if mem_total > 0:
                    usage = ((mem_total - mem_available) / mem_total) * 100
                    return round(usage, 1)
    except Exception as e:
        logger.debug(f"Não foi possível ler uso de RAM: {e}")
    return round(random.uniform(48.0, 55.0), 1)


def get_cpu_usage():
    """Lê o uso real de CPU no Linux (/proc/stat) ou simula no desenvolvimento."""
    try:
        if os.path.exists("/proc/stat"):
            with open("/proc/stat", 'r') as f:
                fields = [float(column) for column in f.readline().strip().split()[1:]]
            idle, total = fields[3], sum(fields)
            
            time.sleep(0.05)
            with open("/proc/stat", 'r') as f:
                fields2 = [float(column) for column in f.readline().strip().split()[1:]]
            idle2, total2 = fields2[3], sum(fields2)
            
            idle_delta = idle2 - idle
            total_delta = total2 - total
            if total_delta > 0:
                return round((1.0 - idle_delta / total_delta) * 100.0, 1)
    except Exception as e:
        logger.debug(f"Erro ao ler uso de CPU: {e}")
    return round(random.uniform(8.0, 18.0), 1)


def display_view(request):
    """Exibe o Dashboard otimizado para o display físico TS35 (480x320)."""
    wan = WANStatus.objects.first()
    devices = NetworkDevice.objects.all().order_by('-status', 'name')
    services = Service.objects.filter(enabled=True).order_by('name')
    adguards = AdGuardConfig.objects.filter(enabled=True)
    
    # Mapeia hosts de AdGuard ativos para busca rápida
    adg_map = {}
    for adg in adguards:
        host = adg.url.replace("http://", "").replace("https://", "").split('/')[0].split(':')[0]
        adg_map[host] = adg
        
    # Separa servidores DNS de outros serviços
    dns_services = []
    other_services = []
    for s in services:
        if "dns" in s.name.lower() or s.target in ["192.168.10.253", "192.168.10.1", "192.168.10.253:53", "192.168.10.1:53"]:
            # É um DNS. Associa o blocked_percentage se corresponder a um AdGuard
            target_host = s.target.split(':')[0]
            blocked_pct = None
            if target_host in adg_map:
                blocked_pct = adg_map[target_host].blocked_percentage
            elif "253" in target_host and any("253" in k for k in adg_map):
                k = next(k for k in adg_map if "253" in k)
                blocked_pct = adg_map[k].blocked_percentage
            elif "10.1" in target_host and any("10.1" in k for k in adg_map):
                k = next(k for k in adg_map if "10.1" in k)
                blocked_pct = adg_map[k].blocked_percentage
                
            s.blocked_percentage = blocked_pct
            dns_services.append(s)
        else:
            other_services.append(s)
            
    # Adiciona os AdGuards cadastrados que não estão na lista de Service monitorados
    dns_targets = {s.target.split(':')[0] for s in dns_services}
    for host, adg in adg_map.items():
        if host not in dns_targets:
            class VirtualDNSService:
                def __init__(self, name, target, is_online, blocked_percentage):
                    self.name = name
                    self.target = target
                    self.is_online = is_online
                    self.blocked_percentage = blocked_percentage
                    self.port = None
                    self.latency_ms = 0.0
            
            dns_services.append(VirtualDNSService(adg.name, host, adg.is_active, adg.blocked_percentage))
            dns_targets.add(host)
            
    # Ordena para garantir o DNS 10.253 no topo se houver
    dns_services.sort(key=lambda x: "253" in x.target, reverse=True)
    
    # Separa outros serviços em OFF e ON
    other_services_off = [s for s in other_services if not s.is_online]
    other_services_on = [s for s in other_services if s.is_online]
    
    # Lista unificada final (DNS sempre em cima, depois outros OFF, depois outros ON)
    unified_services = dns_services + other_services_off + other_services_on
    
    latest_speedtest = SpeedTestResult.objects.first()
    ssids = WifiNetwork.objects.all().order_by('-enabled', 'name')
    
    # Lê configuração do Omada do .env e status do scheduler
    from .scheduler import omada_connected
    omada_env_url = os.environ.get("OMADA_URL")
    omada_env_user = os.environ.get("OMADA_USERNAME")
    omada_env_pass = os.environ.get("OMADA_PASSWORD")
    omada_enabled = bool(omada_env_url and omada_env_user and omada_env_pass)

    context = {
        'wan': wan,
        'devices': devices,
        'unified_services': unified_services,
        'latest_speedtest': latest_speedtest,
        'ssids': ssids,
        'omada_enabled': omada_enabled,
        'omada_connected': omada_connected,
        'cpu_temp': get_cpu_temp(),
        'ram_usage': get_ram_usage(),
        'cpu_usage': get_cpu_usage(),
        'is_speedtesting': is_speedtesting,
        'now': timezone.now()
    }
    return render(request, 'monitor/display.html', context)


def dashboard_view(request):
    """
    Interface de gerenciamento externa.
    Para simplificar o desenvolvimento inicial, não aplicaremos @login_required
    para que o usuário possa testar de imediato sem criar superusuário,
    mas deixaremos a estrutura pronta.
    """
    # Trata inserção de novos serviços se for POST
    if request.method == "POST":
        action = request.POST.get("action")
        
        # 1. Adicionar Serviço
        if action == "add_service":
            name = request.POST.get("name")
            type = request.POST.get("type")
            target = request.POST.get("target")
            port = request.POST.get("port")
            
            port_val = int(port) if port and port.isdigit() else None
            
            if name and target:
                Service.objects.create(
                    name=name,
                    type=type,
                    target=target,
                    port=port_val
                )
            return redirect('dashboard')
            
        # 2. Configurar Omada (Desabilitado - variáveis globais no .env)
        elif action == "save_omada":
            pass
            
        # 3. Configurar/Adicionar AdGuard
        elif action == "save_adguard":
            adg_id = request.POST.get("adguard_id")
            name = request.POST.get("name", "AdGuard")
            url = request.POST.get("url")
            username = request.POST.get("username")
            password = request.POST.get("password")
            enabled = request.POST.get("enabled") == "on"
            
            if adg_id:
                cfg = AdGuardConfig.objects.get(id=adg_id)
            else:
                cfg = AdGuardConfig()
                
            cfg.name = name
            cfg.url = url
            cfg.username = username
            if password:
                cfg.password = password
            cfg.enabled = enabled
            cfg.save()
            return redirect('dashboard')

        # 4. Editar Serviço
        elif action == "edit_service":
            service_id = request.POST.get("service_id")
            name = request.POST.get("name")
            type = request.POST.get("type")
            target = request.POST.get("target")
            port = request.POST.get("port")
            
            port_val = int(port) if port and port.isdigit() else None
            
            if service_id and name and target:
                try:
                    service = Service.objects.get(id=service_id)
                    service.name = name
                    service.type = type
                    service.target = target
                    service.port = port_val
                    service.save()
                except Service.DoesNotExist:
                    pass
            return redirect('dashboard')

        # 5. Salvar Configuração do Relatório Speedtest
        elif action == "save_config":
            enabled = request.POST.get("speedtest_report_enabled") == "on"
            report_time = request.POST.get("speedtest_report_time", "08:00")
            
            config, _ = SystemConfig.objects.get_or_create(id=1)
            config.speedtest_report_enabled = enabled
            if report_time:
                config.speedtest_report_time = report_time
            config.save()
            return redirect('dashboard')

        # 6. Disparar Relatório Speedtest Manualmente
        elif action == "send_speedtest_report":
            from .scheduler import generate_and_send_speedtest_report
            import threading
            threading.Thread(target=generate_and_send_speedtest_report, daemon=True).start()
            return redirect('dashboard')

    # Busca os dados para listar na tela
    services = Service.objects.all().order_by('name')
    adguards = AdGuardConfig.objects.all()
    speedtests = SpeedTestResult.objects.all()[:10]
    wan = WANStatus.objects.first()
    system_config, _ = SystemConfig.objects.get_or_create(id=1)

    context = {
        'services': services,
        'adguards': adguards,
        'speedtests': speedtests,
        'wan': wan,
        'is_speedtesting': is_speedtesting,
        'system_config': system_config
    }
    return render(request, 'monitor/dashboard.html', context)


@require_POST
def delete_service(request, service_id):
    """Exclui um serviço monitorado."""
    try:
        service = Service.objects.get(id=service_id)
        service.delete()
    except Service.DoesNotExist:
        pass
    return redirect('dashboard')


@require_POST
def toggle_service(request, service_id):
    """Ativa ou desativa temporariamente um serviço."""
    try:
        service = Service.objects.get(id=service_id)
        service.enabled = not service.enabled
        service.save()
    except Service.DoesNotExist:
        pass
    return redirect('dashboard')


@require_POST
def delete_adguard(request, adguard_id):
    """Exclui um servidor DNS AdGuard."""
    try:
        adg = AdGuardConfig.objects.get(id=adguard_id)
        adg.delete()
    except AdGuardConfig.DoesNotExist:
        pass
    return redirect('dashboard')


@require_POST
def toggle_adguard(request, adguard_id):
    """Ativa ou desativa temporariamente um servidor AdGuard."""
    try:
        adg = AdGuardConfig.objects.get(id=adguard_id)
        adg.enabled = not adg.enabled
        adg.save()
    except AdGuardConfig.DoesNotExist:
        pass
    return redirect('dashboard')


def api_status(request):
    """
    Retorna os dados completos do monitoramento em JSON.
    Muito útil para atualizar o painel do display TS35 via JavaScript de forma assíncrona.
    """
    wan = WANStatus.objects.first()
    devices = NetworkDevice.objects.all().order_by('-status', 'name')
    services = Service.objects.filter(enabled=True).order_by('name')
    adguards = AdGuardConfig.objects.filter(enabled=True)
    
    # Mapeia hosts de AdGuard ativos para busca rápida
    adg_map = {}
    for adg in adguards:
        host = adg.url.replace("http://", "").replace("https://", "").split('/')[0].split(':')[0]
        adg_map[host] = adg
        
    # Separa servidores DNS de outros serviços
    dns_services = []
    other_services = []
    for s in services:
        if "dns" in s.name.lower() or s.target in ["192.168.10.253", "192.168.10.1", "192.168.10.253:53", "192.168.10.1:53"]:
            # É um DNS. Associa o blocked_percentage se corresponder a um AdGuard
            target_host = s.target.split(':')[0]
            blocked_pct = None
            if target_host in adg_map:
                blocked_pct = adg_map[target_host].blocked_percentage
            elif "253" in target_host and any("253" in k for k in adg_map):
                k = next(k for k in adg_map if "253" in k)
                blocked_pct = adg_map[k].blocked_percentage
            elif "10.1" in target_host and any("10.1" in k for k in adg_map):
                k = next(k for k in adg_map if "10.1" in k)
                blocked_pct = adg_map[k].blocked_percentage
                
            s.blocked_percentage = blocked_pct
            dns_services.append(s)
        else:
            other_services.append(s)
            
    # Adiciona os AdGuards cadastrados que não estão na lista de Service monitorados
    dns_targets = {s.target.split(':')[0] for s in dns_services}
    for host, adg in adg_map.items():
        if host not in dns_targets:
            class VirtualDNSService:
                def __init__(self, name, target, is_online, blocked_percentage):
                    self.name = name
                    self.target = target
                    self.is_online = is_online
                    self.blocked_percentage = blocked_percentage
                    self.port = None
                    self.latency_ms = 0.0
            
            dns_services.append(VirtualDNSService(adg.name, host, adg.is_active, adg.blocked_percentage))
            dns_targets.add(host)
            
    # Ordena DNS
    dns_services.sort(key=lambda x: "253" in x.target, reverse=True)
    
    # Separa outros serviços em OFF e ON
    other_services_off = [s for s in other_services if not s.is_online]
    other_services_on = [s for s in other_services if s.is_online]
    
    # Lista unificada final
    unified_services = dns_services + other_services_off + other_services_on
    
    latest_speedtest = SpeedTestResult.objects.first()
    
    # Lê status da conexão do scheduler e do .env
    from .scheduler import omada_connected
    omada_env_url = os.environ.get("OMADA_URL")
    omada_env_user = os.environ.get("OMADA_USERNAME")
    omada_env_pass = os.environ.get("OMADA_PASSWORD")
    omada_enabled = bool(omada_env_url and omada_env_user and omada_env_pass)

    data = {
        "omada": {
            "enabled": omada_enabled,
            "connected": omada_connected,
        },
        "wan": {
            "wan_online": wan.wan_online if wan else False,
            "dns_primary_online": wan.dns_primary_online if wan else False,
            "dns_secondary_online": wan.dns_secondary_online if wan else False,
            "latency_wan_ms": wan.latency_wan_ms if wan else 0.0,
            "download_rate_mbps": wan.download_rate_mbps if (wan and wan.wan_online) else 0.0,
            "upload_rate_mbps": wan.upload_rate_mbps if (wan and wan.wan_online) else 0.0,
        },
        "system": {
            "cpu_temp": get_cpu_temp(),
            "ram_usage": get_ram_usage(),
            "cpu_usage": get_cpu_usage(),
            "is_speedtesting": is_speedtesting,
        },
        "speedtest": {
            "download": latest_speedtest.download_mbps if latest_speedtest else 0.0,
            "upload": latest_speedtest.upload_mbps if latest_speedtest else 0.0,
            "ping": latest_speedtest.ping_ms if latest_speedtest else 0.0,
            "time": timezone.localtime(latest_speedtest.timestamp).strftime('%H:%M') if latest_speedtest else "N/A",
        },
        "devices": [
            {
                "name": dev.name,
                "model": dev.model,
                "ip": dev.ip,
                "status": dev.status,
                "clients": dev.clients,
                "cpu_util": dev.cpu_util,
                "mem_util": dev.mem_util,
                "temperature": dev.temperature
            } for dev in devices
        ],
        "unified_services": [
            {
                "name": s.name,
                "target": f"{s.target}:{s.port}" if getattr(s, 'port', None) else s.target,
                "is_online": s.is_online,
                "latency_ms": getattr(s, 'latency_ms', 0.0),
                "is_dns": s in dns_services,
                "blocked_percentage": round(s.blocked_percentage, 1) if getattr(s, 'blocked_percentage', None) is not None else None
            } for s in unified_services
        ],
        "adguards": [
            {
                "name": adg.name,
                "url": adg.url,
                "is_active": adg.is_active,
                "total_queries": adg.total_queries,
                "blocked_queries": adg.blocked_queries,
                "blocked_percentage": round(adg.blocked_percentage, 1)
            } for adg in adguards
        ],
        "ssids": [
            {
                "name": ssid.name,
                "band": ssid.band,
                "enabled": ssid.enabled,
                "clients": ssid.clients
            } for ssid in WifiNetwork.objects.all().order_by('-enabled', 'name')
        ]
    }
    return JsonResponse(data)


def api_trigger_speedtest(request):
    """Gatilho manual para iniciar um teste de velocidade físico via Omada."""
    from .scheduler import trigger_omada_speedtest_now
    import os
    
    omada_env_url = os.environ.get("OMADA_URL")
    omada_env_user = os.environ.get("OMADA_USERNAME")
    omada_env_pass = os.environ.get("OMADA_PASSWORD")
    omada_enabled = bool(omada_env_url and omada_env_user and omada_env_pass)
    
    if not omada_enabled:
        return JsonResponse({
            "success": False, 
            "status": "Não configurado", 
            "error": "O controlador Omada não está configurado nas variáveis de ambiente. Testes de velocidade locais foram desativados para evitar medições incorretas devido à porta de rede de 100 Mbps do Raspberry Pi."
        })
        
    success = trigger_omada_speedtest_now()
    if success:
        return JsonResponse({"success": True, "status": "Teste físico de velocidade iniciado no roteador Omada."})
    else:
        return JsonResponse({
            "success": False, 
            "status": "Erro no trigger", 
            "error": "Não foi possível disparar o teste físico de velocidade no Omada. Verifique a conexão com o controlador ou se já há um teste em andamento."
        })

