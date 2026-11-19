#!/usr/bin/env python3
"""Servidor de salas (netplay) para EmulatorJS 4.3, con claves temporales para un TURN propio (coturn).

Hace lo mismo que el servidor oficial (EmulatorJS-Netplay, en Rust), pero en Python con la librería websockets.
No mueve vídeo ni sonido: solo pone en contacto a los jugadores. El anfitrión emula el juego y manda la imagen a los demás por WebRTC, de navegador a navegador; los demás
le mandan sus teclas.

Lo que hace:
  - GET /list?domain=…&game_id=…: las salas abiertas de ese juego.
  - GET /turn: el servidor TURN (coturn) con un usuario y una contraseña temporales (24 h), que la página le pasa
    a EmulatorJS. Si los dos navegadores no consiguen conectarse directamente, la imagen va por el TURN. Las claves
    son las de la «REST API» de coturn: usuario «caducidad:juegos» y contraseña HMAC-SHA1 del usuario con el
    secreto compartido (--turn-secreto). Conviene ponerlo tras una contraseña: quien lo pida puede usar el TURN.
  - GET /registro?d=…: una línea de diagnóstico de la página (cómo va la imagen, errores del emulador), que se
    apunta en el registro del servicio. Como mucho 40 por minuto y por IP.
  - Socket.IO 4 (solo WebSocket, sin «long polling»): open-room, join-room, leave-room y chat-message, y reenvía
    webrtc-signal (a un jugador), data-message, snapshot e input (a los demás de la sala).

El cliente de EmulatorJS se conecta con io(url) a secas, que primero prueba «long polling» y toma la ruta de la URL
como espacio de nombres. Por eso parchear-ejs43.sh cambia esa llamada en emulator.min.js por
io(origen, {path: ruta + "/socket.io/", transports: ["websocket"]}).
"""
import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

PING_CADA, PING_ESPERA = 25, 20  # segundos, como Socket.IO

salas = {}     # sessionid -> {dueño, jugadores {playerid: extra}, pares [(origen, destino)], nombre, juego, dominio, clave, max}
indice = {}    # sid -> {'sala': sessionid, 'jugador': playerid, 'chat': ms}
clientes = {}  # sid -> Cliente
turn = {'secreto': None, 'urls': [], 'horas': 24}
avisos = {}    # IP -> [minuto, líneas]: límite del diagnóstico que mandan los navegadores (/registro)


def registro(*t):
    print(time.strftime('%H:%M:%S'), *t, flush=True)


def texto(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (str, int, float)):
        return str(v)
    return None


def clave_normal(c):
    c = (texto(c) or '').strip()
    return None if not c or c.lower() == 'none' else c


class Cliente:
    def __init__(self, ws):
        self.ws = ws
        self.sid = secrets.token_urlsafe(15)
        self.ns = '/'
        self.conectado = False
        self.binarios = None  # (cabecera, cuántos faltan, recibidos) de un evento con datos binarios a medias

    def _prefijo(self):
        return '' if self.ns == '/' else self.ns + ','

    async def enviar(self, mensaje):
        try:
            await self.ws.send(mensaje)
        except ConnectionClosed:
            pass

    async def evento(self, nombre, *args):
        await self.enviar('42' + self._prefijo() + json.dumps([nombre, *args], separators=(',', ':')))

    async def ack(self, ident, *args):
        if ident is not None:
            await self.enviar('43' + self._prefijo() + str(ident) + json.dumps(list(args), separators=(',', ':')))

    async def crudo(self, cabecera, binarios):
        """Reenvía un evento tal cual (sin su número de ack), con sus partes binarias si las tiene."""
        await self.enviar(cabecera)
        for b in binarios:
            await self.enviar(b)


def ids_de(jugadores):
    return [j['socketId'] for j in jugadores.values() if isinstance(j, dict) and 'socketId' in j]


async def a_todos(sids, nombre, dato, salvo=None):
    for s in sids:
        if s != salvo and s in clientes:
            await clientes[s].evento(nombre, dato)


