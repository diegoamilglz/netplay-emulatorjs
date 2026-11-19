#!/bin/bash
# Copia EmulatorJS 4.3.0-pre y la corrige para usarla con netplay.py. No toca la carpeta original.
# Uso: bash parchear-ejs43.sh <carpeta "data" de EmulatorJS 4.3> <carpeta de destino>
# Se puede repetir: lo ya corregido se deja como está.
#   1. El netplay se conecta solo por WebSocket, en la ruta de EJS_netplayServer (p. ej. https://host/netplay).
#   2. Lee bien el informe de cada núcleo (si no, el de N64 carga la versión WebGL1 y se cuelga).
#   3. Por un TURN, la imagen va a 2,5 Mbit/s como por conexión directa (la 4.3 la baja a 0,9).
#   4. La imagen se manda como mucho a 480 líneas de alto, en vez del tamaño del lienzo del anfitrión.
set -e
EJS=${1:?falta la carpeta data de EmulatorJS 4.3}
DESTINO=${2:?falta la carpeta de destino}
[ -f "$EJS/loader.js" ] && [ -f "$EJS/emulator.min.js" ] || { echo "no encuentro EmulatorJS 4.3 en $EJS" >&2; exit 1; }
rm -rf "$DESTINO.nuevo"
cp -a "$EJS" "$DESTINO.nuevo"
# io(url) a secas probaría antes "long polling" y tomaría la ruta (p. ej. /netplay) como espacio de nombres
if grep -q 'io(this.url)' "$DESTINO.nuevo/emulator.min.js"; then
  sed -i 's#io(this.url)#io(new URL(this.url).origin,{path:new URL(this.url).pathname.replace(/\\/$/,"")+"/socket.io/",transports:["websocket"]})#' "$DESTINO.nuevo/emulator.min.js"
fi
grep -q 'transports:\["websocket"\]' "$DESTINO.nuevo/emulator.min.js" || { echo "no he podido cambiar la conexión del netplay" >&2; exit 1; }
# La 4.3.0-pre no lee bien el informe de cada núcleo (cores/reports/<núcleo>.json): downloadFile le
# devuelve el objeto de su caché en vez del JSON, así que cree que no hay informe, no sabe que el
# núcleo va con WebGL2 y carga el "legacy" (WebGL1). Con el de N64, algunos juegos se cuelgan al pasar
# del título ("memory access out of bounds" y pantalla en blanco). Aquí se saca el JSON de ese objeto.
# No se toca downloadFile porque los descompresores 7z y zip sí esperan el objeto de la caché
python3 - "$DESTINO.nuevo/emulator.min.js" <<'PY'
import sys
ruta = sys.argv[1]
texto = open(ruta, encoding='utf-8').read()
viejo = '"string"==typeof i.data?{}:i.data).buildStart||(console.warn("Could not fetch core report JSON at'
nuevo = ('"string"==typeof i.data?{}:i.data&&i.data.files?(()=>{try{return JSON.parse(new TextDecoder()'
         '.decode(i.data.files[0].bytes))}catch(e){return{}}})():i.data).buildStart||(console.warn('
         '"Could not fetch core report JSON at')
if nuevo in texto:
    print('informe de los núcleos: ya corregido')
elif texto.count(viejo) == 1:
    open(ruta, 'w', encoding='utf-8').write(texto.replace(viejo, nuevo))
    print('informe de los núcleos: corregido')
else:
    sys.exit('no encuentro dónde lee el informe de los núcleos')
PY
# Cuando la imagen va por un repetidor (TURN), la 4.3 la baja de 2,5 a 0,9 Mbit/s. El TURN es el nuestro
# (coturn, con su propio límite), así que se deja en 2,5 también por él
python3 - "$DESTINO.nuevo/emulator.min.js" <<'PY'
import sys
ruta = sys.argv[1]
texto = open(ruta, encoding='utf-8').read()
viejo, nuevo = 'maxBitrate=t?9e5:25e5', 'maxBitrate=25e5'
if viejo not in texto and 'maxFramerate=30,n.encodings[0].maxBitrate=25e5,' in texto:
    print('calidad por el TURN: ya corregida')
elif texto.count(viejo) == 1:
    open(ruta, 'w', encoding='utf-8').write(texto.replace(viejo, nuevo))
    print('calidad por el TURN: corregida')
else:
    sys.exit('no encuentro el límite de calidad por el TURN')
PY
# La imagen se manda al tamaño del lienzo del anfitrión (con la ventana grande, 965x813 o más), aunque la N64 dibuja
# a 320x240 o 640x480. Como la conexión empieza con poca velocidad y la 4.3 prefiere mantener los fps
# ("maintain-framerate"), los primeros segundos (y tras cada bajón) la baja a la mitad y se ve pixelada. Aquí se manda
# como mucho a 480 líneas de alto: la mitad de píxeles, así que la misma velocidad da una imagen más limpia
python3 - "$DESTINO.nuevo/emulator.min.js" <<'PY'
import sys
ruta = sys.argv[1]
texto = open(ruta, encoding='utf-8').read()
viejo = 'scaleResolutionDownBy=1;'
nuevo = 'scaleResolutionDownBy=Math.max(1,(this.emu&&this.emu.canvas&&this.emu.canvas.height||480)/480);'
if viejo not in texto and texto.count(nuevo) == 2:
    print('tamaño de la imagen: ya limitado')
elif texto.count(viejo) == 2:
    open(ruta, 'w', encoding='utf-8').write(texto.replace(viejo, nuevo))
    print('tamaño de la imagen: limitado a 480 líneas')
else:
    sys.exit('no encuentro el tamaño de la imagen del netplay')
PY
chmod -R a+rX,go-w "$DESTINO.nuevo"
rm -rf "$DESTINO"
mv "$DESTINO.nuevo" "$DESTINO"
echo "EmulatorJS 4.3 corregida en $DESTINO"
