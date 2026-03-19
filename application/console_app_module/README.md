# console_app_module

控制台交互模块实现，遵循“应用层只负责用户入口，不承载业务逻辑，仅调用接口层 RequestHandler”的系统边界。

## 功能
- 命令行交互入口
- `rag / agent / hybrid` 模式切换
- `/help /mode /topk /verbose /session /history /export /attach /batch /script /retry /stats /theme`
- 批处理执行、脚本执行、历史导出、统计摘要
- 渲染器与历史存储可替换

## 最小示例

```python
from console_app_module.core.impl import ConsoleApp


class DemoHandler:
    def handle(self, request: dict) -> dict:
        return {
            "code": "SUCCESS",
            "message": "ok",
            "data": {"answer": f"echo: {request.get('query') or request.get('task')}"},
            "trace_id": "demo-trace-id",
            "retryable": False,
        }


app = ConsoleApp(handler=DemoHandler())
app.run()
```

## 测试

```bash
pytest -q
```
