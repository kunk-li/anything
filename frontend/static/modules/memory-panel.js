// ============================================================
// frontend/static/modules/memory-panel.js  (Task KKK #97)
//
// 长期记忆侧栏面板. 调 /memory/* 5 路由 (GGG #93):
//   refreshMemory()           → GET /memory/list (含 tag/limit/offset/tenant_id)
//   searchMemoryInPanel()     → POST /memory/search
//   pinFact(factId, pinned)   → POST /memory/{fact_id}/pin
//   deleteFact(factId)        → DELETE /memory/{fact_id}
//
// 工厂注入模式 (跟 admin-panel.js 一致): deps = { els, t, toast, escapeHtml }
// 调用方 (app.js init):
//   const memPanel = window.AnythingApp.memoryPanel({ els, t, toast, escapeHtml });
// ============================================================

(function () {
    window.AnythingApp = window.AnythingApp || {};

    window.AnythingApp.memoryPanel = function memoryPanel(deps) {
        const { els, t, toast, escapeHtml } = deps;

        function _tenantId() {
            return (els.tenantInput && els.tenantInput.value.trim()) || 'default';
        }

        async function refreshMemory() {
            if (!els.memoryList) return;
            refreshVisibility();   // 执行计划⑥: 顺带刷新"画像 + 待审批维护"可见性块
            els.memoryList.innerHTML =
                `<li class="empty-state">${t('memory.loading') || '加载中...'}</li>`;
            const tagSel = els.memoryTagFilter ? els.memoryTagFilter.value : '';
            try {
                const { payload, status } = await ApiClient.listMemory({
                    tenant_id: _tenantId(),
                    tags: tagSel || undefined,
                    limit: 50,
                });
                if (status === 501) {
                    els.memoryList.innerHTML =
                        `<li class="empty-state">${escapeHtml(payload?.message || '/memory 不可用')}</li>`;
                    return;
                }
                if (status !== 200 || payload?.code !== 'SUCCESS') {
                    els.memoryList.innerHTML =
                        `<li class="empty-state error">× ${payload?.code || status} ${escapeHtml(payload?.message || '')}</li>`;
                    return;
                }
                const facts = (payload.data || {}).facts || [];
                _renderFactsList(facts);
                if (els.memoryCount) els.memoryCount.textContent = facts.length;
                if (els.memoryCountHint) {
                    els.memoryCountHint.textContent =
                        `${facts.length} ${t('memory.count_suffix') || '条 fact'}`;
                }
            } catch (e) {
                els.memoryList.innerHTML =
                    `<li class="empty-state error">× ${escapeHtml(e.message)}</li>`;
            }
        }

        async function searchMemoryInPanel() {
            const q = (els.memorySearchInput && els.memorySearchInput.value || '').trim();
            if (!q) {
                toast('warn', t('memory.search.empty') || '请输入搜索词', '');
                return;
            }
            els.memoryList.innerHTML =
                `<li class="empty-state">${t('memory.searching') || '搜索中...'}</li>`;
            try {
                const { payload, status } = await ApiClient.searchMemory(q, {
                    tenant_id: _tenantId(), top_k: 10,
                });
                if (status !== 200 || payload?.code !== 'SUCCESS') {
                    els.memoryList.innerHTML =
                        `<li class="empty-state error">× ${payload?.code || status} ${escapeHtml(payload?.message || '')}</li>`;
                    return;
                }
                const hits = (payload.data || {}).hits || [];
                // 把 search hits 映射回 facts-list 渲染
                const facts = hits.map(h => ({
                    fact_id: h.fact_id,
                    content: h.content,
                    tags: h.tags || [],
                    confidence: 1.0,  // search 返回里没 confidence; 默认 1.0
                    pinned: false,
                    access_count: 0,
                    last_accessed: Date.now() / 1000,
                    _score: h.score,
                    _reason: h.reason,
                }));
                _renderFactsList(facts);
                if (els.memoryCount) els.memoryCount.textContent = facts.length;
                if (els.memoryCountHint) {
                    els.memoryCountHint.textContent =
                        `搜 "${q}" → ${facts.length} 命中`;
                }
            } catch (e) {
                els.memoryList.innerHTML =
                    `<li class="empty-state error">× ${escapeHtml(e.message)}</li>`;
            }
        }

        function _renderFactsList(facts) {
            if (!facts.length) {
                els.memoryList.innerHTML =
                    `<li class="empty-state">${t('memory.empty') || '记忆库还是空的, 跟 Agent 多聊聊'}</li>`;
                return;
            }
            els.memoryList.innerHTML = '';
            for (const f of facts) {
                const li = document.createElement('li');
                li.className = 'memory-item' + (f.pinned ? ' pinned' : '');
                const tagsHtml = (f.tags || []).map(
                    tag => `<span class="memory-tag">${escapeHtml(tag)}</span>`
                ).join('');
                const lastAccess = f.last_accessed
                    ? new Date(f.last_accessed * 1000).toLocaleString()
                    : '';
                const scoreInfo = f._score != null
                    ? ` · score=${f._score.toFixed(2)} (${escapeHtml(f._reason || '')})`
                    : '';
                li.innerHTML = `
                    <div class="memory-content">${escapeHtml(f.content || '')}</div>
                    <div class="memory-meta">
                        ${tagsHtml}
                        <span class="memory-stat" title="${t('memory.access_count') || '使用次数'}">
                            ${f.pinned ? '📌 ' : ''}🔁 ${f.access_count || 0}${scoreInfo}
                        </span>
                        <span class="memory-time">${escapeHtml(lastAccess)}</span>
                    </div>
                    <div class="memory-actions">
                        <button class="small-btn ghost memory-pin-btn"
                                data-fact-id="${escapeHtml(f.fact_id)}"
                                data-currently-pinned="${f.pinned ? '1' : '0'}">
                            ${f.pinned ? '取消置顶' : '📌 置顶'}
                        </button>
                        <button class="small-btn ghost danger memory-delete-btn"
                                data-fact-id="${escapeHtml(f.fact_id)}">
                            ✕ 删除
                        </button>
                    </div>
                `;
                const pinBtn = li.querySelector('.memory-pin-btn');
                pinBtn.addEventListener('click', () => {
                    const currentlyPinned = pinBtn.dataset.currentlyPinned === '1';
                    pinFact(pinBtn.dataset.factId, !currentlyPinned);
                });
                const delBtn = li.querySelector('.memory-delete-btn');
                delBtn.addEventListener('click', () => {
                    if (!confirm(`删除 fact: "${(f.content || '').slice(0, 60)}..." ?`)) return;
                    deleteFact(delBtn.dataset.factId);
                });
                els.memoryList.appendChild(li);
            }
        }

        async function pinFact(factId, pinned) {
            try {
                const { payload, status } = await ApiClient.pinMemoryFact(
                    factId, pinned, { tenant_id: _tenantId() },
                );
                if (status === 200 && payload?.code === 'SUCCESS') {
                    toast('success',
                        pinned ? '已置顶' : '已取消置顶',
                        '');
                    refreshMemory();
                } else {
                    toast('error', payload?.code || status, payload?.message || '');
                }
            } catch (e) {
                toast('error', '置顶失败', e.message);
            }
        }

        async function deleteFact(factId) {
            try {
                const { payload, status } = await ApiClient.deleteMemoryFact(
                    factId, { tenant_id: _tenantId() },
                );
                if (status === 200 && payload?.code === 'SUCCESS') {
                    toast('success', '已删除', '');
                    refreshMemory();
                } else {
                    toast('error', payload?.code || status, payload?.message || '');
                }
            } catch (e) {
                toast('error', '删除失败', e.message);
            }
        }

        // ===== 执行计划⑥ 可见性: 画像 + 待审批维护提议 (插在 facts 列表上方) =====
        function _visBox() {
            if (!els.memoryList || !els.memoryList.parentNode) return null;
            let box = document.getElementById('memory-vis-box');
            if (!box) {
                box = document.createElement('div');
                box.id = 'memory-vis-box';
                box.className = 'memory-vis';
                els.memoryList.parentNode.insertBefore(box, els.memoryList);
            }
            return box;
        }

        async function refreshVisibility() {
            const box = _visBox();
            if (!box) return;
            const tenant = _tenantId();
            let profile = {}, proposals = [], propEnabled = false;
            try {
                const r = await ApiClient.getProfile({ tenant_id: tenant });
                if (r.status === 200 && r.payload?.code === 'SUCCESS') {
                    profile = (r.payload.data || {}).profile || {};
                }
            } catch (e) { /* 静默, 不阻断 facts */ }
            try {
                const r = await ApiClient.getMaintenanceProposals({ tenant_id: tenant, scope: 'memory' });
                if (r.status === 200 && r.payload?.code === 'SUCCESS') {
                    propEnabled = !!(r.payload.data || {}).enabled;
                    proposals = (r.payload.data || {}).proposals || [];
                }
            } catch (e) { /* 静默 */ }
            _renderVisBox(box, profile, proposals, propEnabled);
        }

        function _renderVisBox(box, profile, proposals, propEnabled) {
            const labels = { preference: '偏好', style: '风格', convention: '约定', domain: '领域', weakness: '需补位' };
            const dims = Object.keys(profile || {}).filter(k => (profile[k] || []).length);
            let html = `<div class="vis-sec"><div class="vis-title">🧠 ${t('memory.profile_title') || 'Agent 眼中的你'}</div>`;
            if (!dims.length) {
                html += `<div class="vis-empty">${t('memory.profile_empty') || '还没建立画像, 多聊聊'}</div>`;
            } else {
                for (const dim of dims) {
                    const items = (profile[dim] || []).map(it => `<li>${escapeHtml(String(it))}</li>`).join('');
                    html += `<div class="vis-dim"><span class="vis-dim-label">${escapeHtml(labels[dim] || dim)}</span><ul>${items}</ul></div>`;
                }
            }
            html += `</div><div class="vis-sec"><div class="vis-title">🔧 ${t('memory.maint_title') || '待审批维护'}</div>`;
            if (!propEnabled) {
                html += `<div class="vis-empty">${t('memory.maint_off') || '自维护未开启 (config: enable_self_reflection)'}</div>`;
            } else if (!proposals.length) {
                html += `<div class="vis-empty">${t('memory.maint_none') || '暂无维护提议'}</div>`;
            } else {
                html += '<ul class="vis-props">';
                for (const p of proposals) {
                    html += `<li><span class="vis-prop-act">${escapeHtml(p.action_type || '?')}</span> `
                        + `${escapeHtml(p.reason || p.problem || '')} `
                        + `<button class="small-btn ghost vis-approve" data-pid="${escapeHtml(p.id || '')}">✓ 批准</button></li>`;
                }
                html += '</ul>';
            }
            html += '</div>';
            // 用户洞察 (analyze_user): LLM 调用, 按需触发 (不随面板自动刷)
            html += `<div class="vis-sec"><div class="vis-title">🔍 ${t('memory.ua_title') || '用户洞察'} `
                + `<button class="small-btn ghost" id="ua-run-btn">${t('memory.ua_run') || '分析使用者'}</button></div>`
                + `<div id="ua-result" class="vis-ua"></div></div>`;
            box.innerHTML = html;
            box.querySelectorAll('.vis-approve').forEach(btn => {
                btn.addEventListener('click', () => _approveProposal(btn.dataset.pid, proposals));
            });
            const uaBtn = box.querySelector('#ua-run-btn');
            if (uaBtn) uaBtn.addEventListener('click', _runUserAnalysis);
        }

        async function _runUserAnalysis() {
            const out = document.getElementById('ua-result');
            if (!out) return;
            out.innerHTML = `<div class="vis-empty">${t('memory.ua_running') || '分析中…'}</div>`;
            try {
                const { payload, status } = await ApiClient.getUserAnalysis({ tenant_id: _tenantId() });
                if (status !== 200 || payload?.code !== 'SUCCESS') {
                    out.innerHTML = `<div class="vis-empty error">× ${escapeHtml(payload?.message || String(status))}</div>`;
                    return;
                }
                const d = payload.data || {};
                if (!d.enabled) {
                    out.innerHTML = `<div class="vis-empty">${escapeHtml(d.note || '用户分析未开启 (config: enable_user_analysis)')}</div>`;
                    return;
                }
                _renderUA(out, d.insights || [], d.proposals || []);
            } catch (e) {
                out.innerHTML = `<div class="vis-empty error">× ${escapeHtml(e.message)}</div>`;
            }
        }

        function _renderUA(out, insights, proposals) {
            let html = '';
            if (insights.length) {
                html += '<ul class="vis-insights">'
                    + insights.map(i => `<li>${escapeHtml(String(i))}</li>`).join('') + '</ul>';
            } else {
                html += `<div class="vis-empty">${t('memory.ua_none') || '暂无洞察 (记忆太少?)'}</div>`;
            }
            if (proposals.length) {
                html += `<div class="vis-title" style="margin-top:6px">${t('memory.ua_props') || '画像增强提议'}</div>`
                    + '<ul class="vis-props">';
                for (const p of proposals) {
                    html += `<li><span class="vis-prop-act">${escapeHtml(p.dim || '')}</span> `
                        + `${escapeHtml(p.content || '')} `
                        + `<button class="small-btn ghost ua-approve" data-pid="${escapeHtml(p.id || '')}">✓ 反哺</button></li>`;
                }
                html += '</ul>';
            }
            out.innerHTML = html;
            out.querySelectorAll('.ua-approve').forEach(btn => {
                btn.addEventListener('click', () => _approveUA(btn.dataset.pid, proposals));
            });
        }

        async function _approveUA(pid, proposals) {
            const p = (proposals || []).find(x => x.id === pid);
            if (!p) return;
            try {
                const { payload, status } = await ApiClient.applyUserInsights([p], [pid], { tenant_id: _tenantId() });
                if (status === 200 && payload?.code === 'SUCCESS') {
                    toast('success', '已反哺画像', p.content || '');
                    refreshVisibility();   // 画像变了, 刷新顶部"Agent 眼中的你"
                } else {
                    toast('error', payload?.code || status, payload?.message || '');
                }
            } catch (e) {
                toast('error', '反哺失败', e.message);
            }
        }

        async function _approveProposal(pid, proposals) {
            const p = (proposals || []).find(x => x.id === pid);
            if (!p) return;
            try {
                const { payload, status } = await ApiClient.applyMaintenance([p], [pid], { tenant_id: _tenantId() });
                if (status === 200 && payload?.code === 'SUCCESS') {
                    toast('success', '已执行', `应用 ${payload.data?.applied || 0} 项`);
                    refreshMemory();   // prune 等会改 facts, 刷新列表 + 画像
                } else {
                    toast('error', payload?.code || status, payload?.message || '');
                }
            } catch (e) {
                toast('error', '执行失败', e.message);
            }
        }

        // 把搜索 input 的 Enter 也绑成 search
        function bindEvents() {
            if (els.memoryRefreshBtn) {
                els.memoryRefreshBtn.addEventListener('click', refreshMemory);
            }
            if (els.memorySearchBtn) {
                els.memorySearchBtn.addEventListener('click', searchMemoryInPanel);
            }
            if (els.memorySearchInput) {
                els.memorySearchInput.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        searchMemoryInPanel();
                    }
                });
            }
            if (els.memoryTagFilter) {
                els.memoryTagFilter.addEventListener('change', refreshMemory);
            }
        }

        return { refreshMemory, searchMemoryInPanel, pinFact, deleteFact, bindEvents, refreshVisibility };
    };
})();
