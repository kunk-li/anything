#!/usr/bin/env bash
# 多租户迁移脚本 — 把扁平目录的老数据搬到 <base>/default/ 子目录
#
# 触发场景:
#   Task #33 PR3b 上线后, 新数据按 <base>/<tenant_id>/ 子目录写;
#   读路径双重 fallback (default 租户可读老扁平). 运维一次性把老数据搬过去
#   后, 下个版本 (PR3b 后 1 周) 移除 fallback 强制 tenant 隔离.
#
# 关键约束 (docs/multi-tenancy-design.md §10.2):
#   1. 必须先停 ApiService / Console 进程 (本脚本不会做 stop)
#   2. 用 cp -a + rm 而非 mv, 防跨设备 fail
#   3. 三件套 (faiss.index + meta.json + embeddings.npy) 原子迁移
#   4. 完成后 ls run/vector_store/default/ 应有这三个文件
#   5. 重启服务前先验证, 出错可用 rollback_tenant_dirs.sh 撤回
#
# 用法:
#   bash scripts/migrate_to_tenant_dirs.sh                # 迁移 run/ 下数据到 default
#   bash scripts/migrate_to_tenant_dirs.sh /custom/base   # 自定义 base 目录
#   bash scripts/migrate_to_tenant_dirs.sh /base tenant-x # 迁移到指定 tenant 而非 default

set -euo pipefail

BASE="${1:-run}"
TARGET_TENANT="${2:-default}"

# 严格校验 tenant_id 字符集 (跟代码层一致, 防 path traversal)
if ! [[ "$TARGET_TENANT" =~ ^[a-z0-9_-]{3,32}$ ]]; then
    echo "ERROR: invalid tenant_id '$TARGET_TENANT' (must match [a-z0-9_-]{3,32})" >&2
    exit 1
fi

echo "=== Multi-tenancy migration ==="
echo "  base directory: $BASE"
echo "  target tenant:  $TARGET_TENANT"
echo

# 安全检查 — 提示运维确认 service 已停
read -r -p "Have you stopped ApiService / Console processes? (y/N) " confirm
case "$confirm" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Aborted. Please stop the service first."; exit 1;;
esac

total_migrated=0

for sub in vector_store documents state_store; do
    src="$BASE/$sub"
    dst="$src/$TARGET_TENANT"

    if [ ! -d "$src" ]; then
        echo "skip $sub: directory not found"
        continue
    fi

    # 收集 src 下需要迁移的项 (不含已经在 target tenant 子目录里的)
    declare -a items=()
    while IFS= read -r -d '' entry; do
        items+=("$entry")
    done < <(find "$src" -maxdepth 1 -mindepth 1 ! -name "$TARGET_TENANT" -print0)

    if [ ${#items[@]} -eq 0 ]; then
        echo "skip $sub: nothing to migrate (already migrated or empty)"
        continue
    fi

    mkdir -p "$dst"
    echo "migrating $sub:"
    for item in "${items[@]}"; do
        name=$(basename "$item")
        echo "  cp -a $name -> $TARGET_TENANT/"
        cp -a "$item" "$dst/"
    done
    # 复制完整再删除老路径 (cp + rm 模式, 不用 mv 防止跨设备问题)
    for item in "${items[@]}"; do
        rm -rf "$item"
    done

    total_migrated=$((total_migrated + ${#items[@]}))
    echo "  migrated ${#items[@]} items to $dst/"
done

echo
echo "=== Migration done: $total_migrated items migrated to tenant=$TARGET_TENANT ==="
echo "Next steps:"
echo "  1. Verify: ls $BASE/vector_store/$TARGET_TENANT/  (expect faiss.index + meta.json + embeddings.npy)"
echo "  2. Restart ApiService"
echo "  3. Smoke test, confirm RAG/Agent still work"
echo "  4. If problems within 24h: bash scripts/rollback_tenant_dirs.sh $BASE $TARGET_TENANT"