async def salir(c):
    ind = indice.pop(c.sid, None)
    if not ind:
        return
    sala = salas.get(ind['sala'])
    if not sala:
        return
    sala['jugadores'].pop(ind['jugador'], None)
    sala['pares'] = [p for p in sala['pares'] if c.sid not in p]
    renegociar = None
    if sala['dueño'] == c.sid and sala['jugadores']:
        nuevo = next(iter(sala['jugadores'].values())).get('socketId')
        if nuevo:
            sala['dueño'] = nuevo
            sala['pares'] = [(nuevo if o == c.sid else o, d) for o, d in sala['pares']]
            if sala['pares']:
                renegociar = (nuevo, sala['pares'][0][1])
    if renegociar and renegociar[0] in clientes:
        await clientes[renegociar[0]].evento('webrtc-signal', {'target': renegociar[1], 'requestRenegotiate': True})
    if sala['jugadores']:
        await a_todos(ids_de(sala['jugadores']), 'users-updated', sala['jugadores'], salvo=c.sid)
    else:
        salas.pop(ind['sala'], None)
        registro('sala cerrada:', sala['nombre'])


async def al_evento(c, nombre, args, ident, cabecera_cruda=None, binarios=()):
    dato = args[0] if args else None
    if nombre in ('open-room', 'join-room'):
        dato = dato if isinstance(dato, dict) else {}
        extra = dato.get('extra') if isinstance(dato.get('extra'), dict) else {}
        sesion = texto(extra.get('sessionid')) or ''
        jugador = texto(extra.get('userid')) or texto(extra.get('playerId')) or ''
        if not sesion or not jugador:
            return await c.ack(ident, 'Invalid data: sessionId and playerId required')
        await salir(c)  # por si ya estaba en otra sala
        extra['socketId'] = c.sid
        if nombre == 'open-room':
            if sesion in salas:
                return await c.ack(ident, 'Room already exists')
            try:
                maximo = max(2, min(8, int(dato.get('maxPlayers') or 4)))
            except (TypeError, ValueError):
                maximo = 4
            salas[sesion] = {'dueño': c.sid, 'jugadores': {jugador: extra}, 'pares': [],
                             'nombre': texto(extra.get('room_name')) or f'Sala {sesion}',
                             'juego': texto(extra.get('game_id')) or 'default',
                             'dominio': texto(extra.get('domain')) or 'unknown',
                             'clave': clave_normal(dato.get('password')), 'max': maximo}
            indice[c.sid] = {'sala': sesion, 'jugador': jugador, 'chat': 0}
            registro('sala abierta:', salas[sesion]['nombre'], '· juego', salas[sesion]['juego'])
            await c.ack(ident, None)
            await c.evento('users-updated', salas[sesion]['jugadores'])
        else:
            sala = salas.get(sesion)
            if not sala:
                return await c.ack(ident, 'Room not found')
            if sala['clave'] and clave_normal(dato.get('password')) != sala['clave']:
                return await c.ack(ident, 'Incorrect password')
            if len(sala['jugadores']) >= sala['max']:
                return await c.ack(ident, 'Room full')
            sala['jugadores'][jugador] = extra
            indice[c.sid] = {'sala': sesion, 'jugador': jugador, 'chat': 0}
            registro('entra en', sala['nombre'] + ':', texto(extra.get('player_name')) or jugador)
            await c.ack(ident, None, sala['jugadores'])
            await a_todos(ids_de(sala['jugadores']), 'users-updated', sala['jugadores'])
    elif nombre == 'leave-room':
        await salir(c)
    elif nombre == 'webrtc-signal':
        dato = dato if isinstance(dato, dict) else {}
        pedir = bool(dato.get('requestRenegotiate'))
        destino = texto(dato.get('target'))
        if not destino and not pedir:
            return
        ind = indice.get(c.sid)
        if dato.get('offer') is not None and ind and ind['sala'] in salas:
            pares = salas[ind['sala']]['pares']
            if (c.sid, destino) not in pares:
                pares.append((c.sid, destino))
        salida = {'sender': c.sid}
        if pedir:
            salida['requestRenegotiate'] = True
        else:
            for k in ('candidate', 'offer', 'answer'):
                if dato.get(k) is not None:
                    salida[k] = dato[k]
        if destino in clientes:
            await clientes[destino].evento('webrtc-signal', salida)
    elif nombre == 'chat-message':
        ind = indice.get(c.sid)
        sala = salas.get(ind['sala']) if ind else None
        if not sala:
            return await c.ack(ident, {'ok': False, 'error': 'Not in a room'})
        ahora = int(time.time() * 1000)
        if ahora - ind['chat'] < 300:
            return await c.ack(ident, {'ok': False, 'error': 'Slow down'})
        ind['chat'] = ahora
        mensaje = (dato.get('message') if isinstance(dato, dict) else texto(dato)) or ''
        mensaje = str(mensaje).strip()[:500]
        if not mensaje:
            return await c.ack(ident, {'ok': False, 'error': 'Empty message'})
        de = sala['jugadores'].get(ind['jugador'], {})
        para = texto(dato.get('to')) if isinstance(dato, dict) else None
        privado = sala['jugadores'].get(para) if para and para != 'all' and para != ind['jugador'] else None
        carga = {'ts': ahora, 'to': para if privado else 'all', 'userid': ind['jugador'],
                 'player_name': de.get('player_name'), 'message': mensaje}
        if privado:
            await c.evento('chat-message', carga)
            if privado.get('socketId') in clientes:
                await clientes[privado['socketId']].evento('chat-message', carga)
        else:
            await a_todos(ids_de(sala['jugadores']), 'chat-message', carga)
        await c.ack(ident, {'ok': True})
    elif nombre in ('data-message', 'snapshot', 'input'):
        ind = indice.get(c.sid)
        sala = salas.get(ind['sala']) if ind else None
        if not sala:
            return
        for s in ids_de(sala['jugadores']):
            if s != c.sid and s in clientes:
                if cabecera_cruda is not None:
                    await clientes[s].crudo(cabecera_cruda, binarios)
                else:
                    await clientes[s].evento(nombre, *args)


