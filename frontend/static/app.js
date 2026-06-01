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
        // Task XXXX (#112): header 只剩 mini chip; settings drawer 有真正可编辑 input
        tenantChipMini: $('tenant-chip-mini'),
        tenantValue: $('tenant-value'),
        tenantInputDrawer: $('tenant-input-drawer'),
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
        // Task SSS (#105) + VVVV (#108): 多会话切换 (左侧 sidebar)
        sessionsSelect: $('sessions-select'),       // 隐藏兼容
        sessionsRefreshBtn: $('sessions-refresh-btn'),
        sessionsNewBtn: $('sessions-new-btn'),
        sessionsDeleteBtn: $('sessions-delete-btn'),  // 隐藏兼容
        sessionList: $('session-list'),              // VVVV: 新 <ul> 列表渲染
        sessionsCount: $('sessions-count'),          // VVVV: 数量徽章
        sessionsSidebar: $('sessions-sidebar'),      // VVVV: 整个 aside
        sessionsSidebarToggle: $('sessions-sidebar-toggle'),  // VVVV: 移动端开关
        sessionsSearchInput: $('sessions-search-input'),  // YYYY-H: 搜索框
    };

    // 当前正在跑的 WebSocket 句柄 (用于"停止"按钮中断)
    let activeStream = null;

    // i18n shortcut
    const t = (key, params) => (window.I18n ? window.I18n.t(key, params) : key);

    // ---------- 状态 ----------
    const state = {
        mode: 'rag',              // rag / agent / hybrid
        history: [],
        sending: false,           // 兼容旧引用; 实际并发逻辑用 inflight Map
        // Task RRRR (#135): per-session 并发跟踪 — sid → { placeholderId, startedAt }
        inflight: new Map(),
        // 待发送的图片附件 [{id, file, previewUrl, status: 'pending'|'uploading'|'ready'|'failed', storedPath?}]
        pendingAttachments: [],
        settings: {
            baseUrl: '',
            // dev 默认 key — 跟 .env.example 里 API_KEY_1 一致, 本地起服务即可用.
            // 生产部署须 unset 这条默认, 不影响 localStorage 已存的真 key.
            apiKey: 'dev_api_key_1_change_in_prod',
            sessionId: '',
            tenant: 'default',
            useStream: false,
            theme: 'dark',  // Task NNNN (#131): dark/light/auto
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

        // Task ZZZZ (#117): 启动后用服务端 session state 覆盖 localStorage 缓存,
        // 避免上次 sessionA 留下来的 history 误显示在切了 sessionB 之后. 服务端是单一真相源.
        setTimeout(() => {
            const sid = state.settings.sessionId;
            if (sid && typeof _loadSessionHistory === 'function') {
                _loadSessionHistory(sid).catch(() => {});
            }
        }, 400);

        // Task FFFF (#123): 启动后拉一次 /agent/tools, 让 "工具调用" tab 空态时
        // 显已注册的全部 Agent 工具 (用户能直观看到能干啥).
        setTimeout(_loadAgentTools, 600);

        // Task PM-7-5: 检测 ?share=<sid> → 进入 read-only 模式
        const shareParam = new URLSearchParams(location.search).get('share');
        if (shareParam) {
            _enterShareReadOnlyMode(shareParam);
            return;  // 早出, 跳过 normal init 流程
        }

        // Task PM-3: 启动后检测有没有可用 LLM, 没有就弹首启向导
        setTimeout(_maybeShowFirstRunWizard, 800);
        _bindFirstRunWizard();

        // Task PM-5: KB 面板初始化
        _bindKbPanel();
        setTimeout(_loadKbList, 700);

        // Task NNNN (#131): 主题切换按钮
        document.querySelectorAll('.theme-opt').forEach(btn => {
            btn.addEventListener('click', () => {
                const theme = btn.dataset.theme;
                if (!theme) return;
                state.settings.theme = theme;
                try { localStorage.setItem('anything_settings', JSON.stringify(state.settings)); } catch (_) {}
                _applyTheme(theme);
                toast('info', '主题已切换', theme);
            });
        });
        // 系统主题变化时, auto 模式跟随
        if (window.matchMedia) {
            const mq = window.matchMedia('(prefers-color-scheme: light)');
            mq.addEventListener?.('change', () => {
                if (state.settings.theme === 'auto') _applyTheme('auto');
            });
        }

        // Task TTTT-2 (#139) + XXXX-9 (#156): workflow 模板按钮 + 列表 + IDB 初始化
        _initWorkflowsStore().catch((e) => console.warn('[workflows] IDB init failed:', e));
        document.getElementById('workflow-save-btn')?.addEventListener('click', _saveCurrentAsWorkflow);
        document.getElementById('session-sysprompt-btn')?.addEventListener('click', _showSessionSysPromptModal);
        document.getElementById('workflow-list-btn')?.addEventListener('click', () => {
            _refreshWorkflowsList();
            openDrawer('workflows');
        });
        document.querySelectorAll('[data-close="workflows"]').forEach(el =>
            el.addEventListener('click', () => closeDrawer('workflows'))
        );
        document.getElementById('workflows-list')?.addEventListener('click', (e) => {
            const del = e.target.closest('.workflow-delete');
            if (del) {
                e.stopPropagation();
                const idx = parseInt(del.dataset.idx, 10);
                if (isNaN(idx)) return;
                if (!confirm('删除此模板?')) return;
                const list = _loadWorkflows();
                list.splice(idx, 1);
                _saveWorkflows(list);
                _refreshWorkflowsList();
                return;
            }
            const item = e.target.closest('.workflow-item');
            if (!item) return;
            const idx = parseInt(item.dataset.idx, 10);
            if (isNaN(idx)) return;
            const list = _loadWorkflows();
            const w = list[idx];
            if (!w) return;
            // 切 mode + 填输入框
            if (w.mode) {
                const tab = document.querySelector(`.mode-tab[data-mode="${w.mode}"]`);
                if (tab) tab.click();
            }
            els.inputText.value = w.text || '';
            els.inputText.focus();
            els.inputText.dispatchEvent(new Event('input', { bubbles: true }));
            closeDrawer('workflows');
            toast('info', '模板已加载', w.name);
        });
        // 启动时刷一次 badge count
        setTimeout(() => {
            const cnt = document.getElementById('workflow-count');
            if (cnt) cnt.textContent = String(_loadWorkflows().length);
        }, 500);

        // Task LLLL (#129): shortcuts modal — button 点 / ? 键 / 全局快捷键
        const sbtn = document.getElementById('shortcuts-btn');
        if (sbtn) sbtn.addEventListener('click', () => openDrawer('shortcuts'));
        document.querySelectorAll('[data-close="shortcuts"]').forEach(el =>
            el.addEventListener('click', () => closeDrawer('shortcuts'))
        );
        document.addEventListener('keydown', (e) => {
            // 输入框/textarea 里输 ? 不触发 (保证用户正常打字)
            const inField = e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable;
            // ?  → shortcuts
            if (!inField && e.key === '?' && !e.ctrlKey && !e.metaKey) {
                e.preventDefault();
                openDrawer('shortcuts');
                return;
            }
            // Ctrl+N → 新建会话
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'n') {
                e.preventDefault();
                if (typeof createNewSession === 'function') createNewSession();
                return;
            }
            // Ctrl+/ → 聚焦 session 搜索
            if ((e.ctrlKey || e.metaKey) && e.key === '/') {
                e.preventDefault();
                els.sessionsSearchInput?.focus();
                return;
            }
            // Ctrl+, → 设置
            if ((e.ctrlKey || e.metaKey) && e.key === ',') {
                e.preventDefault();
                openDrawer('settings');
                return;
            }
            // Esc → 关任意打开的 drawer
            if (e.key === 'Escape') {
                ['shortcuts', 'settings', 'preview', 'plan', 'reflect', 'trace'].forEach(closeDrawer);
            }
        });

        // Task GGGG (#124): 欢迎屏 example prompt 卡片 click → 切 mode + 填输入框
        // Task HHHH (#125): Agent 工具卡 (.agent-tool-card.clickable) → 切 Agent + 填 example
        document.addEventListener('click', (e) => {
            const wcard = e.target.closest('.welcome-example');
            if (wcard) {
                const mode = wcard.dataset.mode;
                const prompt = wcard.dataset.prompt;
                if (mode) {
                    const tab = document.querySelector(`.mode-tab[data-mode="${mode}"]`);
                    if (tab) tab.click();
                }
                if (prompt && els.inputText) {
                    els.inputText.value = prompt;
                    els.inputText.focus();
                    els.inputText.dispatchEvent(new Event('input', { bubbles: true }));
                }
                return;
            }
            const tcard = e.target.closest('.agent-tool-card.clickable');
            if (tcard) {
                const example = tcard.dataset.example;
                if (!example) return;
                // 切 Agent 模式 (工具只在 Agent 下生效)
                const tab = document.querySelector('.mode-tab[data-mode="agent"]');
                if (tab) tab.click();
                if (els.inputText) {
                    els.inputText.value = example;
                    els.inputText.focus();
                    els.inputText.dispatchEvent(new Event('input', { bubbles: true }));
                }
                toast('info', '已预填示例 task', tcard.dataset.tool || '');
                return;
            }
        });

        // Task KKK (#97): 长期记忆面板 — 工厂注入 deps + bindEvents
        if (window.AnythingApp && window.AnythingApp.memoryPanel) {
            const memPanel = window.AnythingApp.memoryPanel({ els, t, toast, escapeHtml });
            window.AnythingApp._memoryPanel = memPanel;
            memPanel.bindEvents();
        }

        // Task UUUU (#107): admin/docs 面板 — 工厂注入. 替换 app.js 里残留的
        // 旧 refreshAdminStatus/renderAdminStatus(只有 5 张卡); 新版本在
        // modules/admin-panel.js 里, 包含 LLL/OOO/PPP 的 7 张新卡.
        if (window.AnythingApp && window.AnythingApp.adminPanel) {
            const adminPanel = window.AnythingApp.adminPanel({ els, t, toast, escapeHtml });
            window.AnythingApp._adminPanel = adminPanel;
        }

        // Task SSS (#105) + VVVV (#108): 多会话切换 bindEvents (左侧 sidebar)
        if (els.sessionsRefreshBtn) els.sessionsRefreshBtn.addEventListener('click', refreshSessions);
        if (els.sessionsNewBtn) els.sessionsNewBtn.addEventListener('click', createNewSession);
        if (els.sessionsDeleteBtn) els.sessionsDeleteBtn.addEventListener('click', deleteSelectedSession);
        // 列表 <ul> 上用事件委托处理 item click / delete click
        if (els.sessionList) {
            els.sessionList.addEventListener('click', (e) => {
                const shareBtn = e.target.closest('.session-item-share');
                if (shareBtn) {
                    e.stopPropagation();
                    const sid = shareBtn.dataset.sid;
                    if (sid) _shareSession(sid);
                    return;
                }
                const delBtn = e.target.closest('.session-item-delete');
                if (delBtn) {
                    e.stopPropagation();
                    const sid = delBtn.dataset.sid;
                    if (sid) deleteSessionById(sid);
                    return;
                }
                const item = e.target.closest('.session-item');
                if (item) {
                    const sid = item.dataset.sid;
                    if (sid) switchSessionTo(sid);
                }
            });
        }
        // 移动端开关
        if (els.sessionsSidebarToggle && els.sessionsSidebar) {
            els.sessionsSidebarToggle.addEventListener('click', () => {
                els.sessionsSidebar.classList.toggle('open');
            });
        }
        // Task YYYY-H (#114): 搜索框 — 本地 filter, 不发请求
        if (els.sessionsSearchInput) {
            els.sessionsSearchInput.addEventListener('input', () => {
                _resetSessionsPagination();  // XXXX-8: 重置分页
                _filterAndRenderSessions();
            });
        }
        // VVVV: 启动就拉一次 — 用户看到 sidebar 已经有内容, 不用点
        setTimeout(refreshSessions, 200);
    }

    // ---------- Task SSS (#105) + VVVV (#108) + YYYY-H (#114): 多会话管理 (含搜索过滤) ----------
    // 缓存当前 session 列表 (供 search filter 用, 不发请求)
    let _sessionsCache = [];
    // Task XXXX-8 (#155): 客户端分页, 默 30 条 + "加载更多" 按钮
    const _SESSIONS_PAGE_SIZE = 30;
    let _sessionsVisibleLimit = _SESSIONS_PAGE_SIZE;

    function _renderSessionList(sessions) {
        if (!els.sessionList) return;
        const current = state.settings.sessionId;
        if (els.sessionsCount) els.sessionsCount.textContent = String(sessions.length);
        if (!sessions.length) {
            // Task XXXX-17: 区分"过滤无匹配"和"真无会话"两种空态
            const q = (els.sessionsSearchInput?.value || '').trim();
            const msg = q ? '无匹配会话' : (window.I18n?.t('sessions.empty') || '无会话');
            els.sessionList.innerHTML = `<li class="empty-state">${escapeHtml(msg)}</li>`;
            return;
        }
        // 分页: 只渲前 _sessionsVisibleLimit 条
        const visible = sessions.slice(0, _sessionsVisibleLimit);
        const hidden = sessions.length - visible.length;
        const items = [];
        for (const s of visible) {
            const sid = s.session_id;
            const when = s.last_modified
                ? new Date(s.last_modified * 1000).toLocaleString()
                : '';
            const flag = s.has_history ? '💬' : '○';
            const active = sid === current ? ' active' : '';
            // YYYY-E (#116): 优先用 title 字段, fallback id
            const displayName = s.title || sid;
            items.push(`<li class="session-item${active}" data-sid="${escapeHtml(sid)}" title="${escapeHtml(sid)}">
                <div class="session-item-main">
                    <span class="session-item-name">${flag} ${escapeHtml(displayName)}</span>
                    <span class="session-item-meta">${escapeHtml(when)}</span>
                </div>
                <button class="session-item-share" data-sid="${escapeHtml(sid)}" title="分享只读链接给同事" aria-label="分享">🔗</button>
                <button class="session-item-delete" data-sid="${escapeHtml(sid)}" title="删除会话" aria-label="删除">✕</button>
            </li>`);
        }
        // 加 "加载更多" footer
        if (hidden > 0) {
            items.push(`<li class="session-list-loadmore"><button type="button" class="small-btn ghost" id="sessions-loadmore-btn">▼ 加载更多 (${hidden} 个剩余)</button></li>`);
        }
        els.sessionList.innerHTML = items.join('');
        // 绑定加载更多
        const moreBtn = els.sessionList.querySelector('#sessions-loadmore-btn');
        if (moreBtn) {
            moreBtn.addEventListener('click', () => {
                _sessionsVisibleLimit += _SESSIONS_PAGE_SIZE;
                _filterAndRenderSessions();
            });
        }
        // Task RRRR: 渲完后给 inflight session 打 thinking 标记
        _updateSessionInflightUI();
    }

    // YYYY-H: 按搜索词过滤 + 重渲
    // Task PM-7-4: 同时搜消息内容 — 当本地 localStorage 里有 session 的 history 时
    //              扫一遍消息文本; 没缓存的 session 只比 id+title
    function _filterAndRenderSessions() {
        const q = (els.sessionsSearchInput?.value || '').trim().toLowerCase();
        if (!q) {
            _renderSessionList(_sessionsCache);
            return;
        }
        // PM-7-4: 加扫 history (localStorage 残存的当前会话, 远端 history 不在客户端)
        const histMatch = (sid) => {
            // 只能搜 当前 session 的 in-memory history (state.history); 跨 session 搜需后端支持, MVP 不做
            if (sid === state.settings.sessionId && Array.isArray(state.history)) {
                return state.history.some(m =>
                    (m.content || '').toLowerCase().includes(q)
                );
            }
            return false;
        };
        const filtered = _sessionsCache.filter(s => {
            const hay = `${s.session_id} ${s.title || ''}`.toLowerCase();
            if (hay.includes(q)) return true;
            return histMatch(s.session_id);
        });
        _renderSessionList(filtered);
    }

    // XXXX-8: 搜索框值变化时, 把分页 limit 重置 (新过滤器要从第一页开始)
    function _resetSessionsPagination() {
        _sessionsVisibleLimit = _SESSIONS_PAGE_SIZE;
    }

    // Task PM-7-6: 输入框 token / cost 粗估 (4 char ≈ 1 token, 单价照 Qwen-max 平均 $0.003/1K)
    // 显示在 composer-extras 右侧. 准确性不是目标 — 让用户对 "大 prompt 烧钱" 有感.
    function _updateCostEstimate() {
        const el = document.getElementById('cost-estimate');
        if (!el) return;
        const text = els.inputText?.value || '';
        if (text.length < 100) {
            el.hidden = true;
            return;
        }
        const inputTokens = Math.round(text.length / 4);
        const estOutputTokens = 500;  // 假定 500 token 回答 (粗略)
        const totalTokens = inputTokens + estOutputTokens;
        // 单价: $0.003 / 1K (qwen-max 中间档), $0.001 是省钱档, $0.03 是 gpt-4o
        const usd = (totalTokens / 1000) * 0.003;
        el.hidden = false;
        el.textContent = `≈ ${inputTokens.toLocaleString()} tok / $${usd.toFixed(4)}`;
        el.title = `输入 ~${inputTokens} token + 预估回答 ~${estOutputTokens} token. 单价按 $0.003/1K 估 (qwen-max 档).`;
    }

    // Task PM-7-5: 进入 read-only 分享模式. 隐藏 composer + sidebar 操作, 拉远端 session 渲.
    async function _enterShareReadOnlyMode(sid) {
        document.body.classList.add('share-readonly');
        // 顶部加个 banner 显这是分享内容
        const banner = document.createElement('div');
        banner.className = 'share-banner';
        banner.innerHTML = `
            🔗 你正在查看一段分享的对话 (只读). <a href="/">回到 Anything 主界面</a>
        `;
        document.body.insertBefore(banner, document.body.firstChild);

        state.settings.sessionId = sid;
        try {
            await _loadSessionHistory(sid);
        } catch (e) {
            // ignore
        }
    }

    // Task PM-7-5: 分享 session — 复制只读链接到剪贴板
    // URL: /?share=<sid> 前端检测到 ?share= 时进入 read-only 模式 (隐藏 composer + 用 GET /sessions/{id} 渲)
    async function _shareSession(sid) {
        if (!sid) return;
        const url = `${location.protocol}//${location.host}/?share=${encodeURIComponent(sid)}`;
        try {
            await navigator.clipboard.writeText(url);
            toast('success', '已复制分享链接', '同事打开会进入只读模式');
        } catch (e) {
            // 剪贴板失败 fallback: prompt 显内容
            prompt('复制下面这个链接发给同事:', url);
        }
    }

    // Task PM-7-3: per-session system prompt (存 localStorage, key by sid)
    const _SYSPROMPT_LSKEY = 'anything_session_sysprompts';
    function _allSessionSysPrompts() {
        try {
            const raw = localStorage.getItem(_SYSPROMPT_LSKEY);
            return raw ? JSON.parse(raw) : {};
        } catch (_) { return {}; }
    }
    function _getSessionSystemPrompt(sid) {
        if (!sid) return '';
        const m = _allSessionSysPrompts();
        return (m[sid] || '').trim();
    }
    function _setSessionSystemPrompt(sid, txt) {
        if (!sid) return;
        const m = _allSessionSysPrompts();
        if (txt && txt.trim()) m[sid] = txt.trim();
        else delete m[sid];
        try { localStorage.setItem(_SYSPROMPT_LSKEY, JSON.stringify(m)); } catch (_) {}
    }
    // 暴露按钮: 在 chat-pane header (这里没有 header 容器, 直接挂到 composer 上方)
    function _showSessionSysPromptModal() {
        const sid = state.settings.sessionId;
        if (!sid) return;
        const current = _getSessionSystemPrompt(sid);
        const txt = prompt(
            `编辑本会话的 system prompt (LLM 系统指令):\n` +
            `例: "你是 Python 老师, 解释代码时用类比"\n` +
            `留空 = 清除`,
            current
        );
        if (txt === null) return;  // cancel
        _setSessionSystemPrompt(sid, txt);
        toast('success', '本会话 system prompt 已更新', txt ? txt.slice(0, 40) : '(已清空)');
    }

    // Task PM-5: Knowledge Base 面板 + per-session attach
    let _kbCache = [];

    function _bindKbPanel() {
        document.getElementById('kb-refresh-btn')?.addEventListener('click', _loadKbList);
        document.getElementById('kb-create-btn')?.addEventListener('click', _createKbInteractive);
        const sel = document.getElementById('kb-current-select');
        if (sel) {
            sel.addEventListener('change', () => {
                const v = sel.value || '';
                state.settings.currentKbId = v || null;
                try { localStorage.setItem('anything_settings', JSON.stringify(state.settings)); } catch (_) {}
                if (v) {
                    const kb = _kbCache.find(k => k.id === v);
                    toast('info', `当前 session 用知识库`, kb?.name || v);
                } else {
                    toast('info', '已切换到 (全部文档)', '');
                }
            });
        }
    }

    async function _loadKbList() {
        const ul = document.getElementById('kb-list');
        const sel = document.getElementById('kb-current-select');
        if (!ul) return;
        try {
            const tenant = state.settings.tenant || 'default';
            const { payload, status } = await ApiClient.listKBs(tenant);
            if (status !== 200 || payload?.code !== 'SUCCESS') {
                ul.innerHTML = `<li class="empty-state">加载失败: ${payload?.code || status}</li>`;
                return;
            }
            _kbCache = (payload.data?.items || []);
            // 渲列表
            if (!_kbCache.length) {
                ul.innerHTML = '<li class="empty-state">还没有知识库, 点 "+新建 KB" 开始</li>';
            } else {
                ul.innerHTML = _kbCache.map(k => `
                    <li class="kb-item" data-kbid="${escapeHtml(k.id)}">
                        <div class="kb-item-main">
                            <span class="kb-name">${escapeHtml(k.name)}</span>
                            <span class="kb-meta">${k.doc_count || 0} 个文档</span>
                        </div>
                        ${k.description ? `<div class="kb-desc">${escapeHtml(k.description)}</div>` : ''}
                        <div class="kb-actions">
                            <button class="small-btn ghost kb-use-btn" data-kbid="${escapeHtml(k.id)}">用这个</button>
                            <button class="small-btn ghost kb-del-btn" data-kbid="${escapeHtml(k.id)}">删</button>
                        </div>
                    </li>
                `).join('');
                ul.querySelectorAll('.kb-use-btn').forEach(btn => {
                    btn.addEventListener('click', () => {
                        sel.value = btn.dataset.kbid;
                        sel.dispatchEvent(new Event('change'));
                    });
                });
                ul.querySelectorAll('.kb-del-btn').forEach(btn => {
                    btn.addEventListener('click', async () => {
                        if (!confirm('确定删除这个知识库?\n(只删 KB, 不删文档本身)')) return;
                        try {
                            await ApiClient.deleteKB(btn.dataset.kbid);
                            toast('success', '已删除', '');
                            await _loadKbList();
                        } catch (e) {
                            toast('error', '删除失败', e.message);
                        }
                    });
                });
            }
            // 同步下拉
            if (sel) {
                const currentVal = state.settings.currentKbId || '';
                sel.innerHTML = '<option value="">(全部文档)</option>' +
                    _kbCache.map(k =>
                        `<option value="${escapeHtml(k.id)}" ${k.id === currentVal ? 'selected' : ''}>${escapeHtml(k.name)} (${k.doc_count || 0})</option>`
                    ).join('');
            }
        } catch (e) {
            ul.innerHTML = `<li class="empty-state">异常: ${escapeHtml(e.message)}</li>`;
        }
    }

    async function _createKbInteractive() {
        const name = (prompt('知识库名字 (例: 产品手册 / 客服 FAQ):', '') || '').trim();
        if (!name) return;
        const desc = (prompt('描述 (可选):', '') || '').trim();
        try {
            const { payload, status } = await ApiClient.createKB(name, desc, state.settings.tenant || 'default');
            if (status === 200 && payload?.code === 'SUCCESS') {
                toast('success', '知识库已创建', name);
                await _loadKbList();
            } else {
                toast('error', payload?.code || status, payload?.message || '');
            }
        } catch (e) {
            toast('error', '创建失败', e.message);
        }
    }

    // Task PM-4: Slash command palette — 输入框首字符 / 时弹工具选择
    // 状态: _allAgentTools 由 _loadAgentTools 启动时填; palette 渲染在
    // input 上方浮动. 上下方向键导航, Enter 选中, Esc 关闭.
    let _allAgentTools = [];   // {name, description, category}
    let _slashPaletteEl = null;
    let _slashPaletteOpen = false;
    let _slashPaletteIndex = 0;
    let _slashPaletteFiltered = [];

    function _slashPaletteOnInput() {
        const v = els.inputText.value || '';
        // 触发条件: textarea 首字符是 /, 第二个字符不是空格
        if (!v.startsWith('/') || v.startsWith('/ ')) {
            _slashPaletteClose();
            return;
        }
        const q = v.slice(1).trim().toLowerCase();
        const tools = _allAgentTools.length ? _allAgentTools : [];
        _slashPaletteFiltered = q
            ? tools.filter(t =>
                  t.name.toLowerCase().includes(q) ||
                  (t.description || '').toLowerCase().includes(q)
              ).slice(0, 12)
            : tools.slice(0, 12);
        _slashPaletteIndex = 0;
        _slashPaletteRender();
    }

    function _slashPaletteRender() {
        if (!_slashPaletteFiltered.length) {
            _slashPaletteClose();
            return;
        }
        if (!_slashPaletteEl) {
            _slashPaletteEl = document.createElement('div');
            _slashPaletteEl.className = 'slash-palette';
            // 挂到 composer 区底部 (input 上方)
            const composer = els.inputText.closest('.composer') || document.body;
            composer.appendChild(_slashPaletteEl);
        }
        _slashPaletteEl.innerHTML = _slashPaletteFiltered.map((t, i) => `
            <div class="slash-item ${i === _slashPaletteIndex ? 'active' : ''}"
                 data-idx="${i}" data-name="${escapeHtml(t.name)}">
                <code class="slash-name">/${escapeHtml(t.name)}</code>
                <span class="slash-desc">${escapeHtml((t.description || '').slice(0, 80))}</span>
            </div>
        `).join('');
        _slashPaletteEl.querySelectorAll('.slash-item').forEach(item => {
            item.addEventListener('mouseenter', () => {
                _slashPaletteIndex = parseInt(item.dataset.idx, 10) || 0;
                _slashPaletteRender();
            });
            item.addEventListener('click', () => {
                _slashPaletteIndex = parseInt(item.dataset.idx, 10) || 0;
                _slashPaletteSelect();
            });
        });
        _slashPaletteOpen = true;
    }

    function _slashPaletteClose() {
        if (_slashPaletteEl) {
            _slashPaletteEl.remove();
            _slashPaletteEl = null;
        }
        _slashPaletteOpen = false;
        _slashPaletteFiltered = [];
    }

    function _slashPaletteSelect() {
        const tool = _slashPaletteFiltered[_slashPaletteIndex];
        if (!tool) return _slashPaletteClose();
        // 把 textarea 替换成示例 task (有的话) 或工具 hint
        const example = _AGENT_TOOL_EXAMPLES[tool.name] || `用 ${tool.name} 工具: `;
        els.inputText.value = example;
        els.inputText.focus();
        // 切到 Agent 模式 (slash 都是 tool 调用, RAG 用不了)
        if (state.mode !== 'agent') {
            state.mode = 'agent';
            renderModeTabs?.();
            updateComposerPlaceholder?.();
        }
        _slashPaletteClose();
    }

    // keydown 拦截: palette 打开时 ↑↓ / Enter / Esc 给 palette 用, return true 表示已处理
    function _slashPaletteHandleKey(e) {
        if (!_slashPaletteOpen) return false;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            _slashPaletteIndex = (_slashPaletteIndex + 1) % _slashPaletteFiltered.length;
            _slashPaletteRender();
            return true;
        }
        if (e.key === 'ArrowUp') {
            e.preventDefault();
            _slashPaletteIndex = (_slashPaletteIndex - 1 + _slashPaletteFiltered.length) % _slashPaletteFiltered.length;
            _slashPaletteRender();
            return true;
        }
        if (e.key === 'Enter') {
            e.preventDefault();
            _slashPaletteSelect();
            return true;
        }
        if (e.key === 'Escape') {
            e.preventDefault();
            _slashPaletteClose();
            return true;
        }
        return false;
    }

    // Task PM-3: 首启向导. 检测有没有可用的 chat 模型, 没有就弹.
    // "可用" 定义: 至少有 1 个 CHAT model 且 configured=true.
    async function _maybeShowFirstRunWizard() {
        try {
            const { payload, status } = await ApiClient.listModels();
            if (status !== 200 || payload?.code !== 'SUCCESS') return;
            const models = payload.data?.models || [];
            const configured = models.filter(
                m => (m.request_type || '').toUpperCase() === 'CHAT' && m.configured
            );
            if (configured.length > 0) return;  // 已有可用, 不弹
            // 没有 — 显向导
            const drawer = document.getElementById('firstrun-drawer');
            if (drawer) drawer.hidden = false;
        } catch (_) {
            // 失败 (服务挂?) 不弹, 让用户自己处理
        }
    }

    // 4 个 provider 的预设
    const _FIRSTRUN_PROVIDERS = {
        dashscope: {
            label: 'DashScope (通义千问)',
            api_base: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
            default_model: 'qwen-max',
            adapter_class: 'OpenAIChatAdapter',
            key_hint: 'sk-xxxx (从 https://dashscope.console.aliyun.com 获取)',
            key_required: true,
        },
        openai: {
            label: 'OpenAI',
            api_base: 'https://api.openai.com/v1',
            default_model: 'gpt-4o-mini',
            adapter_class: 'OpenAIChatAdapter',
            key_hint: 'sk-xxxx (从 https://platform.openai.com/api-keys 获取)',
            key_required: true,
        },
        deepseek: {
            label: 'DeepSeek',
            api_base: 'https://api.deepseek.com/v1',
            default_model: 'deepseek-chat',
            adapter_class: 'OpenAIChatAdapter',
            key_hint: 'sk-xxxx (从 https://platform.deepseek.com 获取)',
            key_required: true,
        },
        ollama: {
            label: 'Ollama (本地)',
            api_base: 'http://localhost:11434/v1',
            default_model: 'llama3.2',
            adapter_class: 'OpenAIChatAdapter',
            key_hint: 'Ollama 不需要 key (填 "ollama" 占位即可). 确保本地 ollama serve 已启动.',
            key_required: false,
        },
    };

    function _bindFirstRunWizard() {
        const drawer = document.getElementById('firstrun-drawer');
        if (!drawer) return;
        let selectedProvider = null;

        // provider 按钮
        drawer.querySelectorAll('.firstrun-provider-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                selectedProvider = btn.dataset.provider;
                drawer.querySelectorAll('.firstrun-provider-btn').forEach(b =>
                    b.classList.toggle('active', b === btn)
                );
                const preset = _FIRSTRUN_PROVIDERS[selectedProvider];
                if (!preset) return;
                // 显后面的 step
                document.getElementById('firstrun-key-row').hidden = false;
                document.getElementById('firstrun-model-row').hidden = false;
                document.getElementById('firstrun-base-row').hidden = false;
                document.getElementById('firstrun-submit-row').hidden = false;
                document.getElementById('firstrun-key-hint').textContent = preset.key_hint;
                document.getElementById('firstrun-model-input').value = preset.default_model;
                document.getElementById('firstrun-base-input').value = preset.api_base;
                // Ollama 不需要 key, 自动填占位
                const keyInput = document.getElementById('firstrun-key-input');
                if (!preset.key_required) {
                    keyInput.value = 'ollama';
                    keyInput.disabled = true;
                } else {
                    keyInput.disabled = false;
                    if (keyInput.value === 'ollama') keyInput.value = '';
                    keyInput.focus();
                }
            });
        });

        // 保存按钮
        document.getElementById('firstrun-submit-btn')?.addEventListener('click', async () => {
            if (!selectedProvider) {
                toast('warn', '请选 provider', '');
                return;
            }
            const preset = _FIRSTRUN_PROVIDERS[selectedProvider];
            const key = document.getElementById('firstrun-key-input').value.trim();
            const modelName = document.getElementById('firstrun-model-input').value.trim();
            const apiBase = document.getElementById('firstrun-base-input').value.trim();
            if (preset.key_required && !key) {
                toast('warn', 'API key 不能为空', '');
                return;
            }
            if (!modelName) {
                toast('warn', '模型名不能为空', '');
                return;
            }
            try {
                // 1. register model
                const reg = await ApiClient.registerModel({
                    name: modelName,
                    request_type: 'CHAT',
                    adapter_class: preset.adapter_class,
                    api_key: key,
                    api_base: apiBase,
                    set_as_default: true,  // 自动设为默认
                });
                if (reg.status !== 200 || reg.payload?.code !== 'SUCCESS') {
                    toast('error', reg.payload?.code || reg.status, reg.payload?.message || '');
                    return;
                }
                toast('success', `${preset.label} 已配置`, `model=${modelName}, 默认 CHAT`);
                drawer.hidden = true;
                // 刷新设置抽屉里 picker (虽然没打开, 提前更新)
                _loadChatModelPicker();
            } catch (e) {
                toast('error', '配置失败', e.message);
            }
        });

        document.getElementById('firstrun-skip-btn')?.addEventListener('click', () => {
            drawer.hidden = true;
        });
    }

    // Task XXXX-20 (#164): 加载 chat 模型 picker. 设置抽屉打开时调.
    // 设计: 已注册的 CHAT 模型全列出来, 其中 is_default=true 的预选.
    //       "⚙ 自动" 永远在第一位; 用户不选 = 走配置层 default_chat_model.
    async function _loadChatModelPicker() {
        const sel = document.getElementById('chat-model-select');
        if (!sel) return;
        try {
            const { payload, status } = await ApiClient.listModels();
            if (status !== 200 || payload?.code !== 'SUCCESS') return;
            const models = (payload.data?.models || []).filter(
                m => (m.request_type || '').toUpperCase() === 'CHAT'
            );
            // 重渲: 保留首个 "自动" option, 后面塞已注册
            const autoOpt = sel.querySelector('option[value=""]');
            sel.innerHTML = '';
            if (autoOpt) sel.appendChild(autoOpt);
            else {
                const o = document.createElement('option');
                o.value = '';
                o.textContent = window.I18n?.t('settings.chatModel.auto') || '⚙ 自动';
                sel.appendChild(o);
            }
            let currentDefault = '';
            for (const m of models) {
                const o = document.createElement('option');
                o.value = m.name;
                const flag = m.is_default ? ' ✓' : '';
                const config = m.configured ? '' : ' (未配置 key)';
                o.textContent = `${m.name}${flag}${config}`;
                sel.appendChild(o);
                if (m.is_default) currentDefault = m.name;
            }
            // 同步选中
            sel.value = currentDefault;
        } catch (e) {
            console.warn('[chat-model-picker] load failed:', e.message);
        }
    }

    // Task XXXX-17: 发送中的会话还没在 _sessionsCache 里 → 乐观插一条占位,
    // 让左栏立刻显有此会话. response 完成 + refreshSessions 之后会被真数据覆盖.
    function _optimisticAddCurrentSession(sid, firstMsg) {
        if (!sid) return;
        if (_sessionsCache.some(s => s.session_id === sid)) return;  // 已有, 跳
        const title = (firstMsg || sid).split('\n')[0].slice(0, 30);
        _sessionsCache.unshift({
            session_id: sid,
            title,
            last_modified: Math.floor(Date.now() / 1000),
            has_history: true,
            _optimistic: true,  // 标记位, refreshSessions 时会被服务端真值覆盖
        });
        _filterAndRenderSessions();
    }

    async function refreshSessions() {
        if (!els.sessionList) return;
        try {
            const { payload, status } = await ApiClient.listSessions(200);  // XXXX-8: 拉 200, 客户端分页
            if (status === 501) {
                els.sessionList.innerHTML = '<li class="empty-state">(state_store 未注入)</li>';
                if (els.sessionsCount) els.sessionsCount.textContent = '0';
                _sessionsCache = [];
                return;
            }
            if (status !== 200 || payload?.code !== 'SUCCESS') {
                els.sessionList.innerHTML = `<li class="empty-state error">× ${payload?.code || status}</li>`;
                if (els.sessionsCount) els.sessionsCount.textContent = '?';
                _sessionsCache = [];
                return;
            }
            // Task XXXX-18: 保留 optimistic 占位 — 后端如果还没扫到这个 sid
            // (响应已回但 _save_state_safe 异步未落盘), 不应该让它从左栏消失
            const backendSessions = (payload.data || {}).sessions || [];
            const backendIds = new Set(backendSessions.map(s => s.session_id));
            const survivingOptimistic = _sessionsCache.filter(
                s => s._optimistic && !backendIds.has(s.session_id)
            );
            _sessionsCache = [...survivingOptimistic, ...backendSessions];
            _filterAndRenderSessions();
        } catch (e) {
            els.sessionList.innerHTML = `<li class="empty-state error">× ${escapeHtml(e.message)}</li>`;
            _sessionsCache = [];
        }
    }

    async function createNewSession() {
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
            // 1. state + sessionInput 同步
            state.settings.sessionId = newId;
            if (els.sessionInput) els.sessionInput.value = newId;
            // 2. localStorage + ApiClient
            try { localStorage.setItem('anything_settings', JSON.stringify(state.settings)); } catch (_) {}
            ApiClient.configure(state.settings);
            // 3. 刷新列表 (新 session 自动 active 高亮)
            await refreshSessions();
            // 4. 清空主聊天区 → 重渲 welcome (含示例 prompt)
            //    + Task MMMM (#130) 淡入动效
            state.history = [];
            renderHistory();
            if (els.messages) {
                els.messages.classList.remove('session-switched');
                void els.messages.offsetWidth;
                els.messages.classList.add('session-switched');
            }
            toast('success', '新会话已创建', newId);
        } catch (e) {
            toast('error', '新建会话失败', e.message);
        }
    }

    // Task XXXX-4 (#151): 删除 session 走 5s 撤销窗口, 不立即 DELETE
    // sid → {timer, sessionItem, listIndex}
    const _pendingDeletes = new Map();

    async function deleteSessionById(sid) {
        if (!sid) return;
        // 已经在待删队列 → 视为再点一次确认, 立即删
        if (_pendingDeletes.has(sid)) {
            _commitDelete(sid);
            return;
        }
        // 不再 confirm 弹窗 — 改 toast + 撤销按钮
        // 1. 视觉上 list item 立即变灰 / 划掉
        const item = els.sessionList?.querySelector(`.session-item[data-sid="${sid}"]`);
        if (item) item.classList.add('pending-delete');
        // 2. 5s 后真删; 期间用户点 undo → 取消
        const timer = setTimeout(() => _commitDelete(sid), 5000);
        _pendingDeletes.set(sid, { timer });
        // 3. toast 含 undo 按钮
        _showUndoDeleteToast(sid);
    }

    function _showUndoDeleteToast(sid) {
        const wrap = document.createElement('div');
        wrap.className = 'toast toast-info undo-toast';
        wrap.innerHTML = `
            <div class="toast-title">${t('session.undo.title')} ${escapeHtml(sid.slice(0,16))}</div>
            <div class="toast-body">${t('session.undo.body')}</div>
            <button class="toast-undo-btn" type="button">${t('session.undo.btn')}</button>
        `;
        const undoBtn = wrap.querySelector('.toast-undo-btn');
        undoBtn.addEventListener('click', () => {
            const entry = _pendingDeletes.get(sid);
            if (entry?.timer) clearTimeout(entry.timer);
            _pendingDeletes.delete(sid);
            const item = els.sessionList?.querySelector(`.session-item[data-sid="${sid}"]`);
            if (item) item.classList.remove('pending-delete');
            wrap.remove();
            toast('info', '已撤销删除', sid);
        });
        // 挂到 toast 容器
        const host = document.querySelector('.toast-host') || document.body;
        host.appendChild(wrap);
        // 5s 后自动消失
        setTimeout(() => wrap.remove(), 5200);
    }

    async function _commitDelete(sid) {
        const entry = _pendingDeletes.get(sid);
        if (entry?.timer) clearTimeout(entry.timer);
        _pendingDeletes.delete(sid);
        try {
            const { payload, status } = await ApiClient.deleteSession(sid);
            if (status === 200 && payload?.code === 'SUCCESS') {
                toast('success', '会话已删除', sid);
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

    // 兼容旧名 (settings drawer 还可能调)
    async function deleteSelectedSession() {
        const sid = state.settings.sessionId;
        if (!sid) {
            toast('warn', '当前没有选中会话', '');
            return;
        }
        return deleteSessionById(sid);
    }

    // Task ZZZZ (#117): 把后端 session state 转成前端 message 数组.
    //   优先级:
    //     1. events 里 role=user/assistant 直接对应 (RAG/chat 路径)
    //     2. events 里 react_started.payload.task / react_final.payload.final_answer_preview (React 路径)
    //     3. state 顶层 task (React agent 短路径 — 任务执行完只保留概要)
    function _stateToMessages(state_data) {
        if (!state_data || typeof state_data !== 'object') return [];
        const events = state_data.events || state_data.history || [];
        const out = [];
        let idx = 0;
        // 路径 1: role event
        let hasRoleMsg = false;
        for (const ev of events) {
            if (!ev || typeof ev !== 'object') continue;
            if (ev.role === 'user' || ev.role === 'assistant') {
                hasRoleMsg = true;
                out.push({
                    id: 'hist_' + (idx++) + '_' + (ev.trace_id || '').slice(0, 8),
                    role: ev.role,
                    content: String(ev.content || ''),
                    timestamp: ev.timestamp || null,
                    trace_id: ev.trace_id || null,
                    type: ev.type || null,
                });
            }
        }
        if (hasRoleMsg) return out;
        // 路径 2: React event 流
        for (const ev of events) {
            if (!ev || typeof ev !== 'object') continue;
            if (ev.event_type === 'react_started') {
                const task = (ev.payload || {}).task;
                if (task) out.push({
                    id: 'hist_' + (idx++) + '_started',
                    role: 'user', content: String(task),
                    timestamp: ev.timestamp || null,
                    trace_id: ev.trace_id || null,
                    type: 'agent',
                });
            } else if (ev.event_type === 'react_final') {
                const ans = (ev.payload || {}).final_answer_preview;
                if (ans) out.push({
                    id: 'hist_' + (idx++) + '_final',
                    role: 'assistant', content: String(ans),
                    timestamp: ev.timestamp || null,
                    trace_id: ev.trace_id || null,
                    type: 'agent',
                });
            }
        }
        if (out.length > 0) return out;
        // 路径 3: 状态顶层 task — 兜底, react agent 执行完后 events 可能被清空只留 task
        let topTask = state_data.task;
        if (topTask) {
            // Task PPPP / PPPP-2: 后端 Agent 把"长期记忆 facts + 原 task"合成 augmented prompt.
            // 解析回:
            //   1) 原 task 当 user content (不再把注入当用户的话)
            //   2) memory hits 挂到 message.meta.memoryHits, 让 UI 显 📚 N 徽章
            // Task QQQQ: state 顶层 task 现在已经是原句 (后端持久化时存 original_task);
            //   仅老格式 (含 [当前任务] 标记) 时才解析. answer 直接读 state.answer.
            const taskStr = String(topTask);
            const memHeader = '[长期记忆 — 已知关于用户/上下文的相关信息]';
            const curHeader = '[当前任务]';
            const memIdx = taskStr.indexOf(memHeader);
            const curIdx = taskStr.indexOf(curHeader);
            let memHits = [];
            // 老 state 兼容: task 里仍含 augmented prompt 时解析
            if (memIdx >= 0 && curIdx > memIdx) {
                const block = taskStr.slice(memIdx + memHeader.length, curIdx);
                memHits = block.split('\n')
                    .map(l => l.trim())
                    .filter(l => l.startsWith('- '))
                    .map((l, i) => ({
                        fact_id: 'hist_' + i,
                        content: l.slice(2).trim(),
                        score: null,
                        reason: 'session_history',
                    }));
                topTask = taskStr.slice(curIdx + curHeader.length).replace(/^\s+/, '');
            }
            // 新 state: 后端写 augmented_task 单独, task 已是原句
            if (state_data.augmented_task && memHits.length === 0) {
                const at = String(state_data.augmented_task);
                const aMemIdx = at.indexOf(memHeader);
                const aCurIdx = at.indexOf(curHeader);
                if (aMemIdx >= 0 && aCurIdx > aMemIdx) {
                    const block = at.slice(aMemIdx + memHeader.length, aCurIdx);
                    memHits = block.split('\n')
                        .map(l => l.trim())
                        .filter(l => l.startsWith('- '))
                        .map((l, i) => ({
                            fact_id: 'hist_' + i,
                            content: l.slice(2).trim(),
                            score: null,
                            reason: 'session_history',
                        }));
                }
            }
            out.push({
                id: 'hist_top_task',
                role: 'user',
                content: String(topTask),
                type: state_data.execution_mode || 'agent',
                meta: memHits.length > 0 ? { memoryHits: memHits } : null,
            });
            // 持久化的 answer 优先; 没有才退回到 placeholder
            const finalAnswer = state_data.answer || '';
            if (finalAnswer) {
                // Task WWWW: 历史消息也跑同样的 image 提取 — 扫 answer 里的图片 URL,
                // 删掉裸 URL 文本 + append markdown image, 让 <img> 渲染 (跟新消息一致).
                let answerStr = String(finalAnswer);
                const urlRe = /https?:\/\/[^\s"'<>]+\.(?:png|jpe?g|webp|gif)(?:\?[^\s"'<>]*)?/gi;
                const urls = Array.from(new Set(answerStr.match(urlRe) || []));
                if (urls.length > 0) {
                    for (const u of urls) {
                        answerStr = answerStr.split(u).join('');
                    }
                    answerStr = answerStr.replace(/\n{3,}/g, '\n\n').trim();
                    answerStr += '\n\n' + urls.map(u => `![生成图](${u})`).join('\n\n');
                }
                out.push({
                    id: 'hist_top_answer',
                    role: 'assistant',
                    content: answerStr,
                    type: state_data.execution_mode || 'agent',
                });
            } else if (state_data.status === 'completed') {
                const preview = String(topTask).length > 60
                    ? String(topTask).slice(0, 60) + '…' : String(topTask);
                out.push({
                    id: 'hist_top_done',
                    role: 'assistant',
                    content: `_（此会话已完成, 但未保留完整回答内容. 任务概要: "${preview}"）_`,
                    type: state_data.execution_mode || 'agent',
                });
            }
        }
        return out;
    }

    async function _loadSessionHistory(sid) {
        // Task XXXX-18: 不再先无脑清空 state.history. 后端可能比本地"旧"
        // (新发送响应还没落盘, 或 LLM 失败没 save), 那种情况要保留本地版本.
        const localBackup = Array.isArray(state.history) ? state.history.slice() : [];
        const hadLocal = localBackup.length > 0;
        if (els.messages && !hadLocal) {
            // 只在 localStorage 也空时显加载中 (避免 flash 清空已有内容)
            els.messages.innerHTML = '<div class="welcome"><p>加载会话历史中…</p></div>';
            // Task MMMM (#130): 触发淡入动效 (replay 需先移除再加 class)
            els.messages.classList.remove('session-switched');
            void els.messages.offsetWidth;
            els.messages.classList.add('session-switched');
        }
        try {
            const { payload, status } = await ApiClient.getSession(sid);
            if (status === 404) {
                // 后端无该 session — 若本地有历史 (localStorage 残留), 保留;
                // 若本地也空, 渲欢迎页.
                if (!hadLocal) {
                    state.history = [];
                    renderHistory();
                }
                return;
            }
            if (status !== 200 || payload?.code !== 'SUCCESS') {
                // 网络/服务错误: 保留本地, 不清屏
                if (!hadLocal) {
                    els.messages.innerHTML = `<div class="welcome"><p style="color:var(--danger)">× 加载失败 ${payload?.code || status} ${payload?.message || ''}</p></div>`;
                }
                return;
            }
            const state_data = payload.data || {};
            const msgs = _stateToMessages(state_data);
            // 关键判断: 后端给了 0 条消息, 但本地有 → 保留本地 (后端还没落盘的情况)
            if (msgs.length === 0 && hadLocal) {
                console.info(`[history] backend empty but local has ${localBackup.length} msgs — keeping local`);
                state.history = localBackup;
                renderHistory();
                return;
            }
            state.history = msgs;
            renderHistory();
        } catch (e) {
            if (!hadLocal) {
                els.messages.innerHTML = `<div class="welcome"><p style="color:var(--danger)">× 加载异常: ${escapeHtml(e.message)}</p></div>`;
            }
        }
    }

    function switchSessionTo(sid) {
        if (!sid || sid === state.settings.sessionId) return;
        // Task RRRR (#135): 不再 stopSending — 多会话并行, 旧 session 的 inflight 让它跑完
        state.settings.sessionId = sid;
        if (els.sessionInput) els.sessionInput.value = sid;
        try { localStorage.setItem('anything_settings', JSON.stringify(state.settings)); } catch (_) {}
        ApiClient.configure(state.settings);
        _updateSendButtonUI();
        refreshSessions();
        // 修复 (切会话残留): 切换前先强制清空 state.history, 避免 _loadSessionHistory
        // 的 hadLocal 防护逻辑把上一个 session 的消息当作"本地备份"保留下来.
        // 切换 = 用户明确换 session, 不存在"网络抖动要保护本地数据"的场景.
        state.history = [];
        if (els.messages) {
            els.messages.innerHTML = '<div class="welcome"><p class="hint">加载中…</p></div>';
        }
        _loadSessionHistory(sid);
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
        // 修复: localStorage['anything_history'] 是全局唯一 key, 多会话场景下会把
        // 老 session 的 history 装到新 session 形成"幽灵历史". 一律删除, 启动后
        // _loadSessionHistory 从后端按 sid 拉, 没有就空白.
        try { localStorage.removeItem('anything_history'); } catch (_) {}
        state.history = [];

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
        // Task NNNN (#131): 启动 apply 主题
        _applyTheme(state.settings.theme || 'dark');
        // Task XXXX (#112): tenant 双向同步 — drawer input + header mini chip
        if (els.tenantInputDrawer) els.tenantInputDrawer.value = state.settings.tenant;
        if (els.tenantValue) els.tenantValue.textContent = state.settings.tenant || 'default';
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
        // Task XXXX (#112): tenant 主输入来源现在是 drawer input, 同步回 header chip + hidden input
        const newTenant = ((els.tenantInputDrawer && els.tenantInputDrawer.value) ||
                            (els.tenantInput && els.tenantInput.value) ||
                            '').trim() || 'default';
        state.settings.tenant = newTenant;
        if (els.tenantInput) els.tenantInput.value = newTenant;
        if (els.tenantValue) els.tenantValue.textContent = newTenant;
        try {
            localStorage.setItem('anything_settings', JSON.stringify(state.settings));
        } catch (_) {}
        ApiClient.configure(state.settings);
        toast('success', t('toast.settings.saved'), t('toast.settings.saved.body'));
        closeDrawer('settings');
    }

    function persistHistory() {
        // 修复 (多会话幽灵历史): 不再写全局 anything_history key.
        // 历史持久化交给后端 (state_store + events append). 切会话拉真实 history.
        // 这个函数保留是为了避免到处删调用方; no-op.
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
                _applyModeAwareComposer();  // WWWW-C: 控制 plan/reflect 显隐
            });
        });
        updateComposerPlaceholder();
        _applyModeAwareComposer();  // WWWW-C: 启动时也跑一次

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

        // 发送 / 停止 (Task GG #67 + Task RRRR #135: per-session 锁)
        els.sendBtn.addEventListener('click', () => {
            if (state.inflight.has(state.settings.sessionId)) {
                stopSending();
            } else {
                send();
            }
        });
        els.inputText.addEventListener('keydown', (e) => {
            // Task PM-4: slash palette 优先 — 打开时方向键 / Enter 给 palette 处理
            if (_slashPaletteHandleKey(e)) return;
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
        // Task PM-4: 监听 input, 输入框首字符是 / 且光标在开头时弹 palette
        els.inputText.addEventListener('input', () => {
            _slashPaletteOnInput();
            _updateCostEstimate();  // PM-7-6
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

        // Task XXXX (#112): tenant 主输入是 drawer input; header 只有可点 mini chip
        if (els.tenantInputDrawer) {
            els.tenantInputDrawer.addEventListener('change', () => {
                const v = (els.tenantInputDrawer.value || '').trim() || 'default';
                state.settings.tenant = v;
                if (els.tenantInput) els.tenantInput.value = v;
                if (els.tenantValue) els.tenantValue.textContent = v;
            });
        }
        // 旧的 hidden tenantInput 也保留监听以防其他代码 dispatch change
        if (els.tenantInput) {
            els.tenantInput.addEventListener('change', () => {
                state.settings.tenant = (els.tenantInput.value || '').trim() || 'default';
            });
        }
        // 点 header tenant mini chip → 打开 settings drawer, 聚焦到 tenant 输入
        if (els.tenantChipMini) {
            els.tenantChipMini.addEventListener('click', () => {
                if (els.settingsBtn) els.settingsBtn.click();
                setTimeout(() => {
                    if (els.tenantInputDrawer) {
                        els.tenantInputDrawer.focus();
                        els.tenantInputDrawer.select();
                    }
                }, 200);
            });
        }

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
            // Task XXXX-20 (#164): 同时刷 chat 模型 picker
            _loadChatModelPicker();
        });
        // Task XXXX-20 (#164): chat 模型 picker change
        const chatModelSelect = document.getElementById('chat-model-select');
        if (chatModelSelect) {
            chatModelSelect.addEventListener('change', async () => {
                const name = chatModelSelect.value;
                try {
                    if (!name) {
                        // "自动" — 服务端没有"清除 default override"端点;
                        // 用户体感上 "选回自动" 就是重启服务后回归 yaml/env 默认.
                        // 提示一下, 不改服务端.
                        toast('info', t('toast.chatModel.cleared'), '重启后生效, 或选具体模型覆盖');
                        return;
                    }
                    const { payload, status } = await ApiClient.setDefaultModel(name, 'CHAT');
                    if (status === 200 && payload?.code === 'SUCCESS') {
                        toast('success', t('toast.chatModel.set'), name);
                    } else {
                        toast('error', payload?.code || status, payload?.message || '');
                    }
                } catch (e) {
                    toast('error', '切换失败', e.message);
                }
            });
        }
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

        // Task WWWW-A (#109): metrics-refresh 从右栏独立 tab 移到 admin 卡内,
        // 元素在 admin-panel.js renderAdminStatus 时才创建. 这里 init 不能直接绑,
        // 改在 admin-panel.js 里 render 后绑 (通过 window.__loadMetrics).
        if (els.metricsRefresh) els.metricsRefresh.addEventListener('click', loadMetrics);

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

        // Task S: admin 面板刷新 (Task UUUU #107: 走 admin-panel 模块, 不再用 app.js 旧实现)
        if (els.adminRefresh) {
            els.adminRefresh.addEventListener('click', () => {
                const ap = window.AnythingApp && window.AnythingApp._adminPanel;
                if (ap && typeof ap.refreshAdminStatus === 'function') {
                    ap.refreshAdminStatus();
                }
            });
        }

        // Task JJ (#70): 已索引文档列表 + 删除 (Task UUUU #107: 同上走模块)
        if (els.docsRefreshBtn) {
            els.docsRefreshBtn.addEventListener('click', () => {
                const ap = window.AnythingApp && window.AnythingApp._adminPanel;
                if (ap && typeof ap.refreshDocsList === 'function') {
                    ap.refreshDocsList();
                }
            });
        }
    }

    function updateComposerPlaceholder() {
        const key = `composer.placeholder.${state.mode || 'rag'}`;
        els.inputText.placeholder = t(key);
    }

    // Task WWWW-C (#111) + YYYY-G (#113): 根据 mode 显隐 composer 控件.
    //   - 计划 / 反思 toggle: 只在 Agent / Hybrid (任何 agent 工作流) 有效, RAG 隐
    //   - top_k input: RAG 检索参数, Agent 不用, Agent 模式隐
    function _applyModeAwareComposer() {
        const mode = state.mode || 'rag';
        const isAgent = (mode === 'agent' || mode === 'hybrid');
        const isRag = (mode === 'rag' || mode === 'hybrid');  // Hybrid 也用 RAG → top_k 还需要
        const planLabel = document.getElementById('plan-toggle')?.closest('label');
        const reflectLabel = document.getElementById('reflect-toggle')?.closest('label');
        const topkLabel = document.getElementById('topk-input')?.closest('label');
        if (planLabel) planLabel.style.display = isAgent ? '' : 'none';
        if (reflectLabel) reflectLabel.style.display = isAgent ? '' : 'none';
        // YYYY-G: top_k 只在 RAG / Hybrid 显 (Hybrid 也用 RAG retrieve 走 top_k)
        if (topkLabel) topkLabel.style.display = isRag ? '' : 'none';
        // 副作用: 隐时取消勾, 避免切回时旧 state 残留
        if (!isAgent) {
            const plan = document.getElementById('plan-toggle');
            const reflect = document.getElementById('reflect-toggle');
            if (plan && plan.checked) plan.checked = false;
            if (reflect && reflect.checked) reflect.checked = false;
        }
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
        // Task RRRR (#135): per-session 锁 — 当前会话有 inflight 才拒绝, 别的会话不受影响
        const currentSid = state.settings.sessionId;
        if (state.inflight.has(currentSid)) {
            toast('warn', '当前会话有正在进行的请求', '请等待或切换到其他会话');
            return;
        }
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

        // 流式 toggle 真生效: 后端 execution_strategy 默 single_shot 不走真流, 必须
        // 显式注入 react 才会拿到 token-by-token 推送 (streaming.py 注释明确这点).
        // 没勾流式时不注入, 保留 yaml 配置默认.
        const useStreamForReq = !!(els.streamToggle && els.streamToggle.checked);
        if (useStreamForReq && (mode === 'agent' || mode === 'hybrid')) {
            body.extra_params = body.extra_params || {};
            body.extra_params.execution_strategy = 'react';
        }

        // Task KKK (#97) / III (#95): Reflection 开关注入 extra_params.enable_reflection
        // Agent 拿到答案后会跑 critique → revise 二阶段优化
        const reflectMode = !!(els.reflectToggle && els.reflectToggle.checked);
        if (reflectMode) {
            body.extra_params = body.extra_params || {};
            body.extra_params.enable_reflection = true;
        }

        // Task PM-5: 当前 session attach 的 KB → extra_params.kb_id
        if (state.settings.currentKbId) {
            body.extra_params = body.extra_params || {};
            body.extra_params.kb_id = state.settings.currentKbId;
        }

        // Task PM-7-3: per-session system prompt → extra_params.system_prompt
        // (跟 AGENTS.md 项目全局指令叠加, 不替代)
        const sysPrompt = _getSessionSystemPrompt(currentSid);
        if (sysPrompt) {
            body.extra_params = body.extra_params || {};
            body.extra_params.system_prompt = sysPrompt;
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

        state.sending = true;  // 兼容旧引用
        // Task RRRR (#135): 记录 inflight, 捕获 sid (本次发送目标会话)
        const capturedSid = currentSid;
        state.inflight.set(capturedSid, { placeholderId, startedAt: Date.now() });
        // 同时刷左栏 inflight 标记 + 按钮视觉 (per-session)
        _updateSessionInflightUI();
        // Task XXXX-17: 当前 sid 还没进 _sessionsCache (后端 state 还没落盘) →
        // 先乐观加一条到本地 cache, 让左栏立刻显有这个会话; 真名/真时间会在
        // 后续 refreshSessions (response 完成后) 拉到再覆盖.
        _optimisticAddCurrentSession(capturedSid, displayContent);
        state.activePlaceholderId = placeholderId;
        els.inputText.value = '';
        els.inputText.focus();

        try {
            if (useStream) {
                await sendStream(body, placeholderId, { tenant, mode, capturedSid });
            } else {
                await sendOnce(body, placeholderId, { tenant, mode, capturedSid });
            }
        } finally {
            // Task RRRR: 释放 inflight 锁; 视觉由 _updateSessionInflightUI / _updateSendButtonUI 统一处理
            state.inflight.delete(capturedSid);
            _updateSessionInflightUI();
            if (state.settings.sessionId !== capturedSid) {
                // 用户已经切走了, 通知一下
                toast('info', '会话已回复', capturedSid);
            }
            state.sending = false;
            state.activePlaceholderId = null;
            // Task XXXX-17: 响应完, 后端 state 已落盘 → 拉真数据覆盖 optimistic 占位
            // (真 title 由 backend 提取 user 首条消息生成, 比前端裁剪准)
            refreshSessions().catch(() => {});
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
        // Task TTTT-6 (#143): 扫 tool_results_summary 看有没有图片 URL
        const imgs = _collectGeneratedImages(d);
        let baseAnswer = d.answer || JSON.stringify(d, null, 2);
        if (imgs.length > 0) {
            // 把已经在 baseAnswer 里赤裸出现的 URL 删掉 (后端 description 把 url 拼了),
            // 否则会同时显原始 URL 文本 + 图片. 删完再 append markdown 图片让 <img> 渲.
            for (const u of imgs) {
                baseAnswer = baseAnswer.split(u).join('');
            }
            baseAnswer = baseAnswer.replace(/\n{3,}/g, '\n\n').trim();
            return baseAnswer + '\n\n' + imgs.map(u => `![生成图](${u})`).join('\n\n');
        }
        return baseAnswer;
    }

    // Task XXXX-1 (#148): 长任务进度提示
    // 根据相邻 user message 推断要做啥 (画图 / 抓网页 / 等), 给更贴切 hint
    function _loadingHintFor(msg) {
        // 找上一条 user message 看用户问啥
        const idx = state.history.findIndex(m => m.id === msg.id);
        let lastUser = '';
        for (let i = idx - 1; i >= 0; i--) {
            if (state.history[i].role === 'user') {
                lastUser = state.history[i].content || '';
                break;
            }
        }
        const lower = lastUser.toLowerCase();
        if (/画|生成图|draw|generate.*image|image.*of/.test(lower)) return '🎨 正在生成图片… (约 30-60s)';
        if (/抓|爬|截图|browser|screenshot/.test(lower)) return '🌐 正在打开浏览器…';
        if (/搜索|search|查一下/.test(lower)) return '🔍 正在搜索…';
        if (/pdf|读文件|excel|表格/.test(lower)) return '📄 正在读文件…';
        return t('msg.processing');
    }

    let _elapsedTickerId = null;
    function _ensureElapsedTicker() {
        if (_elapsedTickerId) return;
        _elapsedTickerId = setInterval(() => {
            const nodes = document.querySelectorAll('.loading-elapsed');
            if (nodes.length === 0) {
                clearInterval(_elapsedTickerId);
                _elapsedTickerId = null;
                return;
            }
            const now = Date.now();
            nodes.forEach(n => {
                const start = parseInt(n.dataset.started || '0', 10);
                if (!start) return;
                const secs = Math.floor((now - start) / 1000);
                n.textContent = secs + 's';
            });
        }, 1000);
    }

    // Task XXXX-6 (#153): 答案高度 > 600px 时折叠 + 加"展开/收起"按钮
    function _maybeFoldLongMessage(body) {
        // 等下一帧让 img 等加载完, 再量高 (粗略但实用)
        setTimeout(() => {
            if (!body || body.dataset.foldChecked) return;
            body.dataset.foldChecked = '1';
            const h = body.scrollHeight;
            if (h <= 600) return;
            body.classList.add('msg-foldable', 'msg-folded');
            const toggle = document.createElement('button');
            toggle.type = 'button';
            toggle.className = 'msg-fold-toggle';
            const labelFolded = `${t('msg.fold.expand')} (${h}px)`;
            const labelOpen = t('msg.fold.collapse');
            toggle.textContent = labelFolded;
            toggle.addEventListener('click', () => {
                const folded = body.classList.toggle('msg-folded');
                toggle.textContent = folded ? labelFolded : labelOpen;
            });
            body.appendChild(toggle);
        }, 100);
    }

    // Task XXXX-10 (#157): 编辑 user 消息 → 替换 body 为 textarea + save/cancel.
    // 保存 = state.history truncate 到该消息 (含) → inputText 填新内容 → send().
    function _enterEditMode(msg, wrapper, body) {
        if (!msg || msg.role !== 'user') return;
        // 防重入
        if (wrapper.classList.contains('msg-editing')) return;
        wrapper.classList.add('msg-editing');

        const origText = msg.content || '';
        const origHtml = body.innerHTML;

        // 渲编辑控件
        const ta = document.createElement('textarea');
        ta.className = 'msg-edit-textarea';
        ta.value = origText;
        ta.rows = Math.min(8, Math.max(2, origText.split('\n').length + 1));

        const actions = document.createElement('div');
        actions.className = 'msg-edit-actions';
        const saveBtn = document.createElement('button');
        saveBtn.type = 'button';
        saveBtn.className = 'small-btn primary';
        saveBtn.textContent = '💾 保存并重发';
        const cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'small-btn ghost';
        cancelBtn.textContent = '取消';
        actions.appendChild(saveBtn);
        actions.appendChild(cancelBtn);

        body.innerHTML = '';
        body.appendChild(ta);
        body.appendChild(actions);
        ta.focus();
        ta.selectionStart = ta.value.length;

        cancelBtn.addEventListener('click', () => {
            body.innerHTML = origHtml;
            wrapper.classList.remove('msg-editing');
        });
        saveBtn.addEventListener('click', () => {
            const newText = ta.value.trim();
            if (!newText) {
                toast('warn', '内容为空', '取消或填新内容');
                return;
            }
            if (newText === origText) {
                // 没变化 — 视为取消
                body.innerHTML = origHtml;
                wrapper.classList.remove('msg-editing');
                return;
            }
            // 找到这条 user message 在 state.history 的位置
            const idx = state.history.findIndex(m => m.id === msg.id);
            if (idx < 0) {
                toast('error', '找不到原消息', '');
                return;
            }
            // truncate: 砍掉本条 user 消息 + 后面所有 (包括对应的 assistant)
            state.history = state.history.slice(0, idx);
            renderHistory();
            // 填到输入框 + send
            els.inputText.value = newText;
            send();
        });
    }

    // Task XXXX-3 (#150): 给 assistant message body 加单条复制按钮 (右上 hover 显)
    // Task PM-7-7: 多模型 side-by-side 对比 — 同 prompt 跑 2-3 个 model, 平铺答案
    // 实现思路: 拉已注册 CHAT models → 让用户勾 2-3 个 → 平行调 /invoke (非流式) →
    //          每个答案渲成一张并列卡片 (用 assistant 消息插入, mode='compare').
    // 简化: 不走流式 (流式 + 多模型并发, UI 状态难管理), 用 sendOnce 模式.
    async function _startModelCompare(userMsg) {
        if (!userMsg) return;
        // 先列 models
        let models = [];
        try {
            const { payload, status } = await ApiClient.listModels();
            if (status !== 200 || payload?.code !== 'SUCCESS') {
                toast('error', '加载模型失败', payload?.code || status);
                return;
            }
            models = (payload.data?.models || []).filter(
                m => (m.request_type || '').toUpperCase() === 'CHAT' && m.configured
            );
        } catch (e) {
            toast('error', '加载模型失败', e.message);
            return;
        }
        if (models.length < 2) {
            toast('warn', '至少需要 2 个已配置 model', '去 ⚙ 设置 → 高级 → 模型管理 注册');
            return;
        }
        // 弹简易选择 (用 prompt; 后续可换 modal)
        const names = models.map(m => m.name);
        const pick = prompt(
            `选 2-3 个模型对比 (逗号分隔, 名字必须精确):\n可选: ${names.join(', ')}\n默全选前 3 个`,
            names.slice(0, 3).join(', ')
        );
        if (pick === null) return;
        const selected = pick.split(',').map(s => s.trim()).filter(s => names.includes(s));
        if (selected.length < 2) {
            toast('warn', '至少选 2 个 (名字要精确)', '');
            return;
        }
        const userText = userMsg.content || '';
        const mode = userMsg.mode || 'agent';
        // 渲一个 compare card 占位
        const placeholderId = addMessage({
            role: 'assistant', mode: 'compare', content: '',
            compareModels: selected, compareResults: {}, loading: true, ts: Date.now(),
        });
        // 并行调
        const tenant = state.settings.tenant || 'default';
        const tasks = selected.map(modelName =>
            ApiClient.invoke({
                type: mode,
                task: userText,
                top_k: 5,
                tenant_id: tenant,
                extra_params: { model_name: modelName },
            }).then(r => ({ modelName, ok: true, payload: r.payload, status: r.status }))
              .catch(e => ({ modelName, ok: false, error: e.message }))
        );
        const results = await Promise.all(tasks);
        // 更新 placeholder msg
        const msg = state.history.find(m => m.id === placeholderId);
        if (!msg) return;
        msg.loading = false;
        msg.compareResults = {};
        for (const r of results) {
            if (r.ok && r.status === 200 && r.payload?.code === 'SUCCESS') {
                msg.compareResults[r.modelName] = {
                    answer: r.payload.data?.answer || '(无回答)',
                    cost_time: r.payload.cost_time,
                };
            } else {
                msg.compareResults[r.modelName] = {
                    answer: `× ${r.error || r.payload?.code || r.status}: ${r.payload?.message || ''}`,
                    error: true,
                };
            }
        }
        renderHistory();
    }

    // Task PM-7-2: assistant 消息加 🔄 重新生成 — 砍掉本条 + 后续, 用上一条 user msg 重发
    function _attachRegenerateButton(body, msg) {
        if (!body || !msg) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'msg-regen-btn';
        btn.title = '重新生成 (会换一种说法)';
        btn.textContent = '🔄';
        btn.addEventListener('click', () => {
            const idx = state.history.findIndex(m => m.id === msg.id);
            if (idx <= 0) return;
            // 找前一条 user msg
            let userIdx = -1;
            for (let i = idx - 1; i >= 0; i--) {
                if (state.history[i].role === 'user') { userIdx = i; break; }
            }
            if (userIdx < 0) {
                toast('warn', '找不到上一条 user 消息', '');
                return;
            }
            const userMsg = state.history[userIdx];
            // truncate 到 userIdx (含), 然后 input 填用户原话 + send()
            state.history = state.history.slice(0, userIdx);
            renderHistory();
            els.inputText.value = userMsg.content || '';
            send();
        });
        body.appendChild(btn);
    }

    function _attachMessageCopyButton(body, rawText) {
        if (!body || !rawText) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'msg-copy-btn';
        btn.title = '复制本条消息';
        btn.textContent = '📋';
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            try {
                await navigator.clipboard.writeText(rawText);
                btn.textContent = '✓';
                setTimeout(() => { btn.textContent = '📋'; }, 1200);
                toast('success', '已复制', '');
            } catch (err) {
                toast('error', '复制失败', err.message);
            }
        });
        body.style.position = 'relative';  // 让 absolute 按钮挂得住
        body.appendChild(btn);
    }

    // Task PM-2: assistant 消息底部加 👍 / 👎 反馈条. 点 👎 弹评语 textarea.
    // 这是产品迭代的"地基"数据 — 没这个就没法知道哪种回答不好.
    function _attachFeedbackButtons(body, msg) {
        if (!body || !msg) return;
        // 已经反馈过的不重复显 (前端 state, 不查后端)
        if (msg.feedback_submitted) return;
        const bar = document.createElement('div');
        bar.className = 'msg-feedback';
        bar.innerHTML = `
            <span class="msg-feedback-hint">这个回答?</span>
            <button class="msg-fb-btn msg-fb-up" type="button" title="有用">👍</button>
            <button class="msg-fb-btn msg-fb-down" type="button" title="不够好">👎</button>
        `;
        const upBtn = bar.querySelector('.msg-fb-up');
        const downBtn = bar.querySelector('.msg-fb-down');

        async function _submit(rating, comment) {
            const userMsg = (function () {
                // 找上一条 user message 当 preview
                const idx = state.history.findIndex(m => m.id === msg.id);
                for (let i = idx - 1; i >= 0; i--) {
                    if (state.history[i].role === 'user') return state.history[i].content || '';
                }
                return '';
            })();
            const toolsUsed = (msg.data?.steps || [])
                .filter(s => s.success !== false)
                .map(s => s.tool_name)
                .filter(Boolean);
            try {
                const { payload, status } = await ApiClient.submitFeedback({
                    trace_id: msg.meta?.traceId || null,
                    session_id: state.settings.sessionId,
                    tenant_id: state.settings.tenant || 'default',
                    rating,
                    comment: comment || null,
                    user_msg_preview: userMsg,
                    assistant_msg_preview: msg.content || '',
                    mode: msg.mode || null,
                    tools_used: toolsUsed,
                });
                if (status === 200 && payload?.code === 'SUCCESS') {
                    msg.feedback_submitted = rating;
                    bar.innerHTML = rating === 1
                        ? '<span class="msg-feedback-done">✓ 已记录, 谢谢反馈</span>'
                        : '<span class="msg-feedback-done">✓ 收到, 我们会改进</span>';
                } else {
                    toast('error', '反馈提交失败', payload?.message || '');
                }
            } catch (e) {
                toast('error', '反馈提交失败', e.message);
            }
        }

        upBtn.addEventListener('click', () => _submit(1, null));
        downBtn.addEventListener('click', () => {
            // 点 👎 → 在 bar 下方塞 textarea + 提交按钮
            if (bar.querySelector('.msg-fb-comment-wrap')) return;  // 已展开
            const wrap = document.createElement('div');
            wrap.className = 'msg-fb-comment-wrap';
            wrap.innerHTML = `
                <textarea class="msg-fb-comment" rows="2"
                          placeholder="哪里不对? (可选, 帮我们改进)"></textarea>
                <div class="msg-fb-actions">
                    <button class="small-btn primary msg-fb-send" type="button">提交</button>
                    <button class="small-btn ghost msg-fb-skip" type="button">跳过</button>
                </div>
            `;
            bar.appendChild(wrap);
            wrap.querySelector('textarea').focus();
            wrap.querySelector('.msg-fb-send').addEventListener('click', () => {
                const txt = wrap.querySelector('textarea').value.trim();
                _submit(-1, txt);
            });
            wrap.querySelector('.msg-fb-skip').addEventListener('click', () => {
                _submit(-1, null);
            });
        });
        body.appendChild(bar);
    }

    // Task VVVV (#146): 给消息里每张图加 hover 出现的下载按钮
    function _attachImageDownloadButtons(root) {
        if (!root) return;
        root.querySelectorAll('img').forEach(img => {
            if (img.dataset.dlBound) return;
            img.dataset.dlBound = '1';
            // 包一层 wrapper, 容纳绝对定位的按钮
            const wrapper = document.createElement('span');
            wrapper.className = 'md-img-wrapper';
            img.parentNode.insertBefore(wrapper, img);
            wrapper.appendChild(img);
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'md-img-download';
            btn.innerHTML = '⬇ 下载';
            btn.title = '下载到本地';
            btn.addEventListener('click', async (e) => {
                e.preventDefault();
                e.stopPropagation();
                const url = img.src;
                try {
                    btn.disabled = true;
                    btn.textContent = '下载中…';
                    const res = await fetch(url);
                    if (!res.ok) throw new Error('HTTP ' + res.status);
                    const blob = await res.blob();
                    const objUrl = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = objUrl;
                    // 文件名: 从 url 抠最后一段 + 时间戳兜底
                    let fname = (url.split('?')[0].split('/').pop() || '');
                    if (!fname || !/\.(png|jpe?g|webp|gif)$/i.test(fname)) {
                        fname = 'image_' + Date.now() + '.png';
                    }
                    a.download = fname;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(objUrl);
                    toast('success', '已保存', fname);
                } catch (err) {
                    // CORS 失败 → 退回直接打开新 tab 让用户右键保存
                    window.open(url, '_blank', 'noopener');
                    toast('warn', '直接下载失败 (已新开 tab)', err.message);
                } finally {
                    btn.disabled = false;
                    btn.innerHTML = '⬇ 下载';
                }
            });
            wrapper.appendChild(btn);
        });
    }

    // Task TTTT-6: 找出工具返回的图片 URL (image_generate 等)
    function _collectGeneratedImages(d) {
        const urls = new Set();
        const summaries = d.tool_results_summary || [];
        for (const s of summaries) {
            if (!s) continue;
            // Task XXXX-11 (#158): 优先读 backend 提供的结构化 images 字段, 不再 regex 扫
            if (Array.isArray(s.images)) {
                s.images.forEach(u => { if (typeof u === 'string' && u.startsWith('http')) urls.add(u); });
            }
            if (!s.summary) continue;
            const text = String(s.summary);
            // Legacy: 兜底支持老工具在 description 里塞 URL 的格式
            const m = text.match(/https?:\/\/[^\s"'<>]+\.(?:png|jpe?g|webp|gif)(?:\?[^\s"'<>]*)?/gi);
            if (m) m.forEach(u => urls.add(u));
            // 抠 JSON 里 image_url / images 字段
            try {
                if (text.startsWith('{') || text.startsWith('[')) {
                    const obj = JSON.parse(text);
                    const _scan = (o) => {
                        if (!o) return;
                        if (typeof o === 'string' && /^https?:\/\/.*\.(png|jpe?g|webp|gif)/i.test(o)) urls.add(o);
                        if (Array.isArray(o)) o.forEach(_scan);
                        else if (typeof o === 'object') {
                            if (o.image_url) _scan(o.image_url);
                            if (o.images) _scan(o.images);
                            if (o.url && typeof o.url === 'string') _scan(o.url);
                            Object.values(o).forEach(_scan);
                        }
                    };
                    _scan(obj);
                }
            } catch (_) {}
        }
        return Array.from(urls);
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
            // 重新插入欢迎块 + GGGG (#124) 示例 prompt 卡片
            els.messages.innerHTML = `<div class="welcome">
                <h2 data-i18n="welcome.title">欢迎使用 Anything</h2>
                <p data-i18n="welcome.desc">RAG 检索 / Agent 任务执行 / Hybrid 混合, 选择模式后输入开始对话.</p>
                <div class="welcome-examples">
                    <div class="welcome-example-row">
                        <button class="welcome-example" data-mode="agent" data-prompt="现在北京几点">
                            <span class="we-icon">🕒</span><span class="we-text">现在北京几点</span><span class="we-tag">Agent</span>
                        </button>
                        <button class="welcome-example" data-mode="agent" data-prompt="12345 乘以 67890 等于多少">
                            <span class="we-icon">🧮</span><span class="we-text">12345 × 67890 = ?</span><span class="we-tag">Agent</span>
                        </button>
                    </div>
                    <div class="welcome-example-row">
                        <button class="welcome-example" data-mode="agent" data-prompt="北京天气怎么样">
                            <span class="we-icon">🌤</span><span class="we-text">北京天气怎么样</span><span class="we-tag">Agent</span>
                        </button>
                        <button class="welcome-example" data-mode="agent" data-prompt="维基百科查一下 Python 编程语言">
                            <span class="we-icon">📖</span><span class="we-text">查 Python 维基</span><span class="we-tag">Agent</span>
                        </button>
                    </div>
                    <div class="welcome-example-row">
                        <button class="welcome-example" data-mode="rag" data-prompt="什么是 RAG">
                            <span class="we-icon">🔍</span><span class="we-text">什么是 RAG</span><span class="we-tag">RAG</span>
                        </button>
                        <button class="welcome-example" data-mode="hybrid" data-prompt="项目里 tenant 是怎么设计的">
                            <span class="we-icon">🧩</span><span class="we-text">tenant 是怎么设计的</span><span class="we-tag">Hybrid</span>
                        </button>
                    </div>
                </div>
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
                // Task IIII (#126): trace chip 默认淡, hover/聚焦 message 时才显
                tr.className = 'chip chip-trace';
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

        // Task PM-7-7: compare 卡片 — 多 model 答案并列
        if (msg.mode === 'compare') {
            const cards = (msg.compareModels || []).map(name => {
                const r = (msg.compareResults || {})[name];
                if (!r) return `<div class="compare-card pending">
                    <div class="compare-card-head"><strong>${escapeHtml(name)}</strong> <span class="hint">运行中…</span></div>
                </div>`;
                const cls = r.error ? 'compare-card error' : 'compare-card';
                const ans = window.Markdown ? window.Markdown.render(r.answer || '') : escapeHtml(r.answer || '');
                const cost = r.cost_time ? ` <span class="hint">${r.cost_time}s</span>` : '';
                return `<div class="${cls}">
                    <div class="compare-card-head"><strong>${escapeHtml(name)}</strong>${cost}</div>
                    <div class="compare-card-body">${ans}</div>
                </div>`;
            }).join('');
            body.innerHTML = `<div class="compare-grid">${cards}</div>`;
            content.appendChild(body);
            wrapper.appendChild(avatar);
            wrapper.appendChild(content);
            return wrapper;
        }

        if (msg.loading) {
            // Task XXXX-1 (#148): 长任务显 elapsed 秒数, 避免卡死错觉
            const startTs = msg.loadingStartedAt || Date.now();
            msg.loadingStartedAt = startTs;
            const hint = _loadingHintFor(msg);  // 检测输入推断"图片生成"等
            body.innerHTML =
                '<span class="message-loading">' +
                '<span class="dot"></span><span class="dot"></span><span class="dot"></span> ' +
                Markdown.escapeHtml(hint) +
                ' <span class="loading-elapsed" data-started="' + startTs + '">0s</span></span>';
            _ensureElapsedTicker();
        } else if (msg.role === 'assistant' && !msg.error && window.Markdown) {
            // 助手成功响应走 markdown 渲染 (用户消息保持 plain text 防 XSS)
            body.innerHTML = window.Markdown.render(msg.content || '');
            window.Markdown.bindCopyButtons(body);
            // Task VVVV (#146): 给每张 <img> 包 wrapper + 下载按钮
            _attachImageDownloadButtons(body);
            // Task XXXX-3 (#150): 加单条复制按钮
            _attachMessageCopyButton(body, msg.content || '');
            // Task PM-7-2: 重新生成按钮 (非 error 都加)
            if (!msg.error) {
                _attachRegenerateButton(body, msg);
            }
            // Task PM-2: 加 👍/👎 反馈按钮 (assistant message + 非 error 才显)
            if (!msg.error) {
                _attachFeedbackButtons(body, msg);
            }
            // Task XXXX-6 (#153): 长答案折叠 — 渲完后 next tick 量高度
            _maybeFoldLongMessage(body);
        } else {
            body.textContent = msg.content || '';
        }
        content.appendChild(body);

        // Task XXXX-10 (#157): user 消息加 ✏ 编辑按钮 — 编辑后从该消息处 rewind + 重发
        if (msg.role === 'user' && !msg.loading) {
            const editBtn = document.createElement('button');
            editBtn.type = 'button';
            editBtn.className = 'msg-edit-btn';
            editBtn.title = '编辑并重发';
            editBtn.textContent = '✏';
            editBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                _enterEditMode(msg, wrapper, body);
            });
            wrapper.appendChild(editBtn);  // 浮在右上 (CSS absolute)

            // Task PM-7-7: 对比按钮 — 让多个 model 跑同 prompt 比答案
            const compareBtn = document.createElement('button');
            compareBtn.type = 'button';
            compareBtn.className = 'msg-compare-btn';
            compareBtn.title = '让 2-3 个模型同时跑这条 prompt, 比答案';
            compareBtn.textContent = '⚖';
            compareBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                _startModelCompare(msg);
            });
            wrapper.appendChild(compareBtn);
        }

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

    // ---------- 侧栏:指标 (Task WWWW-A #109: 移到 admin 卡内, 但函数保留) ----------
    async function loadMetrics() {
        // 重新拿 textarea — admin 卡渲染后 ID 仍然是 #metrics-text
        const txtEl = document.getElementById('metrics-text');
        if (!txtEl) return;
        txtEl.textContent = '加载中...';
        try {
            const { payload, status } = await ApiClient.metrics();
            if (status === 200 && typeof payload === 'string') {
                txtEl.textContent = payload || '(空)';
            } else {
                txtEl.textContent = `加载失败 HTTP ${status}\n${payload}`;
            }
        } catch (e) {
            txtEl.textContent = '加载失败: ' + e.message;
        }
    }
    // WWWW-A: 让 admin-panel.js 渲染后能找到 loadMetrics 绑给新生成的 button
    window.__loadMetrics = loadMetrics;

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

    // ---------- 侧栏:Admin/Docs (Task S / Task JJ) ----------
    // Task UUUU (#107): refreshDocsList / refreshAdminStatus / renderAdminStatus
    // 已迁出到 modules/admin-panel.js. button click 委托到
    // window.AnythingApp._adminPanel (见 init / bindEvents).
    // 这里只保留占位注释,避免后续误以为函数被删丢了实现.

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

    // ---------- Task RRRR (#135): per-session inflight UI ----------
    function _updateSessionInflightUI() {
        if (!els.sessionList) return;
        const inflightSids = new Set(state.inflight.keys());
        els.sessionList.querySelectorAll('.session-item').forEach(item => {
            const sid = item.dataset.sid;
            item.classList.toggle('inflight', inflightSids.has(sid));
        });
        // 当前 session 的 send 按钮视觉也跟着切 — 不同 session 是独立状态
        _updateSendButtonUI();
    }

    // Task RRRR-2: send 按钮视觉是 per-current-session 的, 不是全局
    function _updateSendButtonUI() {
        if (!els.sendBtn) return;
        const currentSid = state.settings.sessionId;
        const isInflight = state.inflight.has(currentSid);
        const sendLabel = els.sendBtn.querySelector('.send-label');
        const sendIcon = els.sendBtn.querySelector('.send-icon');
        if (isInflight) {
            els.sendBtn.classList.add('sending');
            if (sendLabel) sendLabel.textContent = t('composer.stop');
            if (sendIcon) sendIcon.textContent = '⏹';
        } else {
            els.sendBtn.classList.remove('sending');
            if (sendLabel) sendLabel.textContent = t('composer.send');
            if (sendIcon) sendIcon.textContent = '↵';
        }
    }

    // ---------- Task TTTT-2 (#139) + XXXX-9 (#156): Workflow 模板 (IndexedDB mirror) ----------
    const _WORKFLOWS_KEY = 'anything_workflows';
    // XXXX-9: IDB mirror — 同步 API + 后台异步刷盘. 初始化在 init() 完成后赋值.
    let _idbMirror = null;

    async function _initWorkflowsStore() {
        if (!window.IdbStore) return;  // idb-store.js 没加载就降级
        _idbMirror = await IdbStore.openMirror('anything', 'kv', [_WORKFLOWS_KEY]);
        // 一次性迁移: 老 localStorage 有数据但 IDB 没有 → 搬过去
        try {
            const idbVal = _idbMirror.get(_WORKFLOWS_KEY);
            const lsRaw = localStorage.getItem(_WORKFLOWS_KEY);
            if ((!idbVal || !idbVal.length) && lsRaw) {
                const parsed = JSON.parse(lsRaw);
                if (Array.isArray(parsed) && parsed.length) {
                    _idbMirror.set(_WORKFLOWS_KEY, parsed);
                    console.info('[workflows] migrated', parsed.length, 'items from localStorage to IndexedDB');
                }
            }
        } catch (_) {}
        // 渲一次 count, 让 header 数字正确
        const list = _loadWorkflows();
        const countEl = document.getElementById('workflow-count');
        if (countEl) countEl.textContent = String(list.length);
    }

    function _loadWorkflows() {
        // 优先 IDB mirror; 没初始化好就 fallback localStorage
        if (_idbMirror) {
            const v = _idbMirror.get(_WORKFLOWS_KEY);
            return Array.isArray(v) ? v : [];
        }
        try {
            const raw = localStorage.getItem(_WORKFLOWS_KEY);
            return raw ? JSON.parse(raw) : [];
        } catch (_) { return []; }
    }
    function _saveWorkflows(list) {
        if (_idbMirror) {
            _idbMirror.set(_WORKFLOWS_KEY, list);
        } else {
            // mirror 没就绪时同时写 localStorage 当兜底
            try { localStorage.setItem(_WORKFLOWS_KEY, JSON.stringify(list)); } catch (_) {}
        }
        const countEl = document.getElementById('workflow-count');
        if (countEl) countEl.textContent = String(list.length);
    }
    function _refreshWorkflowsList() {
        const ul = document.getElementById('workflows-list');
        if (!ul) return;
        const list = _loadWorkflows();
        if (!list.length) {
            ul.innerHTML = '<li class="empty-state">还没保存任何模板. 在输入框写内容, 点 "💾 存为模板" 创建.</li>';
            return;
        }
        ul.innerHTML = list.map((w, i) => `
            <li class="workflow-item" data-idx="${i}">
                <div class="workflow-main">
                    <span class="workflow-name">${escapeHtml(w.name || ('模板 ' + (i+1)))}</span>
                    <span class="workflow-tag chip">${escapeHtml(w.mode || 'rag')}</span>
                    <div class="workflow-preview">${escapeHtml((w.text || '').slice(0, 80))}</div>
                </div>
                <button class="icon-btn workflow-delete" data-idx="${i}" title="删除">✕</button>
            </li>
        `).join('');
    }
    function _saveCurrentAsWorkflow() {
        const text = (els.inputText.value || '').trim();
        if (!text) {
            toast('warn', '输入框为空', '先写点啥再保存');
            return;
        }
        const name = (prompt('给这个模板起个名字:', text.slice(0, 30)) || '').trim();
        if (!name) return;  // 用户取消
        const list = _loadWorkflows();
        list.unshift({
            name,
            text,
            mode: state.mode,
            saved_at: Date.now(),
        });
        _saveWorkflows(list.slice(0, 50));  // 上限 50
        _refreshWorkflowsList();
        toast('success', '模板已保存', name);
    }

    // ---------- Task NNNN (#131): 主题切换 ----------
    function _applyTheme(theme) {
        let actual = theme;
        if (theme === 'auto') {
            actual = window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
        }
        document.documentElement.dataset.theme = actual;
        // 同步按钮 active 状态
        document.querySelectorAll('.theme-opt').forEach(b => {
            b.classList.toggle('active', b.dataset.theme === theme);
        });
    }

    // ---------- Task FFFF (#123) + HHHH (#125): Agent 工具列表 (可点击预填 demo) ----------
    // 工具名 → 示例 task 映射, 点工具卡时自动填到输入框, 切 Agent 模式
    const _AGENT_TOOL_EXAMPLES = {
        calculator:       '12345 乘以 67890 等于多少',
        currency_convert: '100 美元换多少人民币',
        datetime:         '现在北京几点',
        weather:          '北京天气怎么样',
        email_send:       '发个测试邮件到 demo@example.com',
        rag_search:       '查项目里 tenant 是怎么设计的',
        wikipedia:        '维基百科查一下 Python 编程语言',
        web_search:       '搜一下 OpenAI o1 模型',
        http_get:         '抓取 https://example.com 的首页',
        http_request:     '抓取 https://example.com 的首页',
        document_read:    '读一下文档 doc_xxx 的全文',
        file_write:       '把 "Hello World" 写到 /tmp/test.txt',
        pdf_read:         '读 uploads/some.pdf 第 1-3 页',
        excel_read:       '读 uploads/data.xlsx 第一个 sheet 的前 50 行',
        sql_query:        '从 demo 表查所有 country=CN 的 users',
        browser_visit:    '用浏览器抓 https://news.ycombinator.com 首页文字',
        shell_exec:       '执行 ls /tmp',
        py_sandbox:       '运行 Python: print(sum(range(100)))',
        llm_generate:     '写一首关于秋天的五言诗',
        image_describe:   '描述一下上传的图片',
        image_generate:   '生成一张图: 一只橘猫在窗台上, 阳光透过百叶窗, 水彩风格',
        spawn_subagent:   '派一个 subagent 总结 README.md',
        regex_extract:    '从 "phone: 13800138000" 抽出手机号',
        text_stats:       '统计这段文字的字数: "The quick brown fox"',
        json_query:       '从 {"users":[{"name":"a"},{"name":"b"}]} 抽 users[*].name',
        code_lint:        '检查这段 Python: x=1+',
    };

    async function _loadAgentTools() {
        const grid = document.getElementById('agent-tools-grid');
        if (!grid) return;
        try {
            const r = await fetch('/agent/tools');
            const j = await r.json();
            if (r.status !== 200 || j?.code !== 'SUCCESS') {
                grid.innerHTML = `<div class="hint">加载失败: ${j?.code || r.status}</div>`;
                return;
            }
            const by_category = j.data?.by_category || {};
            const cats = Object.keys(by_category).sort();
            if (cats.length === 0) {
                grid.innerHTML = '<div class="hint">没有注册任何工具</div>';
                return;
            }
            const cat_label = {
                knowledge: '🔍 信息检索', compute: '🧮 计算/时间', text: '📝 文本',
                file: '📄 文件', system: '💻 系统', llm: '🤖 LLM/Agent',
                external: '🌐 外部 API', other: '🧰 其他',
            };
            const html = cats.map(cat => {
                const items = (by_category[cat] || []).map(t => {
                    const example = _AGENT_TOOL_EXAMPLES[t.name] || '';
                    const clickable = example ? ' clickable' : '';
                    const hint = example ? `点击试: "${example.slice(0, 30)}…"` : (t.description || '');
                    return `<div class="agent-tool-card${clickable}"
                              data-tool="${escapeHtml(t.name)}"
                              data-example="${escapeHtml(example)}"
                              title="${escapeHtml(hint)}">
                        <code>${escapeHtml(t.name)}</code>
                        <div class="agent-tool-desc">${escapeHtml((t.description || '').slice(0, 100))}</div>
                    </div>`;
                }).join('');
                return `<div class="agent-tools-cat">
                    <h5>${cat_label[cat] || cat}</h5>
                    ${items}
                </div>`;
            }).join('');
            grid.innerHTML = html;
            // Task PM-4: 同时把 tool 列表存到 module-scope 给 slash command palette 用
            _allAgentTools = (j.data?.tools || []).slice();
        } catch (e) {
            grid.innerHTML = `<div class="hint">加载异常: ${escapeHtml(e.message)}</div>`;
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
        // Task AAAA-A (#118): 检查 LLM 模型健康, 全挂 → 红 banner
        try {
            const adminRes = await ApiClient.getAdminStatus();
            if (adminRes.status === 200 && adminRes.payload?.code === 'SUCCESS') {
                const d = adminRes.payload?.data || {};
                const models = (d.health || {}).models || {};
                const registered = (d.llm_models || {}).count || 0;
                _renderLLMBanner(registered, models);
            }
        } catch (_) {}
    }

    // Task AAAA-A (#118): 全挂时显红 banner; 一旦有 healthy model 则移除
    function _renderLLMBanner(registeredCount, modelHealthMap) {
        const existing = document.getElementById('llm-banner');
        const names = Object.keys(modelHealthMap || {});
        const unhealthy = names.filter(n => modelHealthMap[n].state === 'unhealthy');
        // 警告条件: 一个模型都没注册 OR 注册了但所有曾被调用过的都 unhealthy
        const showWarning = registeredCount === 0 ||
                            (names.length > 0 && unhealthy.length === names.length);
        if (!showWarning) {
            if (existing) existing.remove();
            return;
        }
        if (existing) return;
        const banner = document.createElement('div');
        banner.id = 'llm-banner';
        banner.className = 'llm-banner';
        const errSample = unhealthy.length > 0
            ? (modelHealthMap[unhealthy[0]]?.last_error || '').slice(0, 100)
            : (registeredCount === 0 ? '没有注册任何 LLM 模型' : '');
        banner.innerHTML = `
            <span class="llm-banner-icon">⚠️</span>
            <span class="llm-banner-text">
                <strong>LLM 不可用</strong> — 所有回答都是 stub 占位.
                ${errSample ? '<br/><small>原因: ' + escapeHtml(errSample) + '…</small>' : ''}
                请配 <code>DASHSCOPE_API_KEY</code> 或 <code>OPENAI_API_KEY</code> 然后重启服务.
            </span>
            <button class="llm-banner-close" aria-label="关闭" title="关闭">✕</button>
        `;
        banner.querySelector('.llm-banner-close').addEventListener('click', () => banner.remove());
        document.body.appendChild(banner);
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
