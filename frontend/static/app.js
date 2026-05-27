// ============================================================
// Anything Frontend — UI Logic
//
// 单文件 vanilla JS, 跟 api.js 配合渲染聊天 / 检索 / 工具调用 / 指标 / 上传。
//
// 状态保存:
//   localStorage('anything_settings') = {baseUrl, apiKey, sessionId, tenant}
//   localStorage('anything_history')  = [{role, content, meta, ts}, ...]
// ============================================================

(() => {
    // ---------- DOM 速查 ----------
    const $ = (id) => document.getElementById(id);
    const $$ = (sel) => Array.from(document.querySelectorAll(sel));

    const els = {
        messages: $('messages'),
        inputText: $('input-text'),
        sendBtn: $('send-btn'),
        topkInput: $('topk-input'),
        tenantInput: $('tenant-input'),
        healthBadge: $('health-badge'),
        healthDot: $('health-dot'),
        healthText: $('health-text'),
        settingsBtn: $('settings-btn'),
        settingsDrawer: $('settings-drawer'),
        apiBaseInput: $('api-base-input'),
        apiKeyInput: $('apikey-input'),
        sessionInput: $('session-input'),
        saveSettingsBtn: $('save-settings'),
        clearHistoryBtn: $('clear-history'),
        chunkList: $('chunk-list'),
        retrievedEmpty: $('retrieved-empty'),
        retrievedCount: $('retrieved-count'),
        stepList: $('step-list'),
        stepsEmpty: $('steps-empty'),
        stepsCount: $('steps-count'),
        metricsText: $('metrics-text'),
        metricsRefresh: $('metrics-refresh'),
        uploadArea: $('upload-area'),
        fileInput: $('file-input'),
        uploadResult: $('upload-result'),
        buildIndexBtn: $('build-index-btn'),
        jobResult: $('job-result'),
        toastContainer: $('toast-container'),
        langBtn: $('lang-btn'),
        sidebarToggle: $('sidebar-toggle'),
        sidebar: document.querySelector('.sidebar'),
        previewDrawer: $('preview-drawer'),
        previewTitle: $('preview-title'),
        previewMeta: $('preview-meta'),
        previewText: $('preview-text'),
    };

    // i18n shortcut
    const t = (key, params) => (window.I18n ? window.I18n.t(key, params) : key);

    // ---------- 状态 ----------
    const state = {
        mode: 'rag',              // rag / agent / hybrid
        history: [],
        sending: false,
        settings: {
            baseUrl: '',
            apiKey: '',
            sessionId: '',
            tenant: 'default',
        },
    };

    // ---------- 初始化 ----------
    function init() {
        loadSettings();
        // i18n: 先 apply 一次 DOM 标记的 data-i18n
        if (window.I18n) {
            window.I18n.applyToDom();
            // 语言变化时重渲染已有消息 (role 等动态字符串)
            window.I18n.onChange(() => {
                renderHistory();
                updateComposerPlaceholder();
                updateLangButton();
            });
            updateLangButton();
        }
        renderHistory();
        bindEvents();
        pollHealth();
        setInterval(pollHealth, 30000);
    }

    function updateLangButton() {
        if (!els.langBtn || !window.I18n) return;
        const cur = window.I18n.getLocale();
        els.langBtn.textContent = cur === 'zh' ? 'EN' : '中';
    }

    // ---------- 设置存取 ----------
    function loadSettings() {
        try {
            const raw = localStorage.getItem('anything_settings');
            if (raw) Object.assign(state.settings, JSON.parse(raw));
        } catch (_) {}
        try {
            const hist = localStorage.getItem('anything_history');
            if (hist) state.history = JSON.parse(hist);
        } catch (_) {}

        els.apiBaseInput.value = state.settings.baseUrl;
        els.apiKeyInput.value = state.settings.apiKey;
        els.sessionInput.value = state.settings.sessionId;
        els.tenantInput.value = state.settings.tenant;

        ApiClient.configure({
            baseUrl: state.settings.baseUrl,
            apiKey: state.settings.apiKey,
            sessionId: state.settings.sessionId,
        });
    }

    function saveSettings() {
        state.settings.baseUrl = els.apiBaseInput.value.trim();
        state.settings.apiKey = els.apiKeyInput.value.trim();
        state.settings.sessionId = els.sessionInput.value.trim();
        state.settings.tenant = (els.tenantInput.value || '').trim() || 'default';
        try {
            localStorage.setItem('anything_settings', JSON.stringify(state.settings));
        } catch (_) {}
        ApiClient.configure(state.settings);
        toast('success', t('toast.settings.saved'), t('toast.settings.saved.body'));
        closeDrawer('settings');
    }

    function persistHistory() {
        try {
            // 只保留最近 50 条, 避免 localStorage 爆掉
            const recent = state.history.slice(-50);
            localStorage.setItem('anything_history', JSON.stringify(recent));
        } catch (_) {}
    }

    // ---------- 事件绑定 ----------
    function bindEvents() {
        // 模式切换
        $$('.mode-tab').forEach(btn => {
            btn.addEventListener('click', () => {
                state.mode = btn.dataset.mode;
                $$('.mode-tab').forEach(b => {
                    b.classList.toggle('active', b === btn);
                    b.setAttribute('aria-selected', b === btn);
                });
                updateComposerPlaceholder();
            });
        });
        updateComposerPlaceholder();

        // 侧栏 tab 切换
        $$('.side-tab').forEach(btn => {
            btn.addEventListener('click', () => {
                const tab = btn.dataset.tab;
                $$('.side-tab').forEach(b => {
                    b.classList.toggle('active', b === btn);
                    b.setAttribute('aria-selected', b === btn);
                });
                $$('.side-panel').forEach(p => {
                    p.classList.toggle('active', p.dataset.panel === tab);
                });
                if (tab === 'metrics') loadMetrics();
            });
        });

        // 发送
        els.sendBtn.addEventListener('click', send);
        els.inputText.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault();
                send();
            }
        });

        // tenant_id 失焦时同步到 settings (但不持久化, 持久化在 save 时)
        els.tenantInput.addEventListener('change', () => {
            state.settings.tenant = (els.tenantInput.value || '').trim() || 'default';
        });

        // 健康检查点击重检
        els.healthBadge.addEventListener('click', pollHealth);

        // 语言切换 (header 按钮)
        if (els.langBtn && window.I18n) {
            els.langBtn.addEventListener('click', () => {
                const next = window.I18n.getLocale() === 'zh' ? 'en' : 'zh';
                window.I18n.setLocale(next);
            });
        }
        // 设置抽屉里的语言切换
        $$('.lang-opt').forEach((btn) => {
            btn.addEventListener('click', () => {
                if (window.I18n) window.I18n.setLocale(btn.dataset.locale);
                $$('.lang-opt').forEach((b) =>
                    b.classList.toggle('active', b === btn)
                );
            });
        });
        // 初始 active 标记
        if (window.I18n) {
            const curLoc = window.I18n.getLocale();
            $$('.lang-opt').forEach((b) =>
                b.classList.toggle('active', b.dataset.locale === curLoc)
            );
        }

        // 侧栏切换 (移动端用)
        if (els.sidebarToggle && els.sidebar) {
            els.sidebarToggle.addEventListener('click', () => {
                els.sidebar.classList.toggle('open');
            });
            // 在窄屏选中 chunk/step 时自动打开侧栏
            // 点击聊天区时自动关闭侧栏 (移动端 UX)
            els.messages.addEventListener('click', () => {
                if (window.innerWidth <= 1100 && els.sidebar.classList.contains('open')) {
                    // 仅在 sidebar 当前是悬浮态时关闭
                    els.sidebar.classList.remove('open');
                }
            });
        }

        // 设置抽屉
        els.settingsBtn.addEventListener('click', () => openDrawer('settings'));
        $$('[data-close="settings"]').forEach(el =>
            el.addEventListener('click', () => closeDrawer('settings'))
        );
        // 预览抽屉关闭按钮
        $$('[data-close="preview"]').forEach(el =>
            el.addEventListener('click', () => closeDrawer('preview'))
        );
        els.saveSettingsBtn.addEventListener('click', saveSettings);
        els.clearHistoryBtn.addEventListener('click', clearHistory);

        // 指标刷新
        els.metricsRefresh.addEventListener('click', loadMetrics);

        // 上传
        els.fileInput.addEventListener('change', () => {
            if (els.fileInput.files.length) uploadFile(els.fileInput.files[0]);
        });
        ['dragenter', 'dragover'].forEach(evt =>
            els.uploadArea.addEventListener(evt, (e) => {
                e.preventDefault();
                els.uploadArea.classList.add('dragover');
            })
        );
        ['dragleave', 'drop'].forEach(evt =>
            els.uploadArea.addEventListener(evt, (e) => {
                e.preventDefault();
                els.uploadArea.classList.remove('dragover');
            })
        );
        els.uploadArea.addEventListener('drop', (e) => {
            if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
        });

        els.buildIndexBtn.addEventListener('click', triggerBuildIndex);
    }

    function updateComposerPlaceholder() {
        const key = `composer.placeholder.${state.mode || 'rag'}`;
        els.inputText.placeholder = t(key);
    }

    // ---------- 发送 ----------
    async function send() {
        if (state.sending) return;
        const text = els.inputText.value.trim();
        if (!text) {
            toast('error', t('toast.input.empty'), t('toast.input.empty.body'));
            return;
        }
        const topK = Math.max(1, Math.min(50, Number(els.topkInput.value) || 5));
        const mode = state.mode;
        const tenant = (els.tenantInput.value || '').trim() || 'default';

        const body = { type: mode, top_k: topK, tenant_id: tenant };
        if (mode === 'rag') body.query = text;
        else body.task = text;

        // 加用户消息
        addMessage({ role: 'user', mode, content: text, ts: Date.now() });
        // 占位 assistant 消息
        const placeholderId = addMessage({
            role: 'assistant', mode, content: '', loading: true, ts: Date.now(),
        });

        state.sending = true;
        els.sendBtn.disabled = true;
        els.inputText.value = '';
        els.inputText.focus();

        try {
            const { payload, traceId, costTime, status } = await ApiClient.invoke(body);
            updateMessage(placeholderId, {
                loading: false,
                content: extractAnswer(payload),
                meta: {
                    code: payload?.code || `HTTP_${status}`,
                    traceId: payload?.trace_id || traceId,
                    costTime: costTime || (payload?.cost_time != null ? payload.cost_time.toFixed(3) : ''),
                    tenant,
                    mode,
                },
                data: payload?.data,
                error: payload?.code && payload.code !== 'SUCCESS' ? payload : null,
            });
            // 侧栏渲染
            renderRetrievedChunks(payload?.data?.retrieved_chunks || []);
            renderAgentSteps(payload?.data?.steps || []);
            if (payload?.code && payload.code !== 'SUCCESS') {
                toast('error', payload.code, payload.message || '请求失败');
            }
        } catch (err) {
            updateMessage(placeholderId, {
                loading: false,
                content: `${t('toast.network.error')}: ${err.message}`,
                meta: { code: 'NETWORK_ERROR', tenant, mode },
                error: { code: 'NETWORK_ERROR', message: err.message },
            });
            toast('error', t('toast.network.error'), String(err.message || err));
        } finally {
            state.sending = false;
            els.sendBtn.disabled = false;
        }
    }

    function extractAnswer(payload) {
        if (!payload) return t('msg.empty');
        if (payload.code !== 'SUCCESS') {
            return `[${payload.code}] ${payload.message || ''}`;
        }
        const d = payload.data || {};
        return d.answer || JSON.stringify(d, null, 2);
    }

    // ---------- 消息渲染 ----------
    function addMessage(msg) {
        const id = 'msg_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
        msg.id = id;
        state.history.push(msg);
        const welcome = els.messages.querySelector('.welcome');
        if (welcome) welcome.remove();
        renderMessage(msg);
        scrollToBottom();
        persistHistory();
        return id;
    }

    function updateMessage(id, patch) {
        const idx = state.history.findIndex(m => m.id === id);
        if (idx === -1) return;
        Object.assign(state.history[idx], patch);
        const node = els.messages.querySelector(`[data-id="${id}"]`);
        if (node) {
            node.replaceWith(buildMessageNode(state.history[idx]));
        }
        scrollToBottom();
        persistHistory();
    }

    function renderHistory() {
        els.messages.innerHTML = '';
        if (!state.history.length) {
            // 重新插入欢迎块 (复用 data-i18n 让 setLocale 也能刷新)
            els.messages.innerHTML = `<div class="welcome">
                <h2 data-i18n="welcome.title"></h2>
                <p data-i18n="welcome.desc"></p>
                <ul class="hint-list">
                    <li data-i18n="welcome.hint.rag"></li>
                    <li data-i18n="welcome.hint.agent"></li>
                    <li data-i18n="welcome.hint.hybrid"></li>
                </ul>
            </div>`;
            if (window.I18n) window.I18n.applyToDom(els.messages);
            return;
        }
        state.history.forEach(renderMessage);
        scrollToBottom();
    }

    function renderMessage(msg) {
        els.messages.appendChild(buildMessageNode(msg));
    }

    function buildMessageNode(msg) {
        const wrapper = document.createElement('div');
        wrapper.className = 'message message-' + (msg.role === 'user' ? 'user' : 'assistant');
        wrapper.dataset.id = msg.id;

        const avatar = document.createElement('div');
        avatar.className = 'message-avatar';
        avatar.textContent = msg.role === 'user' ? '👤' : '🤖';

        const content = document.createElement('div');
        content.className = 'message-content';

        // header
        const header = document.createElement('div');
        header.className = 'message-header';
        const role = document.createElement('span');
        role.className = 'message-role';
        role.textContent = msg.role === 'user' ? t('role.user') : t('role.assistant');
        header.appendChild(role);

        const modeBadge = document.createElement('span');
        modeBadge.style.cssText = 'font-family:var(--mono);font-size:11px;color:var(--accent);';
        modeBadge.textContent = (msg.mode || '').toUpperCase();
        header.appendChild(modeBadge);

        if (msg.meta) {
            const meta = document.createElement('span');
            meta.className = 'message-meta';
            if (msg.meta.code && msg.meta.code !== 'SUCCESS') {
                const codeChip = document.createElement('span');
                codeChip.className = 'chip';
                codeChip.style.borderColor = 'var(--danger)';
                codeChip.style.color = 'var(--danger)';
                codeChip.textContent = msg.meta.code;
                meta.appendChild(codeChip);
            }
            if (msg.meta.tenant) {
                const t = document.createElement('span');
                t.className = 'chip';
                t.textContent = 'tenant=' + msg.meta.tenant;
                meta.appendChild(t);
            }
            if (msg.meta.costTime) {
                const c = document.createElement('span');
                c.className = 'chip';
                c.textContent = msg.meta.costTime + 's';
                meta.appendChild(c);
            }
            if (msg.meta.traceId) {
                const tr = document.createElement('span');
                tr.className = 'chip';
                tr.title = msg.meta.traceId;
                tr.textContent = 'trace=' + String(msg.meta.traceId).slice(0, 8);
                tr.style.cursor = 'pointer';
                tr.addEventListener('click', () => {
                    navigator.clipboard?.writeText(msg.meta.traceId);
                    toast('info', t('toast.copied.trace'), msg.meta.traceId);
                });
                meta.appendChild(tr);
            }
            header.appendChild(meta);
        }

        content.appendChild(header);

        // body
        const body = document.createElement('div');
        body.className = 'message-body';
        if (msg.error) body.classList.add('error');

        if (msg.loading) {
            body.innerHTML =
                '<span class="message-loading">' +
                '<span class="dot"></span><span class="dot"></span><span class="dot"></span> ' +
                Markdown.escapeHtml(t('msg.processing')) + '</span>';
        } else if (msg.role === 'assistant' && !msg.error && window.Markdown) {
            // 助手成功响应走 markdown 渲染 (用户消息保持 plain text 防 XSS)
            body.innerHTML = window.Markdown.render(msg.content || '');
            window.Markdown.bindCopyButtons(body);
        } else {
            body.textContent = msg.content || '';
        }
        content.appendChild(body);

        // citations
        if (msg.data && Array.isArray(msg.data.citations) && msg.data.citations.length) {
            const cites = document.createElement('div');
            cites.className = 'message-citations';
            msg.data.citations.forEach((c, i) => {
                const chip = document.createElement('span');
                chip.className = 'citation-chip';
                const fn = c.file_name || c.doc_id || '?';
                const score = c.score != null ? ` ${c.score.toFixed(2)}` : '';
                chip.textContent = `[${i + 1}] ${fn}${score}`;
                chip.title = `chunk_id=${c.chunk_id}\ndoc_id=${c.doc_id}\nclick: focus chunk · shift+click: preview source`;
                chip.addEventListener('click', (e) => {
                    if (e.shiftKey) {
                        openPreview(c);
                    } else {
                        focusChunk(c.chunk_id);
                    }
                });
                cites.appendChild(chip);
            });
            content.appendChild(cites);
        }

        // 错误时显示重试按钮
        if (msg.error && msg.role === 'assistant') {
            const actions = document.createElement('div');
            actions.className = 'message-actions';
            if (msg.error.retryable) {
                const retry = document.createElement('button');
                retry.textContent = t('action.retry');
                retry.addEventListener('click', () => {
                    // 取上一条 user 消息内容重发
                    const idx = state.history.findIndex(m => m.id === msg.id);
                    const prev = idx > 0 ? state.history[idx - 1] : null;
                    if (prev?.role === 'user') {
                        els.inputText.value = prev.content;
                        send();
                    }
                });
                actions.appendChild(retry);
            }
            const copyMsg = document.createElement('button');
            copyMsg.textContent = t('action.copyResp');
            copyMsg.addEventListener('click', () => {
                navigator.clipboard?.writeText(JSON.stringify(msg.error, null, 2));
                toast('info', t('toast.copied.error'), '');
            });
            actions.appendChild(copyMsg);
            content.appendChild(actions);
        }

        wrapper.appendChild(avatar);
        wrapper.appendChild(content);
        return wrapper;
    }

    function scrollToBottom() {
        requestAnimationFrame(() => {
            els.messages.scrollTop = els.messages.scrollHeight;
        });
    }

    function clearHistory() {
        if (!confirm(t('confirm.clearHistory'))) return;
        state.history = [];
        persistHistory();
        renderHistory();
        renderRetrievedChunks([]);
        renderAgentSteps([]);
        toast('info', t('toast.history.cleared'), '');
        closeDrawer('settings');
    }

    // ---------- 侧栏:检索结果 ----------
    function renderRetrievedChunks(chunks) {
        els.chunkList.innerHTML = '';
        els.retrievedCount.textContent = chunks.length;
        if (!chunks.length) {
            els.retrievedEmpty.style.display = 'block';
            return;
        }
        els.retrievedEmpty.style.display = 'none';
        chunks.forEach((c, i) => {
            const li = document.createElement('li');
            li.className = 'chunk-item';
            li.dataset.chunkId = c.chunk_id || '';

            const header = document.createElement('div');
            header.className = 'chunk-header';
            const idSpan = document.createElement('span');
            idSpan.textContent = `#${i + 1} ${c.file_name || c.doc_id || '?'}`;
            const score = document.createElement('span');
            score.className = 'chunk-score';
            score.textContent = c.score != null ? c.score.toFixed(3) : '—';
            header.appendChild(idSpan);
            header.appendChild(score);
            li.appendChild(header);

            const text = document.createElement('div');
            text.className = 'chunk-content';
            text.textContent = (c.content || '(无 content 字段)').slice(0, 500);
            li.appendChild(text);

            const meta = document.createElement('div');
            meta.className = 'chunk-meta-row';
            const parts = [];
            if (c.chunk_id) parts.push(`chunk_id=${c.chunk_id}`);
            if (c.chunk_index != null) parts.push(`idx=${c.chunk_index}`);
            if (c.start_char != null) parts.push(`[${c.start_char}-${c.end_char}]`);
            meta.textContent = parts.join(' · ');
            li.appendChild(meta);

            // "查看原文" 按钮: 调 GET /documents/{doc_id}/preview
            if (c.doc_id) {
                const viewBtn = document.createElement('button');
                viewBtn.className = 'chunk-view-btn';
                viewBtn.textContent = t('preview.viewOriginal');
                viewBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    openPreview(c);
                });
                li.appendChild(viewBtn);
            }

            els.chunkList.appendChild(li);
        });
    }

    /** 打开预览抽屉, 调 GET /documents/{doc_id}/preview */
    async function openPreview(chunk) {
        if (!chunk || !chunk.doc_id) return;
        openDrawer('preview');
        els.previewTitle.textContent = chunk.file_name || chunk.doc_id;
        els.previewMeta.innerHTML = `<span class="chip">${t('preview.loading')}</span>`;
        els.previewText.textContent = '';

        const opts = {};
        if (chunk.start_char != null) opts.start_char = chunk.start_char;
        if (chunk.end_char != null) opts.end_char = chunk.end_char;
        opts.context = 200;
        // 透传 tenant_id (仅 internal IP 时后端才信任)
        const tenant = (els.tenantInput.value || '').trim();
        if (tenant) opts.tenant_id = tenant;

        try {
            const { payload, status } = await ApiClient.getDocumentPreview(chunk.doc_id, opts);
            if (status !== 200 || payload?.code !== 'SUCCESS') {
                els.previewMeta.innerHTML = `<span class="chip" style="color:var(--danger);border-color:var(--danger);">${
                    Markdown.escapeHtml(payload?.code || `HTTP ${status}`)
                }</span>`;
                els.previewText.textContent = payload?.message || t('preview.error');
                return;
            }
            const d = payload.data;
            els.previewTitle.textContent = d.file_name || chunk.doc_id;

            // meta: 总长度 / 高亮范围 / 文件类型 chips
            const chips = [];
            if (d.file_type) chips.push(`<span class="chip">${Markdown.escapeHtml(d.file_type)}</span>`);
            chips.push(`<span class="chip">${t('preview.totalChars')}: ${d.total_chars}</span>`);
            chips.push(
                `<span class="chip">${t('preview.range')}: ${d.snippet_start + d.highlight_start} - ${
                    d.snippet_start + d.highlight_end
                }</span>`
            );
            els.previewMeta.innerHTML = chips.join(' ');

            // 渲染 snippet 把高亮区域包成 <mark>
            const esc = Markdown.escapeHtml;
            const before = esc(d.snippet.slice(0, d.highlight_start));
            const hit = esc(d.snippet.slice(d.highlight_start, d.highlight_end));
            const after = esc(d.snippet.slice(d.highlight_end));
            els.previewText.innerHTML = `${before}<mark>${hit}</mark>${after}`;
            // 滚到高亮位置
            requestAnimationFrame(() => {
                const mk = els.previewText.querySelector('mark');
                if (mk) mk.scrollIntoView({ behavior: 'smooth', block: 'center' });
            });
        } catch (err) {
            els.previewMeta.innerHTML = `<span class="chip" style="color:var(--danger);border-color:var(--danger);">ERROR</span>`;
            els.previewText.textContent = err.message || t('preview.error');
        }
    }

    function focusChunk(chunkId) {
        // 切到侧栏 retrieved tab
        $$('.side-tab').forEach(b => {
            b.classList.toggle('active', b.dataset.tab === 'retrieved');
            b.setAttribute('aria-selected', b.dataset.tab === 'retrieved');
        });
        $$('.side-panel').forEach(p => {
            p.classList.toggle('active', p.dataset.panel === 'retrieved');
        });
        // 移动端: 自动打开侧栏覆盖层
        if (window.innerWidth <= 1100 && els.sidebar) {
            els.sidebar.classList.add('open');
        }
        const node = els.chunkList.querySelector(`[data-chunk-id="${chunkId}"]`);
        if (node) {
            node.scrollIntoView({ behavior: 'smooth', block: 'center' });
            node.style.transition = 'background 0.3s';
            node.style.background = 'var(--accent-soft)';
            setTimeout(() => { node.style.background = 'var(--bg)'; }, 1500);
        }
    }

    // ---------- 侧栏:Agent 步骤 ----------
    function renderAgentSteps(steps) {
        els.stepList.innerHTML = '';
        els.stepsCount.textContent = steps.length;
        if (!steps.length) {
            els.stepsEmpty.style.display = 'block';
            return;
        }
        els.stepsEmpty.style.display = 'none';
        steps.forEach((s, i) => {
            const li = document.createElement('li');
            li.className = 'step-item';

            const header = document.createElement('div');
            header.className = 'step-header';
            const left = document.createElement('span');
            left.innerHTML = `Step ${i + 1} · <span class="step-tool">${s.tool_name || '?'}</span>`;
            header.appendChild(left);
            if (s.step_id) {
                const right = document.createElement('span');
                right.textContent = s.step_id;
                header.appendChild(right);
            }
            li.appendChild(header);

            if (s.description) {
                const desc = document.createElement('div');
                desc.style.cssText = 'color:var(--text-dim);margin-bottom:4px;font-size:11px;';
                desc.textContent = s.description;
                li.appendChild(desc);
            }

            const input = document.createElement('pre');
            input.className = 'step-input';
            input.textContent = JSON.stringify(s.input_data || {}, null, 2);
            li.appendChild(input);

            els.stepList.appendChild(li);
        });
    }

    // ---------- 侧栏:指标 ----------
    async function loadMetrics() {
        els.metricsText.textContent = '加载中...';
        try {
            const { payload, status } = await ApiClient.metrics();
            if (status === 200 && typeof payload === 'string') {
                els.metricsText.textContent = payload || '(空)';
            } else {
                els.metricsText.textContent = `加载失败 HTTP ${status}\n${payload}`;
            }
        } catch (e) {
            els.metricsText.textContent = '加载失败: ' + e.message;
        }
    }

    // ---------- 侧栏:上传 ----------
    async function uploadFile(file) {
        els.uploadResult.className = 'upload-result';
        els.uploadResult.textContent = `上传中: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
        try {
            const { payload, status } = await ApiClient.uploadDocument(file);
            if (status === 200 && payload?.code === 'SUCCESS') {
                els.uploadResult.className = 'upload-result success';
                els.uploadResult.textContent = `✓ ${payload.data.stored_path}`;
                toast('success', t('toast.upload.success'), payload.data.file_name);
            } else {
                els.uploadResult.className = 'upload-result error';
                els.uploadResult.textContent = `× ${payload?.code || status} ${payload?.message || ''}`;
                toast('error', t('toast.upload.fail'), payload?.message || `HTTP ${status}`);
            }
        } catch (e) {
            els.uploadResult.className = 'upload-result error';
            els.uploadResult.textContent = `× ${e.message}`;
            toast('error', t('toast.upload.exception'), e.message);
        }
    }

    async function triggerBuildIndex() {
        els.jobResult.className = 'job-result';
        els.jobResult.textContent = '提交构建任务...';
        try {
            const { payload, status } = await ApiClient.buildIndex();
            if (status === 200 && payload?.code === 'SUCCESS') {
                const jobId = payload.data?.job_id;
                els.jobResult.className = 'job-result success';
                els.jobResult.textContent = `✓ ${jobId}`;
                toast('success', t('toast.index.triggered'), `job_id=${jobId}`);
            } else {
                els.jobResult.className = 'job-result error';
                els.jobResult.textContent = `× ${payload?.code || status} ${payload?.message || ''}`;
            }
        } catch (e) {
            els.jobResult.className = 'job-result error';
            els.jobResult.textContent = `× ${e.message}`;
        }
    }

    // ---------- 健康检查 ----------
    async function pollHealth() {
        els.healthDot.className = 'health-dot';
        els.healthText.textContent = t('header.health.checking');
        try {
            const { status, payload } = await ApiClient.healthz();
            if (status === 200 && payload?.code === 'SUCCESS') {
                els.healthDot.classList.add('up');
                els.healthText.textContent = t('header.health.up');
            } else {
                els.healthDot.classList.add('down');
                els.healthText.textContent = `HTTP ${status}`;
            }
        } catch (e) {
            els.healthDot.classList.add('down');
            els.healthText.textContent = t('header.health.down');
        }
    }

    // ---------- Drawer ----------
    function openDrawer(name) {
        const drawer = $(`${name}-drawer`);
        if (drawer) drawer.hidden = false;
    }
    function closeDrawer(name) {
        const drawer = $(`${name}-drawer`);
        if (drawer) drawer.hidden = true;
    }

    // ---------- Toast ----------
    function toast(type, title, body) {
        const t = document.createElement('div');
        t.className = `toast ${type}`;
        t.innerHTML = `<div class="toast-title">${title}</div>` +
            (body ? `<div class="toast-body">${escapeHtml(String(body))}</div>` : '');
        els.toastContainer.appendChild(t);
        setTimeout(() => {
            t.style.opacity = '0';
            t.style.transform = 'translateX(20px)';
            setTimeout(() => t.remove(), 300);
        }, 3500);
    }

    function escapeHtml(s) {
        return s
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // ---------- 启动 ----------
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
