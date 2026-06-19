from django.db import models
from django.utils import timezone


class AdGuardConfig(models.Model):
    name = models.CharField(max_length=100, default="AdGuard Primary")
    url = models.CharField(max_length=255, default="http://192.168.10.1:3000", help_text="URL do AdGuard Home (API)")
    username = models.CharField(max_length=100, default="admin")
    password = models.CharField(max_length=100, default="senha")
    enabled = models.BooleanField(default=False)
    
    # Cached statistics
    total_queries = models.IntegerField(default=0)
    blocked_queries = models.IntegerField(default=0)
    blocked_percentage = models.FloatField(default=0.0)
    is_active = models.BooleanField(default=False)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuração AdGuard"
        verbose_name_plural = "Configurações AdGuard"

    def __str__(self):
        return f"{self.name} ({self.url})"


class Service(models.Model):
    TYPE_CHOICES = [
        ('ping', 'Ping (ICMP/Subprocess)'),
        ('tcp', 'Porta TCP'),
        ('http', 'Requisição HTTP (200 OK)'),
    ]

    name = models.CharField(max_length=100, help_text="Nome do serviço (ex: Servidor Proxmox)")
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, default='ping')
    target = models.CharField(max_length=255, help_text="IP, domínio ou URL (ex: 192.168.10.100 ou http://192.168.10.100:8123)")
    port = models.IntegerField(blank=True, null=True, help_text="Necessário apenas para conexões TCP (ex: 8006)")
    enabled = models.BooleanField(default=True)
    
    # State fields
    is_online = models.BooleanField(default=False)
    latency_ms = models.FloatField(default=0.0, help_text="Tempo de resposta em milissegundos")
    last_checked = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Serviço Monitorado"
        verbose_name_plural = "Serviços Monitorados"

    def __str__(self):
        return f"{self.name} ({self.type.upper()}: {self.target})"


class SpeedTestResult(models.Model):
    timestamp = models.DateTimeField(default=timezone.now)
    download_mbps = models.FloatField()
    upload_mbps = models.FloatField()
    ping_ms = models.FloatField()

    class Meta:
        ordering = ['-timestamp']
        verbose_name = "Resultado Speedtest"
        verbose_name_plural = "Resultados Speedtest"

    def __str__(self):
        return f"{timezone.localtime(self.timestamp).strftime('%d/%m/%Y %H:%M')} - DL: {self.download_mbps:.1f} Mbps | UL: {self.upload_mbps:.1f} Mbps"


class NetworkDevice(models.Model):
    mac = models.CharField(max_length=17, unique=True, help_text="MAC Address do equipamento")
    name = models.CharField(max_length=100, blank=True, null=True)
    ip = models.CharField(max_length=45, blank=True, null=True)
    model = models.CharField(max_length=50, blank=True, null=True)
    status = models.CharField(max_length=50, default="Desconectado")
    clients = models.IntegerField(default=0, help_text="Número de clientes Wi-Fi conectados")
    
    # Novas métricas de hardware (para Roteador/Switches)
    cpu_util = models.IntegerField(default=0, help_text="Uso de CPU em %")
    mem_util = models.IntegerField(default=0, help_text="Uso de Memória em %")
    temperature = models.FloatField(default=0.0, help_text="Temperatura do dispositivo em °C")
    
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Dispositivo Omada"
        verbose_name_plural = "Dispositivos Omada"

    def __str__(self):
        return f"{self.name or self.model or self.mac} ({self.status})"


class WifiNetwork(models.Model):
    name = models.CharField(max_length=100, unique=True, help_text="SSID da Rede Wi-Fi")
    band = models.CharField(max_length=50, default="2.4G / 5G")
    enabled = models.BooleanField(default=True)
    clients = models.IntegerField(default=0)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Rede Wi-Fi"
        verbose_name_plural = "Redes Wi-Fi"

    def __str__(self):
        status_str = "Ativa" if self.enabled else "Inativa"
        return f"{self.name} ({status_str})"


class WANStatus(models.Model):
    gateway_ip = models.CharField(max_length=45, default="192.168.10.1")
    wan_online = models.BooleanField(default=False)
    dns_primary_online = models.BooleanField(default=False) # 192.168.10.253
    dns_secondary_online = models.BooleanField(default=False) # 192.168.10.1
    latency_wan_ms = models.FloatField(default=0.0) # Ping to 1.1.1.1
    download_rate_mbps = models.FloatField(default=0.0, help_text="Consumo atual de download em Mbps")
    upload_rate_mbps = models.FloatField(default=0.0, help_text="Consumo atual de upload em Mbps")
    last_checked = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Status da WAN e DNS"
        verbose_name_plural = "Status da WAN e DNS"

    def __str__(self):
        status_str = "Internet OK" if self.wan_online else "Internet Offline"
        return f"{status_str} (Latência: {self.latency_wan_ms:.1f}ms)"


class SystemConfig(models.Model):
    speedtest_report_enabled = models.BooleanField(default=False, verbose_name="Relatório Speedtest Ativo")
    speedtest_report_time = models.TimeField(default="08:00", verbose_name="Horário do Relatório")
    last_report_sent_date = models.DateField(null=True, blank=True, verbose_name="Última data em que o relatório foi enviado")

    class Meta:
        verbose_name = "Configuração do Sistema"
        verbose_name_plural = "Configurações do Sistema"

    def __str__(self):
        return f"Configurações Gerais (Relatório: {'Ativo' if self.speedtest_report_enabled else 'Inativo'} às {self.speedtest_report_time})"
