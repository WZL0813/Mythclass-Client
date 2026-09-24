<#
  Mythclass 客户端 安装（命令行 / 静默）

  图形界面版是 gui.ps1（双击安装包走的是它）。
  这个脚本留给批量部署、测试、以及出问题时排查用。

  用法：
    powershell -ExecutionPolicy Bypass -File setup.ps1
    powershell -ExecutionPolicy Bypass -File setup.ps1 -TargetDir D:\test -NoLaunch -NoElevate
    powershell -ExecutionPolicy Bypass -File setup.ps1 -StartMenu -Desktop -Quiet
#>
[CmdletBinding()]
param(
  [string]$Source = '',
  [string]$TargetDir = 'C:\Program Files (x86)\Mythclass',
  [switch]$StartMenu,
  [switch]$Desktop,
  [switch]$NoLaunch,
  [switch]$Quiet,
  [switch]$NoElevate
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'install-core.ps1')

if (-not $Source) { $Source = $PSScriptRoot }
$Version = '2.0.2'
$verFile = Join-Path $Source 'version.txt'
if (Test-Path $verFile) { $Version = (Get-Content $verFile -Raw).Trim() }

if ((-not (Test-Admin)) -and (-not $NoElevate)) {
  Write-Host '需要管理员权限，正在请求提权…'
  $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"",
            '-TargetDir', "`"$TargetDir`"")
  if ($Source)   { $argv += @('-Source', "`"$Source`"") }
  if ($StartMenu){ $argv += '-StartMenu' }
  if ($Desktop)  { $argv += '-Desktop' }
  if ($NoLaunch) { $argv += '-NoLaunch' }
  if ($Quiet)    { $argv += '-Quiet' }
  Start-Process 'powershell.exe' -Verb RunAs -ArgumentList $argv | Out-Null
  exit 0
}

$step = { param($m) if (-not $Quiet) { Write-Host $m } }
$result = Invoke-Install -Source $Source -TargetDir $TargetDir -Version $Version `
  -WantStartMenu ([bool]$StartMenu) -WantDesktop ([bool]$Desktop) `
  -Launch (-not $NoLaunch) -OnStep $step

if (-not $Quiet) {
  Write-Host ''
  Write-Host "装好了：$TargetDir（v$Version）"
  Write-Host '卸载：设置 → 应用 → Mythclass 客户端'
}
