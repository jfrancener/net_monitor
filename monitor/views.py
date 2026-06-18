import os
import random
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.utils import timezone
from .models import Service, OmadaConfig, AdGuardConfig, WANStatus, NetworkDevice, SpeedTestResult, WifiNetwork
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


def display_view(request):
    """Exibe o Dashboard otimizado para o display físico TS35 (480x320)."""
    wan = WANStatus.objects.first()
    devices = NetworkDevice.objects.all().order_by('-status', 'name')
    services = Service.objects.filter(enabled=True).order_by('name')
    
    # Separa servidores DNS de outros serviços
    dns_services = []
    other_services = []
    for s in services:
        if "dns" in s.name.lower() or s.target in ["192.168.10.253", "192.168.10.1", "192.168.10.253:53", "192.168.10.1:53"]:
            dns_services.append(s)
        else:
            other_services.append(s)
    
    # Ordena para garantir o DNS 10.253 no topo
    dns_services.sort(key=lambda x: "253" in x.target, reverse=True)
    
    adguards = AdGuardConfig.objects.filter(enabled=True)
    latest_speedtest = SpeedTestResult.objects.first()
    ssids = WifiNetwork.objects.all().order_by('-enabled', 'name')
    omada_cfg = OmadaConfig.objects.first()

    context = {
        'wan': wan,
        'devices': devices,
        'dns_services': dns_services,
        'other_services': other_services,
        'adguards': adguards,
        'latest_speedtest': latest_speedtest,
        'ssids': ssids,
        'omada_cfg': omada_cfg,
        'cpu_temp': get_cpu_temp(),
        'ram_usage': get_ram_usage(),
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
            
        # 2. Configurar Omada
        elif action == "save_omada":
            url = request.POST.get("url")
            username = request.POST.get("username")
            password = request.POST.get("password")
            site_name = request.POST.get("site_name")
            enabled = request.POST.get("enabled") == "on"
            
            cfg, _ = OmadaConfig.objects.get_or_create(id=1)
            cfg.url = url
            cfg.username = username
            if password:  # Atualiza senha apenas se preenchido
                cfg.password = password
            cfg.site_name = site_name
            cfg.enabled = enabled
            cfg.save()
            return redirect('dashboard')
            
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

    # Busca os dados para listar na tela
    services = Service.objects.all().order_by('name')
    omada_cfg = OmadaConfig.objects.first()
    adguards = AdGuardConfig.objects.all()
    speedtests = SpeedTestResult.objects.all()[:10]
    wan = WANStatus.objects.first()

    context = {
        'services': services,
        'omada_cfg': omada_cfg,
        'adguards': adguards,
        'speedtests': speedtests,
        'wan': wan,
        'is_speedtesting': is_speedtesting
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


def api_status(request):
    """
    Retorna os dados completos do monitoramento em JSON.
    Muito útil para atualizar o painel do display TS35 via JavaScript de forma assíncrona.
    """
    wan = WANStatus.objects.first()
    devices = NetworkDevice.objects.all().order_by('-status', 'name')
    services = Service.objects.filter(enabled=True).order_by('name')
    
    # Separa servidores DNS de outros serviços
    dns_services = []
    other_services = []
    for s in services:
        if "dns" in s.name.lower() or s.target in ["192.168.10.253", "192.168.10.1", "192.168.10.253:53", "192.168.10.1:53"]:
            dns_services.append(s)
        else:
            other_services.append(s)
    
    # Ordena DNS
    dns_services.sort(key=lambda x: "253" in x.target, reverse=True)
    
    adguards = AdGuardConfig.objects.filter(enabled=True)
    latest_speedtest = SpeedTestResult.objects.first()
    omada_cfg = OmadaConfig.objects.first()
    omada_enabled = omada_cfg.enabled if omada_cfg else False
    omada_connected = omada_cfg.is_connected if (omada_cfg and omada_cfg.enabled) else False

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
            "is_speedtesting": is_speedtesting,
        },
        "speedtest": {
            "download": latest_speedtest.download_mbps if latest_speedtest else 0.0,
            "upload": latest_speedtest.upload_mbps if latest_speedtest else 0.0,
            "ping": latest_speedtest.ping_ms if latest_speedtest else 0.0,
            "time": latest_speedtest.timestamp.strftime('%H:%M') if latest_speedtest else "N/A",
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
        "dns_services": [
            {
                "name": s.name,
                "target": f"{s.target}:{s.port}" if s.port else s.target,
                "is_online": s.is_online,
                "latency_ms": s.latency_ms
            } for s in dns_services
        ],
        "other_services": [
            {
                "name": s.name,
                "target": f"{s.target}:{s.port}" if s.port else s.target,
                "is_online": s.is_online,
                "latency_ms": s.latency_ms
            } for s in other_services
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
    """Gatilho manual para sincronizar imediatamente com o Omada."""
    from .scheduler import sync_omada_now
    success = sync_omada_now()
    return JsonResponse({"success": success, "status": "Sincronizado" if success else "Falha na sincronização"})
