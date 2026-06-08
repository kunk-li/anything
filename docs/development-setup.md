# 开发者本地环境设置

> 目标读者:第一次拿到代码仓库的开发者 / 想跑测试或评测的协作者

## 1. 依赖

```bash
# 主依赖
pip install -r requirements.txt

# 测试 / 运行额外依赖
pip install pydantic faiss-cpu sentence-transformers

# 文档解析可选依赖(只在使用 document_parser 时需要)
pip install xmltodict PyPDF2 python-docx pandas python-pptx

# 本地 pre-commit hook(可选,推荐)
pip install pre-commit
```

## 2. 启用 pre-commit hooks(推荐)

```bash
# 一次性安装(写入 .git/hooks/pre-commit)
pre-commit install
```

之后 `git commit` 会自动跑以下守护:

| Hook | 作用 | 触发条件 |
|---|---|---|
| `abc-alignment` | 10 个 (Base, Impl) 配对签名漂移检测 | 改 `core/base.py` / `core/impl.py` / `__init__.py` 时 |
| `fast-unit-tests` | schema + deps + chunker 共 51 个单测 | 改任何 `.py` 时 |
| `check-yaml` / `end-of-file-fixer` / `trailing-whitespace` | 标准格式 | 全部文件 |

失败时提交被阻止。手动跑一次:`pre-commit run --all-files`

## 3. 本地全量回归

```bash
# 9 模块共 122 单测
bash scripts/run_tests.sh

# verbose 详细输出
bash scripts/run_tests.sh -v
```

## 4. ABC 守护(独立跑)

```bash
# 普通模式(打印结果,exit 0 即使有漂移)
python scripts/check_abc_alignment.py

# CI 模式(有漂移就 exit 1)
python scripts/check_abc_alignment.py --ci
```

## 5. Smoke Test(端到端联调)

```bash
cd run
PYTHONPATH="../basic_support:../data_layer:../business:../interface:../application:.:.." \
    python run_smoke_test.py
```

会跑 3 个 case(rag / agent / hybrid),无 API key 时自动回退 DummyLLMClient。

## 6. 索引文档建库

```bash
cd run
PYTHONPATH="../basic_support:../data_layer:../business:../interface:../application:.:.." \
    python index_build.py --source-type folder --source-path ../doc
```

索引产物在 `run/vector_store/` / `run/documents/`。

## 7. 业务质量评测

```bash
PYTHONPATH="basic_support:data_layer:business:interface:application:run:." \
    python evaluation/run_eval.py -v
```

详见 [evaluation/README.md](../evaluation/README.md)。

## 8. PYTHONPATH 设置(macOS / Linux / WSL)

为避免每次都手工设 PYTHONPATH,建议在 shell rc 里加:

```bash
# ~/.bashrc 或 ~/.zshrc(假设仓库在 ~/projects/anything)
export ANYTHING_ROOT="$HOME/projects/anything"
export PYTHONPATH="$ANYTHING_ROOT/basic_support:$ANYTHING_ROOT/data_layer:$ANYTHING_ROOT/business:$ANYTHING_ROOT/interface:$ANYTHING_ROOT/application:$ANYTHING_ROOT/run:$ANYTHING_ROOT"
```

## 9. 配置覆盖

支持环境变量 / yaml / impl 默认参数三层优先级,详见 [docs/configuration-priority.md](configuration-priority.md)。

常用环境变量:

| 变量 | 作用 |
|---|---|
| `ANYTHING_DEV_MODE=1` | 启用 dev 模式(允许 fallback / DummyLLM)|
| `OPENAI_API_KEY` | 真实 LLM 凭证 |
| `DASHSCOPE_API_KEY` | DashScope 凭证 |

## 10. CI 红绿

每个 push / PR 触发 [.github/workflows/ci.yml](../.github/workflows/ci.yml):

| 步骤 | 严格度 |
|---|---|
| Lint (ruff, 独立 job) | 阻塞 |
| 全量 pytest（取代旧“9 模块单测”） | 阻塞 |
| ABC alignment check | 阻塞 |
| Smoke test 端到端 | 阻塞(Run #5 起) |

## 11. 代码风格 / Lint（ruff）

仓库用 [ruff](https://docs.astral.sh/ruff/) 做静态检查（pyflakes F 规则），配置见根目录 `ruff.toml`。

```bash
# 本地全仓检查（与 CI lint job 同口径）
python -m ruff check .
```

- CI 有**独立 lint job**（与单测并行），`ruff check .` 失败即 CI 红。
- **切勿对本仓盲跑 `ruff check --fix`**：多处 `impl.py` / `__init__.py` 有意 re-export
  “看似未用”的符号（back-compat + 被测试 import），`--fix` 会按 F401 把它们删掉而破坏导入；
  个别 import 带 `# 保留` 注释专供单测 monkeypatch。原因详见 `ruff.toml` 顶部注释，需要修时逐条人工核对。
