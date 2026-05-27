# deps_module

基础支撑层依赖容器(DI 注入用)。

## 职责

把 `ConfigManager` / `SystemLogger` / `CommonUtils` / `ExceptionHandler` 这四个
基础组件**打包成一个容器**,在 bootstrap 阶段构造一次,向所有上层模块按引用注入,
避免每个模块都 new 一遍这些重对象。

## 使用

### bootstrap 阶段(构造一次)

```python
from deps_module import build_basic_deps

deps = build_basic_deps()
rag = SimpleRAG(llm_client=..., deps=deps)
agent = SimpleAgent(state_store=..., tool_registry=..., deps=deps)
orchestrator = SimpleOrchestrator(rag_runner=rag, agent_runner=agent, deps=deps)
```

### impl 类的 __init__(可选注入)

```python
def __init__(self, ..., deps: Optional[BasicDeps] = None):
    deps = deps or build_basic_deps()  # 向后兼容:未注入时自行构造
    self.config = deps.config
    self.logger = deps.logger
    self.utils = deps.utils
    self.exception_handler = deps.exception_handler
    # ... 后续配置项读取保持不变
```

## 为什么不用 frozen dataclass?

部分组件构造时会做副作用(load_config 等),外部代码可能在构造后再次操作 deps;
冻结实例会阻断这种合法使用,因此采用普通 dataclass。

## 设计偏离

本模块同 [schema_module](../schema_module/README.md),**有意偏离**架构规范中
"core/base.py + impl.py" 的统一目录结构。理由相同:依赖容器是数据声明,
没有"抽象接口/具体实现"二分关系。
