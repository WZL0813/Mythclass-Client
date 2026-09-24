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
