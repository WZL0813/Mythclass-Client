<#
  Mythclass 客户端 安装向导（图形界面）

  三页：许可协议 → 安装位置与快捷方式 → 安装/完成
  默认不勾任何快捷方式（主人要求）

  被 IExpress 打出来的安装包会通过 run.vbs 悄悄拉起它，所以不会闪黑框。
  测试：powershell -File gui.ps1 -NoElevate -SelfTest
#>
[CmdletBinding()]
param(
  [string]$Source = '',
  [switch]$NoElevate
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
Add-Type -AssemblyName System.Windows.Forms   # 只为那个「浏览」文件夹对话框

. (Join-Path $PSScriptRoot 'install-core.ps1')

if (-not $Source) { $Source = $PSScriptRoot }
$Version = '2.0.2'
$verFile = Join-Path $Source 'version.txt'
if (Test-Path $verFile) { $Version = (Get-Content $verFile -Raw).Trim() }

# 要装到 Program Files，必须提权
if ((-not (Test-Admin)) -and (-not $NoElevate)) {
  $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
            '-File', "`"$PSCommandPath`"", '-Source', "`"$Source`"")
  Start-Process 'powershell.exe' -Verb RunAs -ArgumentList $argv | Out-Null
  exit 0
}

$LICENSE = @'
Mythclass 若思班级一体机管理系统 · 客户端
使用许可与免责声明

一、开源许可
本软件以 GNU Affero 通用公共许可证 v3.0（AGPL-3.0）发布，完整条款见随附的 LICENSE 文件。
你可以自由使用、修改、分发；若把修改后的版本作为网络服务对外提供，需一并开放源代码。

二、它会做什么
这是一个教室设备管理客户端。部署后，管理员（教师端）可以在你授权的范围内：
· 查看该机器屏幕画面
· 记录文件操作与音频会话信息
· 下发锁屏、关机、重启、禁止上网等指令

请仅在你**有权管理**的设备上部署本软件，并事先告知设备使用人。

三、不提供担保
本软件按「现状」提供，不附带任何明示或暗示的担保。
因使用或无法使用本软件造成的任何损失，作者不承担责任。

四、同意
勾选下方选项并继续，表示你已阅读、理解并同意上述条款以及 AGPL-3.0 的全部内容。
'@

$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Mythclass 客户端 安装程序 v$Version"
        Width="620" Height="520"
        WindowStartupLocation="CenterScreen"
        ResizeMode="NoResize"
        Background="#0F1411"
        FontFamily="Microsoft YaHei UI, Segoe UI">
  <Grid Margin="22">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <!-- 标题 -->
    <StackPanel Grid.Row="0" Margin="0,0,0,14">
      <TextBlock Text="Mythclass 客户端" Foreground="#E8EFE6" FontSize="21" FontWeight="SemiBold"/>
      <TextBlock x:Name="SubTitle" Text="第 1 步，共 3 步 · 使用许可" Foreground="#8FA88E" FontSize="12.5" Margin="0,4,0,0"/>
    </StackPanel>

    <!-- 第一页：协议 -->
    <Grid x:Name="PageLicense" Grid.Row="1">
      <Grid.RowDefinitions>
        <RowDefinition Height="*"/>
        <RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>
      <Border Grid.Row="0" Background="#141B17" BorderBrush="#2A3A2E" BorderThickness="1" CornerRadius="10">
        <TextBox x:Name="LicenseBox" Text="$($LICENSE -replace '"','&quot;')"
                 IsReadOnly="True" TextWrapping="Wrap" AcceptsReturn="True"
                 VerticalScrollBarVisibility="Auto" Background="Transparent" BorderThickness="0"
                 Foreground="#C9D6C6" Padding="14" FontSize="12.5" FontFamily="Microsoft YaHei UI"/>
      </Border>
      <CheckBox x:Name="AgreeBox" Grid.Row="1" Margin="2,14,0,0"
                Content="我已阅读并同意上述条款（AGPL-3.0）" Foreground="#E8EFE6" FontSize="13"/>
    </Grid>

    <!-- 第二页：位置与快捷方式 -->
    <Grid x:Name="PageOptions" Grid.Row="1" Visibility="Collapsed">
      <StackPanel>
        <TextBlock Text="安装位置" Foreground="#8FA88E" FontSize="12.5" Margin="2,0,0,6"/>
        <Grid>
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="Auto"/>
          </Grid.ColumnDefinitions>
          <TextBox x:Name="PathBox" Grid.Column="0" Height="34" Padding="9,6"
                   Background="#141B17" Foreground="#E8EFE6" BorderBrush="#2A3A2E"
                   FontSize="13" VerticalContentAlignment="Center"/>
          <Button x:Name="BrowseBtn" Grid.Column="1" Content="浏览…" Width="76" Height="34" Margin="8,0,0,0"
                  Background="#223026" Foreground="#DCE7D9" BorderBrush="#2A3A2E"/>
        </Grid>

        <TextBlock Text="快捷方式（默认都不创建）" Foreground="#8FA88E" FontSize="12.5" Margin="2,22,0,8"/>
        <CheckBox x:Name="StartMenuBox" Content="在开始菜单创建快捷方式" Foreground="#E8EFE6" FontSize="13" Margin="2,0,0,8"/>
        <CheckBox x:Name="DesktopBox"   Content="在桌面创建快捷方式"     Foreground="#E8EFE6" FontSize="13" Margin="2,0,0,0"/>

        <TextBlock Text="当前要安装的版本" Foreground="#8FA88E" FontSize="12.5" Margin="2,24,0,6"/>
        <TextBlock x:Name="VersionText" Text="v$Version" Foreground="#E8EFE6" FontSize="14"/>
      </StackPanel>
    </Grid>

    <!-- 第三页：安装中 / 完成 -->
    <Grid x:Name="PageInstall" Grid.Row="1" Visibility="Collapsed">
      <Grid.RowDefinitions>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="*"/>
        <RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>
      <ProgressBar x:Name="Bar" Grid.Row="0" Height="8" Minimum="0" Maximum="100" Value="0"
                   Background="#141B17" Foreground="#5E9A73" BorderThickness="0"/>
      <Border Grid.Row="1" Margin="0,14,0,0" Background="#141B17" BorderBrush="#2A3A2E" BorderThickness="1" CornerRadius="10">
        <TextBox x:Name="LogBox" IsReadOnly="True" TextWrapping="Wrap" AcceptsReturn="True"
                 VerticalScrollBarVisibility="Auto" Background="Transparent" BorderThickness="0"
                 Foreground="#C9D6C6" Padding="14" FontSize="12.5" FontFamily="Microsoft YaHei UI"/>
      </Border>
      <CheckBox x:Name="LaunchBox" Grid.Row="2" Margin="2,14,0,0" IsChecked="True"
                Content="安装完成后启动客户端" Foreground="#E8EFE6" FontSize="13"/>
    </Grid>

    <!-- 按钮 -->
    <StackPanel Grid.Row="2" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,18,0,0">
      <Button x:Name="BackBtn"   Content="上一步" Width="88" Height="34" Margin="0,0,8,0"
              Background="#1B241E" Foreground="#DCE7D9" BorderBrush="#2A3A2E" IsEnabled="False"/>
      <Button x:Name="NextBtn"   Content="下一步" Width="96" Height="34" Margin="0,0,8,0"
              Background="#3F6B52" Foreground="#F1F6EF" BorderBrush="#4C7D61" IsEnabled="False"/>
      <Button x:Name="CancelBtn" Content="取消"   Width="80" Height="34"
              Background="#1B241E" Foreground="#9FB09C" BorderBrush="#2A3A2E"/>
    </StackPanel>
  </Grid>
</Window>
"@

$window = [Windows.Markup.XamlReader]::Parse($xaml)

# 控件
$sub   = $window.FindName('SubTitle')
$pLic  = $window.FindName('PageLicense')
$pOpt  = $window.FindName('PageOptions')
$pIns  = $window.FindName('PageInstall')
$agree = $window.FindName('AgreeBox')
$pathBox = $window.FindName('PathBox')
$browse = $window.FindName('BrowseBtn')
$smBox = $window.FindName('StartMenuBox')
$dtBox = $window.FindName('DesktopBox')
$bar   = $window.FindName('Bar')
$logBox = $window.FindName('LogBox')
$launchBox = $window.FindName('LaunchBox')
$back  = $window.FindName('BackBtn')
$next  = $window.FindName('NextBtn')
$cancel = $window.FindName('CancelBtn')

$script:page = 1
$pathBox.Text = 'C:\Program Files (x86)\Mythclass'
$pathBox.IsReadOnly = $false

function Pump {
  # 让界面有机会刷新（PowerShell 里跑 WPF 的老办法）
  $window.Dispatcher.Invoke([action] {}, [System.Windows.Threading.DispatcherPriority]::Background)
}

function Set-Step([string]$text) { $logBox.AppendText($text + "`r`n"); $logBox.ScrollToEnd(); Pump }
function Set-Progress([int]$v) { $bar.Value = [Math]::Max(0, [Math]::Min(100, $v)); Pump }

$agree.Add_Checked({ $next.IsEnabled = $true })
$agree.Add_Unchecked({ $next.IsEnabled = $false })

$browse.Add_Click({
  $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
  $dlg.Description = '选一个安装位置'
  $dlg.SelectedPath = $pathBox.Text
  if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $pathBox.Text = $dlg.SelectedPath }
})

