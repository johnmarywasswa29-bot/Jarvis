/* Jarvis Web UI Application */
(function() {
    'use strict';

    // Configuration
    const WS_PATH = '/ws';
    const RECONNECT_DELAY = 2000;
    const MAX_RECONNECT_ATTEMPTS = 5;
    const MAX_AUDIO_SIZE = 10 * 1024 * 1024; // 10MB
    
    // Auth token (can be set via URL parameter or localStorage)
    let authToken = null;
    
    // State
    let ws = null;
    let clientId = null;
    let reconnectAttempts = 0;
    let isConnected = false;
    let pendingRequests = new Map(); // request_id -> {resolve, reject, streaming}
    let messageId = 0;
    let activePanel = 'chat';
    let mediaRecorder = null;
    let audioChunks = [];
    let isRecording = false;
    let audioContext = null;
    let currentAudioSource = null; // Currently playing TTS audio source
    let audioQueue = []; // Queue of TTS audio to play sequentially
    let isPlayingTTS = false;

    // DOM Elements
    const elements = {
        connectionStatus: document.getElementById('connectionStatus'),
        chatArea: document.getElementById('chatArea'),
        messages: document.getElementById('messages'),
        messageForm: document.getElementById('messageForm'),
        messageInput: document.getElementById('messageInput'),
        sendButton: document.getElementById('sendButton'),
        errorToast: document.getElementById('errorToast'),
        sidebar: document.getElementById('sidebar'),
        panels: {
            chat: document.getElementById('panel-chat'),
            memory: document.getElementById('panel-memory'),
            workspace: document.getElementById('panel-workspace'),
            history: document.getElementById('panel-history')
        },
        // Memory elements
        memorySearchInput: document.getElementById('memorySearchInput'),
        memorySearchBtn: document.getElementById('memorySearchBtn'),
        memoryContentInput: document.getElementById('memoryContentInput'),
        memoryTypeSelect: document.getElementById('memoryTypeSelect'),
        memoryImportanceInput: document.getElementById('memoryImportanceInput'),
        memoryConfidenceInput: document.getElementById('memoryConfidenceInput'),
        memoryAddBtn: document.getElementById('memoryAddBtn'),
        memoryResults: document.getElementById('memoryResults'),
        // Workspace elements
        wsProject: document.getElementById('wsProject'),
        wsLanguage: document.getElementById('wsLanguage'),
        wsIde: document.getElementById('wsIde'),
        wsRepo: document.getElementById('wsRepo'),
        wsDir: document.getElementById('wsDir'),
        wsGitRepo: document.getElementById('wsGitRepo'),
        wsApps: document.getElementById('wsApps'),
        wsConfidence: document.getElementById('wsConfidence'),
        workspaceProjects: document.getElementById('workspaceProjects'),
        // History elements
        historyList: document.getElementById('historyList'),
        historyClearBtn: document.getElementById('historyClearBtn'),
        // Navigation
        navButtons: document.querySelectorAll('.nav-btn'),
        // Voice elements
        voiceBtn: document.getElementById('voiceBtn'),
        voiceRecordBtn: document.getElementById('voiceRecordBtn'),
        stopSpeakingBtn: document.getElementById('stopSpeakingBtn')
    };

    // Utility functions
    function generateRequestId() {
        return 'req_' + Date.now() + '_' + (++messageId);
    }

    function formatTimestamp() {
        return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function showToast(message, isError = true) {
        const toast = elements.errorToast;
        toast.textContent = message;
        toast.classList.toggle('visible', true);
        toast.style.background = isError ? 'var(--error-bg)' : 'var(--bg-tertiary)';
        toast.style.color = isError ? 'var(--error-text)' : 'var(--accent)';
        
        setTimeout(() => {
            toast.classList.remove('visible');
        }, 4000);
    }

    function updateConnectionStatus(status) {
        const el = elements.connectionStatus;
        el.className = 'status ' + status;
        
        const indicator = el.querySelector('.status-indicator');
        const text = el.querySelector('.status-text');
        
        switch (status) {
            case 'connected':
                text.textContent = 'Connected';
                break;
            case 'connecting':
                text.textContent = 'Connecting...';
                break;
            case 'disconnected':
                text.textContent = 'Disconnected';
                break;
        }
    }

    function addMessage(role, content, options = {}) {
        const { streaming = false, requestId = null } = options;
        
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message ' + role;
        if (requestId) {
            messageDiv.dataset.requestId = requestId;
        }
        
        const avatarChar = role === 'user' ? 'You' : role === 'assistant' ? 'J' : '•';
        
        messageDiv.innerHTML = `
            <div class="message-avatar">${escapeHtml(avatarChar)}</div>
            <div class="message-content">
                <div class="message-bubble ${streaming ? 'streaming' : ''}">${streaming ? '' : escapeHtml(content)}</div>
            </div>
        `;
        
        elements.messages.appendChild(messageDiv);
        scrollToBottom();
        
        return messageDiv.querySelector('.message-bubble');
    }

    function updateStreamingMessage(bubble, token, done = false) {
        bubble.textContent += token;
        bubble.classList.toggle('streaming', !done);
        scrollToBottom();
    }

    function scrollToBottom() {
        elements.chatArea.scrollTop = elements.chatArea.scrollHeight;
    }

    function setInputState(disabled) {
        elements.messageInput.disabled = disabled;
        elements.sendButton.disabled = disabled;
    }

    function switchPanel(panelName) {
        // Update nav buttons
        elements.navButtons.forEach(btn => {
            btn.classList.toggle('active', btn.dataset.panel === panelName);
        });
        
        // Update panels
        Object.entries(elements.panels).forEach(([name, panel]) => {
            panel.classList.toggle('active', name === panelName);
        });
        
        activePanel = panelName;
        
        // Load panel data if needed
        if (panelName === 'workspace') {
            loadWorkspace();
        } else if (panelName === 'history') {
            loadHistory();
        }
    }

    // WebSocket connection
    function connect() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        updateConnectionStatus('connecting');
        
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        let wsUrl = protocol + '//' + window.location.host + WS_PATH;
        
        // Add auth token to WebSocket URL if available
        if (authToken) {
            wsUrl += '?token=' + encodeURIComponent(authToken);
        }
        
        ws = new WebSocket(wsUrl);
        
        ws.onopen = () => {
            console.log('WebSocket connected');
            isConnected = true;
            reconnectAttempts = 0;
            updateConnectionStatus('connected');
            showToast('Connected to Jarvis', false);
            setInputState(false);
        };
        
        ws.onclose = (event) => {
            console.log('WebSocket closed:', event.code, event.reason);
            isConnected = false;
            updateConnectionStatus('disconnected');
            setInputState(true);
            
            if (event.code !== 1000) { // Not a clean close
                scheduleReconnect();
            }
        };
        
        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            showToast('Connection error');
        };
        
        ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                handleServerMessage(message);
            } catch (e) {
                console.error('Failed to parse WebSocket message:', e);
            }
        };
    }

    function scheduleReconnect() {
        if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
            showToast('Max reconnection attempts reached. Please refresh the page.');
            return;
        }
        
        reconnectAttempts++;
        showToast(`Reconnecting... (attempt ${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})`);
        
        setTimeout(() => {
            connect();
        }, RECONNECT_DELAY * reconnectAttempts);
    }

    function sendMessage(message) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(message));
        } else {
            showToast('Not connected');
        }
    }

    function sendChat(content, stream = true) {
        const requestId = generateRequestId();
        
        const message = {
            type: 'chat',
            request_id: requestId,
            payload: {
                content: content,
                stream: stream
            }
        };
        
        // Add user message immediately
        addMessage('user', content);
        
        // Add assistant message placeholder
        const bubble = addMessage('assistant', '', { streaming: true, requestId });
        
        // Store promise for completion
        return new Promise((resolve, reject) => {
            pendingRequests.set(requestId, { 
                resolve, 
                reject, 
                streaming: true,
                bubble: bubble
            });
            sendMessage(message);
        });
    }

    function sendPing() {
        const requestId = generateRequestId();
        sendMessage({
            type: 'ping',
            request_id: requestId
        });
    }

    function sendCancel(requestId) {
        sendMessage({
            type: 'cancel',
            request_id: generateRequestId(),
            payload: { request_id: requestId }
        });
    }

    function sendStopSpeaking() {
        sendMessage({
            type: 'stop_speaking',
            request_id: generateRequestId()
        });
    }

    function handleServerMessage(message) {
        const { type, request_id, payload } = message;
        
        switch (type) {
            case 'pong':
                break;
                
            case 'chat_token':
                handleChatToken(request_id, payload);
                break;
                
            case 'chat_done':
                handleChatDone(request_id, payload);
                break;
                
            case 'chat_started':
                break;
                
            case 'error':
                handleError(request_id, payload);
                break;
                
            case 'cancelled':
                handleCancelled(request_id, payload);
                break;
                
            case 'status':
                break;
                
            case 'confirmation_required':
                handleConfirmationRequired(payload);
                break;
                
            case 'research_progress':
                handleResearchProgress(payload);
                break;
                
            case 'confirmation_result':
                break;
                
            case 'workspace_changed':
                handleWorkspaceChanged(payload);
                break;
                
            case 'tts_audio':
                if (payload && payload.audio_base64) {
                    queueTTSAudio(payload.audio_base64);
                }
                break;
                
            case 'voice_transcript':
                if (payload && payload.text) {
                    addMessage('user', payload.text);
                }
                break;
                
            case 'tts_stopped':
                handleTTSStopped();
                break;
                
            default:
                console.warn('Unknown message type:', type);
        }
    }

    function handleChatToken(requestId, payload) {
        const request = pendingRequests.get(requestId);
        if (request && request.streaming) {
            updateStreamingMessage(request.bubble, payload.token, payload.done);
        }
    }

    function handleChatDone(requestId, payload) {
        const request = pendingRequests.get(requestId);
        if (request) {
            if (payload.content) {
                request.bubble.textContent = payload.content;
            }
            request.bubble.classList.remove('streaming');
            request.resolve(payload);
            pendingRequests.delete(requestId);
        }
        scrollToBottom();
    }

    function handleError(requestId, payload) {
        const request = pendingRequests.get(requestId);
        if (request) {
            if (request.streaming && request.bubble) {
                request.bubble.classList.remove('streaming');
            }
            request.reject(new Error(payload.message));
            pendingRequests.delete(requestId);
        }
        showToast(payload.message);
    }

    function handleCancelled(requestId, payload) {
        const request = pendingRequests.get(requestId);
        if (request) {
            if (request.streaming && request.bubble) {
                request.bubble.classList.remove('streaming');
                request.bubble.textContent += '\n\n[Cancelled]';
            }
            request.resolve({ cancelled: true });
            pendingRequests.delete(requestId);
        }
        showToast('Request cancelled', false);
    }

    function handleWorkspaceChanged(payload) {
        if (activePanel === 'workspace') {
            loadWorkspace();
        }
    }

    // ---- Phase 9H: research workflow UI ----
    let activeConfirmationId = null;

    function sendConfirmationResponse(confirmationId, decision) {
        const requestId = generateRequestId();
        sendMessage({
            type: 'confirmation_response',
            request_id: requestId,
            payload: { confirmation_id: confirmationId, decision: decision }
        });
    }

    function handleConfirmationRequired(payload) {
        // Render a plan-review card that clearly separates REVIEWING from
        // EXECUTING. The user must explicitly Accept / Deny / Abort.
        activeConfirmationId = payload.confirmation_id || null;

        const card = document.createElement('div');
        card.className = 'message assistant';
        card.dataset.confirmationId = activeConfirmationId || '';

        const stepsHtml = (payload.steps || []).map((s, i) => {
            const risk = s.risk_level ? `<span class="risk-badge risk-${escapeHtml(s.risk_level)}">${escapeHtml(s.risk_level)}</span>` : '';
            const conf = s.confirmation_required ? '<span class="risk-badge risk-high">needs confirmation</span>' : '';
            return `<li><b>${escapeHtml(s.tool)}</b> → ${escapeHtml(s.expected_result || '')} ${risk} ${conf}</li>`;
        }).join('');

        const sourcesHtml = (payload.sources || []).map((c, i) =>
            `<li>${escapeHtml(c.title || c.url || '')} — <a href="${escapeHtml(c.url || '#')}" target="_blank" rel="noopener">${escapeHtml(c.url || '')}</a></li>`
        ).join('');

        card.innerHTML = `
            <div class="message-avatar">J</div>
            <div class="message-content">
                <div class="message-bubble confirmation-card">
                    <div class="confirm-header">Proposed plan — <b>review before executing</b></div>
                    <div class="confirm-objective">${escapeHtml(payload.objective || '')}</div>
                    <div class="confirm-meta">Overall risk: <span class="risk-badge risk-${escapeHtml(payload.overall_risk || 'low')}">${escapeHtml(payload.overall_risk || 'low')}</span></div>
                    ${sourcesHtml ? `<div class="confirm-section-title">Sources</div><ul class="confirm-list">${sourcesHtml}</ul>` : ''}
                    <div class="confirm-section-title">Steps that will execute</div>
                    <ul class="confirm-list">${stepsHtml || '<li>(none)</li>'}</ul>
                    ${payload.expires_at ? `<div class="confirm-expiry">This confirmation expires at ${escapeHtml(payload.expires_at)}</div>` : ''}
                    <div class="confirm-actions">
                        <button class="confirm-btn accept" data-decision="accept">Accept &amp; Execute</button>
                        <button class="confirm-btn deny" data-decision="deny">Deny</button>
                        <button class="confirm-btn abort" data-decision="abort">Abort</button>
                    </div>
                    <div class="confirm-note">Reviewing a plan does not execute anything. Actions run only after you Accept.</div>
                </div>
            </div>`;

        elements.messages.appendChild(card);
        scrollToBottom();

        card.querySelectorAll('.confirm-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const decision = btn.dataset.decision;
                sendConfirmationResponse(activeConfirmationId, decision);
                // Disable buttons and show pending state.
                card.querySelectorAll('.confirm-btn').forEach(b => { b.disabled = true; });
                const note = card.querySelector('.confirm-note');
                if (note) note.textContent = `Decision sent: ${decision}. Awaiting execution…`;
            });
        });
    }

    function handleResearchProgress(payload) {
        // Show research phases, sources, and execution step results inline.
        const phase = payload.phase || '';
        if (phase === 'complete' && payload.message) {
            const bubble = addMessage('assistant', payload.message);
            scrollToBottom();
            return;
        }
        if (phase === 'error') {
            showToast(payload.message || 'Research error', true);
            return;
        }
        // Lightweight phase indicator (no duplicate for the same phase spam).
        const indicator = document.createElement('div');
        indicator.className = 'message assistant';
        indicator.innerHTML = `
            <div class="message-avatar">J</div>
            <div class="message-content">
                <div class="message-bubble research-phase">${escapeHtml(phase)}: ${escapeHtml(payload.message || '')}</div>
            </div>`;
        elements.messages.appendChild(indicator);
        scrollToBottom();
    }

    // TTS Audio Playback Queue
    function queueTTSAudio(base64Audio) {
        audioQueue.push(base64Audio);
        if (!isPlayingTTS) {
            playNextTTSAudio();
        }
    }

    function playNextTTSAudio() {
        if (audioQueue.length === 0) {
            isPlayingTTS = false;
            currentAudioSource = null;
            return;
        }
        
        isPlayingTTS = true;
        const base64Audio = audioQueue.shift();
        
        // Decode base64 and play
        const binary = atob(base64Audio);
        const len = binary.length;
        const buffer = new ArrayBuffer(len);
        const view = new Uint8Array(buffer);
        for (let i = 0; i < len; i++) {
            view[i] = binary.charCodeAt(i);
        }
        
        // Create audio context if needed
        if (!audioContext) {
            audioContext = new (window.AudioContext || window.webkitAudioContext)();
        }
        
        audioContext.decodeAudioData(buffer).then(audioBuffer => {
            if (audioContext.state === 'suspended') {
                audioContext.resume();
            }
            
            const source = audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(audioContext.destination);
            currentAudioSource = source;
            
            source.onended = () => {
                currentAudioSource = null;
                playNextTTSAudio();
            };
            
            source.start(0);
        }).catch(err => {
            console.error('Audio playback error:', err);
            showToast('Audio playback failed');
            currentAudioSource = null;
            playNextTTSAudio();
        });
    }

    function stopCurrentTTS() {
        // Stop currently playing audio
        if (currentAudioSource) {
            try {
                currentAudioSource.stop();
                currentAudioSource.onended = null;
            } catch (e) {
                // Ignore
            }
            currentAudioSource = null;
        }
        
        // Clear queue
        audioQueue = [];
        isPlayingTTS = false;
    }

    function handleTTSStopped() {
        stopCurrentTTS();
    }

    // Voice recording functions
    async function startRecording() {
        if (isRecording) return;
        
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ 
                audio: {
                    sampleRate: 16000,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                } 
            });
            
            audioChunks = [];
            mediaRecorder = new MediaRecorder(stream, {
                mimeType: 'audio/webm;codecs=opus'
            });
            
            mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    audioChunks.push(event.data);
                }
            };
            
            mediaRecorder.onstop = async () => {
                const blob = new Blob(audioChunks, { type: 'audio/webm' });
                await sendAudioForSTT(blob);
                stream.getTracks().forEach(track => track.stop());
            };
            
            mediaRecorder.start(100); // Collect data every 100ms
            isRecording = true;
            
            // Update UI
            elements.voiceRecordBtn.classList.add('recording');
            elements.voiceRecordBtn.setAttribute('aria-label', 'Stop recording');
            elements.voiceRecordBtn.title = 'Release to stop recording';
            
        } catch (err) {
            console.error('Failed to start recording:', err);
            showToast('Microphone access denied or unavailable');
        }
    }

    function stopRecording() {
        if (!isRecording || !mediaRecorder) return;
        
        mediaRecorder.stop();
        isRecording = false;
        
        // Update UI
        elements.voiceRecordBtn.classList.remove('recording');
        elements.voiceRecordBtn.setAttribute('aria-label', 'Start voice input');
        elements.voiceRecordBtn.title = 'Hold to speak';
    }

    async function sendAudioForSTT(blob) {
        if (blob.size === 0) {
            showToast('No audio recorded');
            return;
        }
        
        if (blob.size > MAX_AUDIO_SIZE) {
            showToast('Recording too long (max ~30 seconds)');
            return;
        }
        
        try {
            const formData = new FormData();
            formData.append('audio', blob, 'recording.webm');
            
            const response = await fetch('/api/voice/stt', {
                method: 'POST',
                body: formData
            });
            
            const data = await response.json();
            
            if (data.text) {
                // Send transcript as chat message
                await sendChat(data.text, true);
            } else if (data.text === '') {
                showToast('No speech detected');
            }
        } catch (err) {
            console.error('STT error:', err);
            showToast('Speech recognition failed');
        }
    }

    // Event handlers
    function onSend(event) {
        event.preventDefault();
        
        const content = elements.messageInput.value.trim();
        if (!content) return;
        
        elements.messageInput.value = '';
        setInputState(true);
        
        sendChat(content, true).catch(err => {
            console.error('Chat error:', err);
        }).finally(() => {
            setInputState(false);
            elements.messageInput.focus();
        });
    }

    function onKeyDown(event) {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            onSend(event);
        }
    }

    function onNavClick(event) {
        const btn = event.target.closest('.nav-btn');
        if (btn) {
            switchPanel(btn.dataset.panel);
        }
    }

    function onVoiceBtnClick() {
        // Toggle voice recording on click (for header button)
        if (isRecording) {
            stopRecording();
        } else {
            startRecording();
        }
    }

    function onVoiceRecordMouseDown(event) {
        // Hold to record - start on mousedown/touchstart
        event.preventDefault();
        startRecording();
    }

    function onVoiceRecordMouseUp(event) {
        // Stop on mouseup/touchend
        event.preventDefault();
        stopRecording();
    }

    function onVoiceRecordMouseLeave(event) {
        // Stop if mouse leaves button while recording
        if (isRecording) {
            stopRecording();
        }
    }

    function onStopSpeakingClick(event) {
        event.preventDefault();
        stopCurrentTTS();
        sendStopSpeaking();
        showToast('Speech stopped', false);
    }

    function onMemorySearch(event) {
        event.preventDefault();
        const query = elements.memorySearchInput.value.trim();
        if (!query) return;
        
        fetch('/api/memory/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, limit: 10 })
        })
        .then(r => r.json())
        .then(data => {
            renderMemoryResults(data.results);
        })
        .catch(err => {
            console.error('Memory search error:', err);
            showToast('Memory search failed');
        });
    }

    function onMemoryAdd(event) {
        event.preventDefault();
        const content = elements.memoryContentInput.value.trim();
        if (!content) return;
        
        const memoryData = {
            content: content,
            memory_type: elements.memoryTypeSelect.value,
            importance: parseFloat(elements.memoryImportanceInput.value) || 0.5,
            confidence: parseFloat(elements.memoryConfidenceInput.value) || 0.5,
            source: 'user',
            tags: [],
            related_memories: [],
            deduplicate: true
        };
        
        fetch('/api/memory/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(memoryData)
        })
        .then(r => r.json())
        .then(data => {
            showToast('Memory added', false);
            elements.memoryContentInput.value = '';
            onMemorySearch({ preventDefault: () => {} });
        })
        .catch(err => {
            console.error('Add memory error:', err);
            showToast('Failed to add memory');
        });
    }

    function renderMemoryResults(results) {
        const container = elements.memoryResults;
        if (!results || results.length === 0) {
            container.innerHTML = '<div class="no-results">No memories found</div>';
            return;
        }
        
        container.innerHTML = results.map(r => `
            <div class="memory-result">
                <div class="memory-result-type">${r.memory_type}</div>
                <div class="memory-result-content">${escapeHtml(r.content)}</div>
                <div class="memory-result-meta">
                    <span>Importance: ${r.importance}</span>
                    <span>Confidence: ${r.confidence}</span>
                    <span>Score: ${r.score?.toFixed(2) || 'N/A'}</span>
                </div>
            </div>
        `).join('');
    }

    function loadWorkspace() {
        fetch('/api/workspace/')
        .then(r => r.json())
        .then(data => {
            if (data.available) {
                elements.wsProject.textContent = data.current_project?.name || '-';
                elements.wsLanguage.textContent = data.current_project?.language || '-';
                elements.wsIde.textContent = data.current_project?.ide || '-';
                elements.wsRepo.textContent = data.current_project?.git_repository || '-';
                elements.wsDir.textContent = data.working_directory_name || '-';
                elements.wsGitRepo.textContent = data.git_repository || '-';
                elements.wsApps.textContent = (data.active_applications || []).join(', ') || '-';
                elements.wsConfidence.textContent = data.confidence ? (data.confidence * 100).toFixed(1) + '%' : '-';
            } else {
                ['wsProject', 'wsLanguage', 'wsIde', 'wsRepo', 'wsDir', 'wsGitRepo', 'wsApps', 'wsConfidence'].forEach(id => {
                    elements[id].textContent = 'Unavailable';
                });
            }
        })
        .catch(err => {
            console.error('Load workspace error:', err);
        });
        
        fetch('/api/workspace/projects')
        .then(r => r.json())
        .then(data => {
            const container = document.getElementById('workspaceProjects');
            if (data.projects && data.projects.length > 0) {
                container.innerHTML = data.projects.map(p => `
                    <li>
                        <strong>${escapeHtml(p.name || 'Unknown')}</strong>
                        <span class="project-meta">${escapeHtml(p.language || '')} · ${escapeHtml(p.ide || '')} · ${escapeHtml(p.path_name || '')}</span>
                    </li>
                `).join('');
            } else {
                container.innerHTML = '<li class="no-results">No recent projects</li>';
            }
        })
        .catch(err => {
            console.error('Load projects error:', err);
        });
    }

    function loadHistory() {
        fetch('/api/history/?limit=50')
        .then(r => r.json())
        .then(data => {
            const container = elements.historyList;
            if (data.messages && data.messages.length > 0) {
                container.innerHTML = data.messages.map(m => `
                    <div class="history-item">
                        <span class="history-role">${escapeHtml(m.role)}</span>
                        <span class="history-content">${escapeHtml(m.content)}</span>
                    </div>
                `).join('');
            } else {
                container.innerHTML = '<div class="no-results">No history</div>';
            }
        })
        .catch(err => {
            console.error('Load history error:', err);
            container.innerHTML = '<div class="error">Failed to load history</div>';
        });
    }

    function onHistoryClear(event) {
        event.preventDefault();
        if (!confirm('Clear all chat history?')) return;
        
        fetch('/api/history/', { method: 'DELETE' })
        .then(r => r.json())
        .then(() => {
            showToast('History cleared', false);
            loadHistory();
            elements.messages.innerHTML = `
                <div class="message system">
                    <div class="message-avatar">J</div>
                    <div class="message-content">
                        <div class="message-bubble">
                            Welcome to Jarvis. Type a message to begin.
                        </div>
                    </div>
                </div>
            `;
        })
        .catch(err => {
            console.error('Clear history error:', err);
            showToast('Failed to clear history');
        });
    }

    // Initialize
    function init() {
        // Load auth token from URL parameter or localStorage
        const urlParams = new URLSearchParams(window.location.search);
        authToken = urlParams.get('token') || localStorage.getItem('jarvis_auth_token');
        
        // Cache DOM elements
        elements.navButtons = document.querySelectorAll('.nav-btn');
        elements.voiceBtn = document.getElementById('voiceBtn');
        elements.voiceRecordBtn = document.getElementById('voiceRecordBtn');
        elements.stopSpeakingBtn = document.getElementById('stopSpeakingBtn');
        
        elements.messageForm.addEventListener('submit', onSend);
        elements.messageInput.addEventListener('keydown', onKeyDown);
        elements.messageInput.addEventListener('input', () => {
            elements.sendButton.disabled = !elements.messageInput.value.trim() || !isConnected;
        });
        
        // Navigation
        elements.navButtons.forEach(btn => {
            btn.addEventListener('click', onNavClick);
        });
        
        // Voice buttons
        if (elements.voiceBtn) {
            elements.voiceBtn.addEventListener('click', onVoiceBtnClick);
        }
        if (elements.voiceRecordBtn) {
            // Hold to record
            elements.voiceRecordBtn.addEventListener('mousedown', onVoiceRecordMouseDown);
            elements.voiceRecordBtn.addEventListener('mouseup', onVoiceRecordMouseUp);
            elements.voiceRecordBtn.addEventListener('mouseleave', onVoiceRecordMouseLeave);
            elements.voiceRecordBtn.addEventListener('touchstart', onVoiceRecordMouseDown, { passive: true });
            elements.voiceRecordBtn.addEventListener('touchend', onVoiceRecordMouseUp);
        }
        if (elements.stopSpeakingBtn) {
            elements.stopSpeakingBtn.addEventListener('click', onStopSpeakingClick);
        }
        
        // Memory
        elements.memorySearchBtn.addEventListener('click', onMemorySearch);
        elements.memorySearchInput.addEventListener('keydown', e => {
            if (e.key === 'Enter') onMemorySearch(e);
        });
        elements.memoryAddBtn.addEventListener('click', onMemoryAdd);
        
        // History
        elements.historyClearBtn.addEventListener('click', onHistoryClear);
        
        // Initial connection
        connect();
        
        // Periodic ping to keep connection alive
        setInterval(() => {
            if (isConnected) {
                sendPing();
            }
        }, 30000);
        
        // Focus input on load
        elements.messageInput.focus();
    }

    // Start when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();