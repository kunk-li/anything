# -*- coding: utf-8 -*-
"""
API 启动入口
运行方式：
    uvicorn main_api:app --host 0.0.0.0 --port 8000 --reload
"""

from bootstrap import build_api_app

app = build_api_app()
