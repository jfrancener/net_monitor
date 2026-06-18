from django.apps import AppConfig


class MonitorConfig(AppConfig):
    name = 'monitor'

    def ready(self):
        from monitor.scheduler import start_scheduler
        start_scheduler()
