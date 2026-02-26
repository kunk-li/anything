file_manager/ 
├─ pyproject.toml 
├─ README.md 
└─ file_manager/ 
    ├─ __init__.py # 导出 FileManagerService 等 
    ├─ app.py # 可选：create_app()/create_router()
    │
    ├─ core/ # ✅纯业务（可当库用） 
    │  ├─ __init__.py 
    │  ├─ service.py # FileManagerService：upload/info/download/delete 
    │  ├─ models.py # FileRecord / ObjectMeta 等领域模型（不含 ORM） 
    │  ├─ exceptions.py # 业务异常（不含 HTTP） 
    │  └─ keygen.py # key 生成策略（可选） 
    │ 
    ├─ storage/ # ✅文件内容存储（可插拔） 
    │  ├─ __init__.py 
    │  ├─ base.py # Storage 抽象 
    │  ├─ local.py # 本地实现（开发/小规模） 
    │  └─ s3.py # 以后扩展（占位/可不实现） 
    │ 
    ├─ repository/ # ✅元数据存储（可插拔） 
    │  ├─ __init__.py 
    │  ├─ base.py # Repository 抽象 
    │  ├─ memory.py # 内存实现（先跑通/测试） 
    │  └─ sql.py # 以后扩展（占位/可不实现） 
    │ 
    ├─ api/ # ✅HTTP 适配层（可挂载到外部系统） 
    │   ├─ __init__.py 
    │   ├─ deps.py # 组装：把 storage+repo 注入 core service 
    │   └─ v1/ 
    │       ├─ __init__.py 
    │       ├─ router.py # APIRouter 
    │       ├─ files.py # endpoints：上传/下载/删除/读取 
    │       └─ schemas.py # Pydantic（只在 api 层） 
    │ 
    └─ utils/ 
        ├─ __init__.py 
        ├─ http.py #  请求调用工具
        └─ range.py # Range 解析（HTTP Range -> core Range）



1) File（文件元数据，必须）

用途：repo 存；storage 只存二进制内容。RAG/Agent 最常用。

    字段	            类型	                    必填	    说明
    file_id	        string	                ✅	    外部稳定标识（推荐 ULID/UUID）
    tenant_id	    string	                ✅	    租户隔离（建议强制）
    storage_key	    string	                ✅	    存储中的对象 key（内部用）
    filename	    string	                ✅	    原始文件名
    content_type	string | null	        ✅	    MIME（可为空）
    size	        int	                    ✅	    字节数
    sha256	        string | null	        ⛔	    内容指纹（去重/校验/索引一致性）
    etag	        string | null	        ⛔	    存储返回的 etag（对象存储常用）
    status	        enum	                ✅	    见下方状态枚举
    metadata	    object	                ⛔	    扩展字段（标签、来源、业务关联 id 等）
    created_at	    datetime(ISO)	        ✅	    创建时间
    updated_at	    datetime(ISO)	        ✅	    更新时间
    deleted_at	    datetime(ISO) | null	⛔	    软删除时间

status 枚举（定稿）

    PENDING：上传会话已创建但未完成（大文件/预签名常见）
    
    ACTIVE：可用
    
    DELETED：软删
    
    FAILED：处理失败（上传/校验/解析失败等）

关键约束（建议）

    unique(tenant_id, file_id)
    
    unique(tenant_id, storage_key)
    
    可选：index(tenant_id, created_at)（列表分页）
    
    可选：index(tenant_id, sha256)（去重）

2) UploadSession（上传会话，大文件/分块/直传，建议定义）

        字段	            类型	            必填	    说明
        upload_id	    string	        ✅	    上传会话 id
        tenant_id	    string	        ✅	    租户
        file_id	        string	        ✅	    预生成的 file_id（完成后同一个 file_id 变 ACTIVE）
        storage_key	    string	        ✅	    目标对象 key
        mode	        enum	        ✅	    single / multipart
        part_size	    int | null	    ⛔	    分块大小（multipart 必填）
        expected_size	int | null	    ⛔	    期望总大小（可用于校验）
        status	        enum	        ✅	    见下方状态枚举
        expires_at	    datetime(ISO)	✅	    会话过期时间
        created_at	    datetime(ISO)	✅	    创建时间

