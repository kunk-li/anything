// ============================================================
// frontend/static/modules/ui-helpers.js  (Task SS #79)
//
// 通用 UI helpers — escapeHtml / toast / openDrawer / closeDrawer /
// showPlanApprovalModal. 从原 app.js 拆出, 走工厂注入 deps (els) 让
// 函数体不动直接搬过来.
//
// 调用方 (app.js 早期 init):
//   const ui = window.AnythingApp.uiHelpers({ els });
//   const { escapeHtml, toast, openDrawer, closeDrawer, showPlanApprovalModal } = ui;
// ============================================================

(function () {
    window.AnythingApp = window.AnythingApp || {};

    window.AnythingApp.uiHelpers = function uiHelpers(deps) {
        const { els } = deps;

        // escapeHtml — 5 字符版本 (包含 '), 原 app.js 两处实现合一
        function escapeHtml(s) {
            return String(s).replace(/[&<>"']/g, ch => ({
                '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
            }[ch]));
        }

        function toast(type, title, body) {
            const t = document.createElement('div');
            t.className = `toast ${type}`;
            t.innerHTML = `<div class="toast-title">${title}</div>` +
                (body ? `<div class="toast-body">${escapeHtml(String(body))}</div>` : '');
            els.toastContainer.appendChild(t);
            setTimeout(() => {
                t.style.opacity = '0';
                t.style.transform = 'translateX(20px)';
                setTimeout(() => t.remove(), 300);
            }, 3500);
        }

        function openDrawer(name) {
            const drawer = document.getElementById(`${name}-drawer`);
            if (drawer) drawer.hidden = false;
        }

        function closeDrawer(name) {
            const drawer = document.getElementById(`${name}-drawer`);
            if (drawer) drawer.hidden = true;
        }

        // showPlanApprovalModal — Task DD (#64) plan 审批
        function showPlanApprovalModal(plan, { onApprove, onCancel }) {
            if (!els.planDrawer) {
                // DOM 元素缺失 → fallback to auto-approve (向后兼容旧前端)
                if (onApprove) onApprove();
                return;
            }
            els.planThought.textContent = plan.thought || '(无 thought)';

            if (plan.action && plan.action.tool) {
                els.planActionSection.hidden = false;
                els.planFinalSection.hidden = true;
                els.planToolName.textContent = plan.action.tool;
                try {
                    els.planToolInput.textContent = JSON.stringify(plan.action.input || {}, null, 2);
                } catch (e) {
                    els.planToolInput.textContent = String(plan.action.input || '');
                }
            } else if (plan.final_answer) {
                els.planActionSection.hidden = true;
                els.planFinalSection.hidden = false;
                els.planFinalAnswer.textContent = plan.final_answer;
            } else {
                els.planActionSection.hidden = true;
                els.planFinalSection.hidden = true;
            }

            const handleApprove = () => { cleanup(); if (onApprove) onApprove(); };
            const handleCancel = () => { cleanup(); if (onCancel) onCancel(); };
            const handleBackdrop = (e) => {
                if (e.target && e.target.matches('[data-close="plan"]')) handleCancel();
            };
            const cleanup = () => {
                els.planApproveBtn.removeEventListener('click', handleApprove);
                els.planCancelBtn.removeEventListener('click', handleCancel);
                els.planDrawer.removeEventListener('click', handleBackdrop);
            };
            els.planApproveBtn.addEventListener('click', handleApprove);
            els.planCancelBtn.addEventListener('click', handleCancel);
            els.planDrawer.addEventListener('click', handleBackdrop);

            openDrawer('plan');
        }

        return { escapeHtml, toast, openDrawer, closeDrawer, showPlanApprovalModal };
    };
})();
