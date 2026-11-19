# Netplay propio para EmulatorJS

*[English](README.md) · Español*

Lo necesario para jugar en red con [EmulatorJS](https://emulatorjs.org) 4.3 en tu propio servidor: un servidor de salas en Python, un repetidor TURN propio (coturn), correcciones para la versión previa 4.3.0 y una página de ejemplo.

En el juego en red de EmulatorJS 4.3 **solo emula el anfitrión**: manda la imagen y el sonido a los demás por WebRTC, de navegador a navegador, y los demás le devuelven sus teclas. No se desincroniza nunca, y el retraso es solo el de la conexión entre los dos. El de la 4.2.3 sincronizaba teclas y estados guardados, y su propio código dice que está roto.

## Qué hay

| Fichero | Qué hace |
|---|---|
| `netplay.py` | Servidor de salas (Socket.IO 4, solo por WebSocket), compatible con el cliente de la 4.3. No mueve vídeo: solo pone en contacto a los jugadores. Además da claves temporales para el TURN (`GET /turn`, válidas 24 h). |
| `parchear-ejs43.sh` | Copia EmulatorJS 4.3.0-pre y le aplica las correcciones de abajo. Se puede repetir. |
| `turnserver.conf`, `turn-ip.sh`, `turn-ip.cron` | coturn como repetidor para cuando dos navegadores no pueden conectarse directamente. `turn-ip.sh` mantiene al día la IP pública (para IP dinámica). |
| `instalar.sh` | Instala `netplay.py` como servicio y prepara coturn. |
| `ejemplo.html` | Una página con un juego, el juego en red y el arreglo del sonido. |

## Correcciones para EmulatorJS 4.3.0-pre

`parchear-ejs43.sh`, en `emulator.min.js`:

1. **Conexión**: el cliente llama a `io(url)` a secas, que prueba antes «long polling» y toma la ruta de la URL como espacio de nombres de Socket.IO. Se cambia a solo WebSocket, en `<ruta>/socket.io/`, para que el servidor pueda ir en cualquier ruta detrás de un proxy.
2. **Informe del núcleo**: `downloadFile` devuelve el objeto de la caché en vez del JSON de `cores/reports/<núcleo>.json`. El emulador cree que no hay informe, no sabe que el núcleo va con WebGL2 y carga la versión antigua (WebGL1). Con N64, algunos juegos se cuelgan nada más pasar del título.
3. **Velocidad por el TURN**: la 4.3 baja el vídeo a 0,9 Mbit/s cuando va por un repetidor. Con un TURN propio no hace falta, así que se deja en 2,5.
4. **Tamaño de la imagen**: manda el lienzo entero del anfitrión (965×813 o más con la ventana grande), aunque una N64 dibuja a 320×240 o 640×480. Como la conexión empieza lenta y la 4.3 prefiere mantener los fps, la imagen se ve pixelada los primeros segundos y tras cada bajón. Se limita a 480 líneas.

`ejemplo.html` arregla una cosa más: **los invitados no recibían sonido** con los núcleos que usan RWebAudio (N64 y otros), porque el anfitrión solo captura OpenAL o un `gameManager.audioNode`. Todo lo que va a los altavoces pasa por un nodo de ganancia que el juego en red sí puede capturar. Así, además, la barra de volumen funciona con esos núcleos; antes solo cambiaba el de OpenAL.

## Instalar

Requisitos: Linux con systemd (probado en Debian 13), `python3` y `python3-websockets` (13 o más nueva). `instalar.sh` instala `coturn` con apt.

1. Consigue la carpeta `data` de EmulatorJS 4.3.0-pre y corrígela:
   ```sh
   bash parchear-ejs43.sh <carpeta data de EmulatorJS 4.3> /var/www/html/data43
   ```
2. Instala el servicio y el TURN, con el nombre público de tu servidor:
   ```sh
   sudo DOMINIO=tu.dominio.example bash instalar.sh
   ```
3. Pasa `netplay.py` por el proxy de tu servidor web. Con Apache (`proxy`, `proxy_http`, `proxy_wstunnel`), primero la línea de `socket.io`:
   ```apache
   ProxyPass /netplay/socket.io/ ws://127.0.0.1:9082/socket.io/
   ProxyPass /netplay/           http://127.0.0.1:9082/
   ```
4. Abre en el firewall, y reenvía en el router a los mismos puertos: **3478 TCP y UDP** y **49160-49200 UDP**.
5. Copia `ejemplo.html` junto a `data43/`, pon tu juego (`JUEGO`) y ábrela. El anfitrión pulsa «Juego en Red» en la barra del emulador y crea una sala; los demás se unen desde la lista.

**Ponle una contraseña delante** (por ejemplo, autenticación básica en la página y en `/netplay/`). Quien pueda llegar a `/netplay/turn` consigue claves para tu TURN. coturn solo repite hacia internet (nunca hacia la red local ni a rangos privados) y tiene límites de velocidad, pero sigue siendo tu conexión.

## Probado

- Dos pestañas del mismo navegador: sala creada y listada, WebRTC conectado, el invitado recibe vídeo a 26-30 fps, a 480 líneas, con sonido.
- Anfitrión detrás de una VPN que solo deja pasar TCP, invitado en casa: funciona por el TURN, por TCP.
- Dos navegadores en la misma casa (la misma IP pública): solo conectan por el TURN.

Falta probar: entre dos casas.

## Límites conocidos

- La pestaña del anfitrión tiene que estar a la vista: oculta, el navegador para el emulador y no se manda nada.
- El invitado tiene que hacer clic o pulsar una tecla en la página para desbloquear el sonido (un mando no cuenta).
- El vídeo va a 30 fps como mucho.
- La 4.3.0 es una versión previa. Fallos vistos con N64: cargar un estado guardado deja el juego sin freno (al ritmo del monitor), y el núcleo `parallel_n64` de la 4.3 aborta al arrancar (`emscripten_fiber_swap`).

## Licencia

GPL-3.0 (ver `LICENSE`), la misma que EmulatorJS. EmulatorJS no va incluido: `parchear-ejs43.sh` corrige tu propia copia.
