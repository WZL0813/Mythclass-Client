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

## 卸载

卸载也是**图形界面 + exe**，不是 cmd。

```
双击 <安装目录>\MythclassUninstall.exe
  ↓
弹确认框：是否要卸载「Mythclass 若思班级一体机管理系统」   [确定] [取消]
  ↓
（如果设了卸载密码）要求输入管理员密码
  ↓
（没设密码时）Windows 弹 UAC，标准用户会被要求输入管理员密码
  ↓
进度窗 → 卸干净
```

「设置 → 应用 → Mythclass 若思班级一体机管理系统」里点卸载，走的也是它。

### 为什么重写过一版

原来的 `uninstall.cmd` 有三个错叠在一起，导致「双击了但卸不掉，只能关掉进程」：

| 错 | 后果 |
|---|---|
| 快捷方式名字对不上（装的时候是「Mythclass 客户端.lnk」，删的时候找「MythclassClient.lnk」） | 快捷方式永远删不掉 |
| `rd /s /q "%~dp0"` 里 `%~dp0` 结尾自带反斜杠，`"C:\path\"` 的引号被转义 | 整个目录删不掉 |
| 双击 `.cmd` 不是管理员，Program Files 下没权限删 | 只 kill 掉了进程 |

新版：自己删掉除自己以外的所有东西，剩下的交给一个独立进程收尾
（`rd` 的路径**先 TrimEnd('\\')**，不再踩引号转义的坑）。

### 卸载密码（防止学生自己卸）

设置（在要限制的机器上用管理员身份跑一次）：

```powershell
MythclassUninstall.exe --set-password "你的密码"
```

存的是 PBKDF2-SHA256 哈希（10 万次迭代，带随机盐），
放在 `C:\ProgramData\Mythclass\uninstall.json`，**不存明文**。卸干净时会一起清掉。

> 注意：机器上如果登录的是**管理员账户**，UAC 不一定问密码，
> 学生能直接点「确定」。想挡住他们，**必须设这个卸载密码**。

### 命令行参数

```
--silent           不弹界面
--target <目录>    指定卸载哪个目录（默认取自己所在目录）
--password <密码>  直接提供卸载密码
--keep-data        保留 %APPDATA%\Mythclass 运行数据
--noelevate        跳过管理员检查（测试用）
--set-password <p> 设置卸载密码
```

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
