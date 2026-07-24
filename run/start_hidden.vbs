' Start the Anything backend with NO terminal window.
'
' Why VBS: a .bat is a console program, so double-clicking or "start"-ing it
' always spawns a visible cmd window, and closing that window kills the server
' (exactly what happened when the terminal was closed by hand). VBScript's
' WScript.Shell.Run with window style 0 launches fully hidden -- neither cmd nor
' python shows a window, and there is no console to accidentally close.
'
' It reuses run\start_dev.bat as the single source of env vars / uvicorn args
' (no duplication). It only flips ANYTHING_LOG_TO_FILE=1 so the bat redirects
' uvicorn stdout/stderr into logs\uvicorn.log -- a hidden console has nowhere to
' print, so output must go to a file to stay debuggable.
'
' Usage: double-click this file, or:  wscript "run\start_hidden.vbs"
' Stop the server later with:  Stop-Process -Id <pid>   (find via port 18877)

Dim sh, runDir
runDir = "D:\projects\python\ai_work\anything\run\"

Set sh = CreateObject("WScript.Shell")
' Child processes launched below inherit this process env block.
sh.Environment("PROCESS")("ANYTHING_LOG_TO_FILE") = "1"
sh.CurrentDirectory = runDir

' window style 0 = hidden, bWaitOnReturn = False (fire and forget).
sh.Run """" & runDir & "start_dev.bat""", 0, False
