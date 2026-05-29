@echo off
setlocal
set "PYTHONPATH=D:\projects\python\ai_work\anything\basic_support;D:\projects\python\ai_work\anything\data_layer;D:\projects\python\ai_work\anything\business;D:\projects\python\ai_work\anything\interface;D:\projects\python\ai_work\anything\application;D:\projects\python\ai_work\anything\run;D:\projects\python\ai_work\anything"
set "ANYTHING_DEV_MODE=1"
"C:\ProgramData\miniconda3\python.exe" -m uvicorn main_api:app --app-dir D:/projects/python/ai_work/anything/run --host 127.0.0.1 --port 18877 --log-level info