UploadSession.status 枚举

    INITIATED
    
    UPLOADING
    
    COMPLETED
    
    ABORTED
    
    EXPIRED

3) UploadPart（分块记录，可选）

支持断点续传/服务端校验分块，才需要。

    字段	            类型	            必填	        说明
    upload_id	    string	        ✅	        关联 UploadSession
    part_number	    int	            ✅	        分块序号（从 1 开始）
    etag	        string | null	⛔	        分块 etag（对象存储常见）
    size	        int | null	    ⛔	        分块大小
    created_at	    datetime(ISO)	✅	        创建时间

    约束：unique(upload_id, part_number)

4) DocumentExtract（文本抽取结果，可选但对 RAG 很有用）

不做 RAG，但提供 extract 能让 RAG/Agent 直接拿文本去 embedding/index。

    字段	                类型	            必填	    说明
    extract_id	        string	        ✅	    抽取记录 id
    tenant_id	        string	        ✅	    租户
    file_id	            string	        ✅	    关联文件
    text	            string | null	⛔	    小文本可直接存
    text_storage_key	string | null	⛔	    大文本存 storage，用 key 引用
    chars	            int	            ✅	    字符数
    language	        string | null	⛔	    语言（可选）
    parser	            string | null	⛔	    解析器标识（pdf/docx/…）
    status	            enum	        ✅	    SUCCEEDED / FAILED
    created_at	        datetime(ISO)	✅	    创建时间
    
    约束：可选 unique(tenant_id, file_id)（只保留最新一份）

5) DocumentChunk（切块结果，可选）

        字段	                    类型	            必填	            说明
        chunk_id	            string	        ✅	            chunk id
        tenant_id	            string	        ✅	            租户
        file_id	                string	        ✅	            关联文件
        index	                int	            ✅	            chunk 顺序
        text	                string | null	⛔	            chunk 文本（小块可直接存）
        text_storage_key	    string | null	⛔	            大块/大量 chunks 可存 storage
        offset_start	        int | null	    ⛔	            溯源定位
        offset_end	            int | null	    ⛔	            溯源定位
        metadata	            object	        ⛔	            页码、标题层级等
        created_at	            datetime(ISO)	✅	            创建时间
        
        约束：unique(tenant_id, file_id, index)

6)IdempotencyRecord（幂等记录，建议定义）

    字段	                类型	            必填	    说明
    tenant_id	        string	        ✅	    租户
    key	                string	        ✅	    Idempotency-Key
    operation	        string	        ✅	    如 files.create / files.delete / upload.complete
    request_hash	    string | null	⛔	    防止同 key 不同请求体
    response_status	    int	            ✅	    HTTP 状态码
    response_body	    object	        ✅	    直接缓存响应（或存 resource_id）
    created_at	        datetime(ISO)	✅	    创建时间
    expires_at	        datetime(ISO)	✅	    过期时间（TTL）

    约束：unique(tenant_id, operation, key)

7) Event（事件，建议定义：给 RAG/Agent 同步用）

        字段	            类型	            必填	    说明
        event_id	    string	        ✅	    事件 id
        tenant_id	    string	        ✅	    租户
        type	        enum/string	    ✅	    见下方事件类型
        occurred_at	    datetime(ISO)	✅	    发生时间
        data	        object	        ✅	    至少包含 file_id
        cursor	        string/int	    ✅	    拉取分页用（可用自增序列或时间+id）

事件类型（最小集）

    file.created
    
    file.deleted
    
    可选：document.extracted
    
    可选：document.chunked