@echo off
rem IExpress 解包后会跑这个文件。
rem 走 run.vbs 是为了不闪黑框：wscript 起 powershell 时可以完全隐藏窗口。
if /I "%1"=="/Quiet" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -Source "%~dp0" -Quiet
) else (
  wscript.exe "%~dp0run.vbs"
)
exit /b %ERRORLEVEL%
