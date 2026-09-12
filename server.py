"""
LanChat Server - Servidor simple para chat P2P en LAN
- Sirve la webapp estática por HTTP
- WebSocket (mismo puerto HTTP) para mensajería en tiempo real con el navegador
- UDP Broadcast para descubrimiento de peers
- HTTP POST /api/inbox para entregar mensajes a otras máquinas de la LAN
"""

import asyncio
import json
import os
import platform
import signal
import socket
import sys
import time
import uuid

from aiohttp import web, ClientSession, ClientTimeout, WSMsgType

# --- Configuración ---
HTTP_PORT = 8765          # Puerto HTTP (webapp + WebSocket + inbox)
WS_PORT = HTTP_PORT       # El WebSocket va integrado en el puerto HTTP
UDP_PORT = 8767           # Puerto UDP para discovery
DISCOVERY_INTERVAL = 5    # Segundos entre anuncios UDP
PEER_TIMEOUT = 30         # Segundos sin anuncios antes de dar por muerto a un peer
MAX_PAYLOAD = 32 * 1024 * 1024   # Tamaño máximo de un mensaje (imágenes en base64)

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'webapp')

# Identidad automática por hostname + id único por instancia
HOSTNAME = platform.node().split('.')[0] or 'lanchat'
INSTANCE_ID = uuid.uuid4().hex

_local_ip_cache = None


def get_local_ip():
    """Obtener la IP local de esta máquina (la que sale a la LAN)."""
    global _local_ip_cache
    if _local_ip_cache:
        return _local_ip_cache
    ip = '127.0.0.1'
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 1))
            ip = s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            pass
    _local_ip_cache = ip
    return ip


def get_broadcast_ips():
    """Direcciones de broadcast reales de las interfaces de esta máquina."""
    ips = set()

    # 1) Broadcast declarado por cada interfaz (si psutil está disponible)
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.broadcast:
                    if not addr.broadcast.startswith('127.'):
                        ips.add(addr.broadcast)
    except Exception:
        pass

    # 2) Broadcast /24 derivado de la IP local real (no del hostname, que en
    #    Linux suele resolver a 127.0.1.1 y daba una subred equivocada)
    local_ip = get_local_ip()
    if not local_ip.startswith('127.'):
        parts = local_ip.split('.')
        if len(parts) == 4:
            ips.add('{}.{}.{}.255'.format(parts[0], parts[1], parts[2]))

    # 3) Broadcast limitado: no se enruta, pero llega a todo el segmento local
    ips.add('255.255.255.255')
    return sorted(ips)


class Peer:
    """Representa un peer descubierto en la LAN."""

    def __init__(self, name, ip, port, instance_id=None):
        self.name = name
        self.ip = ip
        self.port = port          # Puerto HTTP (y WebSocket) del peer
        self.instance_id = instance_id
        self.last_seen = time.time()

    def update(self, data):
        """Actualizar información del peer."""
        if isinstance(data, dict):
            self.name = data.get('name', self.name)
            self.ip = data.get('ip', self.ip)
            self.port = data.get('port', self.port)
            self.instance_id = data.get('instance_id', self.instance_id)
        self.last_seen = time.time()

    def to_dict(self):
        return {
            'name': self.name,
            'ip': self.ip,
            'port': self.port,
            'ws_port': self.port,
            'last_seen': self.last_seen
        }


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """Protocolo asyncio para recibir anuncios UDP sin bloquear el event loop."""

    def __init__(self, server):
        self.server = server

    def datagram_received(self, data, addr):
        self.server.process_udp_message(data, addr[0])

    def error_received(self, exc):
        pass


