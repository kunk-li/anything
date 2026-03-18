# API服务模块（api_service_module）

## 1. 模块定位
API 服务模块属于应用层，对外提供 HTTP 接口，不承载核心业务逻辑，只负责：
- 接收 HTTP 请求；
- 校验并标准化输入；
- 调用接口层 `RequestHandler`；
- 返回统一 JSON 响应；
- 提供健康检查、探针、指标、上传与索引管理接口。

## 2. 目录结构
```text
api_service_module/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── base.py
│   └── impl.py
├── model/
│   ├── __init__.py
│   └── data_model.py
├── utils/
│   ├── __init__.py
│   └── tool_functions.py
├── config/
│   ├── __init__.py
│   └── config.py
├── tests/
│   ├── __init__.py
│   └── test_impl.py
├── README.md
└── requirements.txt
```

## 3. 快速启动
```python
from api_service_module.core.impl import ApiService
from request_response_module.core.impl import RequestHandler

handler = RequestHandler(orchestrator=...)
service = ApiService(handler=handler)
app = service.app
```

命令行启动：
```bash
uvicorn api_service_module.core.impl:app --host 0.0.0.0 --port 8000
```

## 4. 已实现接口
- `POST /invoke`：统一业务入口。
- `POST /index/build`：索引构建入口。
- `GET /index/job/{job_id}`：查询索引任务。
- `POST /documents/upload`：文档上传。
- `GET /health`、`GET /healthz`：健康检查。
- `GET /ready`：就绪探针。
- `GET /live`：存活探针。
- `GET /metrics`：Prometheus 文本指标。

## 5. 注入说明
- `handler`：必须注入接口层 `RequestHandler` 或兼容 `handle(dict) -> dict` 接口的对象。
- `index_builder`：可注入自定义索引构建器，需实现 `build(source_type, source_path, chunking)`。
- `dependency_checker`：可注入依赖健康检查函数，返回 `{依赖名: 状态}`。

## 6. 生产建议
- 启用 HTTPS 和 API Key / JWT 鉴权。
- 将 `InMemoryJobStore` 替换为 Redis 或数据库。
- 将 `LocalFolderIndexBuilder` 替换为真实索引构建流水线。
- 将 `/metrics` 接入真实 Prometheus 客户端。
