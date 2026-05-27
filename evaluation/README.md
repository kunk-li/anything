# evaluation — 业务质量评测

单元测试覆盖**契约级**正确性(签名/字段/异常路径);本目录覆盖**业务质量**:

- **RAG**: 召回率 / 文件命中率 / Citation 完整性
- **Agent**: 工具选择正确性 / 答案非空 / 响应码正确

## 目录结构

```
evaluation/
├── datasets/             # JSONL 评测数据集
│   ├── rag_basic.jsonl   # RAG 基础数据集 (5 cases)
│   └── agent_basic.jsonl # Agent 基础数据集 (4 cases)
├── run_eval.py           # 评测脚本入口
└── README.md             # 本文件
```

## 数据集格式

**RAG cases** (一行一个 JSON):
```json
{
  "id": "rag-001",
  "type": "rag",
  "query": "用户查询",
  "expected_file_keywords": ["期望出现在文件名中的关键词,任一命中即可"],
  "expected_content_keywords": ["期望出现在 answer 或 chunk 中的内容关键词"],
  "top_k": 5
}
```

**Agent cases**:
```json
{
  "id": "agent-001",
  "type": "agent",          // 或 "hybrid"
  "task": "用户任务",
  "expected_tools": ["llm_generate"],   // 调用的工具应至少含其中一个
  "expected_answer_min_length": 1,
  "expected_code": "SUCCESS"
}
```

## 使用

### 跑全部数据集
```bash
# 必须设置 PYTHONPATH (见 scripts/run_tests.sh 的同款做法)
export PYTHONPATH="basic_support:data_layer:business:interface:application:run:."
python evaluation/run_eval.py
```

### 跑指定数据集
```bash
python evaluation/run_eval.py --dataset evaluation/datasets/rag_basic.jsonl
```

### CI 阈值守护
```bash
python evaluation/run_eval.py --ci \
    --rag-file-hit-threshold 0.8 \
    --agent-success-threshold 0.75
```

任一指标低于阈值则 `exit 1`,可用于 PR 红绿守护。

### Verbose 输出每个 case 的指标
```bash
python evaluation/run_eval.py -v
```

## 指标定义

| 指标 | 含义 | 计算 |
|---|---|---|
| `rag_recall_keyword` | 平均关键词召回率 | 答案 + 文件名中命中 expected_content_keywords 的比例 |
| `rag_file_hit` | 文件级命中率 | 检索 chunks 的 file_name 是否含 expected_file_keywords 任一关键词 |
| `rag_has_citations` | 引用完整性 | 响应是否含非空 citations |
| `agent_code_match` | 响应码匹配率 | code == expected_code 的比例 |
| `agent_tools_match` | 工具选择匹配率 | 调用工具 ∩ expected_tools ≠ ∅ |
| `agent_answer_nonempty` | 答案非空率 | len(answer) >= expected_answer_min_length |

## 通过判定

- **RAG case PASS**:`code == SUCCESS` 且 `file_hit == 1` 且 `recall_keyword >= 0.5`
- **Agent case PASS**:`code_match == 1` 且 `tools_match == 1` 且 `answer_nonempty == 1`

## 扩展数据集

新增 case 时:
1. 决定 case 类型(rag/agent/hybrid)
2. 想清楚"成功"的判定标准:用文件关键词?内容关键词?
3. 在对应 jsonl 文件追加一行
4. 跑一次 `python evaluation/run_eval.py --verbose` 看 metrics 是否符合预期

## 为什么不上 CI?

主 CI(`.github/workflows/ci.yml`) **不** 自动跑业务质量评测,因为:

- 评测需要真实的向量库 + 已索引的文档(需要先跑 `run/index_build.py`)
- 评测需要真实 LLM(GitHub runner 上 401 时只能跑 Dummy,指标无意义)
- 评测较慢(每个 case 涉及 LLM 调用,30 个 case 约 3-5 分钟)

更合适的做法:
- 本地开发时手工跑(回归时)
- 部署前在 staging 环境跑
- 或加专门的"nightly eval" Action(配 secrets 包含真实 API key)
