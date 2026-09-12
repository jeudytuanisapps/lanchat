"""
LanChat Server - Servidor simple para chat P2P en LAN
- Sirve la webapp estática por HTTP
- WebSocket para mensajería en tiempo real
- UDP Broadcast para descubrimiento de peers
"""

import asyncio
import json
import os
import platform
import socket
import time
from datetime import datetime
from aiohttp import web, WSMsgType

# --- Configuración ---
HTTP_PORT = 8765          # Puerto HTTP (sirve la webapp)
WS_PORT = 8766            # Puerto WebSocket para mensajes
UDP_PORT = 8767           # Puerto UDP para discovery
DISCOVERY_INTERVAL = 5    # Segundos entre anuncios UDP

# Identidad automática por hostname
HOSTNAME = platform.node().split('.')[0] if '.' in platform.node() else platform.node()


class Peer:
    """Representa un peer descubierto en la LAN."""
    def __init__(self, name, ip, port, ws_port):
        self.name = name
        self.ip = ip
        self.port = port          # Puerto HTTP
        self.ws_port = ws_port    # Puerto WebSocket
        self.last_seen = time.time()

    def update(self, data):
        """Actualizar información del peer."""
        if isinstance(data, dict):
            self.name = data.get('name', self.name)
            self.ip = data.get('ip', self.ip)
            self.port = data.get('port', self.port)
            self.ws_port = data.get('ws_port', self.ws_port)
            self.last_seen = time.time()

    def to_dict(self):
        return {
            'name': self.name,
            'ip': self.ip,
            'port': self.port,
            'ws_port': self.ws_port,
            'last_seen': self.last_seen
        }


