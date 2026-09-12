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
PC-A                              PC-B
 │                                │
 ├─ Anuncio UDP "hello" ───────►  │
 │  (puerto 8767)                 │
 │◄──────── Anuncio UDP "hello"──┤│
 │                                │
 ├─ WebSocket ◄────────────────► ├─ WebSocket
 │  (puerto 8766)   mensajes     │  (puerto 8766)
 │                                │
 └─ HTTP estático ──────────────>┴─ Sirve la webapp
    (puerto 8765)                  (puerto 8765)
```

### Protocolo de mensajes

**Discovery UDP:**
```jsonc
// Anuncio periódico (cada 5s)
{ "type": "hello", "name": "<hostname>", "ip": "<local-ip>", "port": 8765, "ws_port": 8766 }
```

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
HTTP server: http://localhost:8765
WebSocket server: ws://localhost:8766

🚀 LanChat v1.0 - Identidad: 'MI-PC'
   Abre http://localhost:8765 en tu navegador
```

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

## ❌ Conocido por corregir

- **Puertos fijos**: Se pueden cambiar en las constantes del server (`HTTP_PORT`, `WS_PORT`, `UDP_PORT`)
- **Firewall**: Puede requerir abrir puertos 8765-8767 en el firewall local
- **Archivos grandes**: Limitados a ~5MB (sin chunking ni servidor de archivos)

---

## 🔜 Fase 2 (planificado)

- [ ] Canal de texto encriptado básico
- [ ] Historial persistente en disco
- [ ] Notificaciones desktop
- [ ] Compartir archivos grandes con chunking
- [ ] Canales grupales
- [ ] Reacciones/emoji

---

## 🛠️ Notas técnicas

- **Python**: 3.8+ (asyncio nativo)
- **aiohttp**: Servidor HTTP + WebSocket async
- **Socket UDP**: Discovery sin dependencias externas
- **Frontend**: Vanilla JS, sin frameworks ni build step
- **Puertos**: 8765 (HTTP), 8766 (WS), 8767 (UDP)
