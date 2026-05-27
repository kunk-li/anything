#!/usr/bin/env bash
# 本地全量回归脚本:跟 .github/workflows/ci.yml 的 "Run unit tests per module"
# 步骤保持一致,便于推送前本地复跑 CI 矩阵。
#
# 用法:
#   bash scripts/run_tests.sh           # 跑全部模块
#   bash scripts/run_tests.sh -v        # 详细模式
#
# 环境要求:
#   - 已激活包含项目依赖的 Python 环境(faiss-cpu / sentence-transformers / pydantic 等)
#   - cwd 必须是仓库根目录
set -u

cd "$(dirname "$0")/.."

# smoke 链路在没真实 LLM key 时启用占位回退
export ANYTHING_DEV_MODE="${ANYTHING_DEV_MODE:-1}"
export PYTHONPATH="basic_support:data_layer:business:interface:application:run:."

modules=(
  "basic_support/schema_module"
  "basic_support/deps_module"
  "basic_support/config_module"
  "basic_support/observability_module"
  "data_layer/chunker_module"
  "data_layer/vector_db_module"
  "data_layer/document_store_module"
  "data_layer/state_store_module"
  "business/rag_module"
  "business/agent_module"
  "interface/request_response_module"
  "application/api_service_module"
  "benchmarks"
)

verbose="${1:-}"
verbose_flag=""
if [ "$verbose" = "-v" ]; then
  verbose_flag="-v"
fi

pass=0
fail=0
fails=()

for m in "${modules[@]}"; do
  if [ -d "$m/tests" ]; then
    echo "=== $m ==="
    if python -m unittest discover -s "$m/tests" $verbose_flag 2>&1 | tail -5; then
      pass=$((pass+1))
    else
      fail=$((fail+1))
      fails+=("$m")
    fi
  fi
done

echo ""
echo "===================="
echo "PASSED modules: $pass"
echo "FAILED modules: $fail"
if [ "${#fails[@]}" -gt 0 ]; then
  echo "Failed: ${fails[*]}"
  exit 1
fi
exit 0
