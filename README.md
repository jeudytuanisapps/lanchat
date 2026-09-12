# 🚀 LanChat v1.0

Chat P2P en LAN para uso personal entre computadoras. Sin registro, sin base de datos, sin configuración compleja.

## 🔧 Arquitectura

### Componentes

| Componente | Descripción |
|---|---|
| **server.py** | Servidor Python que maneja HTTP estático + WebSocket + UDP discovery |
| **webapp/** | Webapp pura HTML/CSS/JS (sin frameworks, sin build step) |

### Flujo de comunicación

```
PC-A                                          PC-B
 │                                             │
 ├─ Anuncio UDP "hello" (8767, broadcast) ──►  │
 │◄────────── Anuncio UDP "hello" (8767) ──────┤
 │                                             │
 ├─ POST http://PC-B:8765/api/inbox ────────►  │   ← mensajes entre máquinas
 │◄───────── POST http://PC-A:8765/api/inbox ──┤
 │                                             │
 ▼                                             ▼
navegador local                          navegador local
 (HTTP + WebSocket en el puerto 8765)
```

El navegador solo habla con **su** servidor local por WebSocket (`ws://localhost:8765/ws`).
El servidor es el que reenvía el mensaje al servidor del peer por HTTP, y ese lo
empuja a los navegadores conectados a esa máquina.

### Protocolo de mensajes

**Discovery UDP:**
```jsonc
// Anuncio periódico (cada 5s) a las direcciones de broadcast reales de la máquina
{ "type": "hello", "name": "<hostname>", "ip": "<local-ip>", "port": 8765,
  "ws_port": 8765, "instance_id": "<uuid>" }
```

`instance_id` sirve para ignorar los propios anuncios (antes se filtraba por
hostname, lo que rompía si dos máquinas se llamaban igual).

Un peer que deja de anunciarse por 30s se da por desconectado y desaparece de la lista.

**Mensajes WebSocket:**
```jsonc
{
  "type": "message",
  "from": "<mi-hostname>",
  "to": "<host-destino>",
  "timestamp": <epoch-ms>,
  "data": { ... }     // contenido del mensaje
}
```

**Tipos de data:**
- `text`: `{ type: "text", text: "mensaje" }`
- `image`: `{ type: "image", base64: "<data-url>", caption: "<nombre>" }`
- `file`: `{ type: "file", fileName: "<name>" }`

---

## 📁 Estructura de archivos

```
lanchat/
├── server.py           # Servidor principal (HTTP + WS + UDP)
└── webapp/
    ├── index.html       # UI principal
    ├── style.css        # Estilos tema oscuro tipo Slack
    └── app.js           # Lógica del cliente
```

---

## 🚀 Cómo correrlo

### 1. Instalar dependencias (una sola vez)

```bash
pip install aiohttp
```

### 2. Ejecutar el servidor en cada computadora

```bash
python server.py
```

**Salida esperada:**
```
🌐 Servidor: http://localhost:8765  (WS incluido)

🚀 LanChat v1.0 - Identidad: 'MI-PC' (192.168.1.42)
   Abre http://localhost:8765 en tu navegador
```

Una sola corrida basta. Si había otra instancia viva, la cierra sola antes de arrancar.

### 3. Abrir la webapp

Abrir en el navegador de cada PC:
```
http://localhost:8765
```

El servidor sirve automáticamente la webapp estática desde `/webapp/`.

---

## ✅ Funcionalidades implementadas (Fase 1)

- [x] Identidad automática por hostname
- [x] Descubrimiento de peers en LAN vía UDP broadcast
- [x] Chat en tiempo real vía WebSocket
- [x] Envío de mensajes de texto
- [x] Envío de imágenes (base64, máx 5MB)
- [x] UI responsive tipo Slack/WhatsApp
- [x] No requiere configuración entre máquinas

---

## ❌ Limitaciones conocidas

- **Puertos fijos**: Se pueden cambiar en las constantes del server (`HTTP_PORT`, `UDP_PORT`)
- **Firewall**: Puede requerir abrir los puertos 8765 (TCP) y 8767 (UDP) en el firewall local
- **Archivos grandes**: Limitados a ~5MB (sin chunking ni servidor de archivos)
- **Misma subred**: El discovery es por broadcast, no cruza routers ni VLANs
- **Sin historial**: Los mensajes viven solo en la pestaña abierta; si recargás, se pierden
- **Sin cifrado**: El tráfico va en claro dentro de la LAN

---

## 📌 Próximos pendientes

- [x] ✅ Poder limpiar conversación (solo local, con badge de no leídos)
- [x] ✅ Detectar cuando un peer se desconecta (toast automático verde/rojo)
- [x] ✅ Agregar scroll al chat (auto-scroll suave + comportamiento inteligente)

---

## 🔜 Fase 2 (planificado)

- [x] Historial en localstorage
- [ ] Revisar límites de historial (rotación, tamaño máximo)
- [ ] Notificaciones desktop
- [ ] Compartir archivos grandes con chunking
- [ ] Reacciones/emoji

---

## 🛠️ Notas técnicas

- **Python**: 3.8+ (asyncio nativo)
- **aiohttp**: Servidor HTTP + WebSocket async
- **Socket UDP**: Discovery sin dependencias externas
- **Frontend**: Vanilla JS, sin frameworks ni build step
- **Puertos**: 8765 (HTTP + WebSocket + inbox entre peers), 8767 (UDP discovery)