$back.Add_Click({
  if ($script:page -eq 2) {
    $script:page = 1
    $pLic.Visibility = 'Visible'; $pOpt.Visibility = 'Collapsed'
    $sub.Text = '第 1 步，共 3 步 · 使用许可'
    $back.IsEnabled = $false; $next.Content = '下一步'
  }
})

$cancel.Add_Click({ $window.Close() })

$next.Add_Click({
  if ($script:page -eq 1) {
    $script:page = 2
    $pLic.Visibility = 'Collapsed'; $pOpt.Visibility = 'Visible'
    $sub.Text = '第 2 步，共 3 步 · 安装位置'
    $back.IsEnabled = $true
    $next.Content = '安装'
    return
  }

  if ($script:page -ne 2) { return }

  $script:page = 3
  $pOpt.Visibility = 'Collapsed'; $pIns.Visibility = 'Visible'
  $sub.Text = '第 3 步，共 3 步 · 正在安装'
  $back.IsEnabled = $false; $next.IsEnabled = $false
  $next.Content = '完成'
  $cancel.Content = '关闭'

  $target = $pathBox.Text.Trim()
  if (-not $target) { $target = 'C:\Program Files (x86)\Mythclass' }

  try {
    Set-Progress 5
    $result = Invoke-Install -Source $Source -TargetDir $target -Version $Version `
      -WantStartMenu ([bool]$smBox.IsChecked) -WantDesktop ([bool]$dtBox.IsChecked) `
      -Launch ([bool]$launchBox.IsChecked) -OnStep { param($m) Set-Step $m }

    if (-not $result.Stopped) { Set-Step '  （提醒：旧客户端似乎还在跑，建议重启机器）' }
    Set-Progress 100
    $sub.Text = '装好了'
    Set-Step ''
    Set-Step "装好了：$target（v$Version）"
    Set-Step '卸载：设置 → 应用 → Mythclass 客户端'
  } catch {
    $sub.Text = '安装失败'
    Set-Step "出错了：$($_.Exception.Message)"
    $next.IsEnabled = $false
  }
})

$window.ShowDialog() | Out-Null
