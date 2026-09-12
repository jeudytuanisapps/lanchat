/* ============================================================
   LanChat - Frontend Webapp (v1.0)
   ============================================================ */

(function () {
    'use strict';

    // --- Constantes ---
    const PORT = 8765;

    // --- Estado de la app ---
    const state = {
        myName: '',
        selfData: null,
        peers: {},            // { peerName: data }
        activeChat: null,     // nombre del peer con el que chateo ahora
        ws: null,             // WebSocket al servidor local
        messageHistory: {}    // { peerName: [msg1, msg2, ...] }, ordenados por timestamp
    };

    // --- Elementos DOM ---
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    const els = {
        currentUser:       $('#currentUser'),
        peersList:         $('#peersList'),
        chatsList:         $('#chatsList'),
        noChatView:        $('#noChatView'),
        chatView:          $('#chatView'),
        chatPeerName:      $('#chatPeerName'),
        chatPeerStatus:    $('#chatPeerStatus'),
        messagesContainer: $('#messagesContainer'),
        messageForm:       $('#messageForm'),
        messageInput:      $('#messageInput'),
        fileInput:         $('#fileInput'),
        attachmentBar:     $('#attachmentBar'),
        toast:             $('#toast')
    };

    // --- Archivos pendientes de envío ---
    let pendingFiles = [];

    // ============================================================
    // Inicialización
    // ============================================================

    async function init() {
        const hostname = getHostname();
        state.myName = hostname;
        els.currentUser.textContent = `🖥️ ${hostname}`;

        // Conectar WebSocket al servidor local (mismo puerto)
        connectWebSocket();

        // Obtener lista de peers
        await refreshPeers();

        // Refrescar peers cada 5 segundos
        setInterval(refreshPeers, 5000);
    }

    function getHostname() {
        const el = document.getElementById('currentUser');
        if (el) return el.textContent.replace('🖥️ ', '');
        return 'lanchat-pc';
    }

    // ============================================================
    // WebSocket - Conexión al servidor local
    // ============================================================

    function connectWebSocket() {
        const wsUrl = `ws://${window.location.host}/ws`;

        try {
            state.ws = new WebSocket(wsUrl);
        } catch (e) {
            console.error('Error creando WebSocket:', e);
            return;
        }

        state.ws.onopen = function () {
            console.log('✅ Conectado al servidor');
        };

        state.ws.onmessage = function (event) {
            try {
                const msg = JSON.parse(event.data);
                handleMessage(msg);
            } catch (e) {
                console.error('Error parseando mensaje:', e);
            }
        };

        state.ws.onclose = function () {
            console.log('❌ Desconectado del servidor');
            // Reintentar después de 3 segundos
            setTimeout(connectWebSocket, 3000);
        };

        state.ws.onerror = function (err) {
            console.error('Error WebSocket:', err);
        };
    }

    function sendMessage(msgData) {
        if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
            showToast('⚠️ No hay conexión con el servidor');
            return;
        }

        const fullMessage = {
            type: 'message',
            from: state.myName,
            to: msgData.to || '*',
            timestamp: Date.now(),
            data: msgData
        };

        try {
            state.ws.send(JSON.stringify(fullMessage));
        } catch (e) {
            console.error('Error enviando mensaje:', e);
            showToast('⚠️ Error al enviar mensaje');
        }
    }

    function handleMessage(msg) {
        const from = msg.from;
        if (!state.messageHistory[from]) {
            state.messageHistory[from] = [];
        }
        state.messageHistory[from].push(msg);

        // Si estoy chateando con esta persona, mostrar en pantalla
        if (state.activeChat === from) {
            renderMessage(msg, false);
            scrollToBottom();
        } else {
            // Notificación toast si no estoy en el chat activo
            showToast(`📩 Nuevo mensaje de ${from}`);
        }
    }

    // ============================================================
    // Discovery - Obtener lista de peers
    // ============================================================

    async function refreshPeers() {
        try {
            const resp = await fetch('/api/peers');
            const data = await resp.json();

            state.selfData = data.self;

            // Actualizar estado de peers conocidos
            if (data.peers) {
                for (const peer of data.peers) {
                    state.peers[peer.name] = peer;
                }
            }

            renderPeersList();
        } catch (e) {
            console.error('Error obteniendo peers:', e);
        }
    }

    function renderPeersList() {
        const names = Object.keys(state.peers).sort();

        if (names.length === 0) {
            els.peersList.innerHTML = '<li class="empty-state">Buscando peers...</li>';
            return;
        }

        let html = '';
        for (const name of names) {
            const peer = state.peers[name];
            const isActive = state.activeChat === name;
            html += `<li class="${isActive ? 'active' : ''}" data-peer="${name}">`;
            html += `<span class="status-dot"></span>`;
            html += `<span class="peer-name-text">${escHtml(name)}</span>`;
            html += `</li>`;
        }
        els.peersList.innerHTML = html;

        // Eventos click en peers
        $$('.peers-list li[data-peer]').forEach(function (li) {
            li.addEventListener('click', function () {
                const peerName = this.getAttribute('data-peer');
                selectChat(peerName);
            });
        });
    }

    // ============================================================
    // Gestión de chats
    // ============================================================

    function selectChat(peerName) {
        state.activeChat = peerName;

        // Actualizar sidebar - marcar activo
        $$('.peers-list li').forEach(function (li) {
            if (li.getAttribute('data-peer') === peerName) {
                li.classList.add('active');
            } else {
                li.classList.remove('active');
            }
        });

        // Actualizar sección de chats activos
        renderActiveChats();

        // Mostrar vista de chat
        els.noChatView.classList.add('hidden');
        els.chatView.classList.remove('hidden');

        // Header del chat
        els.chatPeerName.textContent = peerName;
        const peerData = state.peers[peerName];
        if (peerData) {
            els.chatPeerStatus.textContent = `IP: ${peerData.ip}`;
        } else {
            els.chatPeerStatus.textContent = '';
        }

        // Cargar mensajes del historial
        renderHistory(peerName);
    }

    function renderActiveChats() {
        const names = Object.keys(state.peers).sort();

        if (names.length === 0) {
            els.chatsList.innerHTML = '<li class="empty-state">Sin chats</li>';
            return;
        }

        let html = '';
        for (const name of names) {
            const isActive = state.activeChat === name;
            html += `<li class="${isActive ? 'active' : ''}">`;
            html += `<span class="status-dot"></span>`;
            html += `<span class="chat-name-text">${escHtml(name)}</span>`;
            html += `</li>`;
        }
        els.chatsList.innerHTML = html;

        // Eventos click en chats activos
        $$('.chats-list li').forEach(function (li) {
            const textEl = li.querySelector('.chat-name-text');
            if (textEl) {
                const name = textEl.textContent.trim();
                li.addEventListener('click', function () {
                    selectChat(name);
                });
            }
        });
    }

    // ============================================================
    // Renderizado de mensajes
    // ============================================================

    function renderHistory(peerName) {
        const messages = state.messageHistory[peerName] || [];

        if (messages.length === 0) {
            els.messagesContainer.innerHTML = '<p style="text-align:center; color:#7a7c85;">Inicia una conversación</p>';
            return;
        }

        let html = '';
        const startIdx = Math.max(0, messages.length - 20); // Últimos 20 mensajes

        for (let i = startIdx; i < messages.length; i++) {
            const msg = messages[i];
            if (!msg || !msg.data) continue;

            html += createMessageHTML(msg);
        }

        els.messagesContainer.innerHTML = html;

        // Click en imágenes para ver grande
        $$('.messages-container img').forEach(function (img) {
            img.addEventListener('click', function () {
                const win = window.open('', '_blank');
                if (win) {
                    win.document.write('<img src="' + this.src + '" style="max-width:100%">');
                }
            });
        });

        scrollToBottom();
    }

    function renderMessage(msg, isSent) {
        const html = createMessageHTML(msg);
        els.messagesContainer.insertAdjacentHTML('beforeend', html);

        // Re-attach click events para imágenes
        const newMsgEl = els.messagesContainer.lastElementChild;
        if (newMsgEl) {
            const img = newMsgEl.querySelector('img');
            if (img) {
                img.addEventListener('click', function () {
                    const win = window.open('', '_blank');
                    if (win) {
                        win.document.write('<img src="' + this.src + '" style="max-width:100%">');
                    }
                });
            }
        }

        scrollToBottom();
    }

    function createMessageHTML(msg) {
        const sender = msg.from;
        const ts = new Date(msg.timestamp);
        const timeStr = padZero(ts.getHours()) + ':' + padZero(ts.getMinutes());
        let content = '';

        const data = msg.data || {};

        switch (data.type) {
            case 'text':
                content = '<div class="bubble">' + escHtml(data.text) + '</div>';
                break;

            case 'image':
                if (data.base64 && data.base64 !== '' && data.base64.indexOf('...[truncated]') === -1) {
                    content = `<img src="${escAttr(data.base64)}" alt="Imagen">`;
                    if (data.caption) {
                        content += `<div class="meta">${escHtml(data.caption)}</div>`;
                    }
                } else if (data.fileName) {
                    content = `<a class="file-attachment" href="#" onclick="return false;">📎 ${escHtml(data.fileName)} (${(data.size || 0)/1024|0} KB)</a>`;
                }
                content = '<div class="bubble">' + content + '</div>';
                break;

            case 'file':
                if (data.base64 && data.base64 !== '' && data.base64.indexOf('...[truncated]') === -1) {
                    content = `<a class="file-attachment" href="${escAttr(data.base64)}" download="${escAttr(data.fileName)}">📄 ${escHtml(data.fileName)}</a>`;
                } else if (data.fileName) {
                    content = `<a class="file-attachment" href="#" onclick="return false;">📎 ${escHtml(data.fileName)}</a>`;
                }
                content = '<div class="bubble">' + content + '</div>';
                break;

            default:
                content = `<div class="bubble">Mensaje desconocido</div>`;
        }

        const isSent = (sender === state.myName);
        const sentClass = isSent ? 'sent' : 'received';
        let senderLine = '';

        if (!isSent && sender !== state.myName) {
            senderLine = `<div class="msg-sender">${escHtml(sender)}</div>`;
        }

        return '<div class="message ' + sentClass + '">' +
               senderLine +
               content +
               '<div class="meta">' + timeStr + '</div>' +
           '</div>';
    }

    // ============================================================
    // Envío de mensajes y archivos
    // ============================================================

    els.messageForm.addEventListener('submit', function (e) {
        e.preventDefault();

        if (!state.activeChat) return;

        const text = els.messageInput.value.trim();
        if (!text && pendingFiles.length === 0) return;

        // Enviar texto
        if (text) {
            sendMessage({ to: state.activeChat, type: 'text', text: text });
        }

        // Enviar archivos
        for (let i = 0; i < pendingFiles.length; i++) {
            const fileData = pendingFiles[i];
            if (fileData.base64 && fileData.mimeType) {
                sendMessage({
                    to: state.activeChat,
                    type: 'image',
                    base64: fileData.base64,
                    caption: fileData.fileName,
                    fileName: fileData.fileName,
                    size: fileData.size
                });
            }
        }

        // Limpiar input y archivos
        els.messageInput.value = '';
        els.messageInput.style.height = 'auto';
        pendingFiles = [];
        renderAttachmentBar();
    });

    // Auto-resize textarea
    els.messageInput.addEventListener('input', function () {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });

    // Enter para enviar (Shift+Enter = nueva línea)
    els.messageInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            els.messageForm.dispatchEvent(new Event('submit'));
        }
    });

    // ============================================================
    // Archivos / Adjuntos
    // ============================================================

    els.fileInput.addEventListener('change', function (e) {
        const files = Array.from(e.target.files);

        for (let i = 0; i < files.length; i++) {
            const file = files[i];

            // Limpiar nombre del archivo
            const safeName = sanitizeFileName(file.name);

            if (file.size > 5 * 1024 * 1024) {
                showToast('⚠️ Archivo muy grande (máx 5MB): ' + file.name);
                continue;
            }

            const reader = new FileReader();
            reader.onload = function () {
                pendingFiles.push({
                    fileName: safeName,
                    base64: reader.result,
                    mimeType: file.type,
                    size: file.size
                });
                renderAttachmentBar();
            };
            reader.readAsDataURL(file);
        }

        // Reset input para permitir re-subir el mismo archivo
        e.target.value = '';
    });

    function renderAttachmentBar() {
        let html = '';
        for (let i = 0; i < pendingFiles.length; i++) {
            const f = pendingFiles[i];
            html += '<div class="attachment-item">';
            html += '  <span>' + escHtml(f.fileName) + '</span>';
            html += '  <span class="remove-file" data-index="' + i + '">✕</span>';
            html += '</div>';
        }
        els.attachmentBar.innerHTML = html;

        // Botones eliminar archivos
        $$('.attachment-item .remove-file').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const idx = parseInt(this.getAttribute('data-index'));
                pendingFiles.splice(idx, 1);
                renderAttachmentBar();
            });
        });
    }

    // ============================================================
    // Utilidades
    // ============================================================

    function scrollToBottom() {
        const container = els.messagesContainer;
        requestAnimationFrame(function () {
            container.scrollTop = container.scrollHeight;
        });
    }

    function showToast(msg) {
        els.toast.textContent = msg;
        els.toast.classList.remove('hidden');

        // Resetear animación
        els.toast.style.animation = 'none';
        void els.toast.offsetWidth; // force reflow
        els.toast.style.animation = '';

        setTimeout(function () {
            els.toast.classList.add('hidden');
        }, 3000);
    }

    function escHtml(str) {
        if (typeof str !== 'string') return String(str);
        return str
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function escAttr(str) {
        if (typeof str !== 'string') return String(str);
        return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
    }

    function sanitizeFileName(name) {
        // Eliminar caracteres peligrosos del nombre
        return name.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').substring(0, 200);
    }

    function padZero(n) {
        return n < 10 ? '0' + n : '' + n;
    }

    // ============================================================
    // Arrancar la app
    // ============================================================

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