def leer_paquete(p):
    """'42/ns,12["ev",…]' -> (tipo, ns, ack, carga)."""
    tipo, i, ns = p[0], 1, '/'
    if tipo in '56':  # binario: «45<n>-…»
        guion = p.index('-')
        n, i = int(p[1:guion]), guion + 1
    else:
        n = 0
    if p[i:i + 1] == '/':
        coma = p.find(',', i)
        ns, i = (p[i:coma], coma + 1) if coma >= 0 else (p[i:], len(p))
    j = i
    while j < len(p) and p[j].isdigit():
        j += 1
    ack = int(p[i:j]) if j > i else None
    return tipo, ns, ack, p[j:], n


async def conexion(ws):
    c = Cliente(ws)
    clientes[c.sid] = c
    vivo = {'pong': time.monotonic()}
    await c.enviar('0' + json.dumps({'sid': c.sid, 'upgrades': [], 'pingInterval': PING_CADA * 1000,
                                     'pingTimeout': PING_ESPERA * 1000, 'maxPayload': 64 << 20}))

    async def latido():
        while True:
            await asyncio.sleep(PING_CADA)
            if time.monotonic() - vivo['pong'] > PING_CADA + PING_ESPERA:
                await ws.close()
                return
            await c.enviar('2')

    tarea = asyncio.create_task(latido())
    try:
        async for m in ws:
            if isinstance(m, bytes):  # parte binaria de un evento 45/46
                if c.binarios:
                    cab, faltan, partes = c.binarios
                    partes.append(m)
                    if faltan <= 1:
                        c.binarios = None
                        await al_paquete(c, cab, partes)
                    else:
                        c.binarios = (cab, faltan - 1, partes)
                continue
            if m == '3':
                vivo['pong'] = time.monotonic()
            elif m == '2':
                await c.enviar('3')
            elif m.startswith('4'):
                tipo, ns, ack, carga, n = leer_paquete(m[1:])
                if tipo in '56' and n:
                    c.binarios = (m, n, [])
                else:
                    await al_paquete(c, m, [])
            elif m.startswith('1'):
                break
    except ConnectionClosed:
        pass
    finally:
        tarea.cancel()
        await salir(c)
        clientes.pop(c.sid, None)


