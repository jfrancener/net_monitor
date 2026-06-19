from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('display/', views.display_view, name='display'),
    path('service/delete/<int:service_id>/', views.delete_service, name='delete_service'),
    path('service/toggle/<int:service_id>/', views.toggle_service, name='toggle_service'),
    path('adguard/delete/<int:adguard_id>/', views.delete_adguard, name='delete_adguard'),
    path('adguard/toggle/<int:adguard_id>/', views.toggle_adguard, name='toggle_adguard'),
    path('api/status/', views.api_status, name='api_status'),
    path('api/trigger-speedtest/', views.api_trigger_speedtest, name='api_trigger_speedtest'),
]
