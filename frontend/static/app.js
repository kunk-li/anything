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
        uploadQueue: $('upload-queue'),         // Task P: 多文件队列
        uploadResult: $('upload-result'),
        buildIndexBtn: $('build-index-btn'),
        jobResult: $('job-result'),
        adminRefresh: $('admin-refresh'),       // Task S
        adminGrid: $('admin-grid'),
        // Task JJ (#70): 已索引文档管理
        docsRefreshBtn: $('docs-refresh-btn'),
        docsList: $('docs-list'),
        docsCountHint: $('docs-count-hint'),
        toastContainer: $('toast-container'),
        langBtn: $('lang-btn'),
        sidebarToggle: $('sidebar-toggle'),
        sidebar: document.querySelector('.sidebar'),
        chatPane: document.querySelector('.chat-pane'),
        previewDrawer: $('preview-drawer'),
        previewTitle: $('preview-title'),
        previewMeta: $('preview-meta'),
        previewText: $('preview-text'),
        // Task DD (#64) — Plan approval modal
        planDrawer: $('plan-drawer'),
        planThought: $('plan-thought'),
        planActionSection: $('plan-action-section'),
        planToolName: $('plan-tool-name'),
        planToolInput: $('plan-tool-input'),
        planFinalSection: $('plan-final-section'),
        planFinalAnswer: $('plan-final-answer'),
        planApproveBtn: $('plan-approve-btn'),
        planCancelBtn: $('plan-cancel-btn'),
        // Task GG (#67)
        exportMdBtn: $('export-md-btn'),
        exportJsonBtn: $('export-json-btn'),
        streamToggle: $('stream-toggle'),
        planToggle: $('plan-toggle'),               // Task X (#58): Plan mode
        reflectToggle: $('reflect-toggle'),         // Task III/KKK: Reflection 反思环
        composerInputRow: $('composer-input-row'),
        composerAttachments: $('composer-attachments'),
        imageBtn: $('image-btn'),
        imagePicker: $('image-picker'),
        modelsRefresh: $('models-refresh'),
        modelsAdd: $('models-add'),
        modelsTbody: $('models-tbody'),
        modelForm: $('model-form'),
        mfName: $('mf-name'),
        mfType: $('mf-type'),
        mfAdapter: $('mf-adapter'),
        mfApiBase: $('mf-api-base'),
        mfApiKey: $('mf-api-key'),
        mfDefault: $('mf-default'),
        mfSubmit: $('mf-submit'),
        mfCancel: $('mf-cancel'),
        // Task KKK (#97): 长期记忆面板
        memoryList: $('memory-list'),
        memoryCount: $('memory-count'),
        memoryCountHint: $('memory-count-hint'),
        memoryRefreshBtn: $('memory-refresh-btn'),
        memorySearchInput: $('memory-search-input'),
        memorySearchBtn: $('memory-search-btn'),
        memoryTagFilter: $('memory-tag-filter'),
        // Task NNN (#100): Reflection 详情 modal
        reflectDrawer: $('reflect-drawer'),
        reflectQuality: $('reflect-quality'),
        reflectLlmCalls: $('reflect-llm-calls'),
        reflectCostMs: $('reflect-cost-ms'),
        reflectIssues: $('reflect-issues'),
        reflectMissing: $('reflect-missing'),
        reflectSkipSection: $('reflect-skip-section'),
        reflectSkipReason: $('reflect-skip-reason'),
        reflectRaw: $('reflect-raw'),
        // Task RRR (#104): Trace timeline drawer
        traceDrawer: $('trace-drawer'),
        traceIdChip: $('trace-id-chip'),
        traceTotalChip: $('trace-total-chip'),
        traceModeChip: $('trace-mode-chip'),
        traceTimeline: $('trace-timeline'),
        traceRaw: $('trace-raw'),
        // Task SSS (#105): 多会话切换 (settings drawer 内)
        sessionsSelect: $('sessions-select'),
        sessionsRefreshBtn: $('sessions-refresh-btn'),
        sessionsNewBtn: $('sessions-new-btn'),
        sessionsDeleteBtn: $('sessions-delete-btn'),
    };

    // 当前正在跑的 WebSocket 句柄 (用于"停止"按钮中断)
    let activeStream = null;

    // i18n shortcut
    const t = (key, params) => (window.I18n ? window.I18n.t(key, params) : key);

    // ---------- 状态 ----------
    const state = {
        mode: 'rag',              // rag / agent / hybrid
        history: [],
        sending: false,
        // 待发送的图片附件 [{id, file, previewUrl, status: 'pending'|'uploading'|'ready'|'failed', storedPath?}]
        pendingAttachments: [],
        settings: {
            baseUrl: '',
            apiKey: '',
            sessionId: '',
            tenant: 'default',
            useStream: false,
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

        // Task KKK (#97): 长期记忆面板 — 工厂注入 deps + bindEvents
        if (window.AnythingApp && window.AnythingApp.memoryPanel) {
            const memPanel = window.AnythingApp.memoryPanel({ els, t, toast, escapeHtml });
            window.AnythingApp._memoryPanel = memPanel;
            memPanel.bindEvents();
        }

        // Task SSS (#105): 多会话切换 bindEvents
        if (els.sessionsRefreshBtn) els.sessionsRefreshBtn.addEventListener('click', refreshSessions);
        if (els.sessionsNewBtn) els.sessionsNewBtn.addEventListener('click', createNewSession);
        if (els.sessionsDeleteBtn) els.sessionsDeleteBtn.addEventListener('click', deleteSelectedSession);
        if (els.sessionsSelect) els.sessionsSelect.addEventListener('change', switchSession);
        // 打开 settings 抽屉时自动刷新一次 session 列表
        if (els.settingsBtn) {
            els.settingsBtn.addEventListener('click', () => {
                setTimeout(refreshSessions, 100);
            });
        }
    }

    // ---------- Task SSS (#105): 多会话管理 ----------
    async function refreshSessions() {
        if (!els.sessionsSelect) return;
        try {
            const { payload, status } = await ApiClient.listSessions(50);
            if (status === 501) {
                els.sessionsSelect.innerHTML = '<option value="">(state_store 未注入)</option>';
                return;
            }
            if (status !== 200 || payload?.code !== 'SUCCESS') {
                els.sessionsSelect.innerHTML = `<option value="">(${payload?.code || status})</option>`;
                return;
            }
            const sessions = (payload.data || {}).sessions || [];
            const current = state.settings.sessionId;
            const opts = ['<option value="">(选择会话)</option>'];
            for (const s of sessions) {
                const sid = s.session_id;
                const when = s.last_modified
                    ? new Date(s.last_modified * 1000).toLocaleString()
                    : '';
                const flag = s.has_history ? '💬 ' : '   ';
                const selected = sid === current ? ' selected' : '';
                const label = `${flag}${sid.slice(0, 30)} · ${when}`;
                opts.push(`<option value="${escapeHtml(sid)}"${selected}>${escapeHtml(label)}</option>`);
            }
            els.sessionsSelect.innerHTML = opts.join('');
        } catch (e) {
            els.sessionsSelect.innerHTML = `<option value="">(${escapeHtml(e.message)})</option>`;
        }
    }

    async function createNewSession() {
        // 关键: 不能调 saveSettings() — 它会关抽屉 + 弹 "settings saved" toast,
        // 用户感觉啥都没发生. 自己手动写 localStorage + reconfigure ApiClient 即可.
        try {
            const { payload, status } = await ApiClient.createSession();
            if (status !== 200 || payload?.code !== 'SUCCESS') {
                toast('error', payload?.code || status, payload?.message || '');
                return;
            }
            const newId = payload.data?.session_id;
            if (!newId) {
                toast('error', '创建会话失败', '后端未返 session_id');
                return;
            }
            // 1. state + sessionInput UI 同步新 id
            state.settings.sessionId = newId;
            if (els.sessionInput) els.sessionInput.value = newId;
            // 2. localStorage + ApiClient reconfigure, 不关抽屉
            try { localStorage.setItem('anything_settings', JSON.stringify(state.settings)); } catch (_) {}
            ApiClient.configure(state.settings);
            // 3. 刷下拉, 兜底插一条 option 让用户立马能看见新会话被选中
            await refreshSessions();
            if (els.sessionsSelect) {
                const has = Array.from(els.sessionsSelect.options).some(o => o.value === newId);
                if (!has) {
                    const opt = document.createElement('option');
                    opt.value = newId;
                    opt.textContent = `💬 ${newId} · (新建)`;
                    opt.selected = true;
                    const firstReal = els.sessionsSelect.firstElementChild
                        && els.sessionsSelect.firstElementChild.nextSibling;
                    els.sessionsSelect.insertBefore(opt, firstReal);
                } else {
                    els.sessionsSelect.value = newId;
                }
            }
            toast('success', '新会话已创建', newId);
        } catch (e) {
            toast('error', '新建会话失败', e.message);
        }
    }

    async function deleteSelectedSession() {
        if (!els.sessionsSelect || !els.sessionsSelect.value) {
            toast('warn', '请先选择一个会话', '');
            return;
        }
        const sid = els.sessionsSelect.value;
        if (!confirm(`确认删除会话 ${sid}? 不可恢复.`)) return;
        try {
            const { payload, status } = await ApiClient.deleteSession(sid);
            if (status === 200 && payload?.code === 'SUCCESS') {
                toast('success', '会话已删除', sid);
                // 删的是当前会话 → 重生一个新 id 避免下次发送指向已删 session
                if (state.settings.sessionId === sid) {
                    const newId = 'sess_' + Date.now() + '_' + Math.random().toString(36).slice(2, 10);
                    state.settings.sessionId = newId;
                    if (els.sessionInput) els.sessionInput.value = newId;
                    try { localStorage.setItem('anything_settings', JSON.stringify(state.settings)); } catch (_) {}
                    ApiClient.configure(state.settings);
                }
                refreshSessions();
            } else {
                toast('error', payload?.code || status, payload?.message || '');
            }
        } catch (e) {
            toast('error', '删除会话失败', e.message);
        }
    }

    function switchSession() {
        if (!els.sessionsSelect) return;
        const sid = els.sessionsSelect.value;
        if (!sid) return;
        state.settings.sessionId = sid;
        if (els.sessionInput) els.sessionInput.value = sid;
        try { localStorage.setItem('anything_settings', JSON.stringify(state.settings)); } catch (_) {}
        ApiClient.configure(state.settings);
        toast('info', '已切换会话', sid);
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

        // Task #46: 自动生成稳定 session_id (没显式配置时), 后端 RAG 据此读写会话历史
        if (!state.settings.sessionId) {
            state.settings.sessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).slice(2, 10);
            try {
                localStorage.setItem('anything_settings', JSON.stringify(state.settings));
            } catch (_) {}
        }

        els.apiBaseInput.value = state.settings.baseUrl;
        els.apiKeyInput.value = state.settings.apiKey;
        els.sessionInput.value = state.settings.sessionId;
        els.tenantInput.value = state.settings.tenant;
        if (els.streamToggle) els.streamToggle.checked = !!state.settings.useStream;

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
                // Task KKK (#97): 切到 memory tab 自动刷新
                if (tab === 'memory' && window.AnythingApp && window.AnythingApp._memoryPanel) {
                    window.AnythingApp._memoryPanel.refreshMemory();
                }
            });
        });

        // 发送 / 停止 (Task GG #67)
        els.sendBtn.addEventListener('click', () => {
            if (state.sending) {
                stopSending();
            } else {
                send();
            }
        });
        els.inputText.addEventListener('keydown', (e) => {
            // 中文/日韩输入法 composition 阶段, keyCode 229 + isComposing=true, 不发送
            if (e.isComposing || e.keyCode === 229) return;
            // Enter (无 shift) -> 发送; Shift+Enter -> 让 textarea 自然换行
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                send();
                return;
            }
            // 保留 Ctrl+Enter (向后兼容老用户习惯)
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault();
                send();
            }
        });

        // ========== 📷 文件选择按钮 (拖拽永远 fallback) ==========
        if (els.imageBtn && els.imagePicker) {
            els.imageBtn.addEventListener('click', (e) => {
                e.preventDefault();
                els.imagePicker.value = ''; // 允许重选同一文件
                els.imagePicker.click();
            });
            els.imagePicker.addEventListener('change', (e) => {
                const files = e.target.files;
                if (!files || !files.length) return;
                let added = 0;
                let firstName = '';
                for (const f of files) {
                    if (f.type && f.type.startsWith('image/')) {
                        if (!firstName) firstName = f.name;
                        addAttachment(f);
                        added++;
                    }
                }
                if (added > 0) {
                    toast('success', t('toast.attach.added'),
                        added === 1 ? firstName : `${added} files`);
                }
            });
        }

        // ========== 拖拽图片到输入框 ==========
        // 调试: localStorage.setItem('anything_drag_debug','1') 开启 console.log
        const DRAG_DEBUG = (() => {
            try { return localStorage.getItem('anything_drag_debug') === '1'; }
            catch { return false; }
        })();
        const dlog = (...args) => { if (DRAG_DEBUG) console.log('[drag]', ...args); };
        dlog('init, composerInputRow=', els.composerInputRow, 'chatPane=', els.chatPane);

        // 关键陷阱:
        // 1) 必须在 window 级别 preventDefault 兜底, 否则用户拖偏一点
        //    浏览器会把图片当 URL 打开 (navigate 到 file:// 把整个页面替换)
        // 2) dragover 必须每次 preventDefault, 否则 drop 事件根本不会触发
        // 3) textarea 元素自己接受 drop (默认把文件名插进去), 需要我们覆盖
        // 4) Chrome / Firefox 对 dataTransfer.types 表现不同,
        //    用 files.length > 0 判断比看 types.includes('Files') 更稳
        if (els.composerInputRow) {
            const dropZone = els.composerInputRow;
            const dropHint = () => t('composer.drop.hint');
            dropZone.setAttribute('data-drop-hint', dropHint());
            if (window.I18n) {
                window.I18n.onChange(() => {
                    dropZone.setAttribute('data-drop-hint', dropHint());
                });
            }

            // 用 body 上的 data-drop-hint + has-drag class 渲染全屏拖拽 overlay
            const setBodyHint = () => {
                document.body.setAttribute('data-drop-hint', dropHint());
                if (els.chatPane) els.chatPane.setAttribute('data-drop-hint', dropHint());
            };
            setBodyHint();
            if (window.I18n) {
                window.I18n.onChange(setBodyHint);
            }

            // window 级兜底: 防止用户拖偏导致浏览器导航
            // 检测 dataTransfer 含 Files 时显示 overlay
            let windowDragCounter = 0;
            window.addEventListener('dragenter', (e) => {
                const types = e.dataTransfer ? Array.from(e.dataTransfer.types || []) : [];
                dlog('window dragenter, types=', types, 'counter=', windowDragCounter);
                if (types.includes('Files')) {
                    windowDragCounter++;
                    document.body.classList.add('has-drag');
                }
            });
            window.addEventListener('dragover', (e) => {
                e.preventDefault();
                if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')) {
                    e.dataTransfer.dropEffect = 'copy';
                }
            });
            window.addEventListener('dragleave', (e) => {
                windowDragCounter--;
                if (windowDragCounter <= 0) {
                    windowDragCounter = 0;
                    document.body.classList.remove('has-drag');
                }
            });
            window.addEventListener('drop', (e) => {
                e.preventDefault();
                windowDragCounter = 0;
                document.body.classList.remove('has-drag');
            });

            // 整个 chat-pane 都接拖拽 (用户拖到消息区也能 work) — 转发到 dropZone 处理
            if (els.chatPane && els.chatPane !== dropZone) {
                els.chatPane.addEventListener('dragover', (e) => {
                    e.preventDefault();
                    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
                    dropZone.classList.add('dragover');
                });
                els.chatPane.addEventListener('dragleave', (e) => {
                    // 只有真正离开 chat-pane 才清状态 (避免子元素冒泡误清)
                    if (e.target === els.chatPane) {
                        dropZone.classList.remove('dragover');
                    }
                });
                els.chatPane.addEventListener('drop', (e) => {
                    e.preventDefault();
                    dropZone.classList.remove('dragover');
                    const files = e.dataTransfer && e.dataTransfer.files;
                    dlog('chatPane drop, files.length=', files ? files.length : 0);
                    if (!files || !files.length) return;
                    let added = 0, invalid = 0;
                    for (const f of files) {
                        if (f.type && f.type.startsWith('image/')) {
                            addAttachment(f);
                            added++;
                        } else {
                            invalid++;
                        }
                    }
                    if (added === 0 && invalid > 0) {
                        toast('error', t('toast.attach.invalid'), '');
                    } else if (added > 0) {
                        toast('success', t('toast.attach.added'),
                            added === 1 ? '1 file' : `${added} files`);
                    }
                });
            }

            // dropZone 内: 显示视觉反馈 + 接收文件
            let dragCounter = 0;  // 处理拖拽进入/离开子元素 (textarea) 时的 enter/leave 抖动
            dropZone.addEventListener('dragenter', (e) => {
                e.preventDefault();
                dragCounter++;
                dropZone.classList.add('dragover');
            });
            dropZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                if (e.dataTransfer) {
                    e.dataTransfer.dropEffect = 'copy';
                }
            });
            dropZone.addEventListener('dragleave', (e) => {
                dragCounter--;
                if (dragCounter <= 0) {
                    dragCounter = 0;
                    dropZone.classList.remove('dragover');
                }
            });
            dropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                e.stopPropagation();
                dragCounter = 0;
                dropZone.classList.remove('dragover');

                const files = e.dataTransfer && e.dataTransfer.files;
                dlog('dropZone drop, files.length=', files ? files.length : 0);
                if (!files || !files.length) {
                    return;
                }
                let added = 0;
                let invalid = 0;
                for (const f of files) {
                    if (f.type && f.type.startsWith('image/')) {
                        addAttachment(f);
                        added++;
                    } else {
                        invalid++;
                    }
                }
                if (added === 0 && invalid > 0) {
                    toast('error', t('toast.attach.invalid'), '');
                } else if (added > 0) {
                    toast('success', t('toast.attach.added'),
                        added === 1 ? '1 file' : `${added} files`);
                }
            });

            // textarea 自己也要单独处理 (它会拦 drop)
            // 在 textarea 上 capture 阶段先 preventDefault, 然后转发给 dropZone
            els.inputText.addEventListener('dragover', (e) => {
                e.preventDefault();
                if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
            });
            els.inputText.addEventListener('drop', (e) => {
                // textarea 默认会把 text/uri-list 插到光标位置, 阻止它
                if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                    e.preventDefault();
                    // 事件会冒泡到 dropZone, 由 dropZone 的 listener 处理实际 file 接收
                }
            });

            // 粘贴板里的图片也接 (Ctrl+V)
            els.inputText.addEventListener('paste', (e) => {
                const items = e.clipboardData && e.clipboardData.items;
                if (!items) return;
                for (const item of items) {
                    if (item.type && item.type.startsWith('image/')) {
                        const f = item.getAsFile();
                        if (f) {
                            e.preventDefault();
                            addAttachment(f);
                            toast('success', t('toast.attach.added'), f.name || 'pasted-image');
                        }
                    }
                }
            });
        }

        // tenant_id 失焦时同步到 settings (但不持久化, 持久化在 save 时)
        els.tenantInput.addEventListener('change', () => {
            state.settings.tenant = (els.tenantInput.value || '').trim() || 'default';
        });

        // 流式开关持久化
        if (els.streamToggle) {
            els.streamToggle.addEventListener('change', () => {
                state.settings.useStream = !!els.streamToggle.checked;
                try {
                    localStorage.setItem('anything_settings', JSON.stringify(state.settings));
                } catch (_) {}
            });
        }

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
        els.settingsBtn.addEventListener('click', () => {
            openDrawer('settings');
            // 打开时自动刷新模型列表
            loadModels();
        });
        $$('[data-close="settings"]').forEach(el =>
            el.addEventListener('click', () => closeDrawer('settings'))
        );

        // 模型管理事件
        if (els.modelsRefresh) {
            els.modelsRefresh.addEventListener('click', loadModels);
        }
        if (els.modelsAdd) {
            els.modelsAdd.addEventListener('click', () => openModelForm(null));
        }
        if (els.mfSubmit) {
            els.mfSubmit.addEventListener('click', submitModelForm);
        }
        if (els.mfCancel) {
            els.mfCancel.addEventListener('click', closeModelForm);
        }
        // 切类型时联动 adapter 默认选项
        if (els.mfType && els.mfAdapter) {
            els.mfType.addEventListener('change', () => {
                const t = els.mfType.value;
                const def = {
                    CHAT: 'OpenAIChatAdapter',
                    VECTOR: 'OpenAIVectorAdapter',
                    MULTIMODAL: 'OpenAIMultimodalAdapter',
                };
                els.mfAdapter.value = def[t] || els.mfAdapter.value;
            });
        }
        // 预览抽屉关闭按钮
        $$('[data-close="preview"]').forEach(el =>
            el.addEventListener('click', () => closeDrawer('preview'))
        );
        els.saveSettingsBtn.addEventListener('click', saveSettings);
        els.clearHistoryBtn.addEventListener('click', clearHistory);

        // Task GG (#67): 对话导出
        if (els.exportMdBtn) {
            els.exportMdBtn.addEventListener('click', () => exportConversation('md'));
        }
        if (els.exportJsonBtn) {
            els.exportJsonBtn.addEventListener('click', () => exportConversation('json'));
        }

        // 指标刷新
        els.metricsRefresh.addEventListener('click', loadMetrics);

        // 上传 (Task P: 多文件 + 队列)
        els.fileInput.addEventListener('change', () => {
            if (els.fileInput.files.length) uploadFiles(els.fileInput.files);
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
            if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
        });

        els.buildIndexBtn.addEventListener('click', triggerBuildIndex);

        // Task S: admin 面板刷新
        if (els.adminRefresh) {
            els.adminRefresh.addEventListener('click', refreshAdminStatus);
        }

        // Task JJ (#70): 已索引文档列表 + 删除
        if (els.docsRefreshBtn) {
            els.docsRefreshBtn.addEventListener('click', refreshDocsList);
        }
    }

    function updateComposerPlaceholder() {
        const key = `composer.placeholder.${state.mode || 'rag'}`;
        els.inputText.placeholder = t(key);
    }

    // ---------- 发送 ----------
    // ========== 附件管理 ==========
    function addAttachment(file) {
        const id = 'att_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
        const reader = new FileReader();
        reader.onload = (e) => {
            const att = {
                id,
                file,
                previewUrl: e.target.result,
                status: 'pending',
                storedPath: null,
            };
            state.pendingAttachments.push(att);
            renderAttachments();
        };
        reader.readAsDataURL(file);
    }

    function removeAttachment(id) {
        state.pendingAttachments = state.pendingAttachments.filter(a => a.id !== id);
        renderAttachments();
    }

    function renderAttachments() {
        if (!els.composerAttachments) return;
        if (!state.pendingAttachments.length) {
            els.composerAttachments.hidden = true;
            els.composerAttachments.innerHTML = '';
            return;
        }
        els.composerAttachments.hidden = false;
        els.composerAttachments.innerHTML = '';
        state.pendingAttachments.forEach(a => {
            const chip = document.createElement('div');
            chip.className = 'attachment-chip';
            chip.dataset.id = a.id;

            const img = document.createElement('img');
            img.src = a.previewUrl;
            img.alt = a.file.name;
            chip.appendChild(img);

            const name = document.createElement('span');
            name.className = 'att-name';
            name.textContent = a.file.name;
            name.title = `${a.file.name} (${(a.file.size / 1024).toFixed(1)} KB)`;
            chip.appendChild(name);

            const statusSpan = document.createElement('span');
            statusSpan.className = `att-status ${a.status}`;
            const statusMap = {
                pending: '⏳',
                uploading: t('composer.attach.uploading'),
                ready: '✓',
                failed: '×',
            };
            statusSpan.textContent = statusMap[a.status] || a.status;
            chip.appendChild(statusSpan);

            const rmBtn = document.createElement('button');
            rmBtn.className = 'att-remove';
            rmBtn.title = t('composer.attach.remove');
            rmBtn.textContent = '×';
            rmBtn.addEventListener('click', () => removeAttachment(a.id));
            chip.appendChild(rmBtn);

            els.composerAttachments.appendChild(chip);
        });
    }

    /** 把所有 pending 附件上传到后端, 拿到 stored_path 列表 */
    async function uploadAllAttachments() {
        const paths = [];
        for (const att of state.pendingAttachments) {
            if (att.status === 'ready' && att.storedPath) {
                paths.push(att.storedPath);
                continue;
            }
            att.status = 'uploading';
            renderAttachments();
            try {
                const { payload, status } = await ApiClient.uploadDocument(att.file);
                if (status === 200 && payload?.code === 'SUCCESS') {
                    att.storedPath = payload.data?.stored_path || '';
                    att.status = 'ready';
                    paths.push(att.storedPath);
                } else {
                    att.status = 'failed';
                    throw new Error(payload?.message || `HTTP ${status}`);
                }
            } catch (e) {
                att.status = 'failed';
                renderAttachments();
                throw new Error(`${att.file.name}: ${e.message}`);
            }
        }
        renderAttachments();
        return paths;
    }

    async function send() {
        if (state.sending) return;
        const text = els.inputText.value.trim();
        const hasAttachments = state.pendingAttachments.length > 0;
        if (!text && !hasAttachments) {
            toast('error', t('toast.input.empty'), t('toast.input.empty.body'));
            return;
        }
        const topK = Math.max(1, Math.min(50, Number(els.topkInput.value) || 5));
        let mode = state.mode;
        const tenant = (els.tenantInput.value || '').trim() || 'default';
        const useStream = !!(els.streamToggle && els.streamToggle.checked);

        // 有图片附件 -> 强制 agent 模式 (调 image_describe 工具)
        if (hasAttachments) {
            mode = 'agent';
        }

        const body = { type: mode, top_k: topK, tenant_id: tenant };

        // Task X (#58): plan mode 注入到 extra_params 让 Agent 走 plan_only 分支
        const planMode = !!(els.planToggle && els.planToggle.checked);
        if (planMode) {
            body.extra_params = body.extra_params || {};
            body.extra_params.plan_only = true;
        }

        // Task KKK (#97) / III (#95): Reflection 开关注入 extra_params.enable_reflection
        // Agent 拿到答案后会跑 critique → revise 二阶段优化
        const reflectMode = !!(els.reflectToggle && els.reflectToggle.checked);
        if (reflectMode) {
            body.extra_params = body.extra_params || {};
            body.extra_params.enable_reflection = true;
        }

        // 若有附件: 先上传拿 stored_path, 再把 path 拼进 task
        let finalText = text;
        if (hasAttachments) {
            state.sending = true;
            els.sendBtn.disabled = true;
            try {
                const paths = await uploadAllAttachments();
                const defaultPrompt = paths.length > 1
                    ? t('composer.attach.images_default_prompt')
                    : t('composer.attach.image_default_prompt');
                const userPart = text || defaultPrompt;
                const pathList = paths.map(p => `"${p}"`).join(', ');
                finalText = (
                    `请使用 image_describe 工具识别以下图片 (按顺序逐张处理), 然后回答用户的问题。\n` +
                    `图片路径: [${pathList}]\n` +
                    `用户问题: ${userPart}`
                );
            } catch (e) {
                state.sending = false;
                els.sendBtn.disabled = false;
                toast('error', t('toast.attach.upload_fail'), e.message);
                return;
            }
        }

        if (mode === 'rag') body.query = finalText;
        else body.task = finalText;

        // 用户消息显示原始文本 + 附件个数 (不暴露内部 prompt 拼接)
        let displayContent = text;
        if (hasAttachments) {
            const n = state.pendingAttachments.length;
            const defaultPrompt = n > 1
                ? t('composer.attach.images_default_prompt')
                : t('composer.attach.image_default_prompt');
            displayContent = (text || defaultPrompt) + `\n📎 ${n} 张图片`;
        }
        // 加用户消息
        addMessage({ role: 'user', mode, content: displayContent, ts: Date.now() });
        // 占位 assistant 消息
        const placeholderId = addMessage({
            role: 'assistant', mode, content: '', loading: true, ts: Date.now(),
        });

        state.sending = true;
        // Task GG (#67): 发送中 send 按钮变 Stop, 点击中止流式
        state.activePlaceholderId = placeholderId;
        els.sendBtn.classList.add('sending');
        const sendLabel = els.sendBtn.querySelector('.send-label');
        const sendIcon = els.sendBtn.querySelector('.send-icon');
        if (sendLabel) sendLabel.textContent = t('composer.stop');
        if (sendIcon) sendIcon.textContent = '⏹';
        els.inputText.value = '';
        els.inputText.focus();

        try {
            if (useStream) {
                await sendStream(body, placeholderId, { tenant, mode });
            } else {
                await sendOnce(body, placeholderId, { tenant, mode });
            }
        } finally {
            state.sending = false;
            state.activePlaceholderId = null;
            els.sendBtn.classList.remove('sending');
            if (sendLabel) sendLabel.textContent = t('composer.send');
            if (sendIcon) sendIcon.textContent = '↵';
        }

        // 发送完成后清空附件 (无论成功失败都清, 失败的已经在 UI 上标红)
        if (state.pendingAttachments.length) {
            state.pendingAttachments = [];
            renderAttachments();
        }
    }

    async function sendOnce(body, placeholderId, { tenant, mode }) {
        try {
            const { payload, traceId, costTime, status } = await ApiClient.invoke(body);

            // Task DD (#64): PLAN_PENDING → 弹审批 modal
            if (payload?.code === 'PLAN_PENDING' && payload?.data?.plan) {
                updateMessage(placeholderId, {
                    loading: false,
                    content: t('plan.modal.pending_hint'),
                    meta: {
                        code: 'PLAN_PENDING',
                        traceId: payload?.trace_id || traceId,
                        costTime: payload?.cost_time != null ? payload.cost_time.toFixed(3) : '',
                        tenant, mode,
                    },
                    streaming: false,
                });
                showPlanApprovalModal(payload.data.plan, {
                    onApprove: async () => {
                        closeDrawer('plan');
                        // 把 approve_plan=true 加到同一份 body 重发
                        const approveBody = { ...body };
                        approveBody.extra_params = { ...(approveBody.extra_params || {}), approve_plan: true };
                        updateMessage(placeholderId, {
                            loading: true,
                            content: '',
                            meta: { tenant, mode, code: 'EXECUTING' },
                        });
                        await sendOnce(approveBody, placeholderId, { tenant, mode });
                    },
                    onCancel: () => {
                        closeDrawer('plan');
                        toast('info', t('plan.modal.cancelled'), '');
                    },
                });
                return;  // 不走默认渲染
            }

            updateMessage(placeholderId, {
                loading: false,
                content: extractAnswer(payload),
                meta: {
                    code: payload?.code || `HTTP_${status}`,
                    traceId: payload?.trace_id || traceId,
                    costTime: costTime || (payload?.cost_time != null ? payload.cost_time.toFixed(3) : ''),
                    tenant,
                    mode,
                    // Task KKK (#97): 把 details.memory_hits / details.reflection 透到 UI
                    memoryHits: payload?.details?.memory_hits || null,
                    reflection: payload?.details?.reflection || null,
                },
                data: payload?.data,
                error: payload?.code && payload.code !== 'SUCCESS' ? payload : null,
            });
            renderRetrievedChunks(payload?.data?.retrieved_chunks || []);
            renderAgentSteps(payload?.data?.steps || []);
            if (payload?.code && payload.code !== 'SUCCESS') {
                toast('error', payload.code, payload.message || '');
                // AUTH_REQUIRED / TENANT_REQUIRED -> 主动提示用户去设置里填 key
                if (payload.code === 'AUTH_REQUIRED' || payload.code === 'TENANT_REQUIRED') {
                    setTimeout(() => {
                        toast('info', t('settings.title'), t('settings.apiKey'));
                        openDrawer('settings');
                    }, 300);
                }
            }
        } catch (err) {
            updateMessage(placeholderId, {
                loading: false,
                content: `${t('toast.network.error')}: ${err.message}`,
                meta: { code: 'NETWORK_ERROR', tenant, mode },
                error: { code: 'NETWORK_ERROR', message: err.message },
            });
            toast('error', t('toast.network.error'), String(err.message || err));
        }
    }

    function sendStream(body, placeholderId, { tenant, mode }) {
        return new Promise((resolve) => {
            let accumulated = '';
            let traceId = '';
            let metaCitations = [];
            let metaRetrieved = [];
            let metaSteps = [];
            // Task #48: Agent ReAct 思维链, 实时累积每步 trace
            const reactTrace = [];  // [{iteration, thought, action, observation}]
            let lastIter = null;

            // 1. 先把占位消息切到"流式渲染"态: 显示文本节点 + 光标
            updateMessage(placeholderId, {
                loading: false,
                content: '',
                meta: { tenant, mode, code: 'STREAMING' },
                streaming: true,
            });
            // 清空侧栏 ReAct trace, 准备实时填充
            if (mode === 'agent') {
                renderReactTrace([]);
            }

            activeStream = ApiClient.openStream({
                onStart: (m) => {
                    traceId = m.trace_id || '';
                },
                onChunk: (text) => {
                    accumulated += text;
                    appendStreamChunk(placeholderId, accumulated);
                },
                onMetadata: (m) => {
                    metaCitations = m.citations || [];
                    metaRetrieved = m.retrieved_chunks || [];
                    metaSteps = m.steps || [];
                    renderRetrievedChunks(metaRetrieved);
                    renderAgentSteps(metaSteps);
                },
                // Task #48 — ReAct 流式 trace
                onThought: (m) => {
                    const iter = m.iteration;
                    let row = reactTrace.find(r => r.iteration === iter);
                    if (!row) { row = { iteration: iter }; reactTrace.push(row); }
                    row.thought = m.text || '';
                    lastIter = iter;
                    renderReactTrace(reactTrace);
                },
                onAction: (m) => {
                    const iter = m.iteration;
                    let row = reactTrace.find(r => r.iteration === iter);
                    if (!row) { row = { iteration: iter }; reactTrace.push(row); }
                    row.action = { tool_name: m.tool_name, input: m.input };
                    renderReactTrace(reactTrace);
                },
                onObservation: (m) => {
                    const iter = m.iteration;
                    let row = reactTrace.find(r => r.iteration === iter);
                    if (!row) { row = { iteration: iter }; reactTrace.push(row); }
                    row.observation = {
                        tool_name: m.tool_name,
                        success: m.success,
                        output_summary: m.output_summary,
                    };
                    renderReactTrace(reactTrace);
                },
                // Task DD (#64): 流式 plan 事件 — 也弹审批 modal
                onPlan: (m) => {
                    const plan = m.plan || {};
                    // 流式 plan 之后 server 会接着发 done(code=PLAN_PENDING).
                    // modal 直接弹, Approve 时关闭流 + 重新非流式发 (approve_plan=true)
                    showPlanApprovalModal(plan, {
                        onApprove: async () => {
                            closeDrawer('plan');
                            try { if (activeStream) activeStream.close(); } catch (_) {}
                            const approveBody = { ...body };
                            approveBody.extra_params = { ...(approveBody.extra_params || {}), approve_plan: true };
                            updateMessage(placeholderId, {
                                loading: true, content: '',
                                meta: { tenant, mode, code: 'EXECUTING' },
                            });
                            // 重新走流式 (而非 sendOnce) 以保持 UX 一致
                            sendStream(approveBody, placeholderId, { tenant, mode })
                                .then(() => resolve());
                        },
                        onCancel: () => {
                            closeDrawer('plan');
                            try { if (activeStream) activeStream.close(); } catch (_) {}
                            updateMessage(placeholderId, {
                                loading: false,
                                content: t('plan.modal.cancelled'),
                                meta: { tenant, mode, code: 'PLAN_CANCELLED' },
                            });
                            toast('info', t('plan.modal.cancelled'), '');
                            resolve();
                        },
                    });
                },
                onDone: (m) => {
                    // 完成 -> 重新渲染 (走 markdown 完整路径)
                    updateMessage(placeholderId, {
                        loading: false,
                        streaming: false,
                        content: accumulated,
                        meta: {
                            code: 'SUCCESS',
                            traceId: m.trace_id || traceId,
                            costTime: m.cost_time != null ? m.cost_time.toFixed(3) : '',
                            tenant,
                            mode,
                        },
                        data: {
                            citations: metaCitations,
                            retrieved_chunks: metaRetrieved,
                            steps: metaSteps,
                        },
                    });
                    activeStream = null;
                    resolve();
                },
                onError: (m) => {
                    updateMessage(placeholderId, {
                        loading: false,
                        streaming: false,
                        content: accumulated
                            ? `${accumulated}\n\n[${m.code}] ${m.message || ''}`
                            : `[${m.code}] ${m.message || ''}`,
                        meta: { code: m.code, traceId: m.trace_id || traceId, tenant, mode },
                        error: m,
                    });
                    toast('error', m.code || 'WS_ERROR', m.message || '');
                    activeStream = null;
                    resolve();
                },
                onClose: () => {
                    if (activeStream) {
                        activeStream = null;
                        resolve();
                    }
                },
            });
            activeStream.send(body);
        });
    }

    /** 流式增量渲染: 直接 textContent 替换, 不过 markdown (性能 + 防中途解析出错) */
    function appendStreamChunk(msgId, fullContent) {
        const node = els.messages.querySelector(`[data-id="${msgId}"] .message-body`);
        if (!node) return;
        node.textContent = fullContent;
        // 加一个闪烁光标
        if (!node.querySelector('.stream-cursor')) {
            const cur = document.createElement('span');
            cur.className = 'stream-cursor';
            cur.textContent = '▍';
            node.appendChild(cur);
        }
        scrollToBottom();
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
            // Task KKK (#97): 长期记忆命中徽章 (FFF #92 注入的 details.memory_hits)
            if (msg.meta.memoryHits && msg.meta.memoryHits.length > 0) {
                const m = document.createElement('span');
                m.className = 'chip memory-chip';
                m.style.cssText = 'color:var(--accent);border-color:var(--accent);';
                m.textContent = '📚 ' + msg.meta.memoryHits.length;
                m.title = msg.meta.memoryHits
                    .map(h => `[${(h.reason || '').padEnd(20)}] ${h.content || ''}`)
                    .join('\n');
                meta.appendChild(m);
            }
            // Task KKK (#97) / NNN (#100): Reflection 反思徽章 (点击弹详情 modal)
            if (msg.meta.reflection) {
                const r = document.createElement('span');
                r.className = 'chip reflect-chip';
                r.style.cssText = 'color:#22c55e;border-color:#22c55e;cursor:pointer;';
                const refl = msg.meta.reflection;
                const qStr = refl.overall_quality != null
                    ? ` (q=${refl.overall_quality}→改进)` : '';
                r.textContent = '✨ 反思已应用' + qStr;
                r.title = '点击查看详细 critique';
                r.addEventListener('click', () => openReflectModal(refl));
                meta.appendChild(r);
            }
            if (msg.meta.traceId) {
                const tr = document.createElement('span');
                tr.className = 'chip';
                tr.title = '点击打开 timeline · Shift+点击复制 trace_id';
                tr.textContent = 'trace=' + String(msg.meta.traceId).slice(0, 8);
                tr.style.cursor = 'pointer';
                tr.addEventListener('click', (e) => {
                    if (e.shiftKey) {
                        navigator.clipboard?.writeText(msg.meta.traceId);
                        toast('info', t('toast.copied.trace'), msg.meta.traceId);
                    } else {
                        // Task RRR (#104): 默认点开 trace timeline modal
                        openTraceModal(msg);
                    }
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
        // Task #46: 清空对话时也重置 session_id, 让后端不再拿到上轮历史
        state.settings.sessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).slice(2, 10);
        try {
            localStorage.setItem('anything_settings', JSON.stringify(state.settings));
        } catch (_) {}
        els.sessionInput.value = state.settings.sessionId;
        ApiClient.configure(state.settings);
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
    /** Task #48: 实时 ReAct 思维链 trace 渲染. 流式过程中每收到一个 thought/action/observation
     * 都重渲染一次, 让用户看到 LLM "思考过程".
     * trace: [{iteration, thought?, action?, observation?}, ...]
     */
    function renderReactTrace(trace) {
        if (!els.stepList) return;
        els.stepList.innerHTML = '';
        els.stepsCount.textContent = trace.length;
        if (!trace.length) {
            els.stepsEmpty.style.display = 'block';
            return;
        }
        els.stepsEmpty.style.display = 'none';
        trace.forEach(row => {
            const li = document.createElement('li');
            li.className = 'step-item react-step';

            const header = document.createElement('div');
            header.className = 'step-header';
            const left = document.createElement('span');
            left.innerHTML = `🧠 Iter ${row.iteration}`;
            header.appendChild(left);
            li.appendChild(header);

            // thought (蓝色)
            if (row.thought) {
                const t = document.createElement('div');
                t.className = 'react-thought';
                t.textContent = '💭 ' + row.thought;
                li.appendChild(t);
            }

            // action (橙色)
            if (row.action) {
                const a = document.createElement('div');
                a.className = 'react-action';
                const inputStr = row.action.input
                    ? ' ' + JSON.stringify(row.action.input, null, 0).slice(0, 120)
                    : '';
                a.textContent = `🔧 ${row.action.tool_name || '?'}${inputStr}`;
                li.appendChild(a);
            }

            // observation (绿/红)
            if (row.observation) {
                const o = document.createElement('div');
                o.className = 'react-observation ' + (row.observation.success ? 'ok' : 'fail');
                const icon = row.observation.success ? '✓' : '✗';
                const summary = (row.observation.output_summary || '').slice(0, 200);
                o.textContent = `${icon} ${summary}`;
                li.appendChild(o);
            }

            // Task MMM (#99): web_search 工具结果渲染成 link card 栅格
            const toolName = (row.action && row.action.tool_name) || (row.action && row.action.tool);
            const obsData = row.observation_data;
            if (toolName === 'web_search' && obsData && Array.isArray(obsData.results)) {
                const cards = document.createElement('div');
                cards.className = 'web-search-cards';
                obsData.results.slice(0, 5).forEach(r => {
                    const card = document.createElement('a');
                    card.className = 'web-search-card';
                    card.href = r.url || '#';
                    card.target = '_blank';
                    card.rel = 'noopener noreferrer';
                    card.innerHTML = `
                        <div class="web-search-title">${escapeHtml(r.title || '(无标题)')}</div>
                        <div class="web-search-url">${escapeHtml((r.url || '').slice(0, 60))}</div>
                        <div class="web-search-snippet">${escapeHtml((r.snippet || '').slice(0, 160))}</div>
                    `;
                    cards.appendChild(card);
                });
                if (obsData.source) {
                    const src = document.createElement('div');
                    src.className = 'web-search-source';
                    src.textContent = `🌐 source: ${obsData.source}${obsData.fallback_reason ? ' (' + obsData.fallback_reason + ')' : ''}`;
                    cards.appendChild(src);
                }
                li.appendChild(cards);
            }

            // Task QQQ (#103): spawn_subagent 嵌套可视化
            // obsData 来自 spawn_subagent 工具返的 data: {answer, iterations_used,
            //   tool_results_summary, allowed_tools, role}
            if (toolName === 'spawn_subagent' && obsData && (obsData.answer != null || obsData.tool_results_summary)) {
                const subWrap = document.createElement('details');
                subWrap.className = 'subagent-card';
                subWrap.open = true;
                const role = obsData.role || '(默认)';
                const iters = obsData.iterations_used != null ? obsData.iterations_used : '?';
                const subTools = (obsData.tool_results_summary || []);
                const subToolsHtml = subTools.length
                    ? subTools.map(tr => `
                        <li class="subagent-tool ${tr.success ? 'ok' : 'fail'}">
                            ${tr.success ? '✓' : '✗'} <code>${escapeHtml(tr.tool_name || '?')}</code>
                        </li>`).join('')
                    : '<li class="empty-state">(无子工具调用)</li>';
                const allowedChips = (obsData.allowed_tools || []).slice(0, 8)
                    .map(tn => `<span class="subagent-allowed-tool">${escapeHtml(tn)}</span>`).join('');
                subWrap.innerHTML = `
                    <summary class="subagent-summary">
                        🤖 子 Agent <span class="subagent-role">role=${escapeHtml(role)}</span>
                        <span class="subagent-stats">${iters} 轮 · ${subTools.length} 工具调用</span>
                    </summary>
                    <div class="subagent-body">
                        <div class="subagent-section">
                            <strong>可用工具:</strong>
                            <div class="subagent-allowed">${allowedChips || '<span class="empty-state">(继承全部)</span>'}</div>
                        </div>
                        <div class="subagent-section">
                            <strong>子工具调用:</strong>
                            <ul class="subagent-tool-list">${subToolsHtml}</ul>
                        </div>
                        <div class="subagent-section">
                            <strong>子 Agent 最终答案:</strong>
                            <div class="subagent-answer">${escapeHtml((obsData.answer || '').slice(0, 500))}${(obsData.answer || '').length > 500 ? ' …' : ''}</div>
                        </div>
                    </div>
                `;
                li.appendChild(subWrap);
            }

            els.stepList.appendChild(li);
        });
        // 自动滚动到最新一步
        requestAnimationFrame(() => {
            els.stepList.scrollTop = els.stepList.scrollHeight;
        });
    }

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

    // ---------- Task GG (#67): Stop 中止 + 对话导出 ----------
    function stopSending() {
        // 关闭活跃的流, 把占位消息 mark 为 stopped
        try {
            if (activeStream && typeof activeStream.close === 'function') {
                activeStream.close();
            }
        } catch (_) {}
        activeStream = null;
        if (state.activePlaceholderId) {
            updateMessage(state.activePlaceholderId, {
                loading: false,
                meta: { code: 'STOPPED' },
                streaming: false,
            });
        }
        toast('info', t('composer.stop.toast'), '');
        // UI 复原由 send() 的 finally 处理 (await 解开后)
    }

    /**
     * Task GG (#67): 把当前 chat (state.messages) 导出成 Markdown 或 JSON.
     * fmt: 'md' | 'json'.
     */
    function exportConversation(fmt) {
        const messages = (state.messages || []).filter(m => m && m.content);
        if (!messages.length) {
            toast('warn', t('export.empty'), '');
            return;
        }
        let content, mime, ext;
        if (fmt === 'json') {
            content = JSON.stringify({
                exported_at: new Date().toISOString(),
                tenant: (els.tenantInput && els.tenantInput.value) || 'default',
                messages: messages.map(m => ({
                    role: m.role,
                    mode: m.mode,
                    content: m.content,
                    ts: m.ts,
                    meta: m.meta || null,
                })),
            }, null, 2);
            mime = 'application/json';
            ext = 'json';
        } else {
            // markdown
            const lines = [
                `# Anything 对话导出`,
                ``,
                `**导出时间**: ${new Date().toISOString()}`,
                `**Tenant**: ${(els.tenantInput && els.tenantInput.value) || 'default'}`,
                `**消息数**: ${messages.length}`,
                ``,
                `---`,
                ``,
            ];
            for (const m of messages) {
                const role = m.role === 'user' ? '👤 用户' : '🤖 助手';
                const modeBadge = m.mode ? ` \`[${m.mode}]\`` : '';
                const tsStr = m.ts ? new Date(m.ts).toLocaleString() : '';
                lines.push(`### ${role}${modeBadge} _${tsStr}_`);
                lines.push('');
                lines.push(m.content);
                if (m.meta && m.meta.traceId) {
                    lines.push('');
                    lines.push(`> trace_id: \`${m.meta.traceId}\``);
                }
                lines.push('');
                lines.push('---');
                lines.push('');
            }
            content = lines.join('\n');
            mime = 'text/markdown';
            ext = 'md';
        }
        const blob = new Blob([content], { type: `${mime};charset=utf-8` });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        a.href = url;
        a.download = `anything-chat-${stamp}.${ext}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        toast('success', t('export.done'), `${messages.length} ${t('export.messages')}`);
    }

    // ---------- 侧栏:Admin (Task S) ----------
    // ---------- Task JJ (#70): 已索引文档列表 + 删除 ----------
    async function refreshDocsList() {
        if (!els.docsList) return;
        els.docsList.innerHTML = `<li class="empty-state">${t('docs.loading')}</li>`;
        try {
            const { payload, status } = await ApiClient.listDocuments();
            if (status !== 200 || payload?.code !== 'SUCCESS') {
                els.docsList.innerHTML =
                    `<li class="empty-state error">× ${payload?.code || status} ${payload?.message || ''}</li>`;
                return;
            }
            const docs = (payload.data || {}).documents || [];
            if (els.docsCountHint) {
                els.docsCountHint.textContent = `${docs.length} ${t('docs.count_suffix')}`;
            }
            if (!docs.length) {
                els.docsList.innerHTML = `<li class="empty-state">${t('docs.empty')}</li>`;
                return;
            }
            els.docsList.innerHTML = '';
            for (const d of docs) {
                const li = document.createElement('li');
                li.className = 'doc-item';
                const name = (d.file_name || d.doc_id || '?').slice(0, 60);
                const size = d.content_length
                    ? `${(d.content_length / 1024).toFixed(1)} KB`
                    : '?';
                const created = d.created_time
                    ? new Date(d.created_time).toLocaleString()
                    : '';
                li.innerHTML = `
                    <div class="doc-info">
                        <div class="doc-name">${escapeHtml(name)}</div>
                        <div class="doc-meta">${escapeHtml(d.doc_id || '').slice(0, 30)} · ${size} · ${escapeHtml(created)}</div>
                    </div>
                    <button class="icon-btn doc-delete" title="${t('docs.delete')}">✕</button>
                `;
                const delBtn = li.querySelector('.doc-delete');
                delBtn.addEventListener('click', async () => {
                    if (!confirm(`${t('docs.confirm_delete')}\n${name}`)) return;
                    delBtn.disabled = true;
                    try {
                        const { payload: dp, status: dst } = await ApiClient.deleteDocument(d.doc_id);
                        if (dst === 200 && dp?.code === 'SUCCESS') {
                            toast('success', t('docs.deleted'), name);
                            li.remove();
                        } else {
                            toast('error', dp?.code || dst, dp?.message || '');
                            delBtn.disabled = false;
                        }
                    } catch (e) {
                        toast('error', t('docs.delete_fail'), e.message);
                        delBtn.disabled = false;
                    }
                });
                els.docsList.appendChild(li);
            }
        } catch (e) {
            els.docsList.innerHTML = `<li class="empty-state error">× ${e.message}</li>`;
        }
    }

    async function refreshAdminStatus() {
        if (!els.adminGrid) return;
        els.adminGrid.innerHTML = `<div class="empty-state">${t('admin.empty')}...</div>`;
        try {
            const { payload, status } = await ApiClient.getAdminStatus();
            if (status !== 200 || payload?.code !== 'SUCCESS') {
                els.adminGrid.innerHTML =
                    `<div class="empty-state error">× ${payload?.code || status} ${payload?.message || ''}</div>`;
                return;
            }
            renderAdminStatus(payload.data || {});
        } catch (e) {
            els.adminGrid.innerHTML = `<div class="empty-state error">× ${e.message}</div>`;
        }
    }

    function renderAdminStatus(d) {
        const html = [];
        const onOff = (b) => b ? `<span class="badge-on">${t('on')}</span>` : `<span class="badge-off">${t('off')}</span>`;

        // RAG 配置
        if (d.rag) {
            html.push(`<div class="admin-card">
                <h4>${t('admin.section.rag')}</h4>
                <dl>
                    <dt>${t('admin.kv.hybrid')}</dt><dd>${onOff(d.rag.enable_hybrid_search)}</dd>
                    <dt>${t('admin.kv.rerank')}</dt><dd>${onOff(d.rag.enable_rerank)}</dd>
                    <dt>${t('admin.kv.rewrite')}</dt><dd>${onOff(d.rag.enable_rewrite)}</dd>
                    <dt>${t('admin.kv.topk_retrieve')}</dt><dd>${d.rag.top_k_retrieve}</dd>
                    <dt>${t('admin.kv.topk_rerank')}</dt><dd>${d.rag.top_k_rerank}</dd>
                    <dt>${t('admin.kv.history_max_turns')}</dt><dd>${d.rag.history_max_turns}</dd>
                    <dt>${t('admin.kv.rrf_k')}</dt><dd>${d.rag.hybrid_rrf_k}</dd>
                </dl>
            </div>`);
        }
        // BM25
        if (d.bm25) {
            html.push(`<div class="admin-card">
                <h4>${t('admin.section.bm25')}</h4>
                <dl>
                    <dt>${t('admin.kv.bm25_size')}</dt><dd><strong>${d.bm25.size}</strong></dd>
                    <dt>${t('admin.kv.bm25_avg')}</dt><dd>${d.bm25.avg_doc_len}</dd>
                </dl>
            </div>`);
        }
        // Vector DB
        if (d.vector_db) {
            html.push(`<div class="admin-card">
                <h4>${t('admin.section.vector')}</h4>
                <dl>
                    <dt>${t('admin.kv.vec_ntotal')}</dt><dd><strong>${d.vector_db.ntotal}</strong></dd>
                </dl>
            </div>`);
        }
        // LLM Models
        if (d.llm_models) {
            const byType = d.llm_models.by_type || {};
            const breakdown = Object.entries(byType)
                .map(([k, v]) => `${k}: ${v}`).join(', ') || '-';
            html.push(`<div class="admin-card">
                <h4>${t('admin.section.llm')}</h4>
                <dl>
                    <dt>${t('admin.kv.llm_count')}</dt><dd><strong>${d.llm_models.count}</strong></dd>
                    <dt>类型分布</dt><dd>${escapeHtml(breakdown)}</dd>
                </dl>
            </div>`);
        }
        // Uploads
        if (d.uploads) {
            html.push(`<div class="admin-card">
                <h4>${t('admin.section.uploads')}</h4>
                <dl>
                    <dt>${t('admin.kv.upload_count')}</dt><dd><strong>${d.uploads.count}</strong></dd>
                    <dt>dir</dt><dd class="path">${escapeHtml(d.uploads.dir || '-')}</dd>
                </dl>
            </div>`);
        }
        // Security
        if (d.security) {
            const tenants = (d.security.registered_tenants || []).join(', ') || '-';
            html.push(`<div class="admin-card">
                <h4>${t('admin.section.security')}</h4>
                <dl>
                    <dt>${t('admin.kv.auth_enabled')}</dt><dd>${onOff(d.security.auth_enabled)}</dd>
                    <dt>auth_type</dt><dd>${escapeHtml(d.security.auth_type || '-')}</dd>
                    <dt>${t('admin.kv.tenants')}</dt><dd>${escapeHtml(tenants)}</dd>
                </dl>
            </div>`);
        }
        els.adminGrid.innerHTML = html.join('');
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, ch => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[ch]));
    }

    // ---------- 侧栏:上传 (Task P: 多文件队列) ----------
    /**
     * 多文件按顺序上传 (避免并发触发 embedding GPU/CPU 抢占).
     * 每个文件状态: pending -> uploading -> indexing -> done / error
     * 前端把进度逐项渲染到 #upload-queue.
     */
    async function uploadFiles(fileList) {
        const files = Array.from(fileList || []).filter(f => f && f.size != null);
        if (!files.length) return;
        // 初始化队列项
        els.uploadQueue.innerHTML = '';
        const rows = files.map((f, i) => _renderQueueRow(i, f, 'pending'));
        let okCount = 0;
        let failCount = 0;
        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            _renderQueueRow(i, file, 'uploading', null, rows[i]);
            try {
                const { payload, status } = await ApiClient.uploadDocument(file);
                if (status === 200 && payload?.code === 'SUCCESS') {
                    const d = payload.data || {};
                    if (d.indexed && d.index_summary) {
                        const s = d.index_summary;
                        _renderQueueRow(i, file, 'done',
                            `${s.total_chunks} chunks · ${s.total_vectors} vectors`, rows[i]);
                    } else if (d.index_error) {
                        _renderQueueRow(i, file, 'error', `索引失败: ${d.index_error}`, rows[i]);
                        failCount++;
                        continue;
                    } else {
                        _renderQueueRow(i, file, 'done', '仅落盘, 未索引', rows[i]);
                    }
                    okCount++;
                } else {
                    _renderQueueRow(i, file, 'error',
                        `${payload?.code || status} ${payload?.message || ''}`, rows[i]);
                    failCount++;
                }
            } catch (e) {
                _renderQueueRow(i, file, 'error', e.message, rows[i]);
                failCount++;
            }
        }
        // 汇总
        els.uploadResult.className = failCount === 0 ? 'upload-result success' : 'upload-result';
        els.uploadResult.textContent = `批量上传完成: ${okCount} 成功 / ${failCount} 失败 (共 ${files.length})`;
        if (failCount === 0) {
            toast('success', t('toast.upload.success'), `${okCount} files`);
        } else {
            toast('error', t('toast.upload.fail'), `${failCount} of ${files.length} failed`);
        }
    }

    /**
     * 单文件队列行渲染 / 更新. state ∈ pending/uploading/indexing/done/error.
     * existingRow 传入时复用 DOM 节点 (更新 state 类 + 副文本); 否则新建并 append.
     */
    function _renderQueueRow(index, file, state, detail, existingRow) {
        const ICONS = { pending: '⋯', uploading: '⬆', indexing: '⚙', done: '✓', error: '✗' };
        let row = existingRow;
        if (!row) {
            row = document.createElement('li');
            row.className = 'queue-item';
            row.innerHTML = `
                <span class="queue-icon"></span>
                <span class="queue-name"></span>
                <span class="queue-size"></span>
                <span class="queue-detail"></span>
            `;
            els.uploadQueue.appendChild(row);
        }
        row.dataset.state = state;
        row.querySelector('.queue-icon').textContent = ICONS[state] || '?';
        row.querySelector('.queue-name').textContent = file.name;
        row.querySelector('.queue-size').textContent =
            `${(file.size / 1024).toFixed(1)} KB`;
        row.querySelector('.queue-detail').textContent = detail || '';
        return row;
    }

    // 老 uploadFile 兼容入口 (从拖拽进消息区那条路径还在用)
    async function uploadFile(file) {
        return uploadFiles([file]);
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

    // ---------- 模型管理 ----------
    async function loadModels() {
        if (!els.modelsTbody) return;
        els.modelsTbody.innerHTML = `<tr><td colspan="4" class="empty-state">${t('preview.loading')}</td></tr>`;
        try {
            const { payload, status } = await ApiClient.listModels();
            if (status === 501 || payload?.code === 'SERVICE_UNAVAILABLE') {
                els.modelsTbody.innerHTML = `<tr><td colspan="4" class="empty-state">${
                    Markdown.escapeHtml(payload?.message || '/config/models 不可用')
                }</td></tr>`;
                return;
            }
            if (status !== 200 || payload?.code !== 'SUCCESS') {
                els.modelsTbody.innerHTML = `<tr><td colspan="4" class="empty-state">${
                    payload?.code || ('HTTP_' + status)
                }: ${Markdown.escapeHtml(payload?.message || '')}</td></tr>`;
                return;
            }
            const models = (payload.data && payload.data.models) || [];
            renderModelsTable(models);
        } catch (e) {
            els.modelsTbody.innerHTML = `<tr><td colspan="4" class="empty-state">${Markdown.escapeHtml(e.message)}</td></tr>`;
        }
    }

    function renderModelsTable(models) {
        if (!models.length) {
            els.modelsTbody.innerHTML = `<tr><td colspan="4" class="empty-state">${t('models.empty.list')}</td></tr>`;
            return;
        }
        els.modelsTbody.innerHTML = '';
        models.forEach(m => {
            const tr = document.createElement('tr');

            const nameCell = document.createElement('td');
            nameCell.className = 'model-name-cell';
            nameCell.innerHTML = `<span class="model-name">${Markdown.escapeHtml(m.name)}</span>` +
                (m.is_default ? ` <span class="model-default-badge">${t('models.action.default')}</span>` : '');
            tr.appendChild(nameCell);

            const typeCell = document.createElement('td');
            typeCell.innerHTML = `<span class="model-type-chip">${Markdown.escapeHtml(m.request_type)}</span>`;
            tr.appendChild(typeCell);

            const keyCell = document.createElement('td');
            keyCell.className = 'model-key-cell';
            keyCell.textContent = m.api_key || '—';
            if (!m.configured) {
                keyCell.style.color = 'var(--warning)';
                keyCell.title = '未配置 (走 mock 实现)';
            }
            tr.appendChild(keyCell);

            const actionCell = document.createElement('td');
            actionCell.className = 'model-action-cell';
            const editBtn = document.createElement('button');
            editBtn.className = 'small-btn ghost';
            editBtn.textContent = t('models.action.edit');
            editBtn.addEventListener('click', () => openModelForm(m));
            actionCell.appendChild(editBtn);

            if (!m.is_default) {
                const defaultBtn = document.createElement('button');
                defaultBtn.className = 'small-btn ghost';
                defaultBtn.textContent = t('models.action.setDefault');
                defaultBtn.addEventListener('click', () => setDefault(m));
                actionCell.appendChild(defaultBtn);
            }

            const delBtn = document.createElement('button');
            delBtn.className = 'small-btn ghost danger';
            delBtn.textContent = t('models.action.delete');
            delBtn.addEventListener('click', () => deleteModel(m));
            actionCell.appendChild(delBtn);

            tr.appendChild(actionCell);
            els.modelsTbody.appendChild(tr);
        });
    }

    function openModelForm(model) {
        if (!els.modelForm) return;
        els.modelForm.hidden = false;
        if (model) {
            els.mfName.value = model.name || '';
            els.mfName.readOnly = true;  // 编辑模式锁名字
            els.mfType.value = model.request_type || 'CHAT';
            els.mfAdapter.value = model.adapter_class || 'OpenAIChatAdapter';
            els.mfApiBase.value = model.api_base || '';
            els.mfApiKey.value = '';  // 不回填 (脱敏过), 留空表示不变
            els.mfApiKey.placeholder = '留空 = 保留原 key';
            els.mfDefault.checked = !!model.is_default;
        } else {
            els.mfName.value = '';
            els.mfName.readOnly = false;
            els.mfType.value = 'CHAT';
            els.mfAdapter.value = 'OpenAIChatAdapter';
            els.mfApiBase.value = '';
            els.mfApiKey.value = '';
            els.mfApiKey.placeholder = 'sk-...';
            els.mfDefault.checked = false;
        }
        // 滚到表单
        requestAnimationFrame(() => els.modelForm.scrollIntoView({ behavior: 'smooth' }));
    }

    function closeModelForm() {
        if (els.modelForm) els.modelForm.hidden = true;
    }

    async function submitModelForm() {
        const name = (els.mfName.value || '').trim();
        if (!name) {
            toast('error', t('models.error'), 'name 不能为空');
            return;
        }
        const payload = {
            name,
            request_type: els.mfType.value,
            adapter_class: els.mfAdapter.value,
            api_base: (els.mfApiBase.value || '').trim(),
            set_as_default: els.mfDefault.checked,
        };
        const keyInput = (els.mfApiKey.value || '').trim();
        if (keyInput) payload.api_key = keyInput;

        try {
            const { payload: resp, status } = await ApiClient.registerModel(payload);
            if (status === 200 && resp?.code === 'SUCCESS') {
                toast('success', t('models.toast.saved'), name);
                closeModelForm();
                loadModels();
            } else {
                toast('error', resp?.code || ('HTTP_' + status), resp?.message || '');
            }
        } catch (e) {
            toast('error', t('models.error'), e.message);
        }
    }

    async function setDefault(model) {
        try {
            const { payload, status } = await ApiClient.setDefaultModel(model.name, model.request_type);
            if (status === 200 && payload?.code === 'SUCCESS') {
                toast('success', t('models.toast.defaultSet'), `${model.request_type}=${model.name}`);
                loadModels();
            } else {
                toast('error', payload?.code || ('HTTP_' + status), payload?.message || '');
            }
        } catch (e) {
            toast('error', t('models.error'), e.message);
        }
    }

    async function deleteModel(model) {
        if (!confirm(t('models.confirm.delete', { name: model.name }))) return;
        try {
            const { payload, status } = await ApiClient.deleteModel(model.name);
            if ((status === 200 || status === 404) && payload?.code) {
                toast('success', t('models.toast.deleted'), model.name);
                loadModels();
            } else {
                toast('error', payload?.code || ('HTTP_' + status), payload?.message || '');
            }
        } catch (e) {
            toast('error', t('models.error'), e.message);
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

    // ---------- Plan Approval Modal (Task DD #64) ----------
    /**
     * 显示 plan 审批 modal. 用户点 Approve 触发 onApprove(), Cancel 触发 onCancel().
     * plan: { thought, action?: {tool, input}, final_answer?, summary? }
     */
    function showPlanApprovalModal(plan, { onApprove, onCancel }) {
        if (!els.planDrawer) {
            // DOM 元素缺失 → fallback to auto-approve (向后兼容旧前端)
            if (onApprove) onApprove();
            return;
        }
        // 渲染 thought
        els.planThought.textContent = plan.thought || '(无 thought)';

        // 渲染 action 段
        if (plan.action && plan.action.tool) {
            els.planActionSection.hidden = false;
            els.planFinalSection.hidden = true;
            els.planToolName.textContent = plan.action.tool;
            try {
                els.planToolInput.textContent = JSON.stringify(plan.action.input || {}, null, 2);
            } catch (e) {
                els.planToolInput.textContent = String(plan.action.input || '');
            }
        } else if (plan.final_answer) {
            els.planActionSection.hidden = true;
            els.planFinalSection.hidden = false;
            els.planFinalAnswer.textContent = plan.final_answer;
        } else {
            els.planActionSection.hidden = true;
            els.planFinalSection.hidden = true;
        }

        // 绑定一次性 button 处理 (避免重复绑定)
        const handleApprove = () => {
            cleanup();
            if (onApprove) onApprove();
        };
        const handleCancel = () => {
            cleanup();
            if (onCancel) onCancel();
        };
        const handleBackdrop = (e) => {
            if (e.target && e.target.matches('[data-close="plan"]')) handleCancel();
        };
        const cleanup = () => {
            els.planApproveBtn.removeEventListener('click', handleApprove);
            els.planCancelBtn.removeEventListener('click', handleCancel);
            els.planDrawer.removeEventListener('click', handleBackdrop);
        };
        els.planApproveBtn.addEventListener('click', handleApprove);
        els.planCancelBtn.addEventListener('click', handleCancel);
        els.planDrawer.addEventListener('click', handleBackdrop);

        openDrawer('plan');
    }

    // ---------- Task NNN (#100): Reflection 详情 modal ----------
    /**
     * 弹出 Reflection meta 详情, 显示 critique issues / missing_info / 修订耗时.
     * meta 结构 (III #95 产出):
     *   {
     *     critique: {issues:[], missing_info:[], overall_quality:1-5, should_revise:bool},
     *     n_issues, overall_quality, llm_calls, cost_ms,
     *     skipped?: 'no_llm' | 'critique_llm_failed' | 'critique_json_parse_failed' |
     *               'skipped_revise' | 'revise_llm_failed' | 'revise_empty',
     *     err?: str,
     *   }
     */
    function openReflectModal(refl) {
        if (!els.reflectDrawer) return;
        // chips
        els.reflectQuality.textContent = `quality=${refl.overall_quality != null ? refl.overall_quality : '-'}`;
        els.reflectLlmCalls.textContent = `LLM 调用 ${refl.llm_calls != null ? refl.llm_calls : '-'}`;
        els.reflectCostMs.textContent = `${refl.cost_ms != null ? refl.cost_ms : '-'} ms`;

        const critique = refl.critique || {};
        // issues
        const issues = (critique.issues || []).filter(Boolean);
        if (issues.length) {
            els.reflectIssues.innerHTML = issues.map(
                i => `<li>${escapeHtml(String(i))}</li>`
            ).join('');
        } else {
            els.reflectIssues.innerHTML = '<li class="empty-state">(无)</li>';
        }
        // missing_info
        const missing = (critique.missing_info || []).filter(Boolean);
        if (missing.length) {
            els.reflectMissing.innerHTML = missing.map(
                m => `<li>${escapeHtml(String(m))}</li>`
            ).join('');
        } else {
            els.reflectMissing.innerHTML = '<li class="empty-state">(无)</li>';
        }
        // skipped 原因
        if (refl.skipped) {
            els.reflectSkipSection.hidden = false;
            els.reflectSkipReason.textContent = `${refl.skipped}${refl.err ? ' — ' + refl.err : ''}`;
        } else {
            els.reflectSkipSection.hidden = true;
        }
        // raw JSON
        try {
            els.reflectRaw.textContent = JSON.stringify(refl, null, 2);
        } catch (_) {
            els.reflectRaw.textContent = String(refl);
        }

        const handleBackdrop = (e) => {
            if (e.target && e.target.matches('[data-close="reflect"]')) {
                els.reflectDrawer.removeEventListener('click', handleBackdrop);
                closeDrawer('reflect');
            }
        };
        els.reflectDrawer.addEventListener('click', handleBackdrop);
        openDrawer('reflect');
    }

    // ---------- Task RRR (#104): Trace timeline modal ----------
    /**
     * 打开 trace timeline 详情. msg.data 里有 cost_time 总耗时.
     * 由于 OTel 完整 span 列表前端没拿到, 我们从可见的 phase 推断时序:
     *   - retrieve (RAG): 检索 chunks 数 + 简短
     *   - parse_task / aggregate (Agent): 看 steps / react_history
     *   - reflection: msg.meta.reflection.cost_ms
     *   - memory inject / extract: 推断
     * 没有精确 ms 时用 "?" 占位, 但用户至少能看到一次请求都做了什么.
     */
    function openTraceModal(msg) {
        if (!els.traceDrawer) return;
        const meta = msg.meta || {};
        const data = msg.data || {};
        const refl = meta.reflection || null;
        els.traceIdChip.textContent = 'trace=' + String(meta.traceId || '-').slice(0, 24);
        els.traceTotalChip.textContent = meta.costTime ? `${meta.costTime} s 总耗时` : '- ms';
        els.traceModeChip.textContent = (msg.mode || '?').toUpperCase();

        // Timeline 推断 (没真实 span 拿到时的兜底)
        const phases = [];
        // 1. Mode entrance
        phases.push({
            name: '🚪 入口',
            detail: `mode=${msg.mode || '-'} · session=${(meta.sessionId || '-').slice(0, 12)}`,
            cost_ms: null,
        });
        // 2. Memory inject (KKK)
        if (meta.memoryHits && meta.memoryHits.length) {
            phases.push({
                name: '📚 长期记忆注入',
                detail: `${meta.memoryHits.length} 条 fact 命中`,
                cost_ms: null,
            });
        }
        // 3. RAG retrieve
        const chunks = data.retrieved_chunks || [];
        if (chunks.length) {
            phases.push({
                name: '🔍 RAG 检索',
                detail: `${chunks.length} chunks · scores ${chunks.slice(0, 3).map(c => (c.score || 0).toFixed(2)).join(', ')}`,
                cost_ms: null,
            });
        }
        // 4. Agent ReAct iterations
        const reactHistory = data.react_history || [];
        if (reactHistory.length) {
            phases.push({
                name: '🧠 Agent ReAct',
                detail: `${reactHistory.length} 轮 · ${(data.tool_results_summary || []).length} 工具调用`,
                cost_ms: null,
            });
        }
        // 5. Reflection
        if (refl) {
            phases.push({
                name: '✨ Reflection 反思',
                detail: `${refl.llm_calls || '?'} LLM 调用 · quality=${refl.overall_quality != null ? refl.overall_quality : '?'}`,
                cost_ms: refl.cost_ms,
            });
        }
        // 6. Total
        phases.push({
            name: '✅ 响应聚合',
            detail: `code=${meta.code} · trace=${(meta.traceId || '-').slice(0, 12)}`,
            cost_ms: null,
        });

        if (phases.length) {
            els.traceTimeline.innerHTML = phases.map(p => `
                <li class="trace-phase">
                    <div class="trace-phase-name">${p.name}</div>
                    <div class="trace-phase-detail">${escapeHtml(p.detail)}</div>
                    ${p.cost_ms != null ? `<div class="trace-phase-cost">${p.cost_ms} ms</div>` : ''}
                </li>
            `).join('');
        } else {
            els.traceTimeline.innerHTML = '<li class="empty-state">(无 trace 数据)</li>';
        }

        // Raw response
        try {
            const dump = { mode: msg.mode, content: (msg.content || '').slice(0, 500), meta, data };
            els.traceRaw.textContent = JSON.stringify(dump, null, 2);
        } catch (_) {
            els.traceRaw.textContent = '(无法序列化)';
        }

        const handleBackdrop = (e) => {
            if (e.target && e.target.matches('[data-close="trace"]')) {
                els.traceDrawer.removeEventListener('click', handleBackdrop);
                closeDrawer('trace');
            }
        };
        els.traceDrawer.addEventListener('click', handleBackdrop);
        openDrawer('trace');
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