class LanChatServer:
    """Servidor principal de LanChat."""

    def __init__(self):
        self.peers = {}                 # {peer_name: Peer}
        self.websocket_clients = set()  # Navegadores conectados a esta máquina
        self.session = None             # Cliente HTTP para hablar con otros peers
        self.runner = None
        self.udp_transport = None
        self.announce_task = None
        self.reaper_task = None

        self.app = web.Application(client_max_size=MAX_PAYLOAD)
        self.app.router.add_routes([
            web.static('/static/', WEBAPP_DIR),
            web.get('/api/peers', self.handle_peers),
            web.post('/api/inbox', self.handle_inbox),
            web.get('/ws', self.handle_ws),
            web.get('/', self.handle_index),
        ])

    async def start(self):
        """Iniciar todo: HTTP, WS y UDP discovery."""
        self.session = ClientSession(timeout=ClientTimeout(total=5))

        # HTTP + WebSocket en un solo puerto
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        # shutdown_timeout corto: si no, Ctrl+C tarda hasta 60s esperando
        # a que se cierren las conexiones keep-alive abiertas
        site_http = web.TCPSite(self.runner, '0.0.0.0', HTTP_PORT, shutdown_timeout=2.0)
        await site_http.start()
        print("🌐 Servidor: http://localhost:{}  (WS incluido)".format(HTTP_PORT))

        # Discovery UDP (no bloqueante: corre sobre el event loop)
        await self.start_udp_listener()
        self.announce_task = asyncio.create_task(self.udp_announcer())
        self.reaper_task = asyncio.create_task(self.peer_reaper())

        # Anunciarse de inmediato
        await self.udp_announce_once()

        print("\n🚀 LanChat v1.0 - Identidad: '{}' ({})".format(HOSTNAME, get_local_ip()))
        print("   Abre http://localhost:{} en tu navegador\n".format(HTTP_PORT))

    async def stop(self):
        """Detener el servidor."""
        for task in (self.announce_task, self.reaper_task):
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if self.udp_transport:
            self.udp_transport.close()
        if self.session:
            await self.session.close()
        if self.runner:
            try:
                await self.runner.cleanup()
            except Exception:
                pass
        print("\n🛑 Server detenido.")

    # --- HTTP Handlers ---

    async def handle_index(self, request):
        """Servir la webapp."""
        index_path = os.path.join(WEBAPP_DIR, 'index.html')
        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return web.Response(text=content, content_type='text/html')
        except FileNotFoundError:
            return web.Response(status=404, text='<h1>Webapp no encontrada</h1>')

    async def handle_peers(self, request):
        """API: devolver lista de peers conocidos."""
        self.purge_stale_peers()
        result = {
            'self': {
                'name': HOSTNAME,
                'ip': get_local_ip(),
                'port': HTTP_PORT,
                'ws_port': WS_PORT,
                'instance_id': INSTANCE_ID
            },
            'peers': [p.to_dict() for p in self.peers.values()]
        }
        return web.json_response(result)

    async def handle_inbox(self, request):
        """Recibir un mensaje enviado por otro peer de la LAN."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'ok': False, 'error': 'JSON inválido'}, status=400)

        if not isinstance(data, dict) or data.get('type') != 'message':
            return web.json_response({'ok': False, 'error': 'mensaje inválido'}, status=400)

        await self.deliver_local(data)
        return web.json_response({'ok': True})

    # --- WebSocket Handler (navegadores de esta máquina) ---

    async def handle_ws(self, request):
        """WebSocket para enviar/recibir mensajes en tiempo real."""
        ws = web.WebSocketResponse(max_msg_size=MAX_PAYLOAD, heartbeat=30)
        await ws.prepare(request)
        self.websocket_clients.add(ws)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    await self.route_message(data, ws)
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.websocket_clients.discard(ws)
        return ws

    async def route_message(self, data, origin_ws=None):
        """Entregar un mensaje: a los navegadores locales y al peer destino."""
        if not isinstance(data, dict) or data.get('type') != 'message':
            return

        # Eco a otras pestañas abiertas en esta misma máquina
        await self.deliver_local(data, exclude_ws=origin_ws)

        target = data.get('to')
        if target and target != '*':
            targets = [target]
        else:
            targets = list(self.peers.keys())

        for name in targets:
            error = await self.send_to_peer(name, data)
            if error and origin_ws is not None:
                await self.send_ws(origin_ws, {
                    'type': 'delivery_error',
                    'to': name,
                    'error': error
                })

    async def deliver_local(self, message_data, exclude_ws=None):
        """Enviar un mensaje a los navegadores conectados a esta máquina."""
        clients = [c for c in self.websocket_clients if c is not exclude_ws]
        if not clients:
            return
        await asyncio.gather(
            *(self.send_ws(c, message_data) for c in clients),
            return_exceptions=True
        )

    async def send_ws(self, ws, payload):
        """Enviar JSON como texto por WebSocket (el navegador espera texto)."""
        try:
            if not ws.closed:
                await ws.send_str(json.dumps(payload))
        except Exception:
            self.websocket_clients.discard(ws)

    async def send_to_peer(self, peer_name, message_data):
        """Entregar un mensaje al peer destino por HTTP. Devuelve None si todo ok."""
        peer = self.peers.get(peer_name)
        if not peer:
            return 'peer desconocido o desconectado'

        url = 'http://{}:{}/api/inbox'.format(peer.ip, peer.port)
        try:
            async with self.session.post(url, json=message_data) as resp:
                if resp.status != 200:
                    return 'el peer respondió HTTP {}'.format(resp.status)
        except asyncio.TimeoutError:
            return 'timeout al contactar al peer'
        except Exception as e:
            return str(e) or e.__class__.__name__
        return None

    # --- UDP Discovery ---

    async def start_udp_listener(self):
        """Escuchar anuncios UDP sin bloquear el event loop."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        try:
            sock.bind(('0.0.0.0', UDP_PORT))
        except OSError as e:
            sock.close()
            print("⚠️  No se pudo escuchar discovery UDP en {}: {}".format(UDP_PORT, e))
            print("    No se van a descubrir peers. ¿Hay otra instancia corriendo?")
            return

        loop = asyncio.get_event_loop()
        self.udp_transport, _ = await loop.create_datagram_endpoint(
            lambda: DiscoveryProtocol(self), sock=sock
        )

    async def udp_announcer(self):
        """Enviar anuncios UDP periódicos."""
        while True:
            await asyncio.sleep(DISCOVERY_INTERVAL)
            try:
                await self.udp_announce_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    async def udp_announce_once(self):
        """Enviar un anuncio UDP de 'hello' a cada broadcast conocido."""
        packet = json.dumps({
            'type': 'hello',
            'name': HOSTNAME,
            'ip': get_local_ip(),
            'port': HTTP_PORT,
            'ws_port': WS_PORT,
            'instance_id': INSTANCE_ID,
            'timestamp': time.time()
        }).encode('utf-8')

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            for ip in get_broadcast_ips():
                try:
                    sock.sendto(packet, (ip, UDP_PORT))
                except OSError:
                    pass
        finally:
            sock.close()

    def process_udp_message(self, data, sender_ip):
        """Procesar un mensaje UDP recibido."""
        try:
            msg = json.loads(data.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(msg, dict) or msg.get('type') != 'hello':
            return

        # No agregarte a ti mismo (por id de instancia, no por hostname)
        if msg.get('instance_id') == INSTANCE_ID:
            return

        peer_name = msg.get('name') or 'unknown'
        ip = msg.get('ip') or sender_ip
        # Si la IP anunciada no sirve, usar la del remitente real
        if ip.startswith('127.'):
            ip = sender_ip
        msg['ip'] = ip

        if peer_name in self.peers:
            self.peers[peer_name].update(msg)
        else:
            self.peers[peer_name] = Peer(
                peer_name, ip, msg.get('port', HTTP_PORT), msg.get('instance_id')
            )
            print("🟢 Peer descubierto: {} ({})".format(peer_name, ip))

    def purge_stale_peers(self):
        """Olvidar peers que dejaron de anunciarse."""
        now = time.time()
        stale = [n for n, p in self.peers.items() if now - p.last_seen > PEER_TIMEOUT]
        for name in stale:
            del self.peers[name]
            print("🔴 Peer desconectado: {}".format(name))

    async def peer_reaper(self):
        """Limpiar peers caídos periódicamente."""
        while True:
            await asyncio.sleep(DISCOVERY_INTERVAL)
            self.purge_stale_peers()


async def main():
    """Punto de entrada principal."""
    server = LanChatServer()
    stop_event = asyncio.Event()

    # Cerrar limpio con Ctrl+C o SIGTERM (en Windows no hay add_signal_handler)
    loop = asyncio.get_event_loop()
    for signame in ('SIGINT', 'SIGTERM'):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        await server.start()
        await stop_event.wait()   # Mantener el servidor corriendo
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await server.stop()


# ------------------------------------------------------------------
# Cierre de instancias previas
# ------------------------------------------------------------------

THIS_SCRIPT = os.path.realpath(os.path.abspath(__file__))


def _tcp_port_in_use(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        return s.connect_ex(('127.0.0.1', port)) == 0
    finally:
        s.close()


def _udp_port_in_use(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Mismas opciones que usa el listener real: si él podría bindear, no está ocupado
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    try:
        s.bind(('0.0.0.0', port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _ports_busy():
    return _tcp_port_in_use(HTTP_PORT) or _udp_port_in_use(UDP_PORT)


def _arg_is_this_script(arg, proc_cwd=None):
    """¿Este argumento de línea de comandos apunta a este mismo server.py?"""
    if not arg:
        return False
    try:
        if not os.path.isabs(arg) and proc_cwd:
            arg = os.path.join(proc_cwd, arg)
        return os.path.realpath(arg) == THIS_SCRIPT
    except Exception:
        return False


def _is_instance_cmdline(args, cwd=None):
    """Un proceso es otra instancia si es un Python corriendo este script.

    El chequeo del intérprete evita matar wrappers que solo mencionan el
    script en su línea de comandos (timeout, watch, bash -c, un editor...).
    """
    args = [a for a in (args or []) if a]
    if not args:
        return False
    if not os.path.basename(args[0]).lower().startswith('python'):
        return False
    return any(_arg_is_this_script(a, cwd) for a in args[1:])


def _ancestor_pids():
    """PIDs de nuestros ancestros: nunca hay que matarlos."""
    pids = set()
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        pids = {p.pid for p in proc.parents()}
    except Exception:
        pid = os.getpid()
        for _ in range(32):
            try:
                with open('/proc/{}/status'.format(pid), 'r') as f:
                    ppid = next(int(l.split()[1]) for l in f if l.startswith('PPid:'))
            except (OSError, StopIteration, ValueError):
                break
            if ppid <= 0 or ppid in pids:
                break
            pids.add(ppid)
            pid = ppid
    return pids


def _find_other_instances():
    """PIDs de otros procesos corriendo exactamente este server.py."""
    skip = _ancestor_pids() | {os.getpid()}
    pids = []

    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'cmdline']):
            pid = proc.info['pid']
            if pid in skip:
                continue
            try:
                cwd = proc.cwd()
            except Exception:
                cwd = None
            if _is_instance_cmdline(proc.info['cmdline'], cwd):
                pids.append(pid)
        return pids
    except ImportError:
        pass

    # Fallback sin dependencias (Linux/BSD con /proc)
    if os.path.isdir('/proc'):
        for entry in os.listdir('/proc'):
            if not entry.isdigit() or int(entry) in skip:
                continue
            try:
                with open('/proc/{}/cmdline'.format(entry), 'rb') as f:
                    args = f.read().decode('utf-8', 'replace').split('\0')
                cwd = os.readlink('/proc/{}/cwd'.format(entry))
            except OSError:
                continue
            if _is_instance_cmdline(args, cwd):
                pids.append(int(entry))
    return pids


def _kill_existing_instances():
    """Matar cualquier otra instancia de este server.py que esté corriendo."""
    if not _ports_busy():
        return False

    killed = False
    for pid in _find_other_instances():
        try:
            if os.name == 'nt':
                import subprocess
                subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
            else:
                import signal
                os.kill(pid, signal.SIGTERM)
            print("Instancia previa detectada y cerrada (PID {})".format(pid))
            killed = True
        except (ProcessLookupError, PermissionError, OSError):
            pass

    if not killed:
        print("⚠️  Los puertos {}/{} están ocupados por otro programa.".format(HTTP_PORT, UDP_PORT))
        return False

    # Esperar a que el SO libere los puertos
    for _ in range(50):
        if not _ports_busy():
            return True
        time.sleep(0.1)
    print("⚠️  Los puertos siguen ocupados después de cerrar la instancia previa.")
    return True


if __name__ == '__main__':
    # Configurar UTF-8 en stdout/stderr para Windows
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')

    # Instalar dependencias si no existen
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        print("Instalando dependencia necesaria...")
        os.system('{} -m pip install aiohttp'.format(sys.executable))
        import aiohttp  # noqa: F401

    _kill_existing_instances()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
