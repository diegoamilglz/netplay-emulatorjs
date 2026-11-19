#!/bin/bash
# Instala netplay.py como servicio y coturn como TURN propio, en Debian o parecidos.
# Uso: sudo DOMINIO=tu.dominio.example bash instalar.sh
#   DOMINIO es el nombre público del servidor: los navegadores lo usan para llegar al TURN, y turn-ip.sh
#   saca de él la IP pública. No toca el servidor web ni el firewall (ver el README).
set -e
O=$(cd "$(dirname "$0")" && pwd)
[[ ${DOMINIO:-} =~ ^[A-Za-z0-9.-]+$ ]] || { echo "Uso: sudo DOMINIO=tu.dominio.example bash instalar.sh" >&2; exit 1; }

# Secreto compartido entre coturn y netplay.py (se crea una vez y se conserva)
install -d -m 700 /etc/netplay
if [ ! -s /etc/netplay/turn-secreto ]; then
  (umask 077; python3 -c 'import secrets; print(secrets.token_hex(32))' > /etc/netplay/turn-secreto)
fi

# netplay.py: servicio en 127.0.0.1:9082, con usuario temporal y sin salida a internet
install -d -m 755 /opt/netplay
install -m 644 "$O/netplay.py" /opt/netplay/netplay.py
sed "s#TU_DOMINIO#$DOMINIO#g" "$O/netplay.service" > /etc/systemd/system/netplay.service
systemctl daemon-reload
systemctl enable --now netplay
systemctl restart netplay

# coturn
if ! dpkg -s coturn >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y coturn
fi
[ -f /etc/turnserver.conf.original ] || cp -p /etc/turnserver.conf /etc/turnserver.conf.original
# Las IP quedan de muestra (IP_LOCAL, IP_PUBLICA) hasta que turn-ip.sh, justo después, pone las de verdad
sed -e "s#^static-auth-secret=.*#static-auth-secret=$(cat /etc/netplay/turn-secreto)#" -e "s#TU_DOMINIO#$DOMINIO#g" \
  "$O/turnserver.conf" > /etc/turnserver.conf.nuevo
chown root:turnserver /etc/turnserver.conf.nuevo
chmod 640 /etc/turnserver.conf.nuevo
mv /etc/turnserver.conf.nuevo /etc/turnserver.conf
sed "s#TU_DOMINIO#$DOMINIO#g" "$O/turn-ip.sh" > /usr/local/bin/turn-ip.sh
chmod 755 /usr/local/bin/turn-ip.sh
install -m 644 "$O/turn-ip.cron" /etc/cron.d/turn-ip
systemctl enable coturn
/usr/local/bin/turn-ip.sh --forzar
if grep -q 'IP_LOCAL\|IP_PUBLICA' /etc/turnserver.conf; then
  echo "turn-ip.sh no ha sabido la IP pública o la local: mira journalctl -t turn-ip" >&2
  exit 1
fi
sleep 1
systemctl is-active netplay coturn || true  # solo informativo: con "set -e" cortaría el script
curl -s 'http://127.0.0.1:9082/list?domain=x&game_id=1' && echo "  <- netplay.py responde"
echo "Falta: el proxy del servidor web hacia 127.0.0.1:9082, y abrir (y reenviar en el router) 3478 TCP/UDP y 49160-49200 UDP"
