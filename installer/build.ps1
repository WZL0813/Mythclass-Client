<#
  打一个安装包（单文件 exe）

  用法（仓库根目录）：
    powershell -ExecutionPolicy Bypass -File installer\build.ps1

  做这些：
    1. 版本号从 mythclass\__init__.py 里读
    2. 用 myclass-onedir.spec 打目录版（已存在就复用）
    3. 把产物 + 安装脚本铺到 installer\stage
    4. 压成 payload.zip（IExpress 只认文件清单，一千多个文件没法一个个列）
    5. 生成 SED 配置，调 Windows 自带的 iexpress 出单文件安装包
    6. 产物：installer\out\MythclassSetup-<版本>.exe
#>
[CmdletBinding()]
param(
  [switch]$SkipBuild,
  [switch]$NoCleanup
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Installer = $PSScriptRoot
$Stage = Join-Path $Installer 'stage'
$OutDir = Join-Path $Installer 'out'

# ---------------------------------------------------------------- 版本号
$initPy = Join-Path $Root 'mythclass\__init__.py'
$version = ([regex]::Match((Get-Content $initPy -Raw), '__version__\s*=\s*"([^"]+)"')).Groups[1].Value
if (-not $version) { throw '从 mythclass\__init__.py 里读不到 __version__' }
Write-Host "版本号：$version"

# ---------------------------------------------------------------- 打目录版
$payload = Join-Path $Root 'dist-onedir\MythclassClient'
if (-not $SkipBuild) {
  Write-Host '正在打目录版…'
  Push-Location $Root
  try {
    Remove-Item (Join-Path $Root 'dist-onedir') -Recurse -Force -ErrorAction SilentlyContinue
    python -m PyInstaller build\mythclass-onedir.spec --noconfirm --clean `
      --distpath (Join-Path $Root 'dist-onedir') --workpath (Join-Path $Root 'build\mythclass-onedir') |
      Select-String 'Build complete|ERROR:' | Select-Object -Last 1
  } finally {
    Pop-Location
  }
}
if (-not (Test-Path (Join-Path $payload 'MythclassClient.exe'))) {
  throw "没找到目录版产物：$payload"
}

# ---------------------------------------------------------------- 编码检查
# .vbs 和 .cmd 必须是纯 ASCII 且不带 BOM：
#   VBScript 解析器不认 UTF-8 BOM，会在第 1 行第 1 列报「无效字符」
#   cmd.exe 遇到 BOM 会把 @echo off 读成乱码首命令
# 这个错只有装到别人机器上才会暴露，所以放在构建时拦住。
Write-Host '正在检查 .vbs / .cmd 的编码…'
foreach ($f in @('run.vbs', 'install.cmd')) {
  $path = Join-Path $Installer $f
  $bytes = [System.IO.File]::ReadAllBytes($path)

  # 1) UTF-8 BOM 一律禁止：VBScript 会报「无效字符」，cmd 会把首命令读坏
  if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    throw "$f 带了 UTF-8 BOM，脚本解析器会报「无效字符」。"
  }

  # 2) install.cmd 只能纯 ASCII
  $isUtf16 = ($bytes.Length -ge 2) -and (
    ($bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) -or ($bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF))
  if ($f -eq 'install.cmd' -and $isUtf16) {
    throw 'install.cmd 不能存成 UTF-16，cmd.exe 只认 ASCII/ANSI。'
  }

  # 3) 不是 UTF-16 的话，必须纯 ASCII
  if (-not $isUtf16) {
    $bad = @($bytes | Where-Object { $_ -gt 127 })
    if ($bad.Count -gt 0) {
      throw "$f 里有 $($bad.Count) 个非 ASCII 字节，又不是 UTF-16。要么存 ASCII，要么存 UTF-16。"
    }
  }
}
Write-Host '  run.vbs / install.cmd 编码 ok'

# .ps1 反过来：带中文的脚本必须有 UTF-8 BOM，
# 否则 PowerShell 5.1 按 ANSI 读，中文变乱码、语法直接崩
foreach ($f in @('gui.ps1', 'install-core.ps1', 'setup.ps1', 'uninstall.ps1', 'build.ps1')) {
  $path = Join-Path $Installer $f
  if (-not (Test-Path $path)) { continue }
  $bytes = [System.IO.File]::ReadAllBytes($path)
  if (-not ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)) {
    throw "$f 少了 UTF-8 BOM。带中文的 ps1 没 BOM，PowerShell 5.1 会读成乱码。"
  }
}
Write-Host '  各 ps1 的 BOM ok'

# ---------------------------------------------------------------- 铺 stage
Write-Host '正在铺 stage…'
Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $Stage -Force | Out-Null
foreach ($f in @('install.cmd', 'run.vbs', 'gui.ps1', 'install-core.ps1', 'setup.ps1', 'uninstall.ps1')) {
  Copy-Item (Join-Path $Installer $f) $Stage
}
Set-Content -Path (Join-Path $Stage 'version.txt') -Value $version -Encoding UTF8

Write-Host '正在压 payload.zip（一千多个文件，稍等）…'
$zipPath = Join-Path $Stage 'payload.zip'
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
  $payload, $zipPath, [System.IO.Compression.CompressionLevel]::Optimal, $false)
Write-Host ("  payload.zip：{0:N1} MB" -f ((Get-Item $zipPath).Length / 1MB))

# ---------------------------------------------------------------- SED
$exeName = "MythclassSetup-$version.exe"
$target = Join-Path $OutDir $exeName
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
Remove-Item $target -Force -ErrorAction SilentlyContinue

$files = @('install.cmd', 'run.vbs', 'gui.ps1', 'install-core.ps1',
           'setup.ps1', 'uninstall.ps1', 'version.txt', 'payload.zip')
$strings = @()
for ($i = 0; $i -lt $files.Count; $i++) { $strings += "FILE$i=`"$($files[$i])`"" }
$srcLines = @()
for ($i = 0; $i -lt $files.Count; $i++) { $srcLines += "%FILE$i%=" }

$sed = @"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=1
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName=$target
FriendlyName=Mythclass 客户端 安装程序 $version
AppLaunched=install.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=install.cmd /Quiet
UserQuietInstCmd=install.cmd /Quiet
SourceFiles=SourceFiles
[Strings]
$($strings -join "`r`n")
[SourceFiles]
SourceFiles0=$Stage\
[SourceFiles0]
$($srcLines -join "`r`n")
"@

$sedPath = Join-Path $Stage 'mythclass.sed'
Set-Content -Path $sedPath -Value $sed -Encoding ASCII

Write-Host '正在调 iexpress 打包…'
$iexpress = Join-Path $env:SystemRoot 'System32\iexpress.exe'
& $iexpress /N /Q $sedPath | Out-Null
Start-Sleep -Seconds 2

if (-not (Test-Path $target)) { throw "iexpress 没生成产物：$target" }
Write-Host ''
Write-Host ("装好了：" + $target)
Write-Host ("体积：{0:N1} MB" -f ((Get-Item $target).Length / 1MB))
Write-Host ("SHA256：" + (Get-FileHash $target -Algorithm SHA256).Hash)

if (-not $NoCleanup) { Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue }
