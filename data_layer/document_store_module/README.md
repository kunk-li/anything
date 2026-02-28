# document_store_module

该模块用于**文档解析模块输出的文本内容**的持久化存储（本模块不做解析），并提供统一的 CRUD、重复文件校验、僵尸文件识别/清理、以及独立的信息记录文件（`.info.json`）读写能力。

## 目录结构

- `core/base.py`：抽象接口（ABC）
- `core/impl.py`：默认本地文件系统实现 `LocalDocumentStore`
- `utils/tool_functions.py`：工具函数（doc_id、路径、哈希、备份、JSON等）
- `config/config.py`：默认配置
- `tests/`：单元测试

## 存储规则

- 文档文件：`{storage_dir}/{doc_id}.{storage_type}`，其中 `storage_type` 与 `file_type` 一致；当 `file_type=unknown` 时默认用 `txt`
- 信息文件：`{storage_dir}/{doc_id}.info.json`（UTF-8、JSON）
- 哈希关联表：`{storage_dir}/.hash_doc_map.json`（hash -> doc_id）

## 标准化文档结构（字段）

```json
{
  "doc_id": "...",
  "content": "...",
  "file_name": "原始文件名（必要时会自动生成默认文件名）",
  "file_type": "pdf|docx|...|unknown",
  "storage_type": "与file_type一致，unknown时为txt",
  "create_time": "YYYY-MM-DD HH:MM:SS",
  "update_time": "YYYY-MM-DD HH:MM:SS",
  "file_size": "字节数字字符串",
  "content_hash": "md5/sha256（小写）",
  "last_access_time": "YYYY-MM-DD HH:MM:SS",
  "info_file_path": "存储路径（字符串）"
}
```

## 快速使用

```python
from document_store_module.core.impl import LocalDocumentStore
from document_store_module.utils.tool_functions import calculate_content_hash

store = LocalDocumentStore()
content = "解析后的文本"
file_name = "demo.md"
file_type = "md"
content_hash = calculate_content_hash(content)

doc = store.create_document(content, file_name, file_type, content_hash)
store.save_document(doc)

got = store.get_document(doc["doc_id"])
print(got["content"])
```

## 重复文件校验

- 优先用 `content_hash` 查询 `.hash_doc_map.json`
- 未命中则用 `file_name + file_type + content_length(bytes)` 进行补充比对
- `force_save=True` 可跳过校验（由上层控制）

## 僵尸文件处理

`identify_zombie_files(threshold_days)` 会扫描 `storage_dir`：
- 文档文件缺失 info 文件、或 info 文件缺失文档文件（Type4）
- info 文件损坏（不可解析）视为僵尸候选
- last_access_time 与 update_time 超过阈值未变化（Type2）
- 其他无法关联 doc_id 的孤儿文件也会被记录

`clean_zombie_files(threshold_days, backup=True/False)` 会删除识别出的僵尸文件，`backup=True` 时先拷贝到 `backup_dir`。

## 依赖说明

- 运行时：仅依赖 Python 标准库。
- 如果在完整系统内运行，模块会优先使用外部的：
  - `config_module.core.impl.ConfigManager`
  - `log_module.core.impl.SystemLogger`
  若这些模块不可用，会自动降级为最小可用的内置实现，保证模块可单独运行和测试。

## 运行测试

```bash
python -m unittest -v
```
