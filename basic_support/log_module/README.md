# log_module（基础支撑层 - 日志模块）

本模块实现了统一的系统日志能力，支持：
- 按级别输出（DEBUG/INFO/WARNING/ERROR/CRITICAL）
- 控制台 + 文件双输出
- **多进程安全**：进程锁 + 每进程独立 FileHandler，对外透明
- 外部调用简洁：`from log_module import SystemLogger`

## 目录结构

```text
log_module/
├── __init__.py
├── core/
│   ├── base.py
│   └── impl.py
├── utils/
│   └── tool_functions.py
├── config/
│   └── config.py
├── tests/
│   ├── test_impl.py
│   └── test_multiprocess.py
└── requirements.txt
```

## 快速使用

```python
from log_module import SystemLogger

logger = SystemLogger()
logger.info("模块初始化完成")
logger.error("发生错误", logger_name="rag_module")
```

## 多进程使用

```python
import multiprocessing
import time
from log_module import SystemLogger

def worker(i: int):
    lg = SystemLogger()  # 子进程内直接使用即可
    lg.info(f"worker {i} start", logger_name=f"worker_{i}")
    time.sleep(0.2)
    lg.info(f"worker {i} end", logger_name=f"worker_{i}")

if __name__ == "__main__":
    SystemLogger().info("main start")
    ps = [multiprocessing.Process(target=worker, args=(i,), name=f"W-{i}") for i in range(3)]
    for p in ps: p.start()
    for p in ps: p.join()
    SystemLogger().info("main end")
```

## 配置说明

支持三种来源（优先级从高到低）：
1. 环境变量：`LOG_LEVEL` / `LOG_DIR` / `LOGGER_NAME`
2. 若项目中存在 `config_module`（设计文档依赖），则自动调用其 `ConfigManager` 读取 `log_level/log_dir/logger_name`
3. 默认值：INFO / logs / rag_agent_system

## 运行测试

在项目根目录执行：

```bash
python -m unittest -v log_module.tests.test_impl
python -m unittest -v log_module.tests.test_multiprocess
```

> 日志文件默认输出到 `logs/`，文件名形如 `system_YYYYMMDD.log`
