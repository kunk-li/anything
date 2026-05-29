// ============================================================
// Anything Frontend — API Client
//
// 纯 fetch 封装,跟后端 ApiService (application/api_service_module) 对齐:
//   POST   /invoke                   主入口 (RAG / Agent / Hybrid)
//   GET    /health                   完整健康检查
//   GET    /healthz                  轻量探活 (k8s liveness 用)
//   GET    /metrics                  Prometheus 文本
//   POST   /documents/upload         文件上传
//   POST   /index/build              触发索引构建
//   GET    /index/job/{job_id}       查询任务状态
//
// 错误信封 (ResponseEnvelope): {code, message, data, trace_id, retryable, details}
// SUCCESS / PARAM_MISSING / PARAM_INVALID / AUTH_REQUIRED / TENANT_NOT_FOUND
// API_RATE_LIMITED / QUOTA_* / ...
// ============================================================

const ApiClient = (() => {
    const settings = {
        baseUrl: '',           // '' = 同源
        apiKey: '',            // X-API-Key
        sessionId: '',         // 透传
    };

    function _url(path) {
        return (settings.baseUrl || '').replace(/\/$/, '') + path;
    }

    function _headers(extra) {
        const h = { 'Content-Type': 'application/json', ...(extra || {}) };
        if (settings.apiKey) h['X-API-Key'] = settings.apiKey;
        return h;
    }

    async function _doFetch(method, path, opts = {}) {
        const init = {
            method,
            headers: _headers(opts.headers),
        };
        if (opts.body !== undefined) {
            init.body = opts.isJson === false ? opts.body : JSON.stringify(opts.body);
            if (opts.isJson === false) delete init.headers['Content-Type'];
        }
        const resp = await fetch(_url(path), init);
        const traceId = resp.headers.get('X-Request-Id') || '';
        const costTime = resp.headers.get('X-Cost-Time') || '';

        let payload = null;
        const ct = resp.headers.get('content-type') || '';
        if (opts.rawText) {
            payload = await resp.text();
        } else if (ct.includes('application/json')) {
            try { payload = await resp.json(); } catch { payload = null; }
        } else {
            payload = await resp.text();
        }
        return { status: resp.status, payload, traceId, costTime, ok: resp.ok };
    }

    /**
     * 主入口: POST /invoke
     * @param {Object} body — 至少包含 type 与对应 query/task
     * @returns {Promise<{status, payload, traceId, costTime, ok}>}
     */
    async function invoke(body) {
        const finalBody = { ...body };
        // 自动注入 session_id (若用户设置过)
        if (settings.sessionId && !finalBody.session_id) {
            finalBody.session_id = settings.sessionId;
        }
        return _doFetch('POST', '/invoke', { body: finalBody });
    }

    async function health() {
        return _doFetch('GET', '/health');
    }

    async function healthz() {
        return _doFetch('GET', '/healthz');
    }

    async function metrics() {
        return _doFetch('GET', '/metrics', { rawText: true });
    }

    /**
     * 文件上传: POST /documents/upload (multipart/form-data)
     */
    async function uploadDocument(file) {
        const formData = new FormData();
        formData.append('file', file);
        return _doFetch('POST', '/documents/upload', {
            body: formData,
            isJson: false,
        });
    }

    async function buildIndex() {
        return _doFetch('POST', '/index/build', { body: {} });
    }

    async function getIndexJob(jobId) {
        return _doFetch('GET', `/index/job/${encodeURIComponent(jobId)}`);
    }

    // ==================== Admin / 状态 (Task S) ====================
    /**
     * GET /admin/status — 拿当前运行期状态:
     *   { rag: {enable_hybrid_search, top_k_retrieve, ...},
     *     bm25: {size, avg_doc_len},
     *     vector_db: {ntotal},
     *     llm_models: {count, by_type},
     *     uploads: {count, dir},
     *     security: {auth_enabled, registered_tenants} }
     */
    async function getAdminStatus() {
        return _doFetch('GET', '/admin/status');
    }

    // ==================== 模型配置 ====================
    /**
     * GET /config/models — 拿当前注册的所有 LLM 模型 (api_key 已脱敏)
     */
    async function listModels() {
        return _doFetch('GET', '/config/models');
    }

    /**
     * POST /config/models — 注册或更新一个模型
     * body 必填: name / request_type / adapter_class
     *     可选: api_key / api_base / set_as_default
     */
    async function registerModel(model) {
        return _doFetch('POST', '/config/models', { body: model });
    }

    /**
     * DELETE /config/models/{name}
     */
    async function deleteModel(name) {
        return _doFetch('DELETE', `/config/models/${encodeURIComponent(name)}`);
    }

    /**
     * POST /config/models/{name}/set-default — 设为对应 request_type 的默认
     */
    async function setDefaultModel(name, requestType) {
        return _doFetch('POST', `/config/models/${encodeURIComponent(name)}/set-default`, {
            body: { request_type: requestType || '' },
        });
    }

    // ==================== 文档管理 (Task JJ #70) ====================
    /**
     * GET /documents — 列出当前 tenant 的所有已索引文档
     */
    async function listDocuments() {
        return _doFetch('GET', '/documents');
    }

    /**
     * DELETE /documents/{doc_id} — 删除文档 + 关联向量
     */
    async function deleteDocument(docId) {
        return _doFetch('DELETE', `/documents/${encodeURIComponent(docId)}`);
    }

    /**
     * 文档预览: GET /documents/{doc_id}/preview
     * @param {string} docId
     * @param {Object} opts {start_char, end_char, context, tenant_id?}
     */
    async function getDocumentPreview(docId, opts = {}) {
        const params = new URLSearchParams();
        if (opts.start_char != null) params.set('start_char', opts.start_char);
        if (opts.end_char != null) params.set('end_char', opts.end_char);
        if (opts.context != null) params.set('context', opts.context);
        if (opts.tenant_id) params.set('tenant_id', opts.tenant_id);
        const qs = params.toString();
        const path = `/documents/${encodeURIComponent(docId)}/preview${qs ? '?' + qs : ''}`;
        return _doFetch('GET', path);
    }

    function configure(opts = {}) {
        if (opts.baseUrl !== undefined) settings.baseUrl = opts.baseUrl;
        if (opts.apiKey !== undefined) settings.apiKey = opts.apiKey;
        if (opts.sessionId !== undefined) settings.sessionId = opts.sessionId;
    }

    // ============== Task GGG (#93) — Long-term Memory APIs ==============

    /**
     * GET /memory/list — 列 facts (?tenant_id, ?tags, ?limit, ?offset)
     */
    async function listMemory(opts = {}) {
        const params = new URLSearchParams();
        if (opts.tenant_id) params.set('tenant_id', opts.tenant_id);
        if (opts.tags) params.set('tags', Array.isArray(opts.tags) ? opts.tags.join(',') : opts.tags);
        if (opts.limit != null) params.set('limit', opts.limit);
        if (opts.offset != null) params.set('offset', opts.offset);
        const qs = params.toString();
        return _doFetch('GET', `/memory/list${qs ? '?' + qs : ''}`);
    }

    /**
     * GET /memory/{fact_id} — 单条 fact 详情
     */
    async function getMemoryFact(factId, opts = {}) {
        const params = new URLSearchParams();
        if (opts.tenant_id) params.set('tenant_id', opts.tenant_id);
        const qs = params.toString();
        return _doFetch('GET', `/memory/${encodeURIComponent(factId)}${qs ? '?' + qs : ''}`);
    }

    /**
     * DELETE /memory/{fact_id} — 硬删 fact
     */
    async function deleteMemoryFact(factId, opts = {}) {
        const params = new URLSearchParams();
        if (opts.tenant_id) params.set('tenant_id', opts.tenant_id);
        const qs = params.toString();
        return _doFetch('DELETE', `/memory/${encodeURIComponent(factId)}${qs ? '?' + qs : ''}`);
    }

    /**
     * POST /memory/{fact_id}/pin — pin/unpin fact
     */
    async function pinMemoryFact(factId, pinned, opts = {}) {
        const params = new URLSearchParams();
        if (opts.tenant_id) params.set('tenant_id', opts.tenant_id);
        const qs = params.toString();
        return _doFetch(
            'POST', `/memory/${encodeURIComponent(factId)}/pin${qs ? '?' + qs : ''}`,
            { pinned: !!pinned },
        );
    }

    /**
     * POST /memory/search — Agent 视角的 search_facts (debug)
     */
    async function searchMemory(query, opts = {}) {
        const params = new URLSearchParams();
        if (opts.tenant_id) params.set('tenant_id', opts.tenant_id);
        const qs = params.toString();
        return _doFetch(
            'POST', `/memory/search${qs ? '?' + qs : ''}`,
            { query, top_k: opts.top_k || 5, tags_filter: opts.tags_filter || null },
        );
    }

    function getSettings() { return { ...settings }; }

    /**
     * WebSocket 流式 invoke: WS /invoke/stream
     *
     * 用法:
     *   const ws = ApiClient.openStream({
     *     onStart:    ({trace_id, tenant_id}) => ...,
     *     onChunk:    (text) => ...,           // 多次触发
     *     onMetadata: (data) => ...,            // citations/chunks/steps
     *     onDone:     ({code, cost_time, trace_id}) => ...,
     *     onError:    ({code, message, retryable}) => ...,
     *     onClose:    () => ...,
     *   });
     *   ws.send(requestBody);    // 发送 RequestEnvelope
     *   ws.close();              // 主动关闭
     *
     * 注意: 浏览器 WebSocket API 不支持自定义 header, X-API-Key 通过 ?api_key=
     * 查询字符串传递 (后端 ApiService 在 WS handshake 时校验)。
     */
    function openStream(handlers = {}) {
        const wsUrl = _wsUrl(settings.apiKey);
        let ws;
        try {
            ws = new WebSocket(wsUrl);
        } catch (e) {
            if (handlers.onError) handlers.onError({ code: 'WS_OPEN_FAILED', message: e.message });
            return { send: () => {}, close: () => {}, readyState: 3 };
        }

        ws.addEventListener('message', (ev) => {
            let msg;
            try { msg = JSON.parse(ev.data); } catch { return; }
            switch (msg.type) {
                case 'start':       handlers.onStart && handlers.onStart(msg); break;
                case 'chunk':       handlers.onChunk && handlers.onChunk(msg.text || ''); break;
                case 'metadata':    handlers.onMetadata && handlers.onMetadata(msg); break;
                // Task #48: Agent ReAct 思维链
                case 'thought':     handlers.onThought && handlers.onThought(msg); break;
                case 'action':      handlers.onAction && handlers.onAction(msg); break;
                case 'observation': handlers.onObservation && handlers.onObservation(msg); break;
                // Task V/DD: Plan mode 事件
                case 'plan':        handlers.onPlan && handlers.onPlan(msg); break;
                case 'done':        handlers.onDone && handlers.onDone(msg); break;
                case 'error':       handlers.onError && handlers.onError(msg); break;
            }
        });
        ws.addEventListener('close', (ev) => {
            if (handlers.onClose) handlers.onClose(ev);
        });
        ws.addEventListener('error', (ev) => {
            if (handlers.onError) handlers.onError({
                code: 'WS_ERROR',
                message: 'WebSocket 错误 (可能是网络或服务端断开)',
            });
        });

        function send(body) {
            const finalBody = { ...body };
            if (settings.sessionId && !finalBody.session_id) {
                finalBody.session_id = settings.sessionId;
            }
            const tryFn = () => {
                if (ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify(finalBody));
                } else if (ws.readyState === WebSocket.CONNECTING) {
                    ws.addEventListener('open', () => ws.send(JSON.stringify(finalBody)), { once: true });
                }
            };
            tryFn();
        }

        function close() {
            try { ws.close(); } catch (_) {}
        }

        return { send, close, get readyState() { return ws.readyState; } };
    }

    function _wsUrl(apiKey) {
        // 同源时直接用当前 host;否则用 settings.baseUrl 转 ws://
        let base = settings.baseUrl || `${location.protocol}//${location.host}`;
        let proto = base.startsWith('https') ? 'wss://' : 'ws://';
        if (base.startsWith('http://')) base = base.slice(7);
        else if (base.startsWith('https://')) base = base.slice(8);
        const path = '/invoke/stream';
        const qs = apiKey ? `?api_key=${encodeURIComponent(apiKey)}` : '';
        return proto + base + path + qs;
    }

    return {
        invoke,
        health,
        healthz,
        metrics,
        uploadDocument,
        buildIndex,
        getIndexJob,
        getDocumentPreview,
        listDocuments,        // Task JJ (#70)
        deleteDocument,
        listModels,
        registerModel,
        deleteModel,
        setDefaultModel,
        getAdminStatus,
        openStream,
        configure,
        getSettings,
        // Task GGG (#93) — Long-term Memory
        listMemory,
        getMemoryFact,
        deleteMemoryFact,
        pinMemoryFact,
        searchMemory,
    };
})();

// 显式挂到 window (const 在 browser global 脚本不会自动 attach)
if (typeof window !== 'undefined') window.ApiClient = ApiClient;
