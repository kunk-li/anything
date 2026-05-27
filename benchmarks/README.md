# benchmarks — 性能基线测试

## 目标

建立 API 服务的"内部链路性能基线":延迟分布(p50/p95/p99) + QPS。

让后续修改前后能对比,如果某次改动让 p99 慢一倍或错误率飙升,在 PR 阶段就能发现。

## 跑法

### 1. 首次建立 baseline

```bash
PYTHONPATH="basic_support:data_layer:business:interface:application:run:." \
    python benchmarks/bench_api.py \
        --concurrency 10 --total 100 \
        --output benchmarks/baseline.json
```

### 2. 修改后回归对比

```bash
PYTHONPATH=... python benchmarks/bench_api.py \
    --concurrency 10 --total 100 \
    --baseline benchmarks/baseline.json --ci \
    --max-p99-ratio 1.5 --max-error-rate 0.05
```

- `--ci` + `--baseline` 触发阈值守护
- `--max-p99-ratio 1.5`:p99 不能比 baseline 慢超过 1.5 倍
- `--max-error-rate 0.05`:错误率不能高于 5%
- 不达标 → exit 1,可直接挂在 CI 上

### 3. 自定义请求 body

当前默认 body:`{"type": "rag", "query": "什么是 RAG", "top_k": 3}`。如果想压测其他场景(Agent / Hybrid / 长 query),可以编辑脚本 `run_benchmark` 的 `request_body` 参数。

## 报告字段

```
concurrency / total / success / errors / error_rate
wall_time_seconds / qps
latency_seconds: { min, p50, mean, p95, p99, max }
errors_by_code: { "PARAM_MISSING": 1, ... }
```

## 设计说明

- **不引入额外压测工具**(locust / wrk):用 `ThreadPoolExecutor + TestClient`,直接打 FastAPI 的 ASGI 进程内调用,延迟数据反映纯逻辑路径,不含网络栈开销。
- **预热 1 次**:首次请求触发 sentence-transformers 模型加载(几百 ms),不计入统计。
- **dev_mode 默认开**:无 API key 时走 DummyLLMClient,延迟稳定可比。
- **本地基线 ≠ 生产 SLO**:真实生产 LLM 调用延迟在 500ms-3s 量级,本地基线只用于"代码改动有没有让内部链路变慢"。

## 后续

- 接入真实 LLM 后再跑一次,作为"端到端 baseline"(依赖 Task #30 secrets)
- 加 nightly perf workflow,baseline 自动更新到 `benchmarks/baseline.<date>.json`
- 输出 Prometheus 文本格式,推送到 long-term storage
