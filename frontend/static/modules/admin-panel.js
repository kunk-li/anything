// ============================================================
// frontend/static/modules/admin-panel.js  (Task SS #79)
//
// Admin / Docs 面板 — refreshAdminStatus / renderAdminStatus / refreshDocsList.
// Task S 系统状态可视化 + Task JJ 文档管理. 从原 app.js 拆出.
// ============================================================

(function () {
    window.AnythingApp = window.AnythingApp || {};

    window.AnythingApp.adminPanel = function adminPanel(deps) {
        const { els, t, toast, escapeHtml } = deps;

        // Task JJ (#70): 已索引文档列表 + 删除
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
            if (d.bm25) {
                html.push(`<div class="admin-card">
                    <h4>${t('admin.section.bm25')}</h4>
                    <dl>
                        <dt>${t('admin.kv.bm25_size')}</dt><dd><strong>${d.bm25.size}</strong></dd>
                        <dt>${t('admin.kv.bm25_avg')}</dt><dd>${d.bm25.avg_doc_len}</dd>
                    </dl>
                </div>`);
            }
            if (d.vector_db) {
                html.push(`<div class="admin-card">
                    <h4>${t('admin.section.vector')}</h4>
                    <dl>
                        <dt>${t('admin.kv.vec_ntotal')}</dt><dd><strong>${d.vector_db.ntotal}</strong></dd>
                    </dl>
                </div>`);
            }
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
            if (d.uploads) {
                html.push(`<div class="admin-card">
                    <h4>${t('admin.section.uploads')}</h4>
                    <dl>
                        <dt>${t('admin.kv.upload_count')}</dt><dd><strong>${d.uploads.count}</strong></dd>
                        <dt>dir</dt><dd class="path">${escapeHtml(d.uploads.dir || '-')}</dd>
                    </dl>
                </div>`);
            }
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

            // Task LLL (#98): Token / Cost 仪表 (A)
            if (d.usage) {
                const total = d.usage.total || {};
                const byModel = d.usage.by_model || {};
                const byTenant = d.usage.by_tenant || {};
                const cost = (total.cost_usd || 0).toFixed(4);
                const tokens = total.total_tokens || 0;
                const calls = total.calls || 0;
                const topModels = Object.entries(byModel)
                    .sort((a, b) => (b[1].total_tokens || 0) - (a[1].total_tokens || 0))
                    .slice(0, 5)
                    .map(([name, b]) => `<dt class="path">${escapeHtml(name)}</dt>
                        <dd>${b.total_tokens || 0} tok · $${(b.cost_usd || 0).toFixed(4)} · ${b.calls || 0}×</dd>`)
                    .join('') || '<dd>-</dd>';
                const topTenants = Object.entries(byTenant)
                    .sort((a, b) => (b[1].cost_usd || 0) - (a[1].cost_usd || 0))
                    .slice(0, 5)
                    .map(([name, b]) => `<dt class="path">${escapeHtml(name)}</dt>
                        <dd>$${(b.cost_usd || 0).toFixed(4)} · ${b.calls || 0}×</dd>`)
                    .join('') || '<dd>-</dd>';
                html.push(`<div class="admin-card">
                    <h4>📊 Token 用量 (Y/XX)</h4>
                    <dl>
                        <dt>total cost</dt><dd><strong>$${cost}</strong></dd>
                        <dt>total tokens</dt><dd><strong>${tokens}</strong></dd>
                        <dt>total calls</dt><dd>${calls}</dd>
                    </dl>
                    <div class="admin-subsection">
                        <strong>Top models:</strong>
                        <dl>${topModels}</dl>
                    </div>
                    <div class="admin-subsection">
                        <strong>Top tenants:</strong>
                        <dl>${topTenants}</dl>
                    </div>
                </div>`);
            }

            // Task LLL (#98): Model Health (B)
            if (d.health && d.health.models) {
                const stateChip = (s) => {
                    const colors = { healthy: '#22c55e', probation: '#f59e0b', unhealthy: '#ef4444' };
                    const c = colors[s] || '#6b7280';
                    return `<span class="chip" style="color:${c};border-color:${c};">${escapeHtml(s)}</span>`;
                };
                const modelRows = Object.entries(d.health.models)
                    .map(([name, m]) => {
                        const failRate = ((m.failure_rate || 0) * 100).toFixed(1);
                        const cd = m.cooldown_remaining_seconds || 0;
                        return `<dt class="path">${escapeHtml(name)}</dt>
                            <dd>${stateChip(m.state)} 失败率 ${failRate}% · ${m.total_calls || 0}调用${
                                cd > 0 ? ` · 冷却 ${cd}s` : ''
                            }</dd>`;
                    }).join('') || '<dt>无</dt><dd>(暂无调用记录)</dd>';
                html.push(`<div class="admin-card">
                    <h4>❤️ 模型健康 (HH/BBB)</h4>
                    <dl>
                        <dt>threshold</dt><dd>${d.health.fail_threshold} 连失</dd>
                        <dt>cooldown</dt><dd>${d.health.cooldown_seconds}s</dd>
                    </dl>
                    <div class="admin-subsection">
                        <strong>模型状态:</strong>
                        <dl>${modelRows}</dl>
                    </div>
                </div>`);
            }

            // Task LLL (#98): Quota / Rate limit (BB/AAA)
            if (d.quota) {
                const q = d.quota;
                const dailyRows = Object.entries(q.daily_usd_used_by_tenant || {})
                    .map(([tenant, usd]) => `<dt class="path">${escapeHtml(tenant)}</dt>
                        <dd>$${(usd || 0).toFixed(4)} / $${(q.daily_usd_limit || 0).toFixed(2) || '∞'}</dd>`)
                    .join('') || '<dt>无</dt><dd>(无 tenant 记录)</dd>';
                const rateRows = Object.entries(q.current_rate_window_size || {})
                    .map(([tenant, n]) => `<dt class="path">${escapeHtml(tenant)}</dt>
                        <dd>${n} / ${q.rate_per_minute || '∞'} /min</dd>`)
                    .join('') || '<dt>无</dt><dd>-</dd>';
                html.push(`<div class="admin-card">
                    <h4>🚦 配额 / 限流 (BB/AAA)</h4>
                    <dl>
                        <dt>daily USD limit</dt><dd>${q.daily_usd_limit ? '$' + q.daily_usd_limit : '不限'}</dd>
                        <dt>rate /min</dt><dd>${q.rate_per_minute || '不限'}</dd>
                        <dt>global used</dt><dd>$${(q.global_usd_used || 0).toFixed(4)}${q.global_usd_limit ? ' / $' + q.global_usd_limit : ''}</dd>
                    </dl>
                    <div class="admin-subsection">
                        <strong>Daily USD by tenant:</strong>
                        <dl>${dailyRows}</dl>
                    </div>
                    <div class="admin-subsection">
                        <strong>Rate window:</strong>
                        <dl>${rateRows}</dl>
                    </div>
                </div>`);
            }

            els.adminGrid.innerHTML = html.join('');
        }

        return { refreshDocsList, refreshAdminStatus, renderAdminStatus };
    };
})();
