# common_utils_module

基础支撑层 - 通用工具模块（common_utils_module），为 RAG 与 Agent 系统提供全局通用工具函数。
按功能划分为：文本处理（TextTool）、数据处理（DataTool）、参数校验（ParamValidate）、通用辅助（AssistTool，重点：时间方法）。

## 安装/使用

将 `common_utils_module` 目录加入 Python Path（或作为子模块引入工程），然后：

```python
from common_utils_module import CommonUtils

common_utils = CommonUtils()

# 文本
text_tool = common_utils.get_text_tool()
print(text_tool.text_clean("  测试文本！！\n\t包含特殊字符  "))

# 数据
data_tool = common_utils.get_data_tool()
print(data_tool.dict_to_json({"name": "test", "age": 20}))

# 参数校验
param_tool = common_utils.get_param_validate()
print(param_tool.required_validate({"user": {"name": "kunsheng"}}, ["user.name"]))

# 时间（AssistTool）
assist_tool = common_utils.get_assist_tool()
print(assist_tool.get_current_time())
print(assist_tool.get_timestamp(millisecond=True))
print(assist_tool.get_day_start_end("2026-02-28 10:30:00"))
```

## 运行测试

在工程根目录执行：

```bash
python -m unittest discover -s common_utils_module/tests -p "test_*.py"
```

## 说明

- 时间格式采用文档约定：`YYYY-MM-DD HH:MM:SS`（可自定义）。
- 时区参数 `time_zone` 使用 IANA 时区名（如 `Asia/Shanghai`、`Europe/Berlin`）。
