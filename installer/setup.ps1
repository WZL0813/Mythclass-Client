<#
  Mythclass 客户端 安装脚本

  默认装到 C:\Program Files (x86)\Mythclass
  用法：
    powershell -ExecutionPolicy Bypass -File setup.ps1 -Source .\payload
    powershell -ExecutionPolicy Bypass -File setup.ps1 -Source .\payload -TargetDir D:\test -NoLaunch

  做四件事：停掉旧客户端 → 铺文件 → 建快捷方式 → 在「程序和功能」里登记自己。
#>
[CmdletBinding()]
param(
  [string]$Source = '',
  [string]$TargetDir = 'C:\Program Files (x86)\Mythclass',
  [switch]$NoLaunch,
  [switch]$Quiet,
  [switch]$NoElevate   # 测试用：跳过提权检查
)

$ErrorActionPreference = 'Stop'

$ExeName  = 'MythclassClient.exe'
$AppName  = 'Mythclass 客户端'
$RegPath  = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MythclassClient'

function Copy-Tree($from, $to) {
  $tries = 0
  while ($true) {
    try {
      Copy-Item -Path (Join-Path $from '*') -Destination $to -Recurse -Force
      return
    } catch {
      $tries += 1
      if ($tries -ge 6) { throw "文件被占用，复制失败：$($_.Exception.Message)" }
      Say "  文件被占用，重试第 $tries 次…"
      Start-Sleep -Seconds 2
    }
  }
}

function Say($msg) { if (-not $Quiet) { Write-Host $msg } }

# ---------------------------------------------------------------- 0. 提权
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if ((-not $isAdmin) -and (-not $NoElevate)) {
  Say '需要管理员权限，正在请求提权…'
  $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"",
            '-TargetDir', "`"$TargetDir`"")
  if ($Source)   { $argv += @('-Source', "`"$Source`"") }
  if ($NoLaunch) { $argv += '-NoLaunch' }
  if ($Quiet)    { $argv += '-Quiet' }
  Start-Process 'powershell.exe' -Verb RunAs -ArgumentList $argv | Out-Null
  exit 0
}

# ---------------------------------------------------------------- 1. 源
if (-not $Source) { $Source = $PSScriptRoot }
$zip = Join-Path $Source 'payload.zip'
$plain = Join-Path $Source 'payload'
if (Test-Path $zip) {
  $mode = 'zip'
} elseif (Test-Path (Join-Path $plain $ExeName)) {
  $mode = 'folder'
  $Source = $plain
} elseif (Test-Path (Join-Path $Source $ExeName)) {
  $mode = 'folder'
} else {
  throw "找不到 $ExeName（也没有 payload.zip），安装包不完整。"
}

$version = '2.0.0'
$verFile = Join-Path $Source 'version.txt'
if (Test-Path $verFile) { $version = (Get-Content $verFile -Raw).Trim() }

Say "准备安装 $AppName v$version → $TargetDir"

# ---------------------------------------------------------------- 2. 停旧客户端
# 先给所有用户放「跟班别盯了」的旗子，否则杀了它又会被拉起来
Say '正在停掉旧客户端…'
foreach ($profile in (Get-ChildItem 'C:\Users' -Directory -ErrorAction SilentlyContinue)) {
  $dir = Join-Path $profile.FullName 'AppData\Roaming\Mythclass'
  if (Test-Path $dir) {
    Set-Content -Path (Join-Path $dir 'guardian-stop.flag') -Value 'install' -Encoding ASCII -ErrorAction SilentlyContinue
  }
}
if ($env:APPDATA) {
  $dir = Join-Path $env:APPDATA 'Mythclass'
  if (Test-Path $dir) {
    Set-Content -Path (Join-Path $dir 'guardian-stop.flag') -Value 'install' -Encoding ASCII -ErrorAction SilentlyContinue
  }
}

foreach ($task in @('MythclassClient', 'MythclassGuard')) {
  # 任务不存在是正常的，而 PS 7.4 会把原生命令的 stderr 当异常抛出来
  try { schtasks /End /TN $task 2>$null | Out-Null } catch { }
  try { schtasks /Delete /TN $task /F 2>$null | Out-Null } catch { }
}

$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
  $procs = Get-Process -Name 'MythclassClient' -ErrorAction SilentlyContinue
  if (-not $procs) { break }
  $procs | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 400
}
if (Get-Process -Name 'MythclassClient' -ErrorAction SilentlyContinue) {
  Say '客户端还在跑，安装可能失败 —— 建议先在托盘里退出它再装。'
}

