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

        return { refreshMemory, searchMemoryInPanel, pinFact, deleteFact, bindEvents };
    };
})();
