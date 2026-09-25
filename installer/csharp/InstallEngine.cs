using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace MythclassSetup
{
    /// <summary>安装逻辑。界面和静默模式共用这一份。</summary>
    class InstallEngine
    {
        public Action<string> OnStep;
        public Action<int> OnProgress;

        /// <summary>装不下去的原因（比如不许降级），静默安装靠它判断退出码</summary>
        public string LastError = "";

        void Step(string msg)
        {
            Program.TryLog(msg);
            if (OnStep != null) OnStep(msg);
        }

        void Progress(int percent)
        {
            if (OnProgress != null) OnProgress(percent);
        }

        /// <summary>停掉正在跑的旧客户端，否则文件一直被占着</summary>
        public void StopOldClient()
        {
            Step("正在停掉旧客户端…");

            // 先给「跟班」放旗子，不然杀了客户端几秒后又被拉起来
            WriteStopFlag(Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Mythclass"));
            try
            {
                foreach (var dir in Directory.GetDirectories(@"C:\Users"))
                {
                    WriteStopFlag(Path.Combine(dir, @"AppData\Roaming\Mythclass"));
                }
            }
            catch { }

            foreach (var task in new[] { "MythclassClient", "MythclassGuard" })
            {
                Run("schtasks.exe", "/End /TN " + task);
                Run("schtasks.exe", "/Delete /TN " + task + " /F");
            }

            for (int i = 0; i < 25; i++)
            {
                var procs = Process.GetProcessesByName("MythclassClient");
                if (procs.Length == 0) return;
                foreach (var p in procs)
                {
                    try { p.Kill(); } catch { }
                }
                Thread.Sleep(400);
            }
            Step("  （旧客户端好像还在，装完建议重启一下机器）");
        }

        static void WriteStopFlag(string dir)
        {
            try
            {
                if (!Directory.Exists(dir)) return;
                File.WriteAllText(Path.Combine(dir, "guardian-stop.flag"), "install");
            }
            catch { }
        }

        /// <summary>把 "2.4.0" 拆成数字，好比较大小；认不出来就返回 null</summary>
        static int[] ParseVersion(string text)
        {
            if (string.IsNullOrEmpty(text)) return null;
            var parts = text.Trim().TrimStart('v', 'V').Split('.');
            var nums = new int[parts.Length];
            for (int i = 0; i < parts.Length; i++)
            {
                int n;
                var digits = new string(Array.FindAll(parts[i].ToCharArray(), char.IsDigit));
                if (!int.TryParse(digits, out n)) return null;
                nums[i] = n;
            }
            return nums.Length == 0 ? null : nums;
        }

        static int CompareVersion(int[] a, int[] b)
        {
            if (a == null || b == null) return 0;
            int len = Math.Max(a.Length, b.Length);
            for (int i = 0; i < len; i++)
            {
                int x = i < a.Length ? a[i] : 0;
                int y = i < b.Length ? b[i] : 0;
                if (x != y) return x > y ? 1 : -1;
            }
            return 0;
        }

        /// <summary>注册表里记着的安装目录</summary>
        static string InstalledDir()
        {
            try
            {
                using (var key = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(Program.RegPath))
                {
                    if (key != null)
                    {
                        var loc = key.GetValue("InstallLocation") as string;
                        if (!string.IsNullOrEmpty(loc)) return loc;
                    }
                }
            }
            catch { }
            return "";
        }

        /// <summary>
        /// 装过更高版本就不许降级。
        /// 返回空串表示可以装；否则返回要显示给用户的那句话。
        /// 两个地方都看一眼：这次选的目标目录，以及注册表里记着的目录。
        /// </summary>
        public string DowngradeMessage(string targetDir)
        {
            var mine = ParseVersion(Program.Version);
            if (mine == null) return "";

            var dirs = new string[] { targetDir, InstalledDir() };
            foreach (var dir in dirs)
            {
                if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir)) continue;
                var vf = Path.Combine(dir, "version.txt");
                if (!File.Exists(vf)) continue;
                string old;
                try { old = File.ReadAllText(vf).Trim(); }
                catch { continue; }
                if (old.Length == 0) continue;

                if (CompareVersion(ParseVersion(old), mine) > 0)
                {
                    return "这台机器上已经装了更新的版本（v" + old + "），不能降级安装。"
                         + "要装这个旧版，请先卸载再装。";
                }
            }
            return "";
        }

        /// <summary>跑个外部命令，返回退出码（放防火墙规则要看它成没成）</summary>
        static int Run(string exe, string args)
        {
            try
            {
                var psi = new ProcessStartInfo(exe, args);
                psi.CreateNoWindow = true;
                psi.UseShellExecute = false;
                var p = Process.Start(psi);
                p.WaitForExit(15000);
                return p.ExitCode;
            }
            catch { return -1; }
        }

        /// <summary>清掉旧版本目录（只删自己那个，别人的东西不动）</summary>
        public string ClearOldVersion(string targetDir)
        {
            if (!Directory.Exists(targetDir)) return "";
            bool ours = File.Exists(Path.Combine(targetDir, Program.ExeName))
                     || File.Exists(Path.Combine(targetDir, "version.txt"));
            if (!ours) return "not-ours";

            string old = "";
            var vf = Path.Combine(targetDir, "version.txt");
            if (File.Exists(vf)) old = File.ReadAllText(vf).Trim();

            Step("正在清理旧版本…");
            for (int i = 0; i < 5; i++)
            {
                try { Directory.Delete(targetDir, true); break; }
                catch { Thread.Sleep(1500); }
            }
            return old;
        }

        /// <summary>把内嵌的 payload 直接解到安装目录（不经过任何临时目录）</summary>
        public void Extract(string targetDir)
        {
            Step("正在铺文件…");
            Directory.CreateDirectory(targetDir);

            using (var raw = Program.OpenPayload())
            using (var zip = new ZipArchive(raw, ZipArchiveMode.Read))
            {
                int total = zip.Entries.Count;
                int done = 0;
                var full = Path.GetFullPath(targetDir) + Path.DirectorySeparatorChar;

                foreach (var entry in zip.Entries)
                {
                    var dest = Path.GetFullPath(Path.Combine(targetDir, entry.FullName));
                    if (!dest.StartsWith(full, StringComparison.OrdinalIgnoreCase))
                        throw new IOException("压缩包里有越界的路径：" + entry.FullName);

                    if (entry.FullName.EndsWith("/") || entry.FullName.EndsWith("\\"))
                    {
                        Directory.CreateDirectory(dest);
                    }
                    else
                    {
                        Directory.CreateDirectory(Path.GetDirectoryName(dest));
                        entry.ExtractToFile(dest, true);
                    }

                    done++;
                    if (done % 40 == 0 || done == total) Progress(10 + (int)(80.0 * done / total));
                }
            }

            File.WriteAllText(Path.Combine(targetDir, "version.txt"), Program.Version);
            // 卸载器是个 exe（带管理员清单，双击就弹 UAC），
            // 不再是 cmd —— 老版本留下的 uninstall.cmd 顺手清掉
            WriteResource("uninstaller.exe", Path.Combine(targetDir, "MythclassUninstall.exe"));
            TryDelete(Path.Combine(targetDir, "uninstall.cmd"));

            if (!File.Exists(Path.Combine(targetDir, Program.ExeName)))
                throw new FileNotFoundException("铺完文件却没找到 " + Program.ExeName);
        }

        static void TryDelete(string path)
        {
            try { if (File.Exists(path)) File.Delete(path); } catch { }
        }

        static void WriteResource(string name, string destPath)
        {
            using (var s = System.Reflection.Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
            {
                if (s == null) return;
                using (var f = File.Create(destPath)) s.CopyTo(f);
            }
        }

        /// <summary>给局域网端口和本地网页放行入站（Windows 防火墙默认挡）</summary>
        public void OpenFirewall()
        {
            Step("正在给局域网端口放行…");
            try
            {
                Run("netsh.exe",
                    "advfirewall firewall delete rule name=\"Mythclass 局域网直连\"");
                var result = Run("netsh.exe",
                    "advfirewall firewall add rule name=\"Mythclass 局域网直连\" " +
                    "dir=in action=allow protocol=TCP localport=26924,26925 profile=any");
                if (result != 0)
                    Step("  （防火墙规则没加成，可能不是管理员；老师连不上就手动放行 26924/26925）");
            }
            catch (Exception err)
            {
                Step("  （防火墙规则跳过：" + err.Message + "）");
            }
        }

        /// <summary>设置卸载密码（存 PBKDF2-SHA256 哈希，和卸载器读的格式一致）</summary>
        public static void SetUninstallPassword(string password)
        {
            if (string.IsNullOrEmpty(password)) return;
            var dir = @"C:\ProgramData\Mythclass";
            Directory.CreateDirectory(dir);

            var salt = new byte[16];
            using (var rng = System.Security.Cryptography.RandomNumberGenerator.Create()) rng.GetBytes(salt);
            byte[] hash;
            using (var kdf = new System.Security.Cryptography.Rfc2898DeriveBytes(
                       password, salt, 100000, System.Security.Cryptography.HashAlgorithmName.SHA256))
            {
                hash = kdf.GetBytes(32);
            }
            var json = "{\"salt\":\"" + Convert.ToBase64String(salt) + "\",\"hash\":\"" +
                       Convert.ToBase64String(hash) + "\",\"iterations\":100000}";
            File.WriteAllText(Path.Combine(dir, "uninstall.json"), json, System.Text.Encoding.UTF8);
        }

        /// <summary>建快捷方式（用 WScript.Shell 的 COM，免得引一堆 interop）</summary>
        public void CreateShortcuts(string targetDir, bool startMenu, bool desktop)
        {
            if (!startMenu && !desktop) return;
            Step("正在建快捷方式…");

            var exe = Path.Combine(targetDir, Program.ExeName);
            var type = Type.GetTypeFromProgID("WScript.Shell");
            if (type == null) return;
            dynamic shell = Activator.CreateInstance(type);

            if (startMenu)
            {
                // 放进「Mythclass」文件夹里，开始菜单不至于被一堆单个条目占满。
                // 建不了（比如不是管理员）也不能让整个安装失败，所以包一层。
                try
                {
                    var programs = Path.Combine(
                        Environment.GetFolderPath(Environment.SpecialFolder.CommonStartMenu), "Programs");
                    var group = Path.Combine(programs, "Mythclass");
                    Directory.CreateDirectory(group);
                    SaveShortcut(shell, Path.Combine(group, Program.AppName + ".lnk"), exe, targetDir);

                    // 顺手清掉早期版本丢在「程序」根目录下的那些
                    TryDelete(Path.Combine(programs, Program.AppName + ".lnk"));
                    TryDelete(Path.Combine(programs, "Mythclass 客户端.lnk"));
                    TryDelete(Path.Combine(programs, "MythclassClient.lnk"));
                }
                catch (Exception err)
                {
                    Step("  （开始菜单快捷方式没建成：" + err.Message + "）");
                }
            }
            if (desktop)
            {
                var dir = Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory);
                if (!string.IsNullOrEmpty(dir))
                    SaveShortcut(shell, Path.Combine(dir, Program.AppName + ".lnk"), exe, targetDir);
            }
        }

        static void SaveShortcut(dynamic shell, string lnkPath, string exe, string workDir)
        {
            try
            {
                dynamic lnk = shell.CreateShortcut(lnkPath);
                lnk.TargetPath = exe;
                lnk.WorkingDirectory = workDir;
                lnk.Description = "Mythclass 若思班级一体机管理系统 客户端";
                lnk.Save();
            }
            catch { }
        }

        /// <summary>在「程序和功能」里登记自己</summary>
        public void Register(string targetDir)
        {
            Step("正在登记到「程序和功能」…");
            try
            {
                using (var key = Registry.LocalMachine.CreateSubKey(Program.RegPath))
                {
                    if (key == null) return;
                    key.SetValue("DisplayName", Program.AppName);
                    key.SetValue("DisplayVersion", Program.Version);
                    key.SetValue("Publisher", "Ryokuryuneko");
                    key.SetValue("InstallLocation", targetDir);
                    key.SetValue("DisplayIcon", Path.Combine(targetDir, Program.ExeName));
                    var uninstaller = Path.Combine(targetDir, "MythclassUninstall.exe");
                    key.SetValue("UninstallString", "\"" + uninstaller + "\"");

                    key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                    key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                    key.SetValue("EstimatedSize", 140000, RegistryValueKind.DWord);
                }
            }
            catch (Exception ex)
            {
                Step("  （跳过登记：" + ex.Message + "）");
            }
        }

        /// <summary>以当前登录用户身份启动（安装进程是提权的，直接起会让客户端变管理员）</summary>
        public void Launch(string targetDir)
        {
            Step("正在启动客户端…");
            try
            {
                var psi = new ProcessStartInfo("explorer.exe",
                    "\"" + Path.Combine(targetDir, Program.ExeName) + "\"");
                psi.UseShellExecute = true;
                Process.Start(psi);
            }
            catch { }
        }

        public void Install(string targetDir, bool startMenu, bool desktop, bool launch, Action<string> onStep)
        {
            if (onStep != null) OnStep = onStep;
            try
            {
                Progress(3);
                StopOldClient();

                // 装过更高版本就不许降级
                var blocked = DowngradeMessage(targetDir);
                if (blocked.Length > 0)
                {
                    LastError = blocked;
                    Step(blocked);
                    return;
                }

                var old = ClearOldVersion(targetDir);
                if (old.Length > 0 && old != "not-ours") Step("  发现旧版本 " + old + "，已清掉");

                Extract(targetDir);
                Progress(92);

                OpenFirewall();
                CreateShortcuts(targetDir, startMenu, desktop);
                Register(targetDir);

                if (launch) Launch(targetDir);
                Progress(100);
                Step("");
                Step("装好了：" + targetDir + "（v" + Program.Version + "）");
                Step("卸载：设置 → 应用 → " + Program.AppName);
            }
            catch (Exception ex)
            {
                Program.TryLog("安装失败：" + ex);
                Step("出错了：" + ex.Message);
                throw;
            }
        }
    }
}
