using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Threading;
using System.Windows.Forms;

namespace MythclassSetup
{
    /// <summary>安装界面：协议 + 安装位置 + 快捷方式，一个窗口搞定</summary>
    class SetupForm : Form
    {
        const string LicenseText =
            "Mythclass 若思班级一体机管理系统 · 客户端\r\n" +
            "使用许可与免责声明\r\n\r\n" +
            "一、开源许可\r\n" +
            "本软件以 GNU Affero 通用公共许可证 v3.0（AGPL-3.0）发布，完整条款见随附的 LICENSE 文件。\r\n" +
            "你可以自由使用、修改、分发；若把修改后的版本作为网络服务对外提供，需一并开放源代码。\r\n\r\n" +
            "二、它会做什么\r\n" +
            "这是一个教室设备管理客户端。部署后，管理员（教师端）可以在你授权的范围内：\r\n" +
            "  · 查看该机器屏幕画面\r\n" +
            "  · 记录文件操作与音频会话信息\r\n" +
            "  · 下发锁屏、关机、重启、禁止上网等指令\r\n" +
            "请仅在你【有权管理】的设备上部署本软件，并事先告知设备使用人。\r\n\r\n" +
            "三、不提供担保\r\n" +
            "本软件按「现状」提供，不附带任何明示或暗示的担保。\r\n" +
            "因使用或无法使用本软件造成的任何损失，作者不承担责任。\r\n\r\n" +
            "四、同意\r\n" +
            "勾选下方选项并点击「开始安装」，表示你已阅读、理解并同意上述条款\r\n" +
            "以及 AGPL-3.0 的全部内容。";

        readonly Color CInk = Color.FromArgb(15, 20, 17);
        readonly Color CPanel = Color.FromArgb(20, 27, 23);
        readonly Color CLine = Color.FromArgb(42, 58, 46);
        readonly Color CText = Color.FromArgb(232, 239, 230);
        readonly Color CDim = Color.FromArgb(143, 168, 142);
        readonly Color CAccent = Color.FromArgb(63, 107, 82);

        TextBox licenseBox;
        CheckBox agreeBox;
        TextBox pathBox;
        CheckBox menuBox;
        CheckBox deskBox;
        CheckBox launchBox;
        Button browseBtn;
        Button installBtn;
        Button closeBtn;
        ProgressBar bar;
        Label statusLabel;
        TextBox logBox;
        bool installing;

        public SetupForm()
        {
            Text = Program.AppName + " 安装程序 v" + Program.Version;
            ClientSize = new Size(660, 560);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            BackColor = CInk;
            ForeColor = CText;
            Font = new Font("Microsoft YaHei UI", 9f);

            var title = new Label
            {
                Text = Program.AppName,
                BackColor = Color.Transparent,
                ForeColor = CText,
                Font = new Font("Microsoft YaHei UI", 15f, FontStyle.Bold),
                Location = new Point(20, 16),
                AutoSize = true
            };
            Controls.Add(title);

            var sub = new Label
            {
                Text = "先看协议，再选安装位置。快捷方式默认都不创建。",
                BackColor = Color.Transparent,
                ForeColor = CDim,
                Location = new Point(22, 50),
                AutoSize = true
            };
            Controls.Add(sub);

            licenseBox = new TextBox
            {
                Multiline = true,
                ReadOnly = true,
                ScrollBars = ScrollBars.Vertical,
                Text = LicenseText,
                Location = new Point(22, 78),
                Size = new Size(616, 190),
                BackColor = CPanel,
                ForeColor = Color.FromArgb(201, 214, 198),
                BorderStyle = BorderStyle.FixedSingle
            };
            Controls.Add(licenseBox);

            agreeBox = new CheckBox
            {
                Text = "我已阅读并同意上述条款（AGPL-3.0）",
                ForeColor = CText,
                Location = new Point(24, 278),
                AutoSize = true
            };
            agreeBox.CheckedChanged += (s, e) => installBtn.Enabled = agreeBox.Checked && !installing;
            Controls.Add(agreeBox);

            var pathLabel = new Label
            {
                Text = "安装位置",
                BackColor = Color.Transparent,
                ForeColor = CDim,
                Location = new Point(24, 312),
                AutoSize = true
            };
            Controls.Add(pathLabel);

            pathBox = new TextBox
            {
                Text = Program.DefaultDir,
                Location = new Point(24, 334),
                Size = new Size(520, 26),
                BackColor = CPanel,
                ForeColor = CText,
                BorderStyle = BorderStyle.FixedSingle
            };
            Controls.Add(pathBox);

            browseBtn = new Button
            {
                Text = "浏览…",
                Location = new Point(552, 333),
                Size = new Size(86, 27),
                FlatStyle = FlatStyle.Flat,
                BackColor = CPanel,
                ForeColor = CText
            };
            browseBtn.FlatAppearance.BorderColor = CLine;
            browseBtn.Click += BrowseClick;
            Controls.Add(browseBtn);

            menuBox = new CheckBox
            {
                Text = "在开始菜单创建快捷方式",
                ForeColor = CText,
                Location = new Point(24, 374),
                AutoSize = true
            };
            Controls.Add(menuBox);

            deskBox = new CheckBox
            {
                Text = "在桌面创建快捷方式",
                ForeColor = CText,
                Location = new Point(24, 398),
                AutoSize = true
            };
            Controls.Add(deskBox);

            launchBox = new CheckBox
            {
                Text = "安装完成后启动客户端",
                ForeColor = CText,
                Checked = true,
                Location = new Point(24, 428),
                AutoSize = true
            };
            Controls.Add(launchBox);

            bar = new ProgressBar
            {
                Location = new Point(24, 458),
                Size = new Size(614, 8),
                Style = ProgressBarStyle.Continuous,
                Minimum = 0,
                Maximum = 100
            };
            Controls.Add(bar);

            statusLabel = new Label
            {
                Text = "还没开始。",
                BackColor = Color.Transparent,
                ForeColor = CDim,
                Location = new Point(24, 472),
                Size = new Size(614, 18)
            };
            Controls.Add(statusLabel);

            logBox = new TextBox
            {
                Multiline = true,
                ReadOnly = true,
                ScrollBars = ScrollBars.Vertical,
                Location = new Point(24, 494),
                Size = new Size(614, 20),
                BackColor = CInk,
                ForeColor = Color.FromArgb(201, 214, 198),
                BorderStyle = BorderStyle.None,
                Visible = false
            };
            Controls.Add(logBox);

            installBtn = new Button
            {
                Text = "开始安装",
                Enabled = false,
                Location = new Point(452, 518),
                Size = new Size(110, 32),
                FlatStyle = FlatStyle.Flat,
                BackColor = CAccent,
                ForeColor = Color.FromArgb(241, 246, 239)
            };
            installBtn.FlatAppearance.BorderColor = Color.FromArgb(76, 125, 97);
            installBtn.Click += InstallClick;
            Controls.Add(installBtn);

            closeBtn = new Button
            {
                Text = "取消",
                Location = new Point(572, 518),
                Size = new Size(66, 32),
                FlatStyle = FlatStyle.Flat,
                BackColor = CPanel,
                ForeColor = CDim
            };
            closeBtn.FlatAppearance.BorderColor = CLine;
            closeBtn.Click += (s, e) => Close();
            Controls.Add(closeBtn);

            if (Program.IsAdmin()) statusLabel.Text = "已取得管理员权限，可以开始。";
            else statusLabel.Text = "注意：当前不是管理员，可能装不进 Program Files。";
        }