class LanChatServer:
    """Servidor principal de LanChat."""

    def __init__(self):
        self.peers = {}               # {peer_name: Peer}
        self.websocket_clients = set()  # Clientes WebSocket conectados (mensajes locales)
        self.app = web.Application()
        self.app.router.add_routes([
            web.static('/static/', os.path.join(os.path.dirname(__file__), 'webapp')),
            web.get('/api/peers', self.handle_peers),
            web.get('/ws', self.handle_ws),
            web.get('/', self.handle_index),
        ])
        # Listener UDP en segundo plano
        self.udp_task = None

    async def start(self):
        """Iniciar todo: HTTP, WS y UDP discovery."""
        # Iniciar listener UDP
        self.udp_task = asyncio.create_task(self.udp_listener())
        await asyncio.sleep(0.5)  # Dar tiempo al socket para que se cree
        self.udp_announce_task = asyncio.create_task(self.udp_announcer())

        runner = web.AppRunner(self.app)
        await runner.setup()

        # Todo en un solo puerto (HTTP + WebSocket integrado)
        self.runner = runner
        site_http = web.TCPSite(runner, '0.0.0.0', HTTP_PORT)
        await site_http.start()
        print(f"🌐 Servidor: http://localhost:{HTTP_PORT}  (WS incluido)")

        # Announce ourselves immediately
        await self.udp_announce_once()

        print(f"\n🚀 LanChat v1.0 - Identidad: '{HOSTNAME}'")
        print(f"   Abre http://localhost:{HTTP_PORT} en tu navegador\n")

    async def stop(self):
        """Detener el servidor."""
        if hasattr(self, 'udp_task') and self.udp_task:
            self.udp_task.cancel()
        try:
            await self.runner.cleanup()
        except Exception:
            pass
        print("\n🛑 Server detenido.")

    # --- HTTP Handlers ---

    async def handle_index(self, request):
        """Redirigir a la webapp."""
        index_path = os.path.join(os.path.dirname(__file__), 'webapp', 'index.html')
        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return web.Response(text=content, content_type='text/html')
        except FileNotFoundError:
            return web.Response(status=404, text='<h1>Webapp no encontrada</h1>')

    async def handle_peers(self, request):
        """API: devolver lista de peers conocidos."""
        now = time.time()
        # Eliminar peers inactivos hace más de 30 segundos
        stale = [n for n, p in self.peers.items() if now - p.last_seen > 30]
        for name in stale:
            del self.peers[name]

        result = {
            'self': {
                'name': HOSTNAME,
                'ip': await self.get_local_ip(),
                'port': HTTP_PORT,
                'ws_port': WS_PORT
            },
            'peers': [p.to_dict() for p in self.peers.values()]
        }
        return web.json_response(result)

    # --- WebSocket Handler (mensajes locales) ---

    async def handle_ws(self, request):
        """WebSocket para enviar/recibir mensajes en tiempo real."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.websocket_clients.add(ws)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self.broadcast_message(data, ws)
                elif msg.type == WSMsgType.ERROR:
                    pass
        finally:
            self.websocket_clients.discard(ws)

    async def broadcast_message(self, message_data, exclude_ws=None):
        """Enviar mensaje a todos los clientes WebSocket conectados."""
        payload = json.dumps(message_data).encode('utf-8')
        clients = [c for c in self.websocket_clients if c != exclude_ws]
        if not clients:
            return

        # Enviar a todos los clientes locales
        tasks = []
        for client in clients:
            try:
                tasks.append(client.send_bytes(payload))
            except Exception:
                pass

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # --- UDP Discovery ---

    async def udp_listener(self):
        """Escuchar anuncios UDP de otros peers."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(1.0)
        try:
            sock.bind(('0.0.0.0', UDP_PORT))
            while True:
                try:
                    data, addr = sock.recvfrom(65535)
                    self.process_udp_message(data, addr[0])
                except socket.timeout:
                    continue
        finally:
            sock.close()

    async def udp_announcer(self):
        """Enviar anuncios UDP periódicos."""
        await asyncio.sleep(2)  # Esperar un poco al inicio
        while True:
            await self.udp_announce_once()
            await asyncio.sleep(DISCOVERY_INTERVAL)

    async def udp_announce_once(self):
        """Enviar un anuncio UDP de 'hello'."""
        local_ip = await self.get_local_ip()
        packet = json.dumps({
            'type': 'hello',
            'name': HOSTNAME,
            'ip': local_ip,
            'port': HTTP_PORT,
            'ws_port': WS_PORT,
            'timestamp': time.time()
        }).encode('utf-8')

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            # Enviar al broadcast de cada interfaz conocido
            for ip in await self.get_broadcast_ips():
                try:
                    sock.sendto(packet, (ip, UDP_PORT))
                except Exception as e:
                    pass
        finally:
            sock.close()

    def process_udp_message(self, data, sender_ip):
        """Procesar un mensaje UDP recibido."""
        try:
            msg = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        if msg.get('type') == 'hello':
            peer_name = msg.get('name', 'unknown')
            ip = msg.get('ip', sender_ip)
            port = msg.get('port', HTTP_PORT)
            ws_port = msg.get('ws_port', WS_PORT)

            # No agregarte a ti mismo
            if peer_name == HOSTNAME:
                return

            if peer_name in self.peers:
                self.peers[peer_name].update(msg)
            else:
                new_peer = Peer(peer_name, ip, port, ws_port)
                self.peers[peer_name] = new_peer
                print(f"🟢 Peer descubierto: {peer_name} ({ip})")

    async def get_local_ip(self):
        """Obtener la IP local de esta máquina."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 1))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            # Fallback
            import socket as sock_mod
            try:
                hostname = socket.gethostname()
                return sock_mod.gethostbyname(hostname)
            except Exception:
                return '127.0.0.1'

    async def get_broadcast_ips(self):
        """Obtener IPs de broadcast para UDP."""
        ips = []
        try:
            import socket as sock_mod
            hostname = sock_mod.gethostname()
            addr_info = sock_mod.getaddrinfo(hostname, None, sock_mod.AF_INET)
            for family, _, _, _, sockaddr in addr_info:
                ip = sockaddr[0]
                if not ip.startswith('127.') and ip != '0.0.0.0':
                    # Generar broadcast de la subred /24
                    parts = ip.split('.')
                    if len(parts) == 4:
                        ips.append(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
            # Agregar el broadcast genérico si no tenemos otras IPs
            if not ips:
                ips.append('192.168.1.255')  # Default fallback
        except Exception:
            ips = ['192.168.1.255']
        return ips


async def main():
    """Punto de entrada principal."""
    server = LanChatServer()
    try:
        await server.start()
        # Mantener el servidor corriendo (event loop en espera)
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("\n")
    finally:
        await server.stop()


def _kill_existing_instances():
    """Matar cualquier otra instancia de server.py que esté corriendo."""
    try:
        import socket as sock_mod
        s = sock_mod.socket(sock_mod.AF_INET, sock_mod.SOCK_STREAM)
        result = s.connect_ex(('127.0.0.1', HTTP_PORT))
        s.close()
        if result == 0:  # Puerto ocupado - hay otra instancia corriendo
            import psutil
            for proc in psutil.process_iter(['pid', 'cmdline']):
                try:
                    cmdline = ' '.join(proc.info['cmdline'] or [])
                    if ('server.py' in cmdline) and (proc.info['pid'] != os.getpid()):
                        proc.kill()
                        print(f"Instancia previa detectada y cerrada (PID {proc.info['pid']})")
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            # Si no encontramos con psutil, intentar por puerto
            import subprocess
            try:
                result = subprocess.run(
                    ['netstat', '-ano'],
                    capture_output=True, text=True
                )
                for line in result.stdout.splitlines():
                    if f':{HTTP_PORT}' in line and 'LISTING' in line:
                        pid = line.strip().split()[-1]
                        subprocess.run(['taskkill', '/F', '/PID', pid], 
                                     capture_output=True)
                        print(f"Instancia previa detectada y cerrada (PID {pid})")
            except Exception:
                pass
    except ImportError:
        # psutil no disponible, intentar con netstat directo
        import subprocess
        try:
            result = subprocess.run(
                ['netstat', '-ano'],
                capture_output=True, text=True, shell=False,
                creationflags=0x08000000  # CREATE_NO_WINDOW en Windows
            )
            for line in (result.stdout or '').splitlines():
                if f':{HTTP_PORT}' in line and 'LISTENING' in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        subprocess.run(['taskkill', '/F', '/PID', pid], 
                                     capture_output=True)
                        print(f"Instancia previa detectada y cerrada (PID {pid})")
                        break
        except Exception:
            pass
    return False


if __name__ == '__main__':
    # Configurar UTF-8 en stdout/stderr para Windows
    import sys as _sys
    if hasattr(_sys.stdout, 'reconfigure'):
        _sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(_sys.stderr, 'reconfigure'):
        _sys.stderr.reconfigure(encoding='utf-8')

    # Instalar dependencias si no existen: pip install aiohttp websockets
    try:
        import aiohttp
    except ImportError:
        print("Instalando dependencia necesaria...")
        os.system('pip install aiohttp')
        import aiohttp

    # Cerrar cualquier instancia previa
    _kill_existing_instances()
    
    asyncio.run(main())

    asyncio.run(main())
