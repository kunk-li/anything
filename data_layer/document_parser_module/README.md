# document_parser_module

数据层文档解析模块：将原始文件解析为统一的标准文本结构（仅解析，不做任何存储）。

## 功能

- 支持解析：`txt / pdf / docx / md / py / xlsx / xls / ppt / pptx / csv / json / xml`
- 输出统一结构：

```json
{
  "content": "解析后的文本内容（已做基础清洗）",
  "file_name": "example.pdf",
  "meta": {"ext": ".pdf"}
}
```

## 安装

```bash
pip install -r requirements.txt
```

> 说明：本模块设计依赖系统基础支撑层的 `common_utils_module`（文本清洗）、`log_module`（日志）、`exception_module`（异常）。
> 为便于独立开发/测试，本仓库在缺少这些模块时提供了**兼容降级实现**（不影响对接时替换为系统正式实现）。

## 使用示例

```python
from document_parser_module import LocalDocumentParser

parser = LocalDocumentParser()

# 单文件解析
res = parser.parse_file("/path/to/example.xlsx")
print(res["file_name"], res["meta"], res["content"][:200])

# 文件夹解析
results = parser.parse_folder("/path/to/folder")
print("count=", len(results))
```

## 各格式解析规则

- **txt/py**：按 UTF-8 读取（忽略非法字符），保留原文本结构。
- **pdf**：按页提取文本，格式为 `[Page N]` + 内容。
- **docx**：提取段落文本并拼接。
- **md**：Markdown -> HTML -> 纯文本（去标签）。
- **excel(xlsx/xls)**：按工作表输出：`[Sheet: name]` + 表格（以 TAB 分隔）。
- **ppt/pptx**：按页输出：`[Slide N]` + 该页可提取文本。
- **csv**：读入表格后以 TAB 分隔输出。
- **json/xml**：保留结构，格式化为缩进 JSON 文本（xml 先转 dict 再 dump）。

## 测试

```bash
python -m unittest discover -s document_parser_module/tests -p "test_*.py"
```

## 常见问题

- **PDF 提取为空**：部分 PDF 是扫描图像或字体受限，`PyPDF2` 可能提取不到文本（此模块按设计不做 OCR）。
- **依赖缺失**：优先安装 `requirements.txt`；若接入系统工程，请确保基础支撑层三个模块可 import。