async def al_paquete(c, m, binarios):
    tipo, ns, ack, carga, n = leer_paquete(m[1:])
    if tipo == '0':
        c.ns, c.conectado = ns, True
        await c.enviar('40' + c._prefijo() + json.dumps({'sid': c.sid}))
    elif tipo == '1':
        await salir(c)
    elif tipo in '25':
        try:
            args = json.loads(carga)
        except ValueError:
            return
        if not isinstance(args, list) or not args or not isinstance(args[0], str):
            return
        cruda = None
        if tipo == '5':  # se reenvía tal cual, sin el número de ack
            cruda = '45' + str(n) + '-' + ('' if ns == '/' else ns + ',') + carga
        await al_evento(c, args[0], args[1:], ack, cruda, binarios)


def http(conexion_, peticion):
    url = urllib.parse.urlsplit(peticion.path)
    if url.path.rstrip('/').endswith('/socket.io') or url.path.endswith('/socket.io/'):
        return None  # sigue como WebSocket
    if url.path.endswith('/list'):
        q = urllib.parse.parse_qs(url.query)
        dominio, juego = q.get('domain', [None])[0], q.get('game_id', [None])[0]
        lista = {}
        if dominio and juego:
            for sesion, s in salas.items():
                if s['dominio'] == dominio and s['juego'] == juego:
                    dueño = next((j.get('player_name') for j in s['jugadores'].values()
                                  if j.get('socketId') == s['dueño']), None) or 'Unknown'
                    lista[sesion] = {'room_name': s['nombre'], 'current': len(s['jugadores']), 'max': s['max'],
                                     'player_name': dueño, 'hasPassword': bool(s['clave']), 'gameId': s['juego']}
        r = conexion_.respond(200, json.dumps(lista))
    elif url.path.endswith('/turn'):
        servidores = []
        if turn['secreto'] and turn['urls']:
            usuario = f"{int(time.time()) + turn['horas'] * 3600}:juegos"
            firma = hmac.new(turn['secreto'], usuario.encode(), hashlib.sha1).digest()
            servidores.append({'urls': turn['urls'], 'username': usuario,
                               'credential': base64.b64encode(firma).decode()})
        r = conexion_.respond(200, json.dumps(servidores))
    elif url.path.endswith('/registro'):
        # Diagnóstico que manda la página: cada 10 s cómo va la imagen (velocidad, resolución, si va por el TURN y
        # qué la limita) y los errores del emulador. Se apunta aquí para poder mirarlo después (journalctl -u netplay)
        ip = (peticion.headers.get('X-Forwarded-For') or '?').split(',')[-1].strip()
        minuto = int(time.time() // 60)
        if len(avisos) > 1000:
            avisos.clear()
        cuenta = avisos.setdefault(ip, [minuto, 0])
        if cuenta[0] != minuto:
            cuenta[:] = [minuto, 0]
        cuenta[1] += 1
        if cuenta[1] <= 40:  # como mucho 40 líneas por minuto y por IP
            d = urllib.parse.parse_qs(url.query).get('d', [''])[0]
            registro('navegador', ip, ''.join(ch if ch.isprintable() else ' ' for ch in d)[:1500])
        r = conexion_.respond(204, '')
    else:
        r = conexion_.respond(404, 'No encontrado\n')
    r.headers['Content-Type'] = 'application/json' if r.status_code == 200 else 'text/plain; charset=utf-8'
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Cache-Control'] = 'no-store'
    return r


async def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--puerto', type=int, default=9082)
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--turn-secreto', help='fichero con el secreto compartido con coturn (static-auth-secret)')
    p.add_argument('--turn-url', action='append', default=[],
                   help='URL del TURN para los navegadores, p. ej. turn:host:3478?transport=udp (se puede repetir)')
    a = p.parse_args()
    if a.turn_secreto:
        with open(a.turn_secreto, 'rb') as f:
            turn['secreto'] = f.read().strip()
        turn['urls'] = a.turn_url
        registro('TURN:', ', '.join(a.turn_url) or 'sin URL')
    async with serve(conexion, a.host, a.puerto, process_request=http, max_size=64 << 20, ping_interval=None):
        registro(f'netplay escuchando en {a.host}:{a.puerto}')
        await asyncio.get_running_loop().create_future()


if __name__ == '__main__':
    asyncio.run(main())
