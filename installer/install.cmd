@echo off
rem Entry point after IExpress extracts the package.
rem ASCII-only, no BOM (cmd.exe chokes on a UTF-8 BOM).
if /I "%1"=="/Quiet" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -Source "%~dp0" -Quiet
) else (
  wscript.exe "%~dp0run.vbs"
)
exit /b %ERRORLEVEL%
