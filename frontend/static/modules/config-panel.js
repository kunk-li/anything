// ============================================================
// frontend/static/modules/config-panel.js  (执行计划⑦)
//
// 统一配置界面: 调 /config/agent (读 schema+当前值) / POST 改 (运行期 live) /
// /config/agent/preset(s) (能力档位)。按 group 分组渲染开关, 每控件 change 即应用。
// 工厂注入: deps = { els, t, toast, escapeHtml }; 调用方 app.js init。
// ============================================================
(function () {
    window.AnythingApp = window.AnythingApp || {};

    window.AnythingApp.configPanel = function configPanel(deps) {
        const { t, toast, escapeHtml } = deps;

        async function refresh() {
            const body = document.getElementById('config-panel-body');
            if (!body) return;
            body.innerHTML = `<div class="empty-state">${t('config.loading') || '加载中…'}</div>`;
            try {
                const { payload, status } = await ApiClient.getAgentConfig();
                if (status === 501) {
                    body.innerHTML = `<div class="empty-state">${escapeHtml(payload?.message || 'config 不可用')}</div>`;
                    return;
                }
                if (status !== 200 || payload?.code !== 'SUCCESS') {
                    body.innerHTML = `<div class="empty-state error">× ${payload?.code || status} ${escapeHtml(payload?.message || '')}</div>`;
                    return;
                }
                _render(body, (payload.data || {}).fields || []);
            } catch (e) {
                body.innerHTML = `<div class="empty-state error">× ${escapeHtml(e.message)}</div>`;
            }
        }

        function _render(body, fields) {
            const groups = {};
            for (const f of fields) (groups[f.group] = groups[f.group] || []).push(f);
            let html = '';
            for (const g of Object.keys(groups)) {
                html += `<div class="cfg-group"><div class="cfg-group-title">${escapeHtml(g)}</div>`;
                for (const f of groups[g]) html += _field(f);
                html += '</div>';
            }
            body.innerHTML = html || `<div class="empty-state">无配置项</div>`;
            body.querySelectorAll('[data-key]').forEach(el => {
                el.addEventListener('change', () => _applyOne(el));
            });
        }

        function _field(f) {
            const key = escapeHtml(f.key);
            const cur = f.current;
            let control;
            if (f.type === 'bool') {
                control = `<input type="checkbox" data-key="${key}" data-type="bool" ${cur ? 'checked' : ''}>`;
            } else if (f.choices) {
                control = `<select data-key="${key}" data-type="str">`
                    + f.choices.map(c => `<option value="${escapeHtml(c)}" ${String(cur) === c ? 'selected' : ''}>${escapeHtml(c)}</option>`).join('')
                    + `</select>`;
            } else if (f.type === 'int') {
                control = `<input type="number" data-key="${key}" data-type="int" value="${escapeHtml(String(cur == null ? '' : cur))}">`;
            } else if (f.type === 'list') {
                const v = Array.isArray(cur) ? cur.join(', ') : String(cur == null ? '' : cur);
                control = `<input type="text" data-key="${key}" data-type="list" value="${escapeHtml(v)}" placeholder="逗号分隔">`;
            } else {
                control = `<input type="text" data-key="${key}" data-type="str" value="${escapeHtml(String(cur == null ? '' : cur))}">`;
            }
            const label = f.key.replace(/^agent\./, '');
            return `<div class="cfg-field" title="${escapeHtml(f.description || '')}">`
                + `<label class="cfg-label">${escapeHtml(label)}</label>${control}</div>`;
        }

        async function _applyOne(el) {
            const key = el.dataset.key, type = el.dataset.type;
            let val;
            if (type === 'bool') val = el.checked;
            else if (type === 'int') val = parseInt(el.value, 10);
            else if (type === 'list') val = el.value.split(',').map(s => s.trim()).filter(Boolean);
            else val = el.value;
            try {
                const { payload, status } = await ApiClient.setAgentConfig({ [key]: val });
                if (status === 200 && payload?.code === 'SUCCESS') {
                    const errs = (payload.data || {}).errors || [];
                    if (errs.length) toast('error', '未应用', errs[0].error || key);
                    else toast('success', '已应用', key.replace(/^agent\./, ''));
                } else {
                    toast('error', payload?.code || status, payload?.message || '');
                }
            } catch (e) {
                toast('error', '应用失败', e.message);
            }
        }

        async function _applyPreset(name) {
            try {
                const { payload, status } = await ApiClient.applyAgentPreset(name);
                if (status === 200 && payload?.code === 'SUCCESS') {
                    toast('success', '已套用档位', name);
                    refresh();
                } else {
                    toast('error', payload?.code || status, payload?.message || '');
                }
            } catch (e) {
                toast('error', '套用失败', e.message);
            }
        }

        function bindEvents() {
            document.querySelectorAll('#config-presets [data-preset]').forEach(btn => {
                btn.addEventListener('click', () => _applyPreset(btn.dataset.preset));
            });
            const rb = document.getElementById('config-refresh-btn');
            if (rb) rb.addEventListener('click', refresh);
        }

        return { refresh, bindEvents };
    };
})();
