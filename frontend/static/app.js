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
        toastContainer: $('toast-container'),
        langBtn: $('lang-btn'),
        sidebarToggle: $('sidebar-toggle'),
        sidebar: document.querySelector('.sidebar'),
        chatPane: document.querySelector('.chat-pane'),
        previewDrawer: $('preview-drawer'),
        previewTitle: $('preview-title'),
        previewMeta: $('preview-meta'),
        previewText: $('preview-text'),
        streamToggle: $('stream-toggle'),
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
            });
        });

        // 发送
        els.sendBtn.addEventListener('click', send);
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
        els.sendBtn.disabled = true;
        els.inputText.value = '';
        els.inputText.focus();

        if (useStream) {
            await sendStream(body, placeholderId, { tenant, mode });
        } else {
            await sendOnce(body, placeholderId, { tenant, mode });
        }
        state.sending = false;
        els.sendBtn.disabled = false;

        // 发送完成后清空附件 (无论成功失败都清, 失败的已经在 UI 上标红)
        if (state.pendingAttachments.length) {
            state.pendingAttachments = [];
            renderAttachments();
        }
    }

    async function sendOnce(body, placeholderId, { tenant, mode }) {
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

    // ---------- 侧栏:Admin (Task S) ----------
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
