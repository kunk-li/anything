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
        }

        function updateLangButton() {
            if (!els.langBtn || !window.I18n) return;
            const cur = window.I18n.getLocale();
            els.langBtn.textContent = cur === 'zh' ? 'EN' : '中';
        }

        return { pollHealth, updateLangButton };
    };
})();
