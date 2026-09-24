# 客户端安装包

## 版本号规则

**目录版从 v2.0.0 起步；以后每改动一次，版本号 +0.0.1。**

```
2.0.0  ← 目录版第一次做成安装包
2.0.1  ← 安装包改成图形向导
2.0.2  ← 修 run.vbs 的 BOM（VBScript 不认，会报「无效字符」）
2.0.3  ← 修「双击没反应」：SFX 会删掉解包目录，改成先复制到稳定目录再启动（当前）
...
```

版本号在 `mythclass/__init__.py` 的 `__version__`，打包脚本从那儿读，
写进安装包文件名、安装目录里的 `version.txt` 和「程序和功能」显示。

> v1.0.0 是单文件 exe 时代的最后一个版本，不再往下走。

安装包会**自动停掉正在跑的旧客户端**（给所有用户放 `guardian-stop.flag`、
停掉计划任务、结束进程），并且**升级时先把旧版本目录清干净**，
不会出现新旧文件混在一起。

## 安装界面

安装包双击后是一个**图形向导**（WPF 画的，不是黑框命令行），三步：

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

界面文件：

| 文件 | 干什么 |
|---|---|
| `gui.ps1` | 图形向导本体（WPF 三页） |
| `install-core.ps1` | 安装逻辑的公共函数，图形版和命令行版共用 |
| `run.vbs` | 让向导悄悄起来，不闪黑框 |
| `setup.ps1` | 命令行 / 静默安装（批量部署、测试、排查用） |

## 怎么打包

```powershell
# 仓库根目录
powershell -ExecutionPolicy Bypass -File installer\build.ps1
```

产物：`installer\out\MythclassSetup-<版本>.exe`（单文件，双击即装）

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

## 为什么不做成 MSI

机器上没有 Inno Setup / NSIS / WiX，只有 Windows 自带的 IExpress。
IExpress 只认文件清单（一千多个文件没法一个个列），所以先把目录版压成
`payload.zip`，安装时再解开。

**安装包没做代码签名**，Windows 第一次运行会弹「未知发布者」——点「更多信息 → 仍要运行」。
要消掉这个提示得买代码签名证书。
