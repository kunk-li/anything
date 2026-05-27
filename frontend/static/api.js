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

    function getSettings() { return { ...settings }; }

    return {
        invoke,
        health,
        healthz,
        metrics,
        uploadDocument,
        buildIndex,
        getIndexJob,
        getDocumentPreview,
        configure,
        getSettings,
    };
})();
