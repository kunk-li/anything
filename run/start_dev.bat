@echo off
setlocal
REM 钉死工作目录到 run\ (数据目录 state_store/documents/vector_store/kb.sqlite3 都是
REM 相对路径解析, 不 cd 的话从哪启动数据就写哪, 会话历史会分裂; Docker 同款 WORKDIR /app/run)
cd /d "%~dp0"
REM kb/feedback sqlite 的数据根 (kb.py/feedback.py/rag impl 读 ANYTHING_DATA_ROOT, 默认
REM 相对路径 "run" 只在 CWD=项目根时正确; 不设的话 CWD=run\ 会写出 run\run\ 分裂库)
set "ANYTHING_DATA_ROOT=%~dp0"
set "PYTHONPATH=D:\projects\python\ai_work\anything\basic_support;D:\projects\python\ai_work\anything\data_layer;D:\projects\python\ai_work\anything\business;D:\projects\python\ai_work\anything\interface;D:\projects\python\ai_work\anything\application;D:\projects\python\ai_work\anything\run;D:\projects\python\ai_work\anything"
set "ANYTHING_DEV_MODE=1"
REM 强制 UTF-8: 否则 Windows 控制台/重定向用 GBK, uvicorn stdout 落盘/管道会乱码 (项目自身日志文件已 UTF-8)
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
REM 无终端启动 (start_hidden.vbs) 会置 ANYTHING_LOG_TO_FILE=1: 隐藏控制台无处显示 stdout,
REM 必须落盘到 logs\uvicorn.log 才能排障 (uvicorn 启动栈/access 日志走 stderr)。
REM 交互式直接跑 .bat 时不设该变量 → 前台实时打印, 开发时照旧能看日志。
if not exist "%~dp0logs" mkdir "%~dp0logs"
if defined ANYTHING_LOG_TO_FILE (
    "C:\ProgramData\miniconda3\python.exe" -m uvicorn main_api:app --app-dir D:/projects/python/ai_work/anything/run --host 127.0.0.1 --port 18877 --log-level info > "%~dp0logs\uvicorn.log" 2>&1
) else (
    "C:\ProgramData\miniconda3\python.exe" -m uvicorn main_api:app --app-dir D:/projects/python/ai_work/anything/run --host 127.0.0.1 --port 18877 --log-level info
)
