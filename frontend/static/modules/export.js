// ============================================================
// frontend/static/modules/export.js  (Task SS #79)
//
// 对话导出 — Markdown / JSON 两种格式. 从原 app.js exportConversation
// 拆出, 走 deps 注入 state/els/t/toast.
// ============================================================

(function () {
    window.AnythingApp = window.AnythingApp || {};

    window.AnythingApp.exporter = function exporter(deps) {
        const { state, els, t, toast } = deps;

        function exportConversation(fmt) {
            const messages = (state.history || state.messages || []).filter(m => m && m.content);
            if (!messages.length) {
                toast('warn', t('export.empty'), '');
                return;
            }
            let content, mime, ext;
            if (fmt === 'json') {
                content = JSON.stringify({
                    exported_at: new Date().toISOString(),
                    tenant: (els.tenantInput && els.tenantInput.value) || 'default',
                    messages: messages.map(m => ({
                        role: m.role,
                        mode: m.mode,
                        content: m.content,
                        ts: m.ts,
                        meta: m.meta || null,
                    })),
                }, null, 2);
                mime = 'application/json';
                ext = 'json';
            } else {
                // markdown
                const lines = [
                    `# Anything 对话导出`,
                    ``,
                    `**导出时间**: ${new Date().toISOString()}`,
                    `**Tenant**: ${(els.tenantInput && els.tenantInput.value) || 'default'}`,
                    `**消息数**: ${messages.length}`,
                    ``,
                    `---`,
                    ``,
                ];
                for (const m of messages) {
                    const role = m.role === 'user' ? '👤 用户' : '🤖 助手';
                    const modeBadge = m.mode ? ` \`[${m.mode}]\`` : '';
                    const tsStr = m.ts ? new Date(m.ts).toLocaleString() : '';
                    lines.push(`### ${role}${modeBadge} _${tsStr}_`);
                    lines.push('');
                    lines.push(m.content);
                    if (m.meta && m.meta.traceId) {
                        lines.push('');
                        lines.push(`> trace_id: \`${m.meta.traceId}\``);
                    }
                    lines.push('');
                    lines.push('---');
                    lines.push('');
                }
                content = lines.join('\n');
                mime = 'text/markdown';
                ext = 'md';
            }
            const blob = new Blob([content], { type: `${mime};charset=utf-8` });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
            a.href = url;
            a.download = `anything-chat-${stamp}.${ext}`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            toast('success', t('export.done'), `${messages.length} ${t('export.messages')}`);
        }

        return { exportConversation };
    };
})();
