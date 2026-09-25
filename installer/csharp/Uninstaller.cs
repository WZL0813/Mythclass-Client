// Mythclass 卸载程序
//
// 编译：installer\build-csharp.ps1（和安装器一起编，共用 install-core 那套常量）
//
// 为什么重写：原来的 uninstall.cmd 有三个错叠在一起 ——
//   1. 快捷方式名字对不上（装的时候叫「Mythclass 客户端.lnk」，
//      删的时候找的是「MythclassClient.lnk」），永远删不掉
//   2. rd /s /q "%~dp0" 里 %~dp0 结尾自带反斜杠，"C:\path\" 的引号被转义，
//      删除命令直接失效
//   3. 双击 .cmd 不是管理员，Program Files 下的文件没权限删，
//      结果只 kill 掉了进程
//
// 现在这个 exe：
//   · 带 requireAdministrator 清单，双击就弹 UAC（标准用户会要求输入管理员密码）
//   · 先弹确认框「是否要卸载 Mythclass 若思班级一体机管理系统」确定/取消
//   · 确定后如果要卸载密码，再要求输入（防止学生自己卸）
//   · 自己删掉除自己以外的所有东西，剩下的交给一个独立进程收尾
//
// 参数（给批量/静默用）：
//   --silent              不弹界面
//   --target <目录>       指定卸载哪个目录（默认取自己所在目录）
//   --password <密码>     直接提供卸载密码
//   --noelevate           跳过管理员检查（测试用）
//   --keep-data           保留 %APPDATA% 里的运行数据

using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Windows.Forms;

namespace MythclassSetup
{
    static class Uninstaller
    {
        const string DataDir = @"C:\ProgramData\Mythclass";
        const string PasswordFile = "uninstall.json";

        static string optTarget = null;
        static string optPassword = null;
        static string optUsername = null;
        static bool optSilent = false;
        static bool optNoElevate = false;
        static bool optKeepData = false;

        [STAThread]
        static int Main(string[] args)
        {
            // 设置卸载密码：先处理（不用走后面的卸载流程），
            // 但必须管理员权限 —— 否则学生自己设一个密码就能把客户端卸了
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i].ToLowerInvariant() != "--set-password" || i + 1 >= args.Length) continue;
                if (!optNoElevate && !IsAdmin())
                {
                    try
                    {
                        var psi = new ProcessStartInfo(Application.ExecutablePath, JoinArgs(args));
                        psi.Verb = "runas";
                        psi.UseShellExecute = true;
                        Process.Start(psi);
                    }
                    catch
                    {
                        MessageBox.Show("设置卸载密码需要管理员权限。", Program.DisplayName,
                            MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    }
                    return 0;
                }
                return SetPassword(args[i + 1]);
            }

            for (int i = 0; i < args.Length; i++)
            {
                switch (args[i].ToLowerInvariant())
                {
                    case "--silent": optSilent = true; break;
                    case "--target": if (i + 1 < args.Length) optTarget = args[++i]; break;
                    case "--password": if (i + 1 < args.Length) optPassword = args[++i]; break;
                    case "--username": if (i + 1 < args.Length) optUsername = args[++i]; break;
                    case "--noelevate": optNoElevate = true; break;
                    case "--keep-data": optKeepData = true; break;
                }
            }

