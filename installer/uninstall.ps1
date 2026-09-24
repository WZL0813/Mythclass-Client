<#
  Mythclass 客户端 卸载脚本
  用法：powershell -ExecutionPolicy Bypass -File uninstall.ps1
#>
[CmdletBinding()]
param(
  [string]$TargetDir = 'C:\Program Files (x86)\Mythclass',
  [switch]$KeepData,
  [switch]$Quiet,
  [switch]$NoElevate   # 测试用：跳过提权检查
)

$ErrorActionPreference = 'Stop'
$RegPath = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MythclassClient'
function Say($msg) { if (-not $Quiet) { Write-Host $msg } }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if ((-not $isAdmin) -and (-not $NoElevate)) {
  $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"",
            '-TargetDir', "`"$TargetDir`"")
  if ($KeepData) { $argv += '-KeepData' }
  if ($Quiet)    { $argv += '-Quiet' }
  Start-Process 'powershell.exe' -Verb RunAs -ArgumentList $argv | Out-Null
  exit 0
}

Say '正在停掉客户端…'
foreach ($profile in (Get-ChildItem 'C:\Users' -Directory -ErrorAction SilentlyContinue)) {
  $dir = Join-Path $profile.FullName 'AppData\Roaming\Mythclass'
  if (Test-Path $dir) {
    Set-Content -Path (Join-Path $dir 'guardian-stop.flag') -Value 'uninstall' -Encoding ASCII -ErrorAction SilentlyContinue
  }
}
foreach ($task in @('MythclassClient', 'MythclassGuard')) {
  # 任务不存在是正常的，而 PS 7.4 会把原生命令的 stderr 当异常抛出来
  try { schtasks /End /TN $task 2>$null | Out-Null } catch { }
  try { schtasks /Delete /TN $task /F 2>$null | Out-Null } catch { }
}
Get-Process -Name 'MythclassClient' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 800

Say '正在删快捷方式…'
$shell = New-Object -ComObject WScript.Shell
foreach ($p in @(
  (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Mythclass 客户端.lnk'),
  (Join-Path $env:PUBLIC 'Desktop\Mythclass 客户端.lnk')
)) {
  Remove-Item $p -Force -ErrorAction SilentlyContinue
}

Say '正在删文件…'
if (Test-Path $TargetDir) {
  for ($i = 1; $i -le 5; $i++) {
    try { Remove-Item $TargetDir -Recurse -Force -ErrorAction Stop; break }
    catch { Start-Sleep -Seconds 2 }
  }
}

Say '正在清登记…'
Remove-Item $RegPath -Recurse -Force -ErrorAction SilentlyContinue

if (-not $KeepData) {
  Say '正在清开机自启与运行数据…'
  foreach ($profile in (Get-ChildItem 'C:\Users' -Directory -ErrorAction SilentlyContinue)) {
    $run = Join-Path $profile.FullName 'AppData\Roaming\Mythclass'
    if (Test-Path $run) { Remove-Item $run -Recurse -Force -ErrorAction SilentlyContinue }
    $key = "Registry::HKEY_USERS\$($profile.Name)\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    # 每个用户的 HKCU 不能直接改，这里只清当前用户的
  }
  $hklmRun = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
  Remove-ItemProperty -Path $hklmRun -Name 'MythclassClient' -ErrorAction SilentlyContinue
  $hkuRun = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
  Remove-ItemProperty -Path $hkuRun -Name 'MythclassClient' -ErrorAction SilentlyContinue
}

Say ''
Say '卸干净了。'
