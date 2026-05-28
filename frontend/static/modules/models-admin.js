// ============================================================
// frontend/static/modules/models-admin.js  (Task SS #79)
//
// 模型管理 — loadModels / renderModelsTable / openModelForm /
// closeModelForm / submitModelForm / setDefault / deleteModel.
// Task D 前端模型表单 + register/list/delete model API.
// 从原 app.js 拆出, 走 deps 注入 els/t/toast.
// ============================================================

(function () {
    window.AnythingApp = window.AnythingApp || {};

    window.AnythingApp.modelsAdmin = function modelsAdmin(deps) {
        const { els, t, toast } = deps;

        async function loadModels() {
            if (!els.modelsTbody) return;
            els.modelsTbody.innerHTML = `<tr><td colspan="4" class="empty-state">${t('preview.loading')}</td></tr>`;
            try {
                const { payload, status } = await ApiClient.listModels();
                if (status === 501 || payload?.code === 'SERVICE_UNAVAILABLE') {
                    els.modelsTbody.innerHTML = `<tr><td colspan="4" class="empty-state">${
                        Markdown.escapeHtml(payload?.message || '/config/models 不可用')
                    }</td></tr>`;
                    return;
                }
                if (status !== 200 || payload?.code !== 'SUCCESS') {
                    els.modelsTbody.innerHTML = `<tr><td colspan="4" class="empty-state">${
                        payload?.code || ('HTTP_' + status)
                    }: ${Markdown.escapeHtml(payload?.message || '')}</td></tr>`;
                    return;
                }
                const models = (payload.data && payload.data.models) || [];
                renderModelsTable(models);
            } catch (e) {
                els.modelsTbody.innerHTML = `<tr><td colspan="4" class="empty-state">${Markdown.escapeHtml(e.message)}</td></tr>`;
            }
        }

        function renderModelsTable(models) {
            if (!models.length) {
                els.modelsTbody.innerHTML = `<tr><td colspan="4" class="empty-state">${t('models.empty.list')}</td></tr>`;
                return;
            }
            els.modelsTbody.innerHTML = '';
            models.forEach(m => {
                const tr = document.createElement('tr');

                const nameCell = document.createElement('td');
                nameCell.className = 'model-name-cell';
                nameCell.innerHTML = `<span class="model-name">${Markdown.escapeHtml(m.name)}</span>` +
                    (m.is_default ? ` <span class="model-default-badge">${t('models.action.default')}</span>` : '');
                tr.appendChild(nameCell);

                const typeCell = document.createElement('td');
                typeCell.innerHTML = `<span class="model-type-chip">${Markdown.escapeHtml(m.request_type)}</span>`;
                tr.appendChild(typeCell);

                const keyCell = document.createElement('td');
                keyCell.className = 'model-key-cell';
                keyCell.textContent = m.api_key || '—';
                if (!m.configured) {
                    keyCell.style.color = 'var(--warning)';
                    keyCell.title = '未配置 (走 mock 实现)';
                }
                tr.appendChild(keyCell);

                const actionCell = document.createElement('td');
                actionCell.className = 'model-action-cell';
                const editBtn = document.createElement('button');
                editBtn.className = 'small-btn ghost';
                editBtn.textContent = t('models.action.edit');
                editBtn.addEventListener('click', () => openModelForm(m));
                actionCell.appendChild(editBtn);

                if (!m.is_default) {
                    const defaultBtn = document.createElement('button');
                    defaultBtn.className = 'small-btn ghost';
                    defaultBtn.textContent = t('models.action.setDefault');
                    defaultBtn.addEventListener('click', () => setDefault(m));
                    actionCell.appendChild(defaultBtn);
                }

                const delBtn = document.createElement('button');
                delBtn.className = 'small-btn ghost danger';
                delBtn.textContent = t('models.action.delete');
                delBtn.addEventListener('click', () => deleteModel(m));
                actionCell.appendChild(delBtn);

                tr.appendChild(actionCell);
                els.modelsTbody.appendChild(tr);
            });
        }

        function openModelForm(model) {
            if (!els.modelForm) return;
            els.modelForm.hidden = false;
            if (model) {
                els.mfName.value = model.name || '';
                els.mfName.readOnly = true;
                els.mfType.value = model.request_type || 'CHAT';
                els.mfAdapter.value = model.adapter_class || 'OpenAIChatAdapter';
                els.mfApiBase.value = model.api_base || '';
                els.mfApiKey.value = '';
                els.mfApiKey.placeholder = '留空 = 保留原 key';
                els.mfDefault.checked = !!model.is_default;
            } else {
                els.mfName.value = '';
                els.mfName.readOnly = false;
                els.mfType.value = 'CHAT';
                els.mfAdapter.value = 'OpenAIChatAdapter';
                els.mfApiBase.value = '';
                els.mfApiKey.value = '';
                els.mfApiKey.placeholder = 'sk-...';
                els.mfDefault.checked = false;
            }
            requestAnimationFrame(() => els.modelForm.scrollIntoView({ behavior: 'smooth' }));
        }

        function closeModelForm() {
            if (els.modelForm) els.modelForm.hidden = true;
        }

        async function submitModelForm() {
            const name = (els.mfName.value || '').trim();
            if (!name) {
                toast('error', t('models.error'), 'name 不能为空');
                return;
            }
            const payload = {
                name,
                request_type: els.mfType.value,
                adapter_class: els.mfAdapter.value,
                api_base: (els.mfApiBase.value || '').trim(),
                set_as_default: els.mfDefault.checked,
            };
            const keyInput = (els.mfApiKey.value || '').trim();
            if (keyInput) payload.api_key = keyInput;

            try {
                const { payload: resp, status } = await ApiClient.registerModel(payload);
                if (status === 200 && resp?.code === 'SUCCESS') {
                    toast('success', t('models.toast.saved'), name);
                    closeModelForm();
                    loadModels();
                } else {
                    toast('error', resp?.code || ('HTTP_' + status), resp?.message || '');
                }
            } catch (e) {
                toast('error', t('models.error'), e.message);
            }
        }

        async function setDefault(model) {
            try {
                const { payload, status } = await ApiClient.setDefaultModel(model.name, model.request_type);
                if (status === 200 && payload?.code === 'SUCCESS') {
                    toast('success', t('models.toast.defaultSet'), `${model.request_type}=${model.name}`);
                    loadModels();
                } else {
                    toast('error', payload?.code || ('HTTP_' + status), payload?.message || '');
                }
            } catch (e) {
                toast('error', t('models.error'), e.message);
            }
        }

        async function deleteModel(model) {
            if (!confirm(t('models.confirm.delete', { name: model.name }))) return;
            try {
                const { payload, status } = await ApiClient.deleteModel(model.name);
                if ((status === 200 || status === 404) && payload?.code) {
                    toast('success', t('models.toast.deleted'), model.name);
                    loadModels();
                } else {
                    toast('error', payload?.code || ('HTTP_' + status), payload?.message || '');
                }
            } catch (e) {
                toast('error', t('models.error'), e.message);
            }
        }

        return {
            loadModels, renderModelsTable, openModelForm, closeModelForm,
            submitModelForm, setDefault, deleteModel,
        };
    };
})();
