#!/usr/bin/env bash
# 多租户迁移回滚脚本 — 把 <base>/<tenant_id>/ 子目录数据搬回 <base>/ 扁平
#
# 使用场景:
#   migrate_to_tenant_dirs.sh 跑完后 24h 内发现严重问题, 想快速回到扁平结构.
#   超过 24h 不建议直接 rollback (期间已有新数据写到子目录, 老路径已删除).
#
# 用法:
#   bash scripts/rollback_tenant_dirs.sh                # 把 run/<sub>/default/ 搬回 run/<sub>/
#   bash scripts/rollback_tenant_dirs.sh /base tenant-x # 自定义

set -euo pipefail

BASE="${1:-run}"
SRC_TENANT="${2:-default}"

if ! [[ "$SRC_TENANT" =~ ^[a-z0-9_-]{3,32}$ ]]; then
    echo "ERROR: invalid tenant_id '$SRC_TENANT'" >&2
    exit 1
fi

echo "=== Multi-tenancy ROLLBACK ==="
echo "  base directory: $BASE"
echo "  source tenant:  $SRC_TENANT"
echo "  WARNING: 老扁平路径下的现有文件将被子目录文件覆盖"
echo

read -r -p "Have you stopped the service? Confirm rollback? (y/N) " confirm
case "$confirm" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Aborted."; exit 1;;
esac

total=0
for sub in vector_store documents state_store; do
    src="$BASE/$sub/$SRC_TENANT"
    dst="$BASE/$sub"

    if [ ! -d "$src" ]; then
        echo "skip $sub: $src not found"
        continue
    fi

    echo "rolling back $sub:"
    while IFS= read -r -d '' entry; do
        name=$(basename "$entry")
        echo "  cp -a $name -> $dst/"
        cp -a "$entry" "$dst/"
        total=$((total + 1))
    done < <(find "$src" -maxdepth 1 -mindepth 1 -print0)

    rm -rf "$src"
    echo "  removed $src"
done

echo
echo "=== Rollback done: $total items restored to flat dirs under $BASE/ ==="
echo "Recommendation: revert the code change (PR3b commit) before restarting"
echo "  to avoid the service immediately re-migrating data to subdirs."
