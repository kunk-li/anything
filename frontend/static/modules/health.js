// ============================================================
// frontend/static/modules/health.js  (Task SS #79)
//
// 健康检查 + 语言按钮 — 都是 polling/状态显示性质的小函数, 一起放.
// ============================================================

(function () {
    window.AnythingApp = window.AnythingApp || {};

    window.AnythingApp.health = function health(deps) {
        const { els, t } = deps;

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
            // Task AAAA-A (#118): 检查 LLM 健康, 全部 unhealthy 就警告 banner
            try {
                const adminRes = await ApiClient.getAdminStatus();
                if (adminRes.status === 200 && adminRes.payload?.code === 'SUCCESS') {
                    const models = adminRes.payload?.data?.health?.models || {};
                    const modelNames = Object.keys(models);
                    const unhealthy = modelNames.filter(n => models[n].state === 'unhealthy');
                    _renderLLMBanner(modelNames, unhealthy, models);
                }
            } catch (_) {/* 静默, banner 不显也行 */}
        }

        // Task AAAA-A (#118): 全屏顶部 banner (LLM 全挂时显)
        function _renderLLMBanner(allModels, unhealthy, modelHealthMap) {
            const existing = document.getElementById('llm-banner');
            // 没注册任何模型 OR 全部 unhealthy → 警告
            const showWarning = allModels.length === 0 ||
                                (allModels.length > 0 && unhealthy.length === allModels.length);
            if (!showWarning) {
                if (existing) existing.remove();
                return;
            }
            if (existing) return;  // 已经有了, 不重复加
            const banner = document.createElement('div');
            banner.id = 'llm-banner';
            banner.className = 'llm-banner';
            const errSample = unhealthy.length > 0
                ? (modelHealthMap[unhealthy[0]]?.last_error || '').slice(0, 80)
                : '';
            banner.innerHTML = `
                <span class="llm-banner-icon">⚠️</span>
                <span class="llm-banner-text">
                    <strong>无可用 LLM 服务</strong> — 所有回答都是 stub 占位.
                    ${errSample ? `<br/><small>最近错误: ${errSample}…</small>` : ''}
                    请配 <code>DASHSCOPE_API_KEY</code> 或 <code>OPENAI_API_KEY</code> 并重启服务.
                </span>
                <button class="llm-banner-close" aria-label="关闭" title="关闭">✕</button>
            `;
            banner.querySelector('.llm-banner-close').addEventListener('click', () => banner.remove());
            document.body.appendChild(banner);
        }

        function updateLangButton() {
            if (!els.langBtn || !window.I18n) return;
            const cur = window.I18n.getLocale();
            els.langBtn.textContent = cur === 'zh' ? 'EN' : '中';
        }

        return { pollHealth, updateLangButton };
    };
})();
