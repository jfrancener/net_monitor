from django.apps import AppConfig


class MonitorConfig(AppConfig):
    default_auto_field = 'django.db.models.AutoField'
    name = 'monitor'

    def ready(self):
        from monitor.scheduler import start_scheduler
        start_scheduler()
