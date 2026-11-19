# Netplay for EmulatorJS, self-hosted

*English · [Español](README.es.md)*

Everything needed to play online with [EmulatorJS](https://emulatorjs.org) 4.3 on your own server: a room server in Python, your own TURN relay (coturn), fixes for the 4.3.0 pre-release, and an example page.

In EmulatorJS 4.3 netplay **only the host emulates**: the host streams video and sound to the others over WebRTC, browser to browser, and the others send their key presses back. It never desyncs, and the lag is just the connection between the two players. The 4.2.3 netplay synced inputs and save states instead, and its own code calls it broken.

## What's in here

| File | What it does |
|---|---|
| `netplay.py` | Room server (Socket.IO 4 over WebSocket only), compatible with the 4.3 client. It doesn't carry any video: it only puts players in touch. It also gives out temporary TURN credentials (`GET /turn`, valid for 24 h). |
| `parchear-ejs43.sh` | Copies EmulatorJS 4.3.0-pre and applies the fixes below. Safe to run again. |
| `turnserver.conf`, `turn-ip.sh`, `turn-ip.cron` | coturn as a relay for when two browsers can't connect directly. `turn-ip.sh` keeps the public IP up to date (for dynamic IPs). |
| `instalar.sh` | Installs `netplay.py` as a service and sets up coturn. |
| `ejemplo.html` | A page with one game, netplay and the sound fix. |

The code, its comments and the scripts' output are in Spanish.

## Fixes for EmulatorJS 4.3.0-pre

`parchear-ejs43.sh`, in `emulator.min.js`:

1. **Connection**: the client calls `io(url)` bare, which tries long polling first and takes the URL path as a Socket.IO namespace. It is changed to WebSocket only, at `<path>/socket.io/`, so the server can live under any path behind a proxy.
2. **Core report**: `downloadFile` returns the cache object instead of the JSON for `cores/reports/<core>.json`. The emulator thinks there is no report, doesn't know the core supports WebGL2 and loads the legacy (WebGL1) build. With N64, some games crash right after the title screen.
3. **Bitrate through TURN**: the 4.3 drops video to 0.9 Mbit/s when it goes through a relay. With your own TURN that isn't needed, so it stays at 2.5.
4. **Video size**: it streams the host's whole canvas (965×813 or more with a big window), even though an N64 draws at 320×240 or 640×480. Since the connection starts slow and the 4.3 prefers keeping the frame rate, the picture looks pixelated for the first seconds and after every dip. It is capped at 480 lines.

`ejemplo.html` fixes one more thing: **guests got no sound** with cores that use RWebAudio (N64 and others), because the host only captures OpenAL or a `gameManager.audioNode`. Everything that goes to the speakers is routed through a gain node that the netplay can capture. The volume bar also works with those cores now; it used to only change OpenAL.

## Installing

Requirements: Linux with systemd (tested on Debian 13), `python3` and `python3-websockets` (13 or newer). `instalar.sh` installs `coturn` with apt.

1. Get the `data` folder from EmulatorJS 4.3.0-pre and fix it:
   ```sh
   bash parchear-ejs43.sh <EmulatorJS 4.3 data folder> /var/www/html/data43
   ```
2. Install the service and the TURN, with the public name of your server:
   ```sh
   sudo DOMINIO=your.domain.example bash instalar.sh
   ```
3. Proxy `netplay.py` from your web server. With Apache (`proxy`, `proxy_http`, `proxy_wstunnel`), the `socket.io` line first:
   ```apache
   ProxyPass /netplay/socket.io/ ws://127.0.0.1:9082/socket.io/
   ProxyPass /netplay/           http://127.0.0.1:9082/
   ```
4. Open these on the firewall, and forward them on the router to the same ports: **3478 TCP and UDP** and **49160-49200 UDP**.
5. Copy `ejemplo.html` next to `data43/`, put in your game (`JUEGO`), and open it. The host clicks "Netplay" in the emulator bar and creates a room; the others join from the list.

**Put a password in front of it** (for example basic auth on the page and on `/netplay/`). Whoever can reach `/netplay/turn` gets credentials for your TURN. coturn only relays towards the internet (never to the local network or private ranges) and has bandwidth caps, but it's still your connection.

## Tested

- Two tabs in the same browser: room created and listed, WebRTC connected, guest gets video at 26-30 fps, at 480 lines, with sound.
- Host behind a VPN that only lets TCP through, guest at home: works through the TURN over TCP.
- Two browsers in the same home (same public IP): they only connect through the TURN.

Not tested yet: between two homes.

## Known limits

- The host's tab must stay visible: when it's hidden, the browser stops the emulator and nothing is sent.
- A guest has to click or press a key on the page to unlock sound (a gamepad doesn't count).
- Video goes at 30 fps at most.
- 4.3.0 is a pre-release. Bugs seen with N64: loading a save state leaves the game running unthrottled (at the monitor's refresh rate), and the 4.3 `parallel_n64` core aborts on start (`emscripten_fiber_swap`).

## License

GPL-3.0 (see `LICENSE`), the same as EmulatorJS. EmulatorJS is not included: `parchear-ejs43.sh` patches your own copy.
