#!/bin/bash
# /usr/local/bin/turn-ip.sh. Pone en /etc/turnserver.conf la IP pública (la del dominio,
# que mantenga al día tu DNS dinámico) y la local del servidor, y reinicia coturn si alguna ha cambiado.
# coturn solo lee external-ip al arrancar y la IP pública es dinámica: por eso va en /etc/cron.d/turn-ip
# cada 5 minutos. Con --forzar reinicia coturn aunque no haya cambios.
CONF=/etc/turnserver.conf
DOMINIO=TU_DOMINIO

publica=$(getent ahostsv4 "$DOMINIO" | awk 'NR==1 {print $1}')
local_=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')
ip_valida() { [[ $1 =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; }
if ! ip_valida "$publica" || ! ip_valida "$local_"; then
  logger -t turn-ip "no sé la IP pública ($publica) o la local ($local_); no cambio nada"
  exit 0
fi

cambio=no
if ! grep -qxF "external-ip=$publica/$local_" "$CONF" || ! grep -qxF "relay-ip=$local_" "$CONF"; then
  sed -i -e "s#^external-ip=.*#external-ip=$publica/$local_#" -e "s#^relay-ip=.*#relay-ip=$local_#" "$CONF"
  cambio=sí
fi
if [ "$cambio" = sí ] || [ "$1" = --forzar ]; then
  systemctl restart coturn
  logger -t turn-ip "coturn con IP pública $publica y local $local_ (cambio: $cambio)"
fi
