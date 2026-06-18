import requests
import logging

logger = logging.getLogger(__name__)

def fetch_adguard_status(config):
    """
    Coleta estatísticas em tempo real do AdGuard Home.
    Se estiver desabilitado ou falhar, retorna estatísticas simuladas.
    """
    if config and config.enabled:
        try:
            base_url = config.url.rstrip('/')
            
            # Cabeçalhos comuns e autenticação básica
            auth = (config.username, config.password) if config.username and config.password else None
            
            # 1. Obter estatísticas (/control/stats)
            stats_url = f"{base_url}/control/stats"
            stats_response = requests.get(stats_url, auth=auth, timeout=3)
            
            # 2. Obter status geral (/control/status)
            status_url = f"{base_url}/control/status"
            status_response = requests.get(status_url, auth=auth, timeout=3)
            
            if stats_response.status_code == 200 and status_response.status_code == 200:
                stats_data = stats_response.json()
                status_data = status_response.json()
                
                total_queries = stats_data.get("num_dns_queries", 0)
                blocked_queries = stats_data.get("num_blocked_filtering", 0)
                
                blocked_percentage = 0.0
                if total_queries > 0:
                    blocked_percentage = (blocked_queries / total_queries) * 100
                    
                is_active = status_data.get("protection_enabled", False)
                
                return {
                    "total_queries": total_queries,
                    "blocked_queries": blocked_queries,
                    "blocked_percentage": blocked_percentage,
                    "is_active": is_active,
                    "is_simulated": False
                }
            else:
                logger.error(f"Erro na API AdGuard. HTTP Stats: {stats_response.status_code}, Status: {status_response.status_code}")
        except Exception as e:
            logger.error(f"Falha de conexão com o AdGuard Home em {config.url}: {e}")
            
    # --- Dados Simulados (Mock) para Desenvolvimento ---
    # Se for o AdGuard do IP 10.1 ou 10.253, retornamos estatísticas ligeiramente diferentes para ficar dinâmico
    is_primary = "253" in (config.url if config else "")
    
    if is_primary:
        return {
            "total_queries": 34850,
            "blocked_queries": 6273,
            "blocked_percentage": 18.0,
            "is_active": True,
            "is_simulated": True
        }
    else:
        return {
            "total_queries": 15420,
            "blocked_queries": 1230,
            "blocked_percentage": 8.0,
            "is_active": True,
            "is_simulated": True
        }
