<#
  Mythclass 客户端 安装核心

  图形界面（gui.ps1）和命令行（setup.ps1）都用这里的东西，
  免得两边逻辑各写一份、改一处漏一处。

  对外函数：
    Test-Admin
    Expand-Payload      把 payload.zip / payload 目录铺到目标目录
    Stop-OldClient      停掉正在跑的旧客户端（含跟班与计划任务）
    Copy-Shortcuts      按需要建开始菜单 / 桌面快捷方式
    Register-Uninstall  登记到「程序和功能」
    Start-ClientApp     以当前登录用户身份启动
#>

$script:AppName = 'Mythclass 客户端'
$script:ExeName = 'MythclassClient.exe'
$script:RegPath = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MythclassClient'

function Test-Admin {
  return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Log([string]$msg) {
  Write-Host $msg
}

function Stop-OldClient {
  # 先给所有用户放「跟班别盯了」的旗子：不然杀了它，几秒后又被拉起来
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
    # 任务不存在属正常；PS 7.4 会把原生命令的 stderr 当异常抛，所以包一层
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
  return -not (Get-Process -Name 'MythclassClient' -ErrorAction SilentlyContinue)
}

function Expand-Payload([string]$Source, [string]$TargetDir, [string]$Version) {
  $zip = Join-Path $Source 'payload.zip'
  $plain = Join-Path $Source 'payload'

  if (-not (Test-Path $TargetDir)) { New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null }

  if (Test-Path $zip) {
    # 先解到临时目录再拷进去：直接解到 Program Files，遇到占用会留下半成品
    $tmp = Join-Path $env:TEMP ('mythclass-setup-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    try {
      Add-Type -AssemblyName System.IO.Compression.FileSystem
      [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $tmp)
      Copy-Tree $tmp $TargetDir
    } finally {
      Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
  } elseif (Test-Path (Join-Path $plain $script:ExeName)) {
    Copy-Tree $plain $TargetDir
  } else {
    Copy-Tree $Source $TargetDir
  }

  Set-Content -Path (Join-Path $TargetDir 'version.txt') -Value $Version -Encoding UTF8
  $installed = Join-Path $TargetDir $script:ExeName
  if (-not (Test-Path $installed)) { throw "铺完文件却没找到 $($script:ExeName)" }
  return $installed
}

function Copy-Tree([string]$From, [string]$To) {
  $tries = 0
  while ($true) {
    try {
      Copy-Item -Path (Join-Path $From '*') -Destination $To -Recurse -Force
      return
    } catch {
      $tries += 1
      if ($tries -ge 6) { throw "文件被占用，复制失败：$($_.Exception.Message)" }
      Write-Log "  文件被占用，重试第 $tries 次…"
      Start-Sleep -Seconds 2
    }
  }
}

function Clear-OldVersion([string]$TargetDir) {
  if (-not (Test-Path $TargetDir)) { return '' }
  $ours = (Test-Path (Join-Path $TargetDir $script:ExeName)) -or (Test-Path (Join-Path $TargetDir 'version.txt'))
  if (-not $ours) { return 'not-ours' }

  $old = ''
  $vf = Join-Path $TargetDir 'version.txt'
  if (Test-Path $vf) { $old = (Get-Content $vf -Raw).Trim() }
  for ($i = 1; $i -le 5; $i++) {
    try { Remove-Item $TargetDir -Recurse -Force -ErrorAction Stop; break }
    catch { Start-Sleep -Seconds 2 }
  }
  return $old
}

function Copy-Shortcuts([string]$TargetDir, [bool]$WantStartMenu, [bool]$WantDesktop) {
  $exe = Join-Path $TargetDir $script:ExeName
  $shell = New-Object -ComObject WScript.Shell

  if ($WantStartMenu) {
    $dir = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'
    $lnk = $shell.CreateShortcut((Join-Path $dir "$($script:AppName).lnk"))
    $lnk.TargetPath = $exe
    $lnk.WorkingDirectory = $TargetDir
    $lnk.Description = 'Mythclass 若思班级一体机管理系统 客户端'
    $lnk.Save()
  }
  if ($WantDesktop) {
    $dir = Join-Path $env:PUBLIC 'Desktop'
    if (Test-Path $dir) {
      $lnk = $shell.CreateShortcut((Join-Path $dir "$($script:AppName).lnk"))
      $lnk.TargetPath = $exe
      $lnk.WorkingDirectory = $TargetDir
      $lnk.Save()
    }
  }
}

function Register-Uninstall([string]$TargetDir, [string]$Version) {
  $exe = Join-Path $TargetDir $script:ExeName
  $cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$TargetDir\uninstall.ps1`" -TargetDir `"$TargetDir`""
  New-Item -Path $script:RegPath -Force | Out-Null
  Set-ItemProperty -Path $script:RegPath -Name 'DisplayName'     -Value $script:AppName
  Set-ItemProperty -Path $script:RegPath -Name 'DisplayVersion'  -Value $Version
  Set-ItemProperty -Path $script:RegPath -Name 'Publisher'       -Value 'Ryokuryuneko'
  Set-ItemProperty -Path $script:RegPath -Name 'InstallLocation' -Value $TargetDir
  Set-ItemProperty -Path $script:RegPath -Name 'DisplayIcon'     -Value $exe
  Set-ItemProperty -Path $script:RegPath -Name 'UninstallString' -Value $cmd
  Set-ItemProperty -Path $script:RegPath -Name 'QuietUninstallString' -Value $cmd
  New-ItemProperty -Path $script:RegPath -Name 'NoModify' -Value 1 -PropertyType DWord -Force | Out-Null
  New-ItemProperty -Path $script:RegPath -Name 'NoRepair' -Value 1 -PropertyType DWord -Force | Out-Null
  New-ItemProperty -Path $script:RegPath -Name 'EstimatedSize' -Value 140000 -PropertyType DWord -Force | Out-Null
}

function Start-ClientApp([string]$TargetDir) {
  $exe = Join-Path $TargetDir $script:ExeName
  # 用 explorer 起：安装进程是提权过的，直接起会让客户端以管理员身份跑
  Start-Process 'explorer.exe' -ArgumentList "`"$exe`"" | Out-Null
}

function Invoke-Install([string]$Source, [string]$TargetDir, [string]$Version, `
                        [bool]$WantStartMenu, [bool]$WantDesktop, [bool]$Launch, `
                        [scriptblock]$OnStep = $null) {
  function Step($msg) {
    Write-Log $msg
    if ($OnStep) { & $OnStep $msg }
  }

  Step '正在停掉旧客户端…'
  $stopped = Stop-OldClient

  Step '正在清理旧版本…'
  $old = Clear-OldVersion $TargetDir
  if ($old -and $old -ne 'not-ours') { Step "  发现旧版本 $old，已清掉" }

  Step '正在铺文件…'
  $exe = Expand-Payload $Source $TargetDir $Version
  Copy-Item (Join-Path $PSScriptRoot 'uninstall.ps1') $TargetDir -Force

  Step '正在建快捷方式…'
  Copy-Shortcuts $TargetDir $WantStartMenu $WantDesktop

  Step '正在登记到「程序和功能」…'
  try { Register-Uninstall $TargetDir $Version }
  catch { Step "  （跳过登记：$($_.Exception.Message)）" }

  if ($Launch) {
    Step '正在启动客户端…'
    Start-ClientApp $TargetDir
  }
  return @{ Stopped = $stopped; Exe = $exe; OldVersion = $old }
}
