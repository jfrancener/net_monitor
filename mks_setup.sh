#!/bin/bash

# ==============================================================================
# Script de Configuração Automatizada do Monitor de Rede no MKS Pi
# Executar como root: sudo ./mks_setup.sh
# ==============================================================================

# Cores para logs
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # Sem cor

echo -e "${BLUE}=== 1. Atualizando sistema e instalando dependências do Linux ===${NC}"
apt update
apt install -y python3-pip python3-venv xorg unclutter lightdm chromium-browser

# Caminho absoluto do projeto
PROJECT_DIR=$(pwd)
echo -e "${GREEN}Diretório do projeto detectado: ${PROJECT_DIR}${NC}"

echo -e "${BLUE}=== 2. Configurando o Ambiente Virtual Python ===${NC}"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo -e "${BLUE}=== 3. Criando Serviço do Systemd para o Django ===${NC}"
SERVICE_FILE="/etc/systemd/system/netmonitor.service"

cat <<EOT > $SERVICE_FILE
[Unit]
Description=Django Network Monitor Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/.venv/bin/python $PROJECT_DIR/manage.py runserver 0.0.0.0:8000
Restart=always
RestartSec=5
# Define RUN_MAIN=true para o Django scheduler
Environment=RUN_MAIN=true

[Install]
WantedBy=multi-user.target
EOT

echo -e "${GREEN}Serviço criado em: ${SERVICE_FILE}${NC}"
systemctl daemon-reload
systemctl enable netmonitor.service
systemctl start netmonitor.service
echo -e "${GREEN}Serviço netmonitor iniciado e ativado no boot!${NC}"

echo -e "${BLUE}=== 4. Criando script do Modo Kiosk (Chromium) ===${NC}"
KIOSK_SCRIPT="$PROJECT_DIR/kiosk.sh"

cat <<EOT > $KIOSK_SCRIPT
#!/bin/bash
# Desativa salvamento de tela e gerenciamento de energia
xset -dpms
xset s off
xset s noblank

# Esconde o cursor do mouse se ficar parado
unclutter -idle 0.5 -root &

# Abre o Chromium em tela cheia apontando para o painel de leitura local
chromium-browser \\
  --noerrdialogs \\
  --disable-infobars \\
  --kiosk \\
  --app=http://localhost:8000/display/ \\
  --disable-translate \\
  --no-first-run \\
  --fast \\
  --fast-start \\
  --disable-features=TranslateUI \\
  --disk-cache-dir=/dev/null \\
  --disk-cache-size=1 \\
  --password-store=basic
EOT

chmod +x $KIOSK_SCRIPT
echo -e "${GREEN}Script de kiosk criado em: ${KIOSK_SCRIPT}${NC}"

echo -e "${YELLOW}=== PRÓXIMOS PASSOS (MANUAL) ===${NC}"
echo -e "Para fazer a tela TS35 exibir o Chromium no boot:"
echo -e "1. Configure o sistema para entrar em modo gráfico automaticamente (pode usar 'sudo raspi-config' ou similar no Armbian)."
echo -e "2. Se estiver usando o Openbox/Matchbox (gerenciador leve), adicione a seguinte linha no arquivo autostart do gerenciador:"
echo -e "   ${GREEN}$KIOSK_SCRIPT &${NC}"
echo -e "3. Ou você pode iniciar o X manualmente no boot adicionando no arquivo ~/.bash_profile do usuário principal:"
echo -e "   ${GREEN}xinit $KIOSK_SCRIPT -- -nocursor${NC}"
echo -e "4. Acesse o painel de gerenciamento do seu PC em: ${GREEN}http://<IP_DO_MKS_PI>:8000/${NC}"
echo -e "=============================================================================="