        void BrowseClick(object sender, EventArgs e)
        {
            using (var dlg = new FolderBrowserDialog())
            {
                dlg.Description = "选一个安装位置";
                dlg.SelectedPath = pathBox.Text;
                if (dlg.ShowDialog(this) == DialogResult.OK) pathBox.Text = dlg.SelectedPath;
            }
        }

        void Step(string msg)
        {
            Program.TryLog(msg);
            if (InvokeRequired) { BeginInvoke(new Action<string>(Step), msg); return; }
            statusLabel.Text = msg;
            logBox.Visible = true;
            logBox.AppendText(msg + "\r\n");
            Application.DoEvents();
        }

        void Progress(int percent)
        {
            if (InvokeRequired) { BeginInvoke(new Action<int>(Progress), percent); return; }
            bar.Value = Math.Max(0, Math.Min(100, percent));
            Application.DoEvents();
        }

        void InstallClick(object sender, EventArgs e)
        {
            if (installing) return;
            installing = true;
            installBtn.Enabled = false;
            browseBtn.Enabled = false;
            pathBox.ReadOnly = true;

            var target = pathBox.Text.Trim();
            if (target.Length == 0) target = Program.DefaultDir;

            try
            {
                var engine = new InstallEngine();
                engine.OnStep = Step;
                engine.OnProgress = Progress;
                engine.StopOldClient();

                // 装过更高版本就不许降级
                var blocked = engine.DowngradeMessage(target);
                if (blocked.Length > 0)
                {
                    Step(blocked);
                    statusLabel.Text = "不能降级安装。";
                    installing = false;
                    installBtn.Enabled = true;
                    return;
                }

                var old = engine.ClearOldVersion(target);
                if (old.Length > 0 && old != "not-ours") Step("  发现旧版本 " + old + "，已清掉");
                engine.Extract(target);
                Progress(92);
                engine.CreateShortcuts(target, menuBox.Checked, deskBox.Checked);
                engine.Register(target);
                if (launchBox.Checked) engine.Launch(target);
                Progress(100);

                statusLabel.Text = "装好了。";
                Step("装好了：" + target + "（v" + Program.Version + "）");
                Step("卸载：设置 → 应用 → " + Program.AppName);
                installBtn.Text = "关闭";
                installing = false;
                installBtn.Enabled = true;
                installBtn.Click -= InstallClick;
                installBtn.Click += (s2, e2) => Close();
            }
            catch (Exception ex)
            {
                Program.TryLog("安装失败：" + ex);
                Step("出错了：" + ex.Message);
                statusLabel.Text = "安装失败：" + ex.Message;
                MessageBox.Show("安装失败：\r\n" + ex.Message + "\r\n\r\n日志：%TEMP%\\MythclassSetup.log",
                    Program.AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
                installing = false;
                installBtn.Enabled = agreeBox.Checked;
                browseBtn.Enabled = true;
                pathBox.ReadOnly = false;
            }
        }
    }
}