            var dir = optTarget;
            if (string.IsNullOrEmpty(dir))
                dir = Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location);

            if (!optNoElevate && !IsAdmin())
            {
                try
                {
                    var psi = new ProcessStartInfo(Application.ExecutablePath, JoinArgs(args));
                    psi.Verb = "runas";
                    psi.UseShellExecute = true;
                    Process.Start(psi);
                }
                catch
                {
                    MessageBox.Show("卸载需要管理员权限。", Program.DisplayName,
                        MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
                return 0;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            // 一、先问要不要卸
            if (!optSilent)
            {
                var answer = MessageBox.Show(
                    "是否要卸载「" + Program.DisplayName + "」？\r\n\r\n" +
                    "卸载后这台机器将不再受教师端管理。",
                    "卸载 " + Program.DisplayName,
                    MessageBoxButtons.OKCancel, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2);
                if (answer != DialogResult.OK) return 0;
            }

            // 二、要 Mythclass 密码（防止学生自己卸）
            //
            // 三条路任一条通过就行：
            //   · 本机 Mythclass 管理密码（config.json 里那份，本地就能比）
            //   · 安装时设的卸载密码
            //   · 教师账号密码（发服务端比，而且要求绑定过这台机器）
            if (!optSilent)
            {
                string given = optPassword;
                string account = optUsername ?? "";
                var tries = 0;
                var hint = "输入 Mythclass 的密码。\r\n\r\n" +
                           "本机的管理密码可以直接用（重装过客户端的话会重置成默认的 admin123）；\r\n" +
                           "也可以填教师账号 + 密码。";

                while (true)
                {
                    if (string.IsNullOrEmpty(given))
                    {
                        using (var dialog = new MythPasswordDialog(hint, MythclassPassword.DescribeCredentials()))
                        {
                            // 只认 Mythclass 密码：窗口上的「用管理员账户密码」那条路
                            // 已经拿掉 —— 系统管理员密码在教室里可能就是学生知道的那串
                            if (dialog.ShowDialog() != DialogResult.OK) return 0;
                            given = dialog.Password;
                            if (!string.IsNullOrEmpty(dialog.Username)) account = dialog.Username;
                        }
                    }

                    var local = MythclassPassword.VerifyLocal(given);
                    if (local != MythclassPassword.Source.None)
                    {
                        Program.TryLog("卸载：密码通过（" + local + "）");
                        break;
                    }

                    // 这台机器上没有任何 Mythclass 密码材料（装了但从没跑过客户端，
                    // 也没设过卸载密码）。
                    // 不能拿「是不是管理员」当后门 —— 管理员账户上提权是免费的，
                    // 等于不设防。所以明确拒绝，并告诉他怎么补救。
                    string cfgUid, cfgServer;
                    MythclassPassword.ReadIdentity(out cfgUid, out cfgServer);
                    if (string.IsNullOrEmpty(cfgUid) &&
                        !File.Exists(Path.Combine(@"C:\ProgramData\Mythclass", "uninstall.json")))
                    {
                        Program.TryLog("卸载：机器上没有任何 Mythclass 密码材料，拒绝");
                        MessageBox.Show(
                            "这台机器上找不到任何 Mythclass 密码。\r\n\r\n" +
                            "先运行一次客户端（它会生成默认密码 admin123），\r\n" +
                            "或者用 MythclassUninstall.exe --set-password 设一个卸载密码。",
                            Program.DisplayName, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                        return 4;
                    }

                    string uid, server;
                    MythclassPassword.ReadIdentity(out uid, out server);
                    string message;
                    var online = MythclassPassword.VerifyOnline(server, uid, account, given, out message);
                    if (online)
                    {
                        Program.TryLog("卸载：教师账号验证通过（" + account + "）");
                        break;
                    }

                    tries++;
                    if (tries >= 3)
                    {
                        MessageBox.Show("试了三次都不对，卸载取消。\r\n\r\n" + message,
                            Program.DisplayName, MessageBoxButtons.OK, MessageBoxIcon.Error);
                        return 3;
                    }
                    // 每轮都用固定文案，别再往旧提示前面接（会越叠越长）
                    hint = "密码不对（" + message + "）。再试一次。\r\n\r\n" +
                           "提示：重装过客户端的话，本机管理密码会重置成默认的 admin123；\r\n" +
                           "也可以填教师账号 + 密码（那个账号得绑定过这台机器）。";
                    given = null;
                }
            }
            else
            {
                // 静默模式：**必须**给密码。
                // 不给就直接拒绝 —— 否则学生拿 --silent 一跑就绕过了密码。
                if (string.IsNullOrEmpty(optPassword))
                {
                    Program.TryLog("卸载：静默模式没给 --password，拒绝");
                    return 3;
                }
                if (MythclassPassword.VerifyLocal(optPassword) == MythclassPassword.Source.None)
                {
                    string uid, server;
                    MythclassPassword.ReadIdentity(out uid, out server);
                    string message;
                    if (!MythclassPassword.VerifyOnline(server, uid, optUsername, optPassword, out message))
                    {
                        Program.TryLog("卸载：静默模式密码没过（" + message + "）");
                        return 3;
                    }
                }
            }

            // 三、真卸
            var form = optSilent ? null : new ProgressForm("正在卸载…");
            if (form != null) form.Show();
            Action<string> step = (m) =>
            {
                if (form != null) form.Step(m);
                Program.TryLog("卸载：" + m);
            };

            try
            {
                DoUninstall(dir, step);
            }
            catch (Exception ex)
            {
                Program.TryLog("卸载失败：" + ex);
                if (form != null) form.Close();
                MessageBox.Show("卸载时出错：\r\n" + ex.Message, Program.DisplayName,
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }

            if (form != null)
            {
                form.Close();
                MessageBox.Show("卸干净了。\r\n\r\n" +
                    (optKeepData ? "运行数据保留着。" : "运行数据也清掉了。"),
                    Program.DisplayName, MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            return 0;
        }

        static void DoUninstall(string dir, Action<string> step)
        {
            step("正在结束客户端…");
            foreach (var p in Process.GetProcessesByName("MythclassClient"))
            {
                try { p.Kill(); } catch { }
            }
            foreach (var task in new[] { "MythclassClient", "MythclassGuard" })
            {
                Run("schtasks.exe", "/End /TN " + task);
                Run("schtasks.exe", "/Delete /TN " + task + " /F");
            }
            System.Threading.Thread.Sleep(600);

            step("正在撤防火墙规则…");
            try
            {
                var fwPsi = new ProcessStartInfo("netsh.exe",
                    "advfirewall firewall delete rule name=\"Mythclass 局域网直连\"")
                { CreateNoWindow = true, UseShellExecute = false };
                Process.Start(fwPsi).WaitForExit(10000);
            }
            catch { }

            step("正在删开始菜单与桌面快捷方式…");
            // 名字必须和安装时一致，另外顺手清掉早期版本用错的名字
            var programs = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.CommonStartMenu), "Programs");
            var desktop = Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory);
            foreach (var name in new[] { Program.AppName, "Mythclass 客户端", "MythclassClient" })
            {
                TryDelete(Path.Combine(programs, name + ".lnk"));
                TryDelete(Path.Combine(desktop, name + ".lnk"));
            }
            // 开始菜单里那个「Mythclass」文件夹也一起收掉
            TryDeleteDir(Path.Combine(programs, "Mythclass"));

            step("正在清卸载密码…");
            // 密码是机器级设置，卸干净了就该一起清掉，
            // 不然重装之后还得用旧密码才能卸
            TryDelete(Path.Combine(DataDir, PasswordFile));

            step("正在清「程序和功能」登记…");
            try
            {
                Microsoft.Win32.Registry.LocalMachine.DeleteSubKeyTree(Program.RegPath, false);
            }
            catch { }

            if (!optKeepData)
            {
                step("正在清运行数据与开机自启…");
                TryDeleteDir(Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Mythclass"));
                try
                {
                    var hku = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                        @"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", true);
                    if (hku != null) { hku.DeleteValue("MythclassClient", false); hku.Close(); }
                }
                catch { }
            }

            step("正在删程序文件…");
            if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir))
            {
                step("  目录已经不在了");
                return;
            }

            var self = System.Reflection.Assembly.GetExecutingAssembly().Location;
            // 先把能删的都删掉，剩下自己和空目录交给独立进程收尾
            foreach (var file in Directory.GetFiles(dir))
            {
                if (string.Equals(file, self, StringComparison.OrdinalIgnoreCase)) continue;
                TryDelete(file);
            }
            foreach (var sub in Directory.GetDirectories(dir))
            {
                TryDeleteDir(sub);
            }

            step("正在收尾…");
            // 注意：这里绝不能给路径加尾反斜杠，否则 "C:\path\" 的引号会被转义掉
            var psi = new ProcessStartInfo("cmd.exe",
                "/c ping 127.0.0.1 -n 2 >nul & rd /s /q \"" + dir.TrimEnd('\\') + "\"");
            psi.CreateNoWindow = true;
            psi.UseShellExecute = false;
            try { Process.Start(psi); } catch { }
        }

        static void TryDelete(string path)
        {
            try { if (File.Exists(path)) File.Delete(path); } catch { }
        }

        static void TryDeleteDir(string path)
        {
            try { if (Directory.Exists(path)) Directory.Delete(path, true); } catch { }
        }

        static void Run(string exe, string args)
        {
            try
            {
                var psi = new ProcessStartInfo(exe, args);
                psi.CreateNoWindow = true;
                psi.UseShellExecute = false;
                Process.Start(psi).WaitForExit(10000);
            }
            catch { }
        }

        // ------------------------------ 卸载密码 ------------------------------

        /// <summary>把密码存成 PBKDF2 哈希（只存哈希，不存明文）</summary>
        public static int SetPassword(string password)
        {
            Directory.CreateDirectory(DataDir);
            var salt = new byte[16];
            using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(salt);
            var hash = Pbkdf2(password, salt, 100000);
            var json = "{\"salt\":\"" + Convert.ToBase64String(salt) + "\",\"hash\":\"" +
                       Convert.ToBase64String(hash) + "\",\"iterations\":100000}";
            File.WriteAllText(Path.Combine(DataDir, PasswordFile), json, Encoding.UTF8);
            Console.WriteLine("卸载密码已设置。");
            return 0;
        }

        static string LoadPasswordHash()
        {
            try
            {
                var path = Path.Combine(DataDir, PasswordFile);
                if (!File.Exists(path)) return null;
                return File.ReadAllText(path, Encoding.UTF8);
            }
            catch { return null; }
        }

        static bool VerifyPassword(string stored, string given)
        {
            try
            {
                var salt = Convert.FromBase64String(Field(stored, "salt"));
                var hash = Convert.FromBase64String(Field(stored, "hash"));
                var iterations = int.Parse(Field(stored, "iterations"));
                var got = Pbkdf2(given ?? "", salt, iterations);
                if (got.Length != hash.Length) return false;
                return FixedTimeEquals(got, hash);
            }
            catch { return false; }
        }

        /// <summary>等时比较：别让比较耗时泄漏信息。
        /// .NET Framework 4 没有 CryptographicOperations，自己写一个。</summary>
        static bool FixedTimeEquals(byte[] a, byte[] b)
        {
            if (a == null || b == null || a.Length != b.Length) return false;
            var diff = 0;
            for (var i = 0; i < a.Length; i++) diff |= a[i] ^ b[i];
            return diff == 0;
        }

        static string Field(string json, string name)
        {
            var key = "\"" + name + "\":\"";
            var at = json.IndexOf(key, StringComparison.Ordinal);
            if (at < 0)
            {
                key = "\"" + name + "\":";
                at = json.IndexOf(key, StringComparison.Ordinal);
                if (at < 0) throw new FormatException("缺字段 " + name);
                at += key.Length;
                var tail = json.IndexOfAny(new[] { ',', '}' }, at);
                return json.Substring(at, tail - at).Trim().Trim('"');
            }
            at += key.Length;
            return json.Substring(at, json.IndexOf('"', at) - at);
        }

        static byte[] Pbkdf2(string password, byte[] salt, int iterations)
        {
            // 必须指定 SHA256：不指定的话 Rfc2898DeriveBytes 默认用 SHA1，
            // 和 MythclassPassword 那边（SHA256）对不上，密码永远验不过
            using (var kdf = new Rfc2898DeriveBytes(password, salt, iterations, HashAlgorithmName.SHA256))
            {
                return kdf.GetBytes(32);
            }
        }

        static bool IsAdmin()
        {
            var id = System.Security.Principal.WindowsIdentity.GetCurrent();
            return new System.Security.Principal.WindowsPrincipal(id)
                .IsInRole(System.Security.Principal.WindowsBuiltInRole.Administrator);
        }

        static string JoinArgs(string[] args)
        {
            var sb = new StringBuilder();
            foreach (var a in args)
            {
                if (sb.Length > 0) sb.Append(' ');
                sb.Append(a.IndexOf(' ') >= 0 ? "\"" + a + "\"" : a);
            }
            return sb.ToString();
        }
    }

    /// <summary>要 Mythclass 密码时弹的那个小框</summary>
    class MythPasswordDialog : Form
    {
        public string Password { get; private set; }
        public string Username { get; private set; }
        readonly TextBox passwordBox;
        readonly TextBox userBox;

        public MythPasswordDialog(string hint, string diagnostic)
        {
            Text = "卸载需要 Mythclass 密码";
            ClientSize = new Size(470, 330);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterScreen;
            MaximizeBox = false;
            MinimizeBox = false;
            BackColor = Color.FromArgb(15, 20, 17);
            ForeColor = Color.FromArgb(232, 239, 230);
            Font = new Font("Microsoft YaHei UI", 9f);

            var tip = new Label
            {
                Text = hint,
                ForeColor = Color.FromArgb(201, 214, 198),
                BackColor = Color.Transparent,
                Location = new Point(20, 16),
                Size = new Size(430, 66)
            };
            Controls.Add(tip);

            Controls.Add(new Label
            {
                Text = diagnostic,
                ForeColor = Color.FromArgb(143, 168, 142),
                BackColor = Color.Transparent,
                Location = new Point(20, 84),
                Size = new Size(430, 18)
            });

            Controls.Add(new Label
            {
                Text = "教师账号（只有要用服务端验证时才填）",
                ForeColor = Color.FromArgb(143, 168, 142),
                BackColor = Color.Transparent,
                Location = new Point(20, 112),
                AutoSize = true
            });
            userBox = new TextBox
            {
                Location = new Point(22, 134),
                Size = new Size(426, 26),
                BackColor = Color.FromArgb(20, 27, 23),
                ForeColor = Color.FromArgb(232, 239, 230),
                BorderStyle = BorderStyle.FixedSingle
            };
            Controls.Add(userBox);

            Controls.Add(new Label
            {
                Text = "忘了的话：重装过的默认是 admin123",
                ForeColor = Color.FromArgb(120, 140, 120),
                BackColor = Color.Transparent,
                Location = new Point(20, 216),
                AutoSize = true
            });

            Controls.Add(new Label
            {
                Text = "Mythclass 密码",
                ForeColor = Color.FromArgb(143, 168, 142),
                BackColor = Color.Transparent,
                Location = new Point(20, 170),
                AutoSize = true
            });
            passwordBox = new TextBox
            {
                UseSystemPasswordChar = true,
                Location = new Point(22, 192),
                Size = new Size(426, 26),
                BackColor = Color.FromArgb(20, 27, 23),
                ForeColor = Color.FromArgb(232, 239, 230),
                BorderStyle = BorderStyle.FixedSingle
            };
            Controls.Add(passwordBox);

            var ok = new Button
            {
                Text = "确定",
                DialogResult = DialogResult.OK,
                Location = new Point(282, 276),
                Size = new Size(80, 32),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(63, 107, 82),
                ForeColor = Color.FromArgb(241, 246, 239)
            };
            ok.FlatAppearance.BorderColor = Color.FromArgb(76, 125, 97);
            Controls.Add(ok);

            var cancel = new Button
            {
                Text = "取消",
                DialogResult = DialogResult.Cancel,
                Location = new Point(370, 276),
                Size = new Size(80, 32),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(20, 27, 23),
                ForeColor = Color.FromArgb(143, 168, 142)
            };
            cancel.FlatAppearance.BorderColor = Color.FromArgb(42, 58, 46);
            Controls.Add(cancel);


            AcceptButton = ok;
            CancelButton = cancel;
            passwordBox.KeyDown += (s, e) =>
            {
                if (e.KeyCode == Keys.Enter) { Take(); DialogResult = DialogResult.OK; }
            };
            ok.Click += (s, e) => Take();
        }

        void Take()
        {
            Password = passwordBox.Text;
            Username = userBox.Text.Trim();
        }
    }

    /// <summary>卸载时的进度窗</summary>
    class ProgressForm : Form
    {
        readonly TextBox log;

        public ProgressForm(string title)
        {
            Text = title + " " + Program.DisplayName;
            ClientSize = new Size(520, 300);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterScreen;
            ControlBox = false;
            BackColor = Color.FromArgb(15, 20, 17);
            Font = new Font("Microsoft YaHei UI", 9f);

            log = new TextBox
            {
                Multiline = true,
                ReadOnly = true,
                ScrollBars = ScrollBars.Vertical,
                Location = new Point(18, 18),
                Size = new Size(484, 262),
                BackColor = Color.FromArgb(20, 27, 23),
                ForeColor = Color.FromArgb(201, 214, 198),
                BorderStyle = BorderStyle.FixedSingle
            };
            Controls.Add(log);
        }

        public void Step(string message)
        {
            log.AppendText(message + "\r\n");
            Application.DoEvents();
        }
    }
}
