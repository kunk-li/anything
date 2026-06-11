// ============================================================
// Anything Frontend — 极简 i18n (中英双语)
//
// 用法:
//   t('chat.send')              -> '发送' (zh) / 'Send' (en)
//   t('chat.error', {code: 'X'})-> 占位符替换
//   setLocale('en')             -> 切换语言, 触发 data-i18n 重渲染
//   getLocale()                 -> 当前语言代码
//
// 标记规则:
//   HTML 元素加 data-i18n="key" 自动翻译 textContent
//   data-i18n-attr="placeholder:key,title:key2" 翻译属性
// ============================================================

const I18n = (() => {
    const STORAGE_KEY = 'anything_locale';

    const dict = {
        zh: {
            // header
            'app.title': 'Anything',
            'app.subtitle': 'RAG & Agent',
            'header.tenant': 'tenant',
            'header.health.checking': '检测中',
            'header.health.up': 'UP',
            'header.health.down': 'DOWN',
            'header.settings': '设置',
            'header.lang': '语言',

            // welcome
            'welcome.title': '欢迎使用 Anything',
            'welcome.desc': 'RAG 检索 / Agent 任务执行 / Hybrid 混合,选择模式后输入开始对话。',
            'welcome.hint.rag': '<b>RAG</b> 模式适合"在文档里找答案" — 输入问题,返回引用的 chunks',
            'welcome.hint.agent': '<b>Agent</b> 模式适合"调工具完成任务" — 输入任务,返回执行步骤 + 答案',
            'welcome.hint.hybrid': '<b>Hybrid</b> 模式综合两者 — 检索 + 推理',

            // composer
            'composer.send': '发送',
            'composer.topk': 'top_k',
            'composer.stream': '流式',
            'composer.stream.title': '勾选后用 WebSocket 流式接收答案 (打字机效果)',
            'composer.plan': '计划',
            'composer.plan.title': 'Plan mode — Agent 先输出计划让你审批, 不执行工具 (借鉴 Claude Code)',
            'composer.reflect': '反思',
            'composer.reflect.title': 'Reflection 反思环 — 答案后跑 critique → revise 二阶段优化 (Reflexion / OpenAI o1)',
            'reflect.modal.title': '✨ Reflection 反思详情',
            'reflect.modal.issues': '🔍 发现的问题',
            'reflect.modal.missing': '📋 缺失信息',
            'reflect.modal.skipped': '⏭ 跳过原因',
            'reflect.modal.raw': '📄 完整 critique (JSON)',
            'sessions.title': '会话切换 (Sessions)',
            'sessions.refresh': '🔁 刷新',
            'sessions.new': '+ 新会话',
            'sessions.delete': '✕ 删除',
            'sessions.hint': '每个 session 独立的 Agent 状态 + RAG 对话历史.',
            'trace.modal.title': '🔍 Trace 时序详情',
            'trace.modal.timeline': '⏱ 阶段时序',
            'trace.modal.raw': '📄 原始 response',
            'plan.modal.title': '计划审批',
            'plan.modal.hint': 'Agent 准备执行以下计划. 审批后执行, 或取消返回. (Claude Code Plan Mode 风格)',
            'plan.modal.thought': '💭 思考',
            'plan.modal.action': '🔧 拟调工具',
            'plan.modal.final': '🎯 最终答案 (不调工具)',
            'plan.modal.approve': '✓ 批准执行',
            'plan.modal.cancel': '✗ 取消',
            'plan.modal.pending_hint': '⏸ 计划待审批 — 见下方弹窗',
            'plan.modal.cancelled': '已取消执行',
            'composer.stop': '停止',
            'composer.stop.toast': '已中断生成',
            'export.title': '导出对话',
            'export.hint': '把当前 chat 下载到本地, 支持 Markdown / JSON 两种格式.',
            'export.md': '📝 Markdown',
            'export.json': '{ } JSON',
            'export.empty': '没有可导出的消息',
            'export.done': '导出成功',
            'export.messages': '条消息',
            'docs.refresh': '已索引文档',
            'docs.hint': '从 /documents 拉取',
            'docs.loading': '加载中...',
            'docs.empty': '(空)',
            'docs.count_suffix': '个文档',
            'docs.delete': '删除',
            'docs.deleted': '已删除',
            'docs.delete_fail': '删除失败',
            'docs.confirm_delete': '确定删除这个文档?',
            'composer.stop': '停止',
            'composer.drop.hint': '拖拽文件到这里 (图片 / PDF / Word / Excel / 文本等)',
            'composer.attach.upload_image': '点击或拖拽文件 (图片/文档)',
            'composer.attach.remove': '移除',
            'composer.attach.uploading': '上传中...',
            'composer.attach.file_default_prompt': '请分析这个附件的内容',
            'composer.attach.files_default_prompt': '请分析这些附件的内容',
            'composer.attach.unit': '个附件',
            'toast.attach.added': '已添加附件',
            'toast.attach.invalid': '不支持的文件类型',
            'toast.attach.invalid.body': '支持图片及 txt/pdf/docx/md/xlsx/ppt/csv/json/html 等文档',
            'toast.attach.upload_fail': '附件上传失败',
            'composer.placeholder.rag': '输入问题, 在已索引文档里检索答案... (Ctrl+Enter 发送)',
            'composer.placeholder.agent': '描述一个任务, Agent 会拆解为工具调用步骤... (Ctrl+Enter 发送)',
            'composer.placeholder.hybrid': '同时使用检索与推理 (任务描述)... (Ctrl+Enter 发送)',

            // sidebar
            'sidebar.retrieved': '检索结果',
            'sidebar.steps': '工具调用',
            'sidebar.metrics': '系统指标',
            'sidebar.upload': '文档',
            'sidebar.retrieved.empty': '最近一次 RAG 检索的 chunks 会显示在这里',
            'sidebar.steps.empty': '最近一次 Agent 任务的工具调用步骤会显示在这里',
            'sidebar.metrics.refresh': '刷新',
            'sidebar.metrics.hint': '来自 /metrics (Prometheus 文本)',
            'sidebar.metrics.placeholder': '点击刷新查看实时指标',
            'sidebar.upload.label': '点击或拖拽多文件到此上传',
            'sidebar.upload.hint': '支持 md / txt / pdf / docx, 支持多选',
            'sidebar.upload.build': '触发索引构建',
            'sidebar.admin': '管理',
            'sidebar.memory': '记忆',
            'memory.refresh': '刷新',
            'memory.search': '搜索',
            'memory.search.placeholder': '搜索 facts...',
            'memory.search.empty': '请输入搜索词',
            'memory.hint': '长期记忆 fact 库',
            'memory.empty': '记忆库还是空的, 跟 Agent 多聊聊就有了',
            'memory.loading': '加载中...',
            'memory.searching': '搜索中...',
            'memory.access_count': '使用次数',
            'memory.count_suffix': '条 fact',
            'admin.refresh': '刷新',
            'admin.hint': '来自 /admin/status (只读)',
            'admin.empty': '点击刷新查看运行期状态',
            'admin.section.rag': 'RAG 配置',
            'admin.section.bm25': 'BM25 倒排索引',
            'admin.section.vector': '向量库',
            'admin.section.llm': 'LLM 模型',
            'admin.section.uploads': '上传文件',
            'admin.section.security': '安全',
            'admin.kv.hybrid': '混合检索',
            'admin.kv.rerank': '重排',
            'admin.kv.rewrite': '查询改写',
            'admin.kv.topk_retrieve': '检索 top_k',
            'admin.kv.topk_rerank': '重排 top_k',
            'admin.kv.history_max_turns': '会话记忆 (轮)',
            'admin.kv.rrf_k': 'RRF k',
            'admin.kv.bm25_size': 'chunks 数',
            'admin.kv.bm25_avg': '平均 token/chunk',
            'admin.kv.vec_ntotal': '向量条数',
            'admin.kv.llm_count': '已注册模型',
            'admin.kv.upload_count': '上传文件数',
            'admin.kv.auth_enabled': '认证启用',
            'admin.kv.tenants': '已注册租户',
            'on': '开',
            'off': '关',

            // settings drawer
            'settings.title': '设置',
            'settings.apiBase': 'API Base URL',
            'settings.apiBase.placeholder': '同域(留空)',
            'settings.apiBase.hint': '留空 = 使用当前页面所在域名,生产部署时配置网关地址',
            'settings.apiKey': 'X-API-Key',
            'settings.apiKey.placeholder': '可选,认证时需要',
            'settings.apiKey.hint': '配置 security.auth_enabled=true 时必填',
            'settings.session': 'Session ID',
            'settings.session.placeholder': '自动生成',
            'settings.session.hint': '用于跨请求关联,留空自动生成',
            'settings.locale': '界面语言',
            'settings.save': '保存',
            'settings.clear': '清空对话历史',

            // toast
            'toast.settings.saved': '设置已保存',
            'toast.settings.saved.body': '同时已应用到下次请求',
            'toast.input.empty': '输入为空',
            'toast.input.empty.body': '请输入内容后再发送',
            'toast.network.error': '网络异常',
            'toast.copied.trace': '已复制 trace_id',
            'toast.copied.error': '已复制错误详情',
            'toast.history.cleared': '对话已清空',
            'toast.upload.success': '文件已上传',
            'toast.upload.fail': '上传失败',
            'toast.upload.exception': '上传异常',
            'toast.index.triggered': '索引构建已触发',

            // role / misc
            'role.user': '你',
            'role.assistant': 'Anything',
            'msg.processing': '正在处理...',
            'msg.empty': '(无响应)',
            'action.retry': '↻ 重试',
            'action.copyResp': '复制响应',
            'confirm.clearHistory': '确定清空所有对话历史?',
            'health.checkAgain.title': '点击重新检测',

            // preview
            'preview.title': '文档预览',
            'preview.loading': '加载预览中...',
            'preview.error': '加载预览失败',
            'preview.totalChars': '总长度',
            'preview.range': '高亮范围',
            'preview.viewOriginal': '查看原文',

            // models management
            'models.title': 'LLM 模型管理',
            'models.hint': '运行期注册的模型 + key, 不持久化到 yaml (重启丢失)。生产部署应在网关屏蔽 /config/* 端点。',
            'models.refresh': '刷新',
            'models.add': '+ 新增',
            'models.col.name': '名称',
            'models.col.type': '类型',
            'models.col.key': 'Key',
            'models.col.action': '操作',
            'models.empty': '点击刷新查看模型列表',
            'models.empty.list': '尚未注册任何模型',
            'models.field.name': '模型名',
            'models.field.type': '类型',
            'models.field.adapter': '适配器',
            'models.field.apiBase': 'API Base',
            'models.field.apiKey': 'API Key',
            'models.field.setDefault': '同时设为该类型的默认',
            'models.submit': '保存',
            'models.cancel': '取消',
            'models.action.edit': '编辑',
            'models.action.setDefault': '设默认',
            'models.action.delete': '删除',
            'models.action.default': '✓ 默认',
            'models.toast.refreshed': '已刷新',
            'models.toast.saved': '已保存',
            'models.toast.deleted': '已删除',
            'models.toast.defaultSet': '默认已设置',
            'models.confirm.delete': '确定删除模型 {name}?',
            'models.error': '操作失败',

            // Task XXXX-7 (#154): 补漏 — 快捷键 modal / workflow / theme / welcome examples
            'shortcuts.title': '键盘快捷键',
            'shortcuts.section.chat': '聊天',
            'shortcuts.section.sessions': '会话',
            'shortcuts.section.ui': '界面',
            'shortcuts.desc.send': '发送消息',
            'shortcuts.desc.escape': '关闭弹窗 / 停止生成',
            'shortcuts.desc.new_session': '新建会话',
            'shortcuts.desc.focus_search': '聚焦会话搜索框',
            'shortcuts.desc.help': '显示本帮助',
            'shortcuts.desc.open_settings': '打开设置',
            'shortcuts.hint': '提示: 这些快捷键不影响输入框内的正常打字',

            'workflow.save_template': '💾 存为模板',
            'workflow.my_templates': '📋 我的模板',
            'workflow.modal.title': '📋 我的任务模板',
            'workflow.modal.hint': '点模板回填到输入框. 模板存在浏览器 localStorage, 跨设备不同步.',

            'theme.label': '主题',
            'theme.dark': '🌙 暗色',
            'theme.light': '☀️ 亮色',
            'theme.auto': '🖥 跟系统',

            'docs.section.upload': '📤 上传新文档',
            'docs.section.indexed': '📚 已索引文档',
            'docs.refresh_full': '🔁 刷新列表',

            'agent.tools.title': 'Agent 可用工具',
            'agent.tools.hint': '在 Agent 模式下让 LLM 调用',
            'agent.tools.loading': '加载中…',

            'sessions.empty': '无会话',
            'sessions.search.placeholder': '🔍 搜索会话…',

            // Task XXXX-3 / XXXX-4 / XXXX-6 复制按钮 / 撤销 / 折叠
            'msg.copy': '📋',
            'msg.copy.done': '✓',
            'msg.fold.expand': '▼ 展开全部',
            'msg.fold.collapse': '▲ 收起',
            'session.undo.title': '即将删除会话',
            'session.undo.body': '5 秒内可撤销',
            'session.undo.btn': '↩ 撤销',

            // Task XXXX-20 (#164): chat model picker
            'settings.chatModel': 'Chat 模型',
            'settings.chatModel.auto': '⚙ 自动 (用配置默认)',
            'settings.chatModel.hint': '选一个 = 所有 chat 走这个; 不选 = 走 .env / yaml 里 default_chat_model 配置. 想加更多型号去下面 "LLM 模型管理" 注册.',
            'toast.chatModel.set': '默认 chat 模型已切换',
            'toast.chatModel.cleared': '已清空选择, 回退配置默认',
        },
        en: {
            'app.title': 'Anything',
            'app.subtitle': 'RAG & Agent',
            'header.tenant': 'tenant',
            'header.health.checking': 'Checking',
            'header.health.up': 'UP',
            'header.health.down': 'DOWN',
            'header.settings': 'Settings',
            'header.lang': 'Language',

            'welcome.title': 'Welcome to Anything',
            'welcome.desc': 'RAG retrieval / Agent task execution / Hybrid — pick a mode and start typing.',
            'welcome.hint.rag': '<b>RAG</b> mode answers questions from indexed docs — returns cited chunks',
            'welcome.hint.agent': '<b>Agent</b> mode calls tools to complete a task — returns steps + answer',
            'welcome.hint.hybrid': '<b>Hybrid</b> combines both — retrieval + reasoning',

            'composer.send': 'Send',
            'composer.topk': 'top_k',
            'composer.stream': 'Stream',
            'composer.stream.title': 'Use WebSocket streaming (typewriter effect)',
            'composer.plan': 'Plan',
            'composer.plan.title': 'Plan mode — Agent emits plan for review before executing tools (Claude Code-style)',
            'composer.reflect': 'Reflect',
            'composer.reflect.title': 'Reflection — critique → revise the initial answer (Reflexion / OpenAI o1 style)',
            'reflect.modal.title': '✨ Reflection details',
            'reflect.modal.issues': '🔍 Issues found',
            'reflect.modal.missing': '📋 Missing info',
            'reflect.modal.skipped': '⏭ Skip reason',
            'reflect.modal.raw': '📄 Full critique (JSON)',
            'sessions.title': 'Sessions',
            'sessions.refresh': '🔁 Refresh',
            'sessions.new': '+ New session',
            'sessions.delete': '✕ Delete',
            'sessions.hint': 'Each session keeps independent Agent state + RAG history.',
            'trace.modal.title': '🔍 Trace timeline',
            'trace.modal.timeline': '⏱ Phases',
            'trace.modal.raw': '📄 Raw response',
            'plan.modal.title': 'Plan Approval',
            'plan.modal.hint': 'The agent prepared the following plan. Approve to execute, or cancel.',
            'plan.modal.thought': '💭 Thought',
            'plan.modal.action': '🔧 Proposed tool call',
            'plan.modal.final': '🎯 Final answer (no tool needed)',
            'plan.modal.approve': '✓ Approve',
            'plan.modal.cancel': '✗ Cancel',
            'plan.modal.pending_hint': '⏸ Plan pending approval — see modal below',
            'plan.modal.cancelled': 'Execution cancelled',
            'composer.stop': 'Stop',
            'composer.stop.toast': 'Generation stopped',
            'export.title': 'Export Conversation',
            'export.hint': 'Download the current chat as Markdown or JSON.',
            'export.md': '📝 Markdown',
            'export.json': '{ } JSON',
            'export.empty': 'Nothing to export',
            'export.done': 'Exported',
            'export.messages': 'messages',
            'docs.refresh': 'Indexed docs',
            'docs.hint': 'From /documents',
            'docs.loading': 'Loading...',
            'docs.empty': '(empty)',
            'docs.count_suffix': 'docs',
            'docs.delete': 'Delete',
            'docs.deleted': 'Deleted',
            'docs.delete_fail': 'Delete failed',
            'docs.confirm_delete': 'Delete this document?',
            'composer.stop': 'Stop',
            'composer.drop.hint': 'Drop files here (images / PDF / Word / Excel / text...)',
            'composer.attach.upload_image': 'Click or drop files (images/documents)',
            'composer.attach.remove': 'Remove',
            'composer.attach.uploading': 'Uploading...',
            'composer.attach.file_default_prompt': 'Please analyze this attachment',
            'composer.attach.files_default_prompt': 'Please analyze these attachments',
            'composer.attach.unit': 'attachment(s)',
            'toast.attach.added': 'Attachment added',
            'toast.attach.invalid': 'Unsupported file type',
            'toast.attach.invalid.body': 'Images and documents (txt/pdf/docx/md/xlsx/ppt/csv/json/html...) are supported',
            'toast.attach.upload_fail': 'Attachment upload failed',
            'composer.placeholder.rag': 'Ask a question; will search indexed docs... (Ctrl+Enter to send)',
            'composer.placeholder.agent': 'Describe a task; agent breaks it into tool calls... (Ctrl+Enter to send)',
            'composer.placeholder.hybrid': 'Task description with both retrieval and reasoning... (Ctrl+Enter to send)',

            'sidebar.retrieved': 'Chunks',
            'sidebar.steps': 'Tool calls',
            'sidebar.metrics': 'Metrics',
            'sidebar.upload': 'Docs',
            'sidebar.retrieved.empty': 'Recent RAG retrieved chunks will appear here',
            'sidebar.steps.empty': 'Recent Agent tool-call steps will appear here',
            'sidebar.metrics.refresh': 'Refresh',
            'sidebar.metrics.hint': 'From /metrics (Prometheus text)',
            'sidebar.metrics.placeholder': 'Click refresh to view live metrics',
            'sidebar.upload.label': 'Click or drag multiple files here',
            'sidebar.upload.hint': 'Supports md / txt / pdf / docx, multi-select OK',
            'sidebar.upload.build': 'Trigger index build',
            'sidebar.admin': 'Admin',
            'sidebar.memory': 'Memory',
            'memory.refresh': 'Refresh',
            'memory.search': 'Search',
            'memory.search.placeholder': 'Search facts...',
            'memory.search.empty': 'Please type a query',
            'memory.hint': 'Long-term memory facts',
            'memory.empty': 'No memory yet — chat with the agent to accumulate',
            'memory.loading': 'Loading...',
            'memory.searching': 'Searching...',
            'memory.access_count': 'Access count',
            'memory.count_suffix': 'facts',
            'admin.refresh': 'Refresh',
            'admin.hint': 'From /admin/status (read-only)',
            'admin.empty': 'Click refresh to view runtime status',
            'admin.section.rag': 'RAG config',
            'admin.section.bm25': 'BM25 inverted index',
            'admin.section.vector': 'Vector DB',
            'admin.section.llm': 'LLM models',
            'admin.section.uploads': 'Uploaded files',
            'admin.section.security': 'Security',
            'admin.kv.hybrid': 'Hybrid search',
            'admin.kv.rerank': 'Rerank',
            'admin.kv.rewrite': 'Query rewrite',
            'admin.kv.topk_retrieve': 'top_k_retrieve',
            'admin.kv.topk_rerank': 'top_k_rerank',
            'admin.kv.history_max_turns': 'history (turns)',
            'admin.kv.rrf_k': 'RRF k',
            'admin.kv.bm25_size': 'chunks',
            'admin.kv.bm25_avg': 'avg tokens/chunk',
            'admin.kv.vec_ntotal': 'vectors',
            'admin.kv.llm_count': 'registered models',
            'admin.kv.upload_count': 'uploaded files',
            'admin.kv.auth_enabled': 'auth enabled',
            'admin.kv.tenants': 'registered tenants',
            'on': 'on',
            'off': 'off',

            'settings.title': 'Settings',
            'settings.apiBase': 'API Base URL',
            'settings.apiBase.placeholder': 'Same origin (leave empty)',
            'settings.apiBase.hint': 'Empty = current page origin; set this for cross-origin gateway',
            'settings.apiKey': 'X-API-Key',
            'settings.apiKey.placeholder': 'Optional, required when auth enabled',
            'settings.apiKey.hint': 'Required when security.auth_enabled=true',
            'settings.session': 'Session ID',
            'settings.session.placeholder': 'Auto-generated',
            'settings.session.hint': 'Used for cross-request correlation; empty = auto',
            'settings.locale': 'UI Language',
            'settings.save': 'Save',
            'settings.clear': 'Clear chat history',

            'toast.settings.saved': 'Settings saved',
            'toast.settings.saved.body': 'Applied to next request',
            'toast.input.empty': 'Empty input',
            'toast.input.empty.body': 'Please type something first',
            'toast.network.error': 'Network error',
            'toast.copied.trace': 'trace_id copied',
            'toast.copied.error': 'Error details copied',
            'toast.history.cleared': 'Chat history cleared',
            'toast.upload.success': 'File uploaded',
            'toast.upload.fail': 'Upload failed',
            'toast.upload.exception': 'Upload exception',
            'toast.index.triggered': 'Index build triggered',

            'role.user': 'You',
            'role.assistant': 'Anything',
            'msg.processing': 'Processing...',
            'msg.empty': '(no response)',
            'action.retry': '↻ Retry',
            'action.copyResp': 'Copy response',
            'confirm.clearHistory': 'Clear all chat history?',
            'health.checkAgain.title': 'Click to re-check',

            'preview.title': 'Document preview',
            'preview.loading': 'Loading preview...',
            'preview.error': 'Failed to load preview',
            'preview.totalChars': 'Total chars',
            'preview.range': 'Highlight range',
            'preview.viewOriginal': 'View source',

            'models.title': 'LLM model registry',
            'models.hint': 'Runtime registrations + keys (not persisted to yaml, lost on restart). In production, block /config/* at the gateway.',
            'models.refresh': 'Refresh',
            'models.add': '+ Add',
            'models.col.name': 'Name',
            'models.col.type': 'Type',
            'models.col.key': 'Key',
            'models.col.action': 'Actions',
            'models.empty': 'Click refresh to view',
            'models.empty.list': 'No models registered yet',
            'models.field.name': 'Model name',
            'models.field.type': 'Type',
            'models.field.adapter': 'Adapter',
            'models.field.apiBase': 'API Base',
            'models.field.apiKey': 'API Key',
            'models.field.setDefault': 'Set as default for this type',
            'models.submit': 'Save',
            'models.cancel': 'Cancel',
            'models.action.edit': 'Edit',
            'models.action.setDefault': 'Set default',
            'models.action.delete': 'Delete',
            'models.action.default': '✓ default',
            'models.toast.refreshed': 'Refreshed',
            'models.toast.saved': 'Saved',
            'models.toast.deleted': 'Deleted',
            'models.toast.defaultSet': 'Default updated',
            'models.confirm.delete': 'Delete model {name}?',
            'models.error': 'Operation failed',

            // Task XXXX-7 (#154): mirror — shortcuts / workflow / theme / welcome examples
            'shortcuts.title': 'Keyboard shortcuts',
            'shortcuts.section.chat': 'Chat',
            'shortcuts.section.sessions': 'Sessions',
            'shortcuts.section.ui': 'UI',
            'shortcuts.desc.send': 'Send message',
            'shortcuts.desc.escape': 'Close modal / stop generation',
            'shortcuts.desc.new_session': 'New session',
            'shortcuts.desc.focus_search': 'Focus session search',
            'shortcuts.desc.help': 'Show this help',
            'shortcuts.desc.open_settings': 'Open settings',
            'shortcuts.hint': 'Tip: shortcuts do not interfere with normal typing in inputs',

            'workflow.save_template': '💾 Save as template',
            'workflow.my_templates': '📋 My templates',
            'workflow.modal.title': '📋 My task templates',
            'workflow.modal.hint': 'Click to fill back into input. Stored in browser localStorage, not synced across devices.',

            'theme.label': 'Theme',
            'theme.dark': '🌙 Dark',
            'theme.light': '☀️ Light',
            'theme.auto': '🖥 Follow system',

            'docs.section.upload': '📤 Upload new docs',
            'docs.section.indexed': '📚 Indexed docs',
            'docs.refresh_full': '🔁 Refresh',

            'agent.tools.title': 'Available agent tools',
            'agent.tools.hint': 'Called by the LLM in Agent mode',
            'agent.tools.loading': 'Loading…',

            'sessions.empty': 'No sessions',
            'sessions.search.placeholder': '🔍 Search sessions…',

            'msg.copy': '📋',
            'msg.copy.done': '✓',
            'msg.fold.expand': '▼ Expand all',
            'msg.fold.collapse': '▲ Collapse',
            'session.undo.title': 'Deleting session',
            'session.undo.body': 'Undo within 5 seconds',
            'session.undo.btn': '↩ Undo',

            'settings.chatModel': 'Chat model',
            'settings.chatModel.auto': '⚙ Auto (config default)',
            'settings.chatModel.hint': 'Pick one = all chat uses it; pick none = use default_chat_model from .env / yaml. Register more models below under "LLM model management".',
            'toast.chatModel.set': 'Default chat model switched',
            'toast.chatModel.cleared': 'Selection cleared, reverting to config default',
        },
    };

    let current = 'zh';
    const subscribers = [];

    function detect() {
        try {
            const saved = localStorage.getItem(STORAGE_KEY);
            if (saved && dict[saved]) return saved;
        } catch (_) {}
        const navLang = (navigator.language || navigator.userLanguage || '').toLowerCase();
        if (navLang.startsWith('en')) return 'en';
        return 'zh';
    }

    function t(key, params) {
        const tbl = dict[current] || dict.zh;
        let s = tbl[key] != null ? tbl[key] : (dict.zh[key] != null ? dict.zh[key] : key);
        if (params) {
            Object.keys(params).forEach((k) => {
                s = s.replace(new RegExp('\\{' + k + '\\}', 'g'), String(params[k]));
            });
        }
        return s;
    }

    function setLocale(loc) {
        if (!dict[loc]) return;
        current = loc;
        try { localStorage.setItem(STORAGE_KEY, loc); } catch (_) {}
        applyToDom();
        subscribers.forEach((fn) => { try { fn(loc); } catch (_) {} });
    }

    function getLocale() { return current; }
    function locales() { return Object.keys(dict); }

    function onChange(fn) { subscribers.push(fn); }

    /**
     * 扫描 DOM 上的 data-i18n / data-i18n-attr 节点并应用翻译。
     * 在 setLocale 时自动调用; 也可手工触发 (动态注入节点后)。
     */
    function applyToDom(root) {
        const scope = root || document;
        scope.querySelectorAll('[data-i18n]').forEach((el) => {
            const key = el.getAttribute('data-i18n');
            // 允许内含 HTML (welcome hints 有 <b>)
            el.innerHTML = t(key);
        });
        scope.querySelectorAll('[data-i18n-attr]').forEach((el) => {
            const spec = el.getAttribute('data-i18n-attr') || '';
            spec.split(',').forEach((pair) => {
                const [attr, key] = pair.split(':').map((s) => (s || '').trim());
                if (attr && key) el.setAttribute(attr, t(key));
            });
        });
        // 文档语言属性
        document.documentElement.setAttribute('lang', current === 'en' ? 'en' : 'zh-CN');
    }

    // 初始化
    current = detect();

    return { t, setLocale, getLocale, locales, onChange, applyToDom };
})();

// 浏览器全局脚本里 `const` 不会自动挂到 window 上 (跟 `var` 不同),
// 显式 attach 让 app.js 里 `window.I18n.t(...)` 这种调用能工作。
if (typeof window !== 'undefined') window.I18n = I18n;
