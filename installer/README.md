# 客户端安装包

## 版本号规则

**目录版从 v2.0.0 起步；以后每改动一次，版本号 +0.0.1。**

```
2.0.0  ← 目录版第一次做成安装包
2.0.1  ← 安装包改成图形向导
2.0.2  ← 修 run.vbs 的 BOM（VBScript 不认，会报「无效字符」）
2.0.3  ← 修「双击没反应」：SFX 会删掉解包目录，改成先复制到稳定目录再启动
2.0.4  ← 换掉整个打包方式：用系统自带的 csc 编译原生安装器（当前）
...
```

版本号在 `mythclass/__init__.py` 的 `__version__`，打包脚本从那儿读，
写进安装包文件名、安装目录里的 `version.txt` 和「程序和功能」显示。

> v1.0.0 是单文件 exe 时代的最后一个版本，不再往下走。

安装包会**自动停掉正在跑的旧客户端**（给所有用户放 `guardian-stop.flag`、
停掉计划任务、结束进程），并且**升级时先把旧版本目录清干净**，
不会出现新旧文件混在一起。

## 安装界面

安装包双击后是**原生 Windows 界面**（C# WinForms，不是黑框命令行），一个窗口：

```
第 1 步 · 使用许可
  · 满屏可滚动的许可与免责声明（AGPL-3.0 + 会采集什么 + 不担保）
  · 必须勾「我已阅读并同意」才能点下一步（没勾时按钮是灰的）

第 2 步 · 安装位置
  · 安装位置可看可改，带「浏览…」选文件夹
  · 两个快捷方式勾选框：开始菜单 / 桌面
    **默认都不勾**（要就自己勾）

第 3 步 · 正在安装
  · 进度条 + 逐条日志（停旧客户端 / 清旧版本 / 铺文件 / 快捷方式 / 登记）
  · 装完可以选「安装完成后启动客户端」（默认勾上）
```

文件：

| 文件 | 干什么 |
|---|---|
| `csharp\MythclassSetup.cs` | 入口：解析参数、提权、静默模式 |
| `csharp\SetupForm.cs` | 界面：协议 + 同意 + 安装位置 + 两个快捷方式勾选 |
| `csharp\InstallEngine.cs` | 安装逻辑：停旧客户端、清旧版本、解 payload、快捷方式、登记 |
| `csharp\uninstall.cmd` | 卸载脚本（装进安装目录，注册表指向它） |
| `build-csharp.ps1` | 打包脚本 |
| `setup.ps1` / `install-core.ps1` / `uninstall.ps1` | 兜底：纯 PowerShell 的命令行安装（排查用） |

## 怎么打包

```powershell
# 仓库根目录
powershell -ExecutionPolicy Bypass -File installer\build-csharp.ps1
```

产物：`installer\out\MythclassSetup-<版本>.exe`（单文件，双击即装）

里面做的事：读版本号 → 打目录版（PyInstaller onedir）→ 压成 payload.zip →
**用系统自带的 `csc.exe` 把 `installer\csharp\*.cs` 编成原生 exe**，
payload 作为资源内嵌进去。

不需要 Inno / NSIS / 任何下载：`csc.exe` 是 Windows 自带的（.NET Framework 4）。

支持的命令行参数（批量部署、测试用）：

```
--target <目录>   指定安装位置
--silent          不显示界面直接装
--nolaunch        装完不启动客户端
--noelevate       跳过管理员检查（测试用）
--uninstall       走卸载流程
```

## 装到哪儿

默认 `C:\Program Files (x86)\Mythclass`，装的时候会：

1. 请求管理员权限
2. 给所有用户放 `guardian-stop.flag`、停掉旧的客户端与计划任务
3. 铺文件（被占用会自动重试）
4. 建开始菜单与公共桌面快捷方式
5. 在「程序和功能」里登记（卸载走同一个路径）
6. 用 explorer 以**当前登录用户**身份启动客户端（不是管理员身份）

## 手动装 / 卸载（不开安装包）

```powershell
powershell -ExecutionPolicy Bypass -File installer\setup.ps1 -Source .\installer\stage
powershell -ExecutionPolicy Bypass -File installer\uninstall.ps1
```

测试时用 `-TargetDir D:\test -NoLaunch` 避免动真格。

## 为什么是「自己用 C# 写一个」而不是 IExpress / Inno / NSIS

- **IExpress**（Windows 自带）做出来的其实是自解压包，链条特别长：
  `SFX → cmd → wscript → 隐藏 PowerShell → WPF`。任何一层出问题，
  用户看到的都只是「双击没反应」——而且自解压包会把自己解出来的临时目录
  在 install.cmd 返回后删掉，正好把刚启动的向导一起删了（踩过这个坑）。
- **Inno Setup / NSIS** 机器上没有装，而且下载源在当前网络下拿不到文件。
- **csc.exe** 是每台 Windows 都有的 .NET Framework 编译器，
  编译出来的就是一个普通 exe：界面是系统控件、payload 内嵌、
  解压由它自己完成、不经过任何临时目录。一层，没有中间环节。

**安装包没做代码签名**，Windows 第一次运行会弹「未知发布者」——点「更多信息 → 仍要运行」。
要消掉这个提示得买代码签名证书。
