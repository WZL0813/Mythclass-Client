<#
  Mythclass 客户端 安装包 —— 用系统自带的 csc.exe 编译

  为什么不用 IExpress / Inno / NSIS：
    · IExpress 那条链太薄：SFX → cmd → wscript → 隐藏 PowerShell → WPF，
      任何一层出问题用户都只看到「双击没反应」（已经踩过两次）
    · Inno / NSIS 这机器上没有，而且下载源被网络拦了
    · csc.exe 是 Windows 自带的（.NET Framework 4），不用装任何东西，
      编译出来就是一个原生 exe：界面是系统控件、payload 内嵌、自己解压

  用法（仓库根目录）：
    powershell -ExecutionPolicy Bypass -File installer\build-csharp.ps1

  产物：installer\out\MythclassSetup-<版本>.exe
#>
[CmdletBinding()]
param(
  [switch]$SkipBuild,
  [switch]$KeepTemp
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Installer = $PSScriptRoot
$CsDir = Join-Path $Installer 'csharp'
$OutDir = Join-Path $Installer 'out'
$Tmp = Join-Path $env:TEMP ('mythclass-csc-' + [Guid]::NewGuid().ToString('N'))

function Head([string]$t) { Write-Host ''; Write-Host "== $t" -ForegroundColor Cyan }

# ---------------------------------------------------------------- 版本号
Head '版本号'
$initPy = Join-Path $Root 'mythclass\__init__.py'
$version = ([regex]::Match((Get-Content $initPy -Raw), '__version__\s*=\s*"([^"]+)"')).Groups[1].Value
if (-not $version) { throw '读不到 __version__' }
$csVersion = ([regex]::Match((Get-Content (Join-Path $CsDir 'MythclassSetup.cs') -Raw), 'Version\s*=\s*"([^"]+)"')).Groups[1].Value
Write-Host "  __init__.py : $version"
Write-Host "  C# 里写的   : $csVersion"
if ($version -ne $csVersion) {
  Write-Host "  两边不一致，把 C# 里的改成 $version" -ForegroundColor Yellow
  $p = Join-Path $CsDir 'MythclassSetup.cs'
  $t = Get-Content $p -Raw
  $t = $t -replace 'Version = "[^"]+"', "Version = `"$version`""
  [System.IO.File]::WriteAllText($p, $t, (New-Object System.Text.UTF8Encoding($true)))
}

# ---------------------------------------------------------------- 目录版产物
$payload = Join-Path $Root 'dist-onedir\MythclassClient'
if (-not $SkipBuild) {
  Head '打目录版'
  Push-Location $Root
  try {
    Remove-Item (Join-Path $Root 'dist-onedir') -Recurse -Force -ErrorAction SilentlyContinue
    python -m PyInstaller build\mythclass-onedir.spec --noconfirm --clean `
      --distpath (Join-Path $Root 'dist-onedir') --workpath (Join-Path $Root 'build\mythclass-onedir') |
      Select-String 'Build complete|ERROR:' | Select-Object -Last 1
  } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $payload 'MythclassClient.exe'))) { throw "没有目录版产物：$payload" }

# ---------------------------------------------------------------- 压 payload
Head '压 payload.zip'
New-Item -ItemType Directory -Path $Tmp -Force | Out-Null
$zipPath = Join-Path $Tmp 'payload.zip'
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
  $payload, $zipPath, [System.IO.Compression.CompressionLevel]::Optimal, $false)
Write-Host ("  {0:N1} MB" -f ((Get-Item $zipPath).Length / 1MB))

# ---------------------------------------------------------------- 编译卸载器
# 先编它，因为安装器要把它作为资源内嵌进去。
# 它自己带 requireAdministrator 清单：双击就弹 UAC，
# 标准用户会被要求输入管理员密码。
Head '编译卸载器'
# 先确保 out 目录在：删掉 out 之后再编译，csc 写 Win32 资源会报
# 「系统找不到指定的路径」(CS1567)
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$csc = @(
  "$env:SystemRoot\Microsoft.NET.Framework64\v4.0.30319\csc.exe",
  "$env:SystemRoot\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) { throw '找不到 csc.exe' }

$uninstaller = Join-Path $OutDir 'MythclassUninstall.exe'
Remove-Item $uninstaller -Force -ErrorAction SilentlyContinue

$refs = @(
  "$env:SystemRoot\Microsoft.NET\Framework64\v4.0.30319\System.Windows.Forms.dll",
  "$env:SystemRoot\Microsoft.NET\Framework64\v4.0.30319\System.Drawing.dll",
  "$env:SystemRoot\Microsoft.NET\Framework64\v4.0.30319\System.IO.Compression.dll",
  "$env:SystemRoot\Microsoft.NET\Framework64\v4.0.30319\System.IO.Compression.FileSystem.dll",
  "$env:SystemRoot\Microsoft.NET\Framework64\v4.0.30319\Microsoft.CSharp.dll"
) | Where-Object { Test-Path $_ }
$refArgs = $refs | ForEach-Object { "/r:`"$_`"" }
$srcs = Get-ChildItem $CsDir -Filter *.cs | ForEach-Object { "`"$($_.FullName)`"" }
$icon = Join-Path $Root 'build\mythclass.ico'

$unArgs = @(
  '/nologo', '/target:winexe', '/platform:anycpu', '/optimize+',
  '/main:MythclassSetup.Uninstaller',
  # 注意：不给它挂 requireAdministrator 清单。
  # 那样 Windows 会直接拒绝非提权启动，连 --noelevate 测试都跑不了。
  # 提权在代码里做（和安装器一样），UAC 提示照旧。
  "/out:`"$uninstaller`""
) + $refArgs
if (Test-Path $icon) { $unArgs += "/win32icon:`"$icon`"" }
$unArgs += $srcs
$out = & $csc @unArgs 2>&1
if ($out) { $out | ForEach-Object { "    $_" } }
if (-not (Test-Path $uninstaller)) { throw '卸载器编译失败' }
Write-Host ("  卸载器：{0:N0} KB" -f ((Get-Item $uninstaller).Length / 1KB))

# ---------------------------------------------------------------- 编译安装器
Head '编译安装器'
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$exeName = "MythclassSetup-$version.exe"
$target = Join-Path $OutDir $exeName
Remove-Item $target -Force -ErrorAction SilentlyContinue

$args = @(
  '/nologo', '/target:winexe', '/platform:anycpu', '/optimize+',
  # 卸载器里也有 Main，两个入口点必须显式指定
  '/main:MythclassSetup.Program',
  "/out:`"$target`"",
  "/resource:`"$zipPath`",payload.zip",
  "/resource:`"$uninstaller`",uninstaller.exe"
) + $refArgs
if (Test-Path $icon) { $args += "/win32icon:`"$icon`"" }
$args += $srcs

Write-Host '  正在编译…'
$output = & $csc @args 2>&1
if ($output) { $output | ForEach-Object { "    $_" } }
if (-not (Test-Path $target)) { throw "编译失败，没有产物：$target" }

# ---------------------------------------------------------------- 结果
Head '结果'
$size = (Get-Item $target).Length
Write-Host ("  安装器：$target")
Write-Host ("  体积  ：{0:N1} MB" -f ($size / 1MB))
Write-Host ("  卸载器：$uninstaller")
Write-Host ("  SHA256（安装器）：" + (Get-FileHash $target -Algorithm SHA256).Hash)
Write-Host ("  SHA256（卸载器）：" + (Get-FileHash $uninstaller -Algorithm SHA256).Hash)
if (-not $KeepTemp) { Remove-Item $Tmp -Recurse -Force -ErrorAction SilentlyContinue }
