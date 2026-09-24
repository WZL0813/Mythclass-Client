@echo off
rem Entry point after IExpress extracts the package.
rem ASCII-only, no BOM (cmd.exe chokes on a UTF-8 BOM).
rem
rem Why copy first: the SFX deletes its extraction folder (%TEMP%\IXP000.TMP)
rem as soon as this script returns. If we launched the wizard straight from
rem there, the wizard would lose its own files mid-startup and die silently.
setlocal
set "WORK=%TEMP%\MythclassSetup"
if exist "%WORK%" rd /s /q "%WORK%" >nul 2>&1
mkdir "%WORK%" >nul 2>&1
xcopy /e /i /q /y "%~dp0." "%WORK%\" >nul

if /I "%1"=="/Quiet" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%WORK%\setup.ps1" -Source "%WORK%" -Quiet
) else (
  wscript.exe "%WORK%\run.vbs"
)
exit /b %ERRORLEVEL%
