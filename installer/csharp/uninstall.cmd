@echo off
rem ASCII-only, no BOM. Removes the client and this folder.
setlocal
taskkill /F /IM MythclassClient.exe >nul 2>&1
for %%T in (MythclassClient MythclassGuard) do (
  schtasks /End /TN %%T >nul 2>&1
  schtasks /Delete /TN %%T /F >nul 2>&1
)
reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MythclassClient" /f >nul 2>&1
del "%ProgramData%\Microsoft\Windows\Start Menu\Programs\MythclassClient.lnk" >nul 2>&1
del "%PUBLIC%\Desktop\MythclassClient.lnk" >nul 2>&1
cd /d "%TEMP%"
start "" cmd /c "ping 127.0.0.1 -n 2 >nul & rd /s /q ""%~dp0"""
exit /b 0
