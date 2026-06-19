import os
import requests
import logging

logger = logging.getLogger(__name__)

def send_telegram_message(message):
    """Envia uma mensagem para o chat do Telegram configurado nas variáveis de ambiente."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        logger.warning("Telegram Alertas: TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurados no arquivo .env.")
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                logger.info("Alerta enviado com sucesso para o Telegram.")
                return True
            else:
                logger.error(f"Erro retornado da API do Telegram: {data.get('description')}")
        else:
            logger.error(f"Erro HTTP ao enviar mensagem para o Telegram. Status: {response.status_code}")
    except Exception as e:
        logger.error(f"Falha de conexão ao tentar enviar mensagem para o Telegram: {e}")
        
    return False
