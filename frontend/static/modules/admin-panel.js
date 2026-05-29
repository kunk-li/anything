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
            // Task WWWW-B (#110): 每张卡打 data-cat, 让 sub-tab 切类型时控制 display.
            //   monitoring: Token 用量 / Model Health / Quota / Audit / 系统指标
            //   config:     RAG 设置 / 安全 / Hooks / Skills
            //   resource:   LLM 模型 / BM25 / 向量库 / 上传
            //   task:       Scheduler
            const html = [];
            const onOff = (b) => b ? `<span class="badge-on">${t('on')}</span>` : `<span class="badge-off">${t('off')}</span>`;

            if (d.rag) {
                html.push(`<div class="admin-card" data-cat="config">
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
                html.push(`<div class="admin-card" data-cat="resource">
                    <h4>${t('admin.section.bm25')}</h4>
                    <dl>
                        <dt>${t('admin.kv.bm25_size')}</dt><dd><strong>${d.bm25.size}</strong></dd>
                        <dt>${t('admin.kv.bm25_avg')}</dt><dd>${d.bm25.avg_doc_len}</dd>
                    </dl>
                </div>`);
            }
            if (d.vector_db) {
                html.push(`<div class="admin-card" data-cat="resource">
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
                html.push(`<div class="admin-card" data-cat="resource">
                    <h4>${t('admin.section.llm')}</h4>
                    <dl>
                        <dt>${t('admin.kv.llm_count')}</dt><dd><strong>${d.llm_models.count}</strong></dd>
                        <dt>类型分布</dt><dd>${escapeHtml(breakdown)}</dd>
                    </dl>
                </div>`);
            }
            if (d.uploads) {
                html.push(`<div class="admin-card" data-cat="resource">
                    <h4>${t('admin.section.uploads')}</h4>
                    <dl>
                        <dt>${t('admin.kv.upload_count')}</dt><dd><strong>${d.uploads.count}</strong></dd>
                        <dt>dir</dt><dd class="path">${escapeHtml(d.uploads.dir || '-')}</dd>
                    </dl>
                </div>`);
            }
            if (d.security) {
                const tenants = (d.security.registered_tenants || []).join(', ') || '-';
                html.push(`<div class="admin-card" data-cat="config">
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
                html.push(`<div class="admin-card" data-cat="monitoring">
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
                html.push(`<div class="admin-card" data-cat="monitoring">
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
                html.push(`<div class="admin-card" data-cat="monitoring">
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

            // Task OOO (#101): Hooks (Z) — 显示装了哪些 hook (per event)
            if (d.hooks) {
                const cnt = d.hooks.count || {};
                const list = d.hooks.list || {};
                const totalHooks = Object.values(cnt).reduce((a, b) => a + b, 0);
                const hookRows = Object.entries(list).map(([event, fns]) => {
                    const fnsHtml = (fns || []).length
                        ? (fns || []).map(n => `<code style="font-size:10px;">${escapeHtml(n)}</code>`).join(', ')
                        : '<span style="color:var(--text-faint)">(无)</span>';
                    return `<dt class="path">${escapeHtml(event)} (${(fns || []).length})</dt><dd>${fnsHtml}</dd>`;
                }).join('') || '<dt>无</dt><dd>-</dd>';
                html.push(`<div class="admin-card" data-cat="config">
                    <h4>🪝 Hooks (Z)</h4>
                    <dl>
                        <dt>total hooks</dt><dd><strong>${totalHooks}</strong></dd>
                    </dl>
                    <div class="admin-subsection">
                        <strong>By event:</strong>
                        <dl>${hookRows}</dl>
                    </div>
                </div>`);
            }

            // Task OOO (#101): Skills (AA)
            if (d.skills) {
                const items = d.skills.items || [];
                const itemRows = items.length
                    ? items.slice(0, 10).map(s => `
                        <dt class="path">${escapeHtml(s.name)}</dt>
                        <dd>${escapeHtml(s.description || '')}${(s.tags || []).map(tag => ` <span class="memory-tag">${escapeHtml(tag)}</span>`).join('')}</dd>
                    `).join('')
                    : '<dt>(空)</dt><dd>没有加载到 skill</dd>';
                html.push(`<div class="admin-card" data-cat="config">
                    <h4>📜 Skills (AA)</h4>
                    <dl>
                        <dt>loaded from</dt><dd class="path">${escapeHtml(d.skills.loaded_from || '(未加载)')}</dd>
                        <dt>count</dt><dd><strong>${d.skills.count}</strong></dd>
                    </dl>
                    <div class="admin-subsection">
                        <strong>Skills (top 10):</strong>
                        <dl>${itemRows}</dl>
                    </div>
                </div>`);
            }

            // Task PPP (#102): Scheduler (II) — cron 任务列表 + 触发/取消按钮
            if (d.scheduler) {
                const tasks = d.scheduler.tasks || [];
                const taskRows = tasks.length
                    ? tasks.slice(0, 10).map(t => `
                        <dt class="path">${escapeHtml(t.id)}</dt>
                        <dd>
                            ${onOff(t.enabled)} · <span style="font-family:var(--mono);font-size:10px;">${escapeHtml(JSON.stringify(t.schedule || {}))}</span>
                            <br/>next: ${escapeHtml(t.next_run || '-')}
                            · runs: ${t.runs || 0}
                            ${t.last_error ? '<br/><span style="color:var(--danger);font-size:10px;">err: ' + escapeHtml(String(t.last_error).slice(0, 80)) + '</span>' : ''}
                            <br/>
                            <button class="small-btn ghost sched-trigger-btn" data-task-id="${escapeHtml(t.id)}" title="立刻触发一次">▶ 触发</button>
                            <button class="small-btn ghost danger sched-cancel-btn" data-task-id="${escapeHtml(t.id)}" title="取消任务">✕ 取消</button>
                        </dd>
                    `).join('')
                    : '<dt>(空)</dt><dd>无已注册任务</dd>';
                html.push(`<div class="admin-card" data-cat="task">
                    <h4>⏰ 调度任务 (II)</h4>
                    <dl>
                        <dt>total tasks</dt><dd><strong>${tasks.length}</strong></dd>
                    </dl>
                    <div class="admin-subsection">
                        <strong>Tasks:</strong>
                        <dl>${taskRows}</dl>
                    </div>
                </div>`);
            }

            // Task OOO (#101): Audit log (CC) — 元数据 (不展示完整事件)
            if (d.audit) {
                const sizeKB = ((d.audit.current_size_bytes || 0) / 1024).toFixed(1);
                const maxKB = ((d.audit.max_bytes || 0) / 1024).toFixed(0);
                html.push(`<div class="admin-card" data-cat="monitoring">
                    <h4>📋 Audit log (CC)</h4>
                    <dl>
                        <dt>path</dt><dd class="path">${escapeHtml(d.audit.path || '-')}</dd>
                        <dt>size</dt><dd>${sizeKB} KB / ${maxKB} KB</dd>
                        <dt>rotates kept</dt><dd>${d.audit.backup_count}</dd>
                        <dt>writes (this proc)</dt><dd>${d.audit.writes_this_process || 0}</dd>
                    </dl>
                </div>`);
            }

            // Task WWWW-A (#109): Prometheus metrics 卡 (从右栏独立 tab 移过来)
            // 复用原 #metrics-refresh / #metrics-text 元素 ID, app.js 的 loadMetrics() 仍能找到.
            html.push(`<div class="admin-card" data-cat="monitoring">
                <h4>📈 系统指标 (Prometheus)</h4>
                <div class="metrics-controls">
                    <button id="metrics-refresh" class="small-btn">刷新</button>
                    <span class="hint">来自 /metrics</span>
                </div>
                <pre class="metrics-text" id="metrics-text" style="max-height:200px;overflow:auto;">点击刷新查看实时指标</pre>
            </div>`);

            els.adminGrid.innerHTML = html.join('');

            // Task WWWW-B (#110): 渲完后按 active sub-tab 过滤显示
            const activeCat = els.adminGrid.dataset.activeCat || 'monitoring';
            _applyAdminCategoryFilter(activeCat);

            // metrics-refresh 是新渲的, app.js 里的 bindEvents 已经在 init 时绑过了
            // (els.metricsRefresh 那时指向原 HTML 里的 button, 现在该 button 不存在了).
            // 重新绑一次 — 用全局 loadMetrics 函数 (在 app.js 顶层定义).
            const mRefresh = document.getElementById('metrics-refresh');
            if (mRefresh && typeof window.__loadMetrics === 'function' && !mRefresh._bound) {
                mRefresh._bound = true;
                mRefresh.addEventListener('click', window.__loadMetrics);
            }
        }

        // Task WWWW-B (#110): sub-tab 切换 = 给 admin-grid 加 data-active-cat,
        // CSS 控制 [data-active-cat=X] .admin-card:not([data-cat=X]) { display:none }
        function _applyAdminCategoryFilter(cat) {
            if (!els.adminGrid) return;
            els.adminGrid.dataset.activeCat = cat;
            // 同步 sub-tab aria-selected / active class
            document.querySelectorAll('.admin-subtab').forEach(b => {
                const isActive = b.dataset.cat === cat;
                b.classList.toggle('active', isActive);
                b.setAttribute('aria-selected', isActive ? 'true' : 'false');
            });
        }

        // sub-tab click delegation (绑一次)
        const subnav = document.querySelector('.admin-subnav');
        if (subnav && !subnav._bound) {
            subnav._bound = true;
            subnav.addEventListener('click', (e) => {
                const btn = e.target.closest('.admin-subtab');
                if (btn && btn.dataset.cat) _applyAdminCategoryFilter(btn.dataset.cat);
            });
        }

        // Task PPP (#102): event delegation 给调度任务的 trigger/cancel 按钮
        // (renderAdminStatus 重渲染会替换整个 innerHTML, 不能直接 addEventListener
        // 到每个 button — 用 click delegation 一次性绑.)
        if (els.adminGrid && !els.adminGrid._schedulerClickBound) {
            els.adminGrid._schedulerClickBound = true;
            els.adminGrid.addEventListener('click', async (e) => {
                const trig = e.target.closest('.sched-trigger-btn');
                if (trig) {
                    const taskId = trig.dataset.taskId;
                    if (!confirm(`立刻触发任务 ${taskId}?`)) return;
                    trig.disabled = true;
                    try {
                        const { payload, status } = await ApiClient.triggerSchedulerTask(taskId);
                        if (status === 200 && payload?.code === 'SUCCESS') {
                            toast('success', '任务已触发', taskId);
                            refreshAdminStatus();
                        } else {
                            toast('error', payload?.code || status, payload?.message || '');
                            trig.disabled = false;
                        }
                    } catch (err) {
                        toast('error', '触发失败', err.message);
                        trig.disabled = false;
                    }
                    return;
                }
                const cancel = e.target.closest('.sched-cancel-btn');
                if (cancel) {
                    const taskId = cancel.dataset.taskId;
                    if (!confirm(`取消任务 ${taskId}? 不可恢复.`)) return;
                    cancel.disabled = true;
                    try {
                        const { payload, status } = await ApiClient.cancelSchedulerTask(taskId);
                        if (status === 200 && payload?.code === 'SUCCESS') {
                            toast('success', '任务已取消', taskId);
                            refreshAdminStatus();
                        } else {
                            toast('error', payload?.code || status, payload?.message || '');
                            cancel.disabled = false;
                        }
                    } catch (err) {
                        toast('error', '取消失败', err.message);
                        cancel.disabled = false;
                    }
                    return;
                }
            });
        }

        return { refreshDocsList, refreshAdminStatus, renderAdminStatus };
    };
})();
