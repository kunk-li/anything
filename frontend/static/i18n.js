// ============================================================
// Anything Frontend — 极简 i18n (中英双语)
//
// 用法:
//   t('chat.send')              -> '发送' (zh) / 'Send' (en)
//   t('chat.error', {code: 'X'})-> 占位符替换
//   setLocale('en')             -> 切换语言, 触发 data-i18n 重渲染
//   getLocale()                 -> 当前语言代码
//
// 标记规则:
//   HTML 元素加 data-i18n="key" 自动翻译 textContent
//   data-i18n-attr="placeholder:key,title:key2" 翻译属性
// ============================================================

const I18n = (() => {
    const STORAGE_KEY = 'anything_locale';

    const dict = {
        zh: {
            // header
            'app.title': 'Anything',
            'app.subtitle': 'RAG & Agent',
            'header.tenant': 'tenant',
            'header.health.checking': '检测中',
            'header.health.up': 'UP',
            'header.health.down': 'DOWN',
            'header.settings': '设置',
            'header.lang': '语言',

            // welcome
            'welcome.title': '欢迎使用 Anything',
            'welcome.desc': 'RAG 检索 / Agent 任务执行 / Hybrid 混合,选择模式后输入开始对话。',
            'welcome.hint.rag': '<b>RAG</b> 模式适合"在文档里找答案" — 输入问题,返回引用的 chunks',
            'welcome.hint.agent': '<b>Agent</b> 模式适合"调工具完成任务" — 输入任务,返回执行步骤 + 答案',
            'welcome.hint.hybrid': '<b>Hybrid</b> 模式综合两者 — 检索 + 推理',

            // composer
            'composer.send': '发送',
            'composer.topk': 'top_k',
            'composer.placeholder.rag': '输入问题, 在已索引文档里检索答案... (Ctrl+Enter 发送)',
            'composer.placeholder.agent': '描述一个任务, Agent 会拆解为工具调用步骤... (Ctrl+Enter 发送)',
            'composer.placeholder.hybrid': '同时使用检索与推理 (任务描述)... (Ctrl+Enter 发送)',

            // sidebar
            'sidebar.retrieved': '检索结果',
            'sidebar.steps': '工具调用',
            'sidebar.metrics': '系统指标',
            'sidebar.upload': '文档',
            'sidebar.retrieved.empty': '最近一次 RAG 检索的 chunks 会显示在这里',
            'sidebar.steps.empty': '最近一次 Agent 任务的工具调用步骤会显示在这里',
            'sidebar.metrics.refresh': '刷新',
            'sidebar.metrics.hint': '来自 /metrics (Prometheus 文本)',
            'sidebar.metrics.placeholder': '点击刷新查看实时指标',
            'sidebar.upload.label': '点击或拖拽文件到此上传',
            'sidebar.upload.hint': '支持 md / txt / pdf / docx',
            'sidebar.upload.build': '触发索引构建',

            // settings drawer
            'settings.title': '设置',
            'settings.apiBase': 'API Base URL',
            'settings.apiBase.placeholder': '同域(留空)',
            'settings.apiBase.hint': '留空 = 使用当前页面所在域名,生产部署时配置网关地址',
            'settings.apiKey': 'X-API-Key',
            'settings.apiKey.placeholder': '可选,认证时需要',
            'settings.apiKey.hint': '配置 security.auth_enabled=true 时必填',
            'settings.session': 'Session ID',
            'settings.session.placeholder': '自动生成',
            'settings.session.hint': '用于跨请求关联,留空自动生成',
            'settings.locale': '界面语言',
            'settings.save': '保存',
            'settings.clear': '清空对话历史',

            // toast
            'toast.settings.saved': '设置已保存',
            'toast.settings.saved.body': '同时已应用到下次请求',
            'toast.input.empty': '输入为空',
            'toast.input.empty.body': '请输入内容后再发送',
            'toast.network.error': '网络异常',
            'toast.copied.trace': '已复制 trace_id',
            'toast.copied.error': '已复制错误详情',
            'toast.history.cleared': '对话已清空',
            'toast.upload.success': '文件已上传',
            'toast.upload.fail': '上传失败',
            'toast.upload.exception': '上传异常',
            'toast.index.triggered': '索引构建已触发',

            // role / misc
            'role.user': '你',
            'role.assistant': 'Anything',
            'msg.processing': '正在处理...',
            'msg.empty': '(无响应)',
            'action.retry': '↻ 重试',
            'action.copyResp': '复制响应',
            'confirm.clearHistory': '确定清空所有对话历史?',
            'health.checkAgain.title': '点击重新检测',

            // preview
            'preview.title': '文档预览',
            'preview.loading': '加载预览中...',
            'preview.error': '加载预览失败',
            'preview.totalChars': '总长度',
            'preview.range': '高亮范围',
            'preview.viewOriginal': '查看原文',
        },
        en: {
            'app.title': 'Anything',
            'app.subtitle': 'RAG & Agent',
            'header.tenant': 'tenant',
            'header.health.checking': 'Checking',
            'header.health.up': 'UP',
            'header.health.down': 'DOWN',
            'header.settings': 'Settings',
            'header.lang': 'Language',

            'welcome.title': 'Welcome to Anything',
            'welcome.desc': 'RAG retrieval / Agent task execution / Hybrid — pick a mode and start typing.',
            'welcome.hint.rag': '<b>RAG</b> mode answers questions from indexed docs — returns cited chunks',
            'welcome.hint.agent': '<b>Agent</b> mode calls tools to complete a task — returns steps + answer',
            'welcome.hint.hybrid': '<b>Hybrid</b> combines both — retrieval + reasoning',

            'composer.send': 'Send',
            'composer.topk': 'top_k',
            'composer.placeholder.rag': 'Ask a question; will search indexed docs... (Ctrl+Enter to send)',
            'composer.placeholder.agent': 'Describe a task; agent breaks it into tool calls... (Ctrl+Enter to send)',
            'composer.placeholder.hybrid': 'Task description with both retrieval and reasoning... (Ctrl+Enter to send)',

            'sidebar.retrieved': 'Chunks',
            'sidebar.steps': 'Tool calls',
            'sidebar.metrics': 'Metrics',
            'sidebar.upload': 'Docs',
            'sidebar.retrieved.empty': 'Recent RAG retrieved chunks will appear here',
            'sidebar.steps.empty': 'Recent Agent tool-call steps will appear here',
            'sidebar.metrics.refresh': 'Refresh',
            'sidebar.metrics.hint': 'From /metrics (Prometheus text)',
            'sidebar.metrics.placeholder': 'Click refresh to view live metrics',
            'sidebar.upload.label': 'Click or drag a file here',
            'sidebar.upload.hint': 'Supports md / txt / pdf / docx',
            'sidebar.upload.build': 'Trigger index build',

            'settings.title': 'Settings',
            'settings.apiBase': 'API Base URL',
            'settings.apiBase.placeholder': 'Same origin (leave empty)',
            'settings.apiBase.hint': 'Empty = current page origin; set this for cross-origin gateway',
            'settings.apiKey': 'X-API-Key',
            'settings.apiKey.placeholder': 'Optional, required when auth enabled',
            'settings.apiKey.hint': 'Required when security.auth_enabled=true',
            'settings.session': 'Session ID',
            'settings.session.placeholder': 'Auto-generated',
            'settings.session.hint': 'Used for cross-request correlation; empty = auto',
            'settings.locale': 'UI Language',
            'settings.save': 'Save',
            'settings.clear': 'Clear chat history',

            'toast.settings.saved': 'Settings saved',
            'toast.settings.saved.body': 'Applied to next request',
            'toast.input.empty': 'Empty input',
            'toast.input.empty.body': 'Please type something first',
            'toast.network.error': 'Network error',
            'toast.copied.trace': 'trace_id copied',
            'toast.copied.error': 'Error details copied',
            'toast.history.cleared': 'Chat history cleared',
            'toast.upload.success': 'File uploaded',
            'toast.upload.fail': 'Upload failed',
            'toast.upload.exception': 'Upload exception',
            'toast.index.triggered': 'Index build triggered',

            'role.user': 'You',
            'role.assistant': 'Anything',
            'msg.processing': 'Processing...',
            'msg.empty': '(no response)',
            'action.retry': '↻ Retry',
            'action.copyResp': 'Copy response',
            'confirm.clearHistory': 'Clear all chat history?',
            'health.checkAgain.title': 'Click to re-check',

            'preview.title': 'Document preview',
            'preview.loading': 'Loading preview...',
            'preview.error': 'Failed to load preview',
            'preview.totalChars': 'Total chars',
            'preview.range': 'Highlight range',
            'preview.viewOriginal': 'View source',
        },
    };

    let current = 'zh';
    const subscribers = [];

    function detect() {
        try {
            const saved = localStorage.getItem(STORAGE_KEY);
            if (saved && dict[saved]) return saved;
        } catch (_) {}
        const navLang = (navigator.language || navigator.userLanguage || '').toLowerCase();
        if (navLang.startsWith('en')) return 'en';
        return 'zh';
    }

    function t(key, params) {
        const tbl = dict[current] || dict.zh;
        let s = tbl[key] != null ? tbl[key] : (dict.zh[key] != null ? dict.zh[key] : key);
        if (params) {
            Object.keys(params).forEach((k) => {
                s = s.replace(new RegExp('\\{' + k + '\\}', 'g'), String(params[k]));
            });
        }
        return s;
    }

    function setLocale(loc) {
        if (!dict[loc]) return;
        current = loc;
        try { localStorage.setItem(STORAGE_KEY, loc); } catch (_) {}
        applyToDom();
        subscribers.forEach((fn) => { try { fn(loc); } catch (_) {} });
    }

    function getLocale() { return current; }
    function locales() { return Object.keys(dict); }

    function onChange(fn) { subscribers.push(fn); }

    /**
     * 扫描 DOM 上的 data-i18n / data-i18n-attr 节点并应用翻译。
     * 在 setLocale 时自动调用; 也可手工触发 (动态注入节点后)。
     */
    function applyToDom(root) {
        const scope = root || document;
        scope.querySelectorAll('[data-i18n]').forEach((el) => {
            const key = el.getAttribute('data-i18n');
            // 允许内含 HTML (welcome hints 有 <b>)
            el.innerHTML = t(key);
        });
        scope.querySelectorAll('[data-i18n-attr]').forEach((el) => {
            const spec = el.getAttribute('data-i18n-attr') || '';
            spec.split(',').forEach((pair) => {
                const [attr, key] = pair.split(':').map((s) => (s || '').trim());
                if (attr && key) el.setAttribute(attr, t(key));
            });
        });
        // 文档语言属性
        document.documentElement.setAttribute('lang', current === 'en' ? 'en' : 'zh-CN');
    }

    // 初始化
    current = detect();

    return { t, setLocale, getLocale, locales, onChange, applyToDom };
})();
