// 向用户要一次 Windows 管理员凭据
//
// 为什么需要这个：如果机器上用 Administrator 账户登录（教室一体机常见），
// Windows 不会因为「以管理员身份运行」再问一次密码 —— 它默认你已经通过了。
// 结果就是点一下「确定」就卸掉了，学生也能卸。
//
// 这里调用系统自己的凭据窗口（跟「以其他用户身份运行」弹的是同一个），
// 拿到用户名/域/密码后用 LogonUser 验证，并且要求这个账户**属于管理员组**。
// 任何一步不对就不放行。

using System;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;

namespace MythclassSetup
{
    static class WindowsCredential
    {
        const int LOGON32_LOGON_INTERACTIVE = 2;
        const int LOGON32_PROVIDER_DEFAULT = 0;
        const int CREDUI_MAX_USERNAME_LENGTH = 513;
        const int CREDUI_MAX_PASSWORD_LENGTH = 256;
        const int CREDUI_MAX_DOMAIN_TARGET_LENGTH = 337;
        const uint CREDUIWIN_GENERIC = 0x1;
        const uint CREDUIWIN_ENUMERATE_CURRENT_USER = 0x200;
        const uint ERROR_SUCCESS = 0;
        const uint ERROR_CANCELLED = 1223;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct CREDUI_INFO
        {
            public int cbSize;
            public IntPtr hwndParent;
            [MarshalAs(UnmanagedType.LPWStr)] public string pszMessageText;
            [MarshalAs(UnmanagedType.LPWStr)] public string pszCaptionText;
            public IntPtr hbmBanner;
        }

        [DllImport("credui.dll", CharSet = CharSet.Unicode)]
        static extern uint CredUIPromptForWindowsCredentials(
            ref CREDUI_INFO pUiInfo, int dwAuthError, ref uint pulAuthPackage,
            IntPtr pvInAuthBuffer, uint ulInAuthBufferSize,
            out IntPtr ppvOutAuthBuffer, out uint pulOutAuthBufferSize,
            ref bool pfSave, uint dwFlags);

        [DllImport("credui.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool CredUnPackAuthenticationBuffer(
            uint dwFlags, IntPtr pAuthBuffer, uint cbAuthBuffer,
            StringBuilder pszUserName, ref uint pcchMaxUserName,
            StringBuilder pszDomainName, ref uint pcchMaxDomainName,
            StringBuilder pszPassword, ref uint pcchMaxPassword);

        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool LogonUser(string user, string domain, string password,
            int logonType, int logonProvider, out IntPtr token);

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool CloseHandle(IntPtr handle);

        public enum Result { Ok, Cancelled, NotAdmin, Unavailable }

        /// <summary>弹系统凭据窗口，验证是不是管理员账户</summary>
        public static Result Ask(string message, string caption, out string who)
        {
            who = "";
            var info = new CREDUI_INFO
            {
                cbSize = Marshal.SizeOf(typeof(CREDUI_INFO)),
                hwndParent = IntPtr.Zero,
                pszMessageText = message,
                pszCaptionText = caption,
                hbmBanner = IntPtr.Zero,
            };
            uint package = 0;
            IntPtr buffer;
            uint bufferSize;
            var save = false;

            var code = CredUIPromptForWindowsCredentials(
                ref info, 0, ref package, IntPtr.Zero, 0, out buffer, out bufferSize,
                ref save, CREDUIWIN_GENERIC | CREDUIWIN_ENUMERATE_CURRENT_USER);

            if (code == ERROR_CANCELLED) return Result.Cancelled;
            if (code != ERROR_SUCCESS) return Result.Unavailable;

            try
            {
                var user = new StringBuilder(CREDUI_MAX_USERNAME_LENGTH);
                var domain = new StringBuilder(CREDUI_MAX_DOMAIN_TARGET_LENGTH);
                var password = new StringBuilder(CREDUI_MAX_PASSWORD_LENGTH);
                uint userLen = (uint)user.Capacity,
                     domainLen = (uint)domain.Capacity,
                     passwordLen = (uint)password.Capacity;

                if (!CredUnPackAuthenticationBuffer(0, buffer, bufferSize,
                        user, ref userLen, domain, ref domainLen, password, ref passwordLen))
                    return Result.Unavailable;

                var name = user.ToString();
                var dom = domain.ToString();
                who = string.IsNullOrEmpty(dom) ? name : dom + "\\" + name;

                IntPtr token;
                if (!LogonUser(name, dom, password.ToString(),
                        LOGON32_LOGON_INTERACTIVE, LOGON32_PROVIDER_DEFAULT, out token))
                    return Result.NotAdmin; // 密码不对

                try
                {
                    using (var identity = new WindowsIdentity(token))
                    {
                        var principal = new WindowsPrincipal(identity);
                        var admin = principal.IsInRole(WindowsBuiltInRole.Administrator);
                        who = identity.Name;
                        return admin ? Result.Ok : Result.NotAdmin;
                    }
                }
                finally { CloseHandle(token); }
            }
            finally
            {
                if (buffer != IntPtr.Zero) Marshal.FreeCoTaskMem(buffer);
            }
        }
    }
}
