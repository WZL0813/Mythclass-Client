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

        static void Run(string exe, string args)
        {
            try
            {
                var psi = new ProcessStartInfo(exe, args);
                psi.CreateNoWindow = true;
                psi.UseShellExecute = false;
                var p = Process.Start(psi);
                p.WaitForExit(15000);
            }
            catch { }
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
                var dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonStartMenu), "Programs");
                SaveShortcut(shell, Path.Combine(dir, Program.AppName + ".lnk"), exe, targetDir);
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

                var old = ClearOldVersion(targetDir);
                if (old.Length > 0 && old != "not-ours") Step("  发现旧版本 " + old + "，已清掉");

                Extract(targetDir);
                Progress(92);

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
