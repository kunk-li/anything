# file_manager/main.py
from __future__ import annotations

import uvicorn
# from file_manager.app import create_app


def main() -> None:
    """
    Python 启动入口：
    - 直接 python file_manager/main.py
    - 适合开发 / 部署脚本调用
    """
    # app = create_app()

    uvicorn.run(
        "file_manager.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        reload=True,      # 开发模式，生产可关
        log_level="info",
    )


if __name__ == "__main__":
    main()
