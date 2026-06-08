@echo off
setlocal
set "PYTHONPATH=D:\projects\python\ai_work\anything\basic_support;D:\projects\python\ai_work\anything\data_layer;D:\projects\python\ai_work\anything\business;D:\projects\python\ai_work\anything\interface;D:\projects\python\ai_work\anything\application;D:\projects\python\ai_work\anything\run;D:\projects\python\ai_work\anything"
set "ANYTHING_DEV_MODE=1"
REM 强制 UTF-8: 否则 Windows 控制台/重定向用 GBK, uvicorn stdout 落盘/管道会乱码 (项目自身日志文件已 UTF-8)
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"C:\ProgramData\miniconda3\python.exe" -m uvicorn main_api:app --app-dir D:/projects/python/ai_work/anything/run --host 127.0.0.1 --port 18877 --log-level info