# ---------------------------------------------------------------- 3. 铺文件
Say "正在写入 $TargetDir"

# 升级：先把旧版本清干净，免得新旧文件混在一起
if (Test-Path $TargetDir) {
  $ours = (Test-Path (Join-Path $TargetDir $ExeName)) -or (Test-Path (Join-Path $TargetDir 'version.txt'))
  if ($ours) {
    $oldVer = ''
    $oldVerFile = Join-Path $TargetDir 'version.txt'
    if (Test-Path $oldVerFile) { $oldVer = (Get-Content $oldVerFile -Raw).Trim() }
    Say "  发现旧版本 $oldVer，先清掉它"
    for ($i = 1; $i -le 5; $i++) {
      try { Remove-Item $TargetDir -Recurse -Force -ErrorAction Stop; break }
      catch { Start-Sleep -Seconds 2 }
    }
  } else {
    Say "  $TargetDir 里不是本程序的东西，只往里加文件，不删"
  }
}
New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null

if ($mode -eq 'zip') {
  # 先解到旁边的临时目录，再拷进去：直接解到 Program Files 遇到占用会半途而废
  $tmp = Join-Path $env:TEMP ('mythclass-setup-' + [Guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $tmp -Force | Out-Null
  try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $tmp)
    $srcRoot = $tmp
    Copy-Tree $srcRoot $TargetDir
  } finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  }
} else {
  Copy-Tree $Source $TargetDir
}

Set-Content -Path (Join-Path $TargetDir 'version.txt') -Value $version -Encoding UTF8
$installedExe = Join-Path $TargetDir $ExeName
if (-not (Test-Path $installedExe)) { throw "装完了却没找到 $installedExe" }

# 卸载脚本得先放进去，不然前面哪一步失败就没了
Copy-Item (Join-Path $PSScriptRoot 'uninstall.ps1') $TargetDir -Force

# ---------------------------------------------------------------- 4. 快捷方式
Say '正在建快捷方式…'
try {
$shell = New-Object -ComObject WScript.Shell
$smDir = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'
$lnk = $shell.CreateShortcut((Join-Path $smDir "$AppName.lnk"))
$lnk.TargetPath = $installedExe
$lnk.WorkingDirectory = $TargetDir
$lnk.Description = 'Mythclass 若思班级一体机管理系统 客户端'
$lnk.Save()

$pubDesktop = Join-Path $env:PUBLIC 'Desktop'
if (Test-Path $pubDesktop) {
  $lnk2 = $shell.CreateShortcut((Join-Path $pubDesktop "$AppName.lnk"))
  $lnk2.TargetPath = $installedExe
  $lnk2.WorkingDirectory = $TargetDir
  $lnk2.Save()
}
} catch {
  Say "  （跳过快捷方式：$($_.Exception.Message)）"
}

# ---------------------------------------------------------------- 5. 卸载登记
Say '正在登记到「程序和功能」…'
$uninstallCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$TargetDir\uninstall.ps1`" -TargetDir `"$TargetDir`""
try {
New-Item -Path $RegPath -Force | Out-Null
Set-ItemProperty -Path $RegPath -Name 'DisplayName'     -Value $AppName
Set-ItemProperty -Path $RegPath -Name 'DisplayVersion'  -Value $version
Set-ItemProperty -Path $RegPath -Name 'Publisher'       -Value 'Ryokuryuneko'
Set-ItemProperty -Path $RegPath -Name 'InstallLocation' -Value $TargetDir
Set-ItemProperty -Path $RegPath -Name 'DisplayIcon'     -Value $installedExe
Set-ItemProperty -Path $RegPath -Name 'UninstallString' -Value $uninstallCmd
Set-ItemProperty -Path $RegPath -Name 'QuietUninstallString' -Value $uninstallCmd
New-ItemProperty -Path $RegPath -Name 'NoModify' -Value 1 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $RegPath -Name 'NoRepair' -Value 1 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $RegPath -Name 'EstimatedSize' -Value 140000 -PropertyType DWord -Force | Out-Null
} catch {
  Say "  （非管理员/受限环境，跳过「程序和功能」登记：$($_.Exception.Message)）"
}

# ---------------------------------------------------------------- 6. 起它
if (-not $NoLaunch) {
  Say '正在启动客户端…'
  # 用 explorer 起：刚才是提权进程，直接起会变成管理员身份运行
  Start-Process 'explorer.exe' -ArgumentList "`"$installedExe`"" | Out-Null
}

Say ''
Say "装好了：$TargetDir（v$version）"
Say '卸装：设置 → 应用 → Mythclass 客户端'
