// Mythclass 客户端 安装程序
//
// 编译：installer\build-csharp.ps1（用系统自带的 csc.exe，不需要 SDK）
//
// 为什么是它：之前的 IExpress 方案要经过 SFX → cmd → wscript → 隐藏 PowerShell
// 四层，任何一层出问题用户都只看到「双击没反应」。这个 exe 一层到位：
// 界面是系统控件，安装包自己带 payload（内嵌资源），不经过任何解包目录。
//
// 支持的命令行参数（给批量部署和测试用）：
//   --target <目录>   指定安装目录
//   --silent          不显示界面，直接装
//   --noelevate       不检查管理员权限（测试用）
//   --nolaunch        装完不启动客户端
//   --uninstall       走卸载流程（注册表里指向的就是它）

using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Windows.Forms;
using Microsoft.Win32;

namespace MythclassSetup
{
    static class Program
    {
        public const string AppName = "Mythclass 客户端";
        // 对话框和「程序和功能」里给用户看的全名
        public const string DisplayName = "Mythclass 若思班级一体机管理系统";
        public const string ExeName = "MythclassClient.exe";
        public const string Version = "2.9.9";
        public const string DefaultDir = @"C:\Program Files (x86)\Mythclass";
        public const string RegPath = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MythclassClient";
        public const string PayloadResource = "payload.zip";

        static string optTarget = null;
        static bool optSilent = false;
        static bool optNoElevate = false;
        static bool optNoLaunch = false;
        static bool optStartMenu = false;
        static bool optDesktop = false;
        static string uninstallPassword = null;

        [STAThread]
        static int Main(string[] args)
        {
            for (int i = 0; i < args.Length; i++)
            {
                switch (args[i].ToLowerInvariant())
                {
                    case "--target": if (i + 1 < args.Length) optTarget = args[++i]; break;
                    case "--silent": optSilent = true; break;
                    case "--noelevate": optNoElevate = true; break;
                    // 静默装也建快捷方式（批量部署用）
                    case "--startmenu": optStartMenu = true; break;
                    case "--desktop": optDesktop = true; break;
                    // 装的时候就设好卸载密码（防止学生自己卸）
                    case "--uninstall-password":
                        if (i + 1 < args.Length) uninstallPassword = args[++i];
                        break;
                    case "--nolaunch": optNoLaunch = true; break;
                }
            }

            if (!optSilent && !optNoElevate && !IsAdmin())
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
                    MessageBox.Show("需要管理员权限才能安装到 Program Files。", AppName,
                        MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
                return 0;
            }

            if (optSilent)
            {
                try
                {
                    var engine = new InstallEngine();
                    engine.Install(optTarget ?? DefaultDir, optStartMenu, optDesktop, !optNoLaunch, null);

                    // 不许降级：装过更高版本就退出去（退出码 5）
                    if (!string.IsNullOrEmpty(engine.LastError))
                    {
                        Console.WriteLine(engine.LastError);
                        return 5;
                    }
                    // 顺手把卸载密码设上（批量部署时防止学生自己卸）
                    if (!string.IsNullOrEmpty(uninstallPassword))
                    {
                        InstallEngine.SetUninstallPassword(uninstallPassword);
                        Console.WriteLine("卸载密码已设。");
                    }
                    return 0;
                }
                catch (Exception ex)
                {
                    TryLog("静默安装失败：" + ex);
                    return 1;
                }
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new SetupForm());
            return 0;
        }

        static string JoinArgs(string[] args)
        {
            var sb = new System.Text.StringBuilder();
            foreach (var a in args)
            {
                if (sb.Length > 0) sb.Append(' ');
                sb.Append(a.IndexOf(' ') >= 0 ? "\"" + a + "\"" : a);
            }
            return sb.ToString();
        }

        public static bool IsAdmin()
        {
            var id = System.Security.Principal.WindowsIdentity.GetCurrent();
            return new System.Security.Principal.WindowsPrincipal(id)
                .IsInRole(System.Security.Principal.WindowsBuiltInRole.Administrator);
        }

        public static void TryLog(string text)
        {
            try
            {
                File.AppendAllText(Path.Combine(Path.GetTempPath(), "MythclassSetup.log"),
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + text + Environment.NewLine,
                    System.Text.Encoding.UTF8);
            }
            catch { }
        }

        public static Stream OpenPayload()
        {
            var asm = Assembly.GetExecutingAssembly();
            var stream = asm.GetManifestResourceStream(PayloadResource);
            if (stream == null)
                throw new InvalidOperationException("安装包里没有 payload（编译时漏了 /resource）");
            return stream;
        }
    }
}
