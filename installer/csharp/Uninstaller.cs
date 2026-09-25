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
        static bool optSilent = false;
        static bool optNoElevate = false;
        static bool optKeepData = false;

        [STAThread]
        static int Main(string[] args)
        {
            // 设置卸载密码不需要提权（只是写个哈希），先处理掉
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i].ToLowerInvariant() == "--set-password" && i + 1 < args.Length)
                    return SetPassword(args[i + 1]);
            }

            for (int i = 0; i < args.Length; i++)
            {
                switch (args[i].ToLowerInvariant())
                {
                    case "--silent": optSilent = true; break;
                    case "--target": if (i + 1 < args.Length) optTarget = args[++i]; break;
                    case "--password": if (i + 1 < args.Length) optPassword = args[++i]; break;
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

            // 二、有卸载密码就要密码（防止学生自己卸）
            var stored = LoadPasswordHash();
            if (!string.IsNullOrEmpty(stored))
            {
                var given = optPassword;
                if (string.IsNullOrEmpty(given))
                {
                    if (optSilent) return 3;
                    using (var dialog = new PasswordDialog())
                    {
                        if (dialog.ShowDialog() != DialogResult.OK) return 0;
                        given = dialog.Password;
                    }
                }
                if (!VerifyPassword(stored, given))
                {
                    if (!optSilent)
                        MessageBox.Show("密码不对，卸载取消。", Program.DisplayName,
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return 3;
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

            step("正在删开始菜单与桌面快捷方式…");
            // 名字必须和安装时一致，另外顺手清掉早期版本用错的名字
            var startMenu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonStartMenu), "Programs");
            var desktop = Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory);
            foreach (var name in new[] { Program.AppName, "Mythclass 客户端", "MythclassClient" })
            {
                TryDelete(Path.Combine(startMenu, name + ".lnk"));
                TryDelete(Path.Combine(desktop, name + ".lnk"));
            }

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
            using (var kdf = new Rfc2898DeriveBytes(password, salt, iterations))
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

    /// <summary>要卸载密码时弹的那个小框</summary>
    class PasswordDialog : Form
    {
        public string Password { get; private set; }
        readonly TextBox box;

        public PasswordDialog()
        {
            Text = "需要管理员密码";
            ClientSize = new Size(400, 190);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterScreen;
            MaximizeBox = false;
            MinimizeBox = false;
            BackColor = Color.FromArgb(15, 20, 17);
            ForeColor = Color.FromArgb(232, 239, 230);
            Font = new Font("Microsoft YaHei UI", 9f);

            Controls.Add(new Label
            {
                Text = "卸载这台机器需要管理员密码。",
                ForeColor = Color.FromArgb(232, 239, 230),
                BackColor = Color.Transparent,
                Location = new Point(20, 18),
                AutoSize = true
            });
            Controls.Add(new Label
            {
                Text = "被学生看到的话，这台机器就管不住了。",
                ForeColor = Color.FromArgb(143, 168, 142),
                BackColor = Color.Transparent,
                Location = new Point(20, 42),
                AutoSize = true
            });

            box = new TextBox
            {
                UseSystemPasswordChar = true,
                Location = new Point(22, 74),
                Size = new Size(356, 26),
                BackColor = Color.FromArgb(20, 27, 23),
                ForeColor = Color.FromArgb(232, 239, 230),
                BorderStyle = BorderStyle.FixedSingle
            };
            Controls.Add(box);

            var ok = new Button
            {
                Text = "确定",
                DialogResult = DialogResult.OK,
                Location = new Point(212, 124),
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
                Location = new Point(300, 124),
                Size = new Size(80, 32),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(20, 27, 23),
                ForeColor = Color.FromArgb(143, 168, 142)
            };
            cancel.FlatAppearance.BorderColor = Color.FromArgb(42, 58, 46);
            Controls.Add(cancel);

            AcceptButton = ok;
            CancelButton = cancel;
            box.KeyDown += (s, e) =>
            {
                if (e.KeyCode == Keys.Enter) { Password = box.Text; DialogResult = DialogResult.OK; }
            };
            ok.Click += (s, e) => { Password = box.Text; };
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
