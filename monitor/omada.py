import requests
import urllib3
import logging

# Desabilitar avisos de certificado SSL autoassinado (comum em redes locais)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

class OmadaClient:
    def __init__(self, url, username, password, site_name="Default"):
        self.base_url = url.rstrip('/')
        self.username = username
        self.password = password
        self.site_name = site_name
        self.session = requests.Session()
        self.session.verify = False  # Permite HTTPS sem verificar SSL local
        
        # Headers padrao para simular um navegador e requisicoes AJAX
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Omada-Request-Source": "web-local",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        
        self.token = None
        self.site_id = None
        self.omadac_id = None

    def login(self):
        try:
            # 1. Tentar obter o omadacId do controlador antes de logar (obrigatorio no Omada local)
            try:
                info_url = f"{self.base_url}/api/info"
                info_resp = self.session.get(info_url, timeout=5)
                if info_resp.status_code == 200:
                    info_data = info_resp.json()
                    self.omadac_id = info_data.get("result", {}).get("omadacId")
                    logger.info(f"OmadacId obtido com sucesso: {self.omadac_id}")
            except Exception as ie:
                logger.warning(f"Nao foi possivel obter o omadacId de /api/info (usando fallback): {ie}")

            # 2. Construir a URL de login apropriada
            if self.omadac_id:
                url = f"{self.base_url}/{self.omadac_id}/api/v2/login"
            else:
                url = f"{self.base_url}/api/v2/login"

            payload = {
                "username": self.username,
                "password": self.password
            }
            
            response = self.session.post(url, json=payload, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("errorCode") == 0:
                    self.token = data.get("result", {}).get("token")
                    
                    # Salva o omadacId se nao tiver pego antes mas veio no login
                    if not self.omadac_id:
                        self.omadac_id = data.get("result", {}).get("omadacId")
                        
                    # Configura os tokens nos cabecalhos (Csrf-Token com hifen eh o correto no Omada v5)
                    self.session.headers.update({
                        "Csrf-Token": self.token,
                        "CsrfToken": self.token,
                        "token": self.token
                    })
                    return True
                else:
                    logger.error(f"Erro no login Omada: {data.get('msg')}")
            return False
        except Exception as e:
            logger.error(f"Falha de conexao ao autenticar no Omada: {e}")
            return False

    def get_site_id(self):
        if not self.token and not self.login():
            return None
        
        try:
            # 1. Tentar buscar os privilégios do usuário atual para obter os IDs reais dos sites
            url = f"{self.base_url}/{self.omadac_id}/api/v2/users/current" if self.omadac_id else f"{self.base_url}/api/v2/users/current"
            response = self.session.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("errorCode") == 0:
                    sites = data.get("result", {}).get("privilege", {}).get("sites", [])
                    for site in sites:
                        # No users/current, a estrutura costuma conter 'name' e 'key' (que eh o ID do site)
                        if site.get("name").lower() == self.site_name.lower():
                            self.site_id = site.get("key")
                            logger.info(f"Site ID real para '{self.site_name}' obtido de users/current: {self.site_id}")
                            return self.site_id
                    
                    if sites:
                        self.site_id = sites[0].get("key")
                        logger.info(f"Fallback site ID obtido de users/current: {self.site_id}")
                        return self.site_id

            # 2. Fallback caso users/current nao funcione
            url_sites = f"{self.base_url}/{self.omadac_id}/api/v2/sites" if self.omadac_id else f"{self.base_url}/api/v2/sites"
            response_sites = self.session.get(url_sites, timeout=5)
            if response_sites.status_code == 200:
                data = response_sites.json()
                if data.get("errorCode") == 0:
                    sites = data.get("result", {}).get("data", [])
                    # Se vier direto no result
                    if not sites and isinstance(data.get("result"), list):
                        sites = data.get("result")
                        
                    for site in sites:
                        name = site.get("name")
                        site_key = site.get("id") or site.get("key")
                        if name and name.lower() == self.site_name.lower():
                            self.site_id = site_key
                            return self.site_id
                    if sites:
                        self.site_id = sites[0].get("id") or sites[0].get("key")
                        return self.site_id
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar site ID do Omada: {e}")
            return None

    def get_devices(self):
        """Retorna os dispositivos gerenciados (APs, switches, gateways)"""
        if not self.site_id:
            self.get_site_id()
            
        if not self.site_id:
            return None
            
        try:
            url = f"{self.base_url}/{self.omadac_id}/api/v2/sites/{self.site_id}/devices" if self.omadac_id else f"{self.base_url}/api/v2/sites/{self.site_id}/devices"
            response = self.session.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("errorCode") == 0:
                    result = data.get("result", [])
                    # O result pode vir como lista de dispositivos ou dicionario contendo a chave data
                    if isinstance(result, list):
                        return result
                    elif isinstance(result, dict):
                        return result.get("data", [])
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar dispositivos no Omada: {e}")
            return None

    def get_ssids(self):
        """Retorna as redes Wi-Fi (SSIDs) configuradas no site, navegando pelos grupos WLAN."""
        if not self.site_id:
            self.get_site_id()
            
        if not self.site_id:
            return None
            
        try:
            # 1. Obter a lista de grupos WLAN (WLAN Groups)
            wlans_url = f"{self.base_url}/{self.omadac_id}/api/v2/sites/{self.site_id}/setting/wlans" if self.omadac_id else f"{self.base_url}/api/v2/sites/{self.site_id}/setting/wlans"
            response = self.session.get(wlans_url, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("errorCode") == 0:
                    groups = data.get("result", {}).get("data", [])
                    if not isinstance(groups, list) and isinstance(data.get("result"), list):
                        groups = data.get("result")
                        
                    all_ssids = []
                    # 2. Para cada grupo WLAN, buscar os SSIDs associados
                    for g in groups:
                        g_id = g.get("id") or g.get("key")
                        if g_id:
                            ssids_url = f"{self.base_url}/{self.omadac_id}/api/v2/sites/{self.site_id}/setting/wlans/{g_id}/ssids?currentPage=1&currentPageSize=100" if self.omadac_id else f"{self.base_url}/api/v2/sites/{self.site_id}/setting/wlans/{g_id}/ssids?currentPage=1&currentPageSize=100"
                            r_ssids = self.session.get(ssids_url, timeout=5)
                            if r_ssids.status_code == 200:
                                ssids_data = r_ssids.json()
                                if ssids_data.get("errorCode") == 0:
                                    ssids_list = ssids_data.get("result", {}).get("data", [])
                                    if not isinstance(ssids_list, list) and isinstance(ssids_data.get("result"), list):
                                        ssids_list = ssids_data.get("result")
                                    
                                    for s in ssids_list:
                                        all_ssids.append(s)
                    
                    if all_ssids:
                        return all_ssids

            # 3. Fallback caso a rota estruturada wlans/{id}/ssids falhe
            legacy_url = f"{self.base_url}/{self.omadac_id}/api/v2/sites/{self.site_id}/setting/wireless/ssids" if self.omadac_id else f"{self.base_url}/api/v2/sites/{self.site_id}/setting/wireless/ssids"
            response = self.session.get(legacy_url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("errorCode") == 0:
                    result = data.get("result", [])
                    if isinstance(result, list):
                        return result
                    elif isinstance(result, dict):
                        return result.get("data", [])
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar SSIDs no Omada: {e}")
            return None

    def get_gateway_detail(self, gateway_mac):
        """Retorna os detalhes de um gateway específico (incluindo status de portas WAN)"""
        if not self.site_id:
            self.get_site_id()
            
        if not self.site_id or not self.omadac_id:
            return None
            
        try:
            url = f"{self.base_url}/{self.omadac_id}/api/v2/sites/{self.site_id}/gateways/{gateway_mac}"
            response = self.session.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("errorCode") == 0:
                    return data.get("result", {})
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar detalhes do gateway {gateway_mac} no Omada: {e}")
            return None

    def get_wan_bandwidth(self):
        """Retorna o tráfego da WAN e o status do link em tempo real no dashboard do site"""
        if not self.site_id:
            self.get_site_id()
            
        if not self.site_id or not self.token or not self.omadac_id:
            return None
            
        try:
            url = f"{self.base_url}/openapi/v1/{self.omadac_id}/sites/{self.site_id}/dashboard/gateway/isp/load"
            response = self.session.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("errorCode") == 0:
                    gw_data_list = data.get("result", {}).get("data", [])
                    for gw in gw_data_list:
                        isp_arr = gw.get("ispInfo", {}).get("ispArr", [])
                        for isp in isp_arr:
                            if isp.get("name") == "WAN3":
                                return {
                                    "name": isp.get("name"),
                                    "online": isp.get("onLineStatus") == 1,
                                    "download_speed_bps": isp.get("downloadSpeed", 0.0),
                                    "upload_speed_bps": isp.get("uploadSpeed", 0.0),
                                    "port_uuid": isp.get("portUuid")
                                }
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar consumo de banda da WAN no Omada: {e}")
            return None

    def get_gateway_speedtest(self, gateway_mac, port_uuid):
        """Busca o histórico de testes de velocidade do gateway (speedtest do roteador)"""
        if not self.site_id:
            self.get_site_id()
            
        if not self.site_id or not self.token or not self.omadac_id or not port_uuid:
            return None
            
        try:
            url = f"{self.base_url}/openapi/v1/{self.omadac_id}/sites/{self.site_id}/gateways/{gateway_mac}/speedTestResult/dateList"
            payload = {
                "portUuid": port_uuid,
                "currentPage": 1,
                "currentPageSize": 5
            }
            response = self.session.post(url, json=payload, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("errorCode") == 0:
                    test_list = data.get("result", {}).get("data", [])
                    return test_list
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar histórico de speedtest do gateway no Omada: {e}")
            return None


def fetch_omada_status(config):
    """
    Função principal que tenta coletar dados reais do Omada.
    Se estiver desabilitado ou falhar, retorna None (sem dados simulados).
    """
    if config and config.enabled:
        client = OmadaClient(config.url, config.username, config.password, config.site_name)
        devices_data = client.get_devices()
        
        if devices_data is not None:
            devices = []
            for dev in devices_data:
                status_val = dev.get("status", 0)
                # No Omada local, status 14 significa conectado. Consideramos também 1 e 2 para compatibilidade.
                status_str = "Online" if status_val in [1, 2, 14] else "Offline"
                
                # Extração de métricas de hardware
                cpu = dev.get("cpuUtil", 0)
                mem = dev.get("memUtil", 0)
                temp = dev.get("temperature", 0.0)
                
                devices.append({
                    "mac": dev.get("mac"),
                    "name": dev.get("name") or dev.get("model"),
                    "ip": dev.get("ip"),
                    "model": dev.get("model"),
                    "status": status_str,
                    "clients": dev.get("clientNum", 0),
                    "cpu_util": cpu,
                    "mem_util": mem,
                    "temperature": temp,
                    "is_simulated": False
                })
            return devices
    return None


def fetch_omada_ssids(config):
    """
    Retorna as redes Wi-Fi ativas.
    Se estiver desabilitado ou falhar, retorna None.
    """
    if config and config.enabled:
        client = OmadaClient(config.url, config.username, config.password, config.site_name)
        ssids_data = client.get_ssids()
        if ssids_data is not None:
            ssids = []
            for ssid in ssids_data:
                # O Omada retorna o nome da rede no campo 'name' ou 'ssid'
                name = ssid.get("name") or ssid.get("ssid")
                
                # Mapeia as bandas de frequência (band: 1=2.4G, 2=5G, 3=2.4G/5G)
                band_val = ssid.get("band", 0)
                band_str = "2.4G" if band_val == 1 else "5G" if band_val == 2 else "2.4G / 5G"
                
                # Se o SSID está na lista de WLANs ativas, consideramos habilitado.
                clients_num = ssid.get("clientNum", 0)
                
                ssids.append({
                    "name": name,
                    "band": band_str,
                    "enabled": ssid.get("enabled", True) if "enabled" in ssid else ssid.get("broadcast", True),
                    "clients": clients_num,
                    "is_simulated": False
                })
            return ssids
    return None
