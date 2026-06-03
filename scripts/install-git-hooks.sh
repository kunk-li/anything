#!/bin/sh
# 安装项目 git hooks。
#
# 用户固定偏好 (2026-06-03): main 上的提交自动 push 到 origin/main, 不询问。
# 这是"不依赖 AI 会话"的机制性双保险 —— 由 git 本地 hook 保证, 任何来源
# (人工 / 工具 / AI) 在 main 上的 commit 都会触发自动推送。
#
# 用法 (clone 仓库后跑一次即可恢复):
#   sh scripts/install-git-hooks.sh
set -e

HOOK_DIR="$(git rev-parse --git-dir)/hooks"
mkdir -p "$HOOK_DIR"

cat > "$HOOK_DIR/post-commit" <<'HOOK'
#!/bin/sh
# [自动生成 by scripts/install-git-hooks.sh] 提交后自动推送 origin/main。
# 仅在当前分支为 main 时推送, 避免误推其他分支。
# git push 不会再触发 commit, 故无递归风险; push 失败不影响已完成的 commit。
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ "$branch" = "main" ]; then
  echo "[post-commit] auto-push -> origin/main ..."
  git push origin main || echo "[post-commit] push 未成功, 可稍后手动 git push origin main"
fi
HOOK

chmod +x "$HOOK_DIR/post-commit"
echo "[install-git-hooks] 已安装 post-commit hook (auto-push origin/main) -> $HOOK_DIR/post-commit"
