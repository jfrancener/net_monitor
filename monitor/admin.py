from django.contrib import admin
from .models import OmadaConfig, AdGuardConfig, Service, SpeedTestResult, NetworkDevice, WANStatus, WifiNetwork

@admin.register(OmadaConfig)
class OmadaConfigAdmin(admin.ModelAdmin):
    list_display = ('url', 'username', 'site_name', 'enabled', 'last_updated')

@admin.register(AdGuardConfig)
class AdGuardConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'url', 'enabled', 'is_active', 'total_queries', 'blocked_queries', 'blocked_percentage', 'last_updated')

@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'type', 'target', 'port', 'enabled', 'is_online', 'latency_ms', 'last_checked')
    list_filter = ('type', 'enabled', 'is_online')
    search_fields = ('name', 'target')

@admin.register(SpeedTestResult)
class SpeedTestResultAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'download_mbps', 'upload_mbps', 'ping_ms')
    list_filter = ('timestamp',)

@admin.register(NetworkDevice)
class NetworkDeviceAdmin(admin.ModelAdmin):
    list_display = ('name', 'ip', 'mac', 'model', 'status', 'clients', 'last_seen')
    list_filter = ('status', 'model')
    search_fields = ('name', 'ip', 'mac')

@admin.register(WANStatus)
class WANStatusAdmin(admin.ModelAdmin):
    list_display = ('gateway_ip', 'wan_online', 'dns_primary_online', 'dns_secondary_online', 'latency_wan_ms', 'last_checked')

@admin.register(WifiNetwork)
class WifiNetworkAdmin(admin.ModelAdmin):
    list_display = ('name', 'band', 'enabled', 'clients', 'last_updated')
