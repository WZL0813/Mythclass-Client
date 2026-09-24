@echo off
chcp 65001 >nul
rem IExpress 解包后会跑这个文件
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -Source "%~dp0" %*
exit /b %ERRORLEVEL%
