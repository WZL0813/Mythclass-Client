// 验「Mythclass 的密码」
//
// 三条路，任一条通过就放行：
//   1. 本机的 Mythclass 管理密码 —— config.json 里的 adminPasswordHash
//      （格式 pbkdf2_sha256$迭代$盐$摘要，本地就能比，不需要联网）
//   2. 安装时设的卸载密码 —— C:\ProgramData\Mythclass\uninstall.json
//   3. 教师账号密码 —— 发给服务端比哈希，而且要求这个账号**绑定过这台机器**
//
// 前两条不需要网络；第三条最严格（只有这台机器的负责老师能过）。

using System;
using System.Globalization;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;

namespace MythclassSetup
{
    static class MythclassPassword
    {
        static readonly string ConfigPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Mythclass", "config.json");

        static readonly string UninstallPath = Path.Combine(@"C:\ProgramData\Mythclass", "uninstall.json");

        public enum Source { None, AdminPassword, InstallPassword, TeacherAccount }

        /// <summary>本地能验的两条：本机管理密码、安装时设的卸载密码</summary>
        public static Source VerifyLocal(string password)
        {
            if (string.IsNullOrEmpty(password)) return Source.None;

            var stored = FieldOf(ConfigPath, "adminPasswordHash");
            if (!string.IsNullOrEmpty(stored) && VerifyPbkdf2Sha256(stored, password))
                return Source.AdminPassword;

            if (File.Exists(UninstallPath) && VerifyOwnFormat(File.ReadAllText(UninstallPath), password))
                return Source.InstallPassword;

            return Source.None;
        }

        /// <summary>本机的客户端 ID 和服务器地址（从 config.json 里读）</summary>
        public static void ReadIdentity(out string clientUid, out string serverUrl)
        {
            clientUid = FieldOf(ConfigPath, "clientUid") ?? "";
            serverUrl = "";

            var text = ReadAll(ConfigPath);
            if (text == null) return;
            // servers 是个数组，取第一个 url
            var at = text.IndexOf("\"url\"", StringComparison.Ordinal);
            if (at < 0) return;
            var start = text.IndexOf('"', text.IndexOf(':', at) + 1);
            if (start < 0) return;
            var end = text.IndexOf('"', start + 1);
            if (end > start) serverUrl = text.Substring(start + 1, end - start - 1).Trim().TrimEnd('/');
        }

        /// <summary>把密码发给服务端验：必须是绑定了这台机器的老师账号</summary>
        public static bool VerifyOnline(string serverUrl, string clientUid, string username, string password,
            out string message)
        {
            message = "";
            if (string.IsNullOrEmpty(serverUrl) || string.IsNullOrEmpty(clientUid) ||
                string.IsNullOrEmpty(username) || string.IsNullOrEmpty(password))
            {
                message = "缺服务器地址或账号";
                return false;
            }

            try
            {
                ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
                var url = serverUrl + "/api/client/verify-uninstall";
                var body = "{\"clientUid\":\"" + Escape(clientUid) + "\",\"username\":\"" + Escape(username) +
                           "\",\"password\":\"" + Escape(password) + "\"}";
                var data = Encoding.UTF8.GetBytes(body);

                var request = (HttpWebRequest)WebRequest.Create(url);
                request.Method = "POST";
                request.ContentType = "application/json; charset=utf-8";
                request.ContentLength = data.Length;
                request.Timeout = 12000;
                using (var stream = request.GetRequestStream()) stream.Write(data, 0, data.Length);

                using (var response = (HttpWebResponse)request.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                {
                    var text = reader.ReadToEnd();
                    if (text.IndexOf("\"ok\":true", StringComparison.OrdinalIgnoreCase) >= 0) return true;
                    message = Extract(text, "message") ?? "服务端没通过";
                    return false;
                }
            }
            catch (WebException err)
            {
                // 密码不对时服务端回 401，会走到这里
                var response = err.Response as HttpWebResponse;
                if (response != null)
                {
                    try
                    {
                        using (var reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                        {
                            var text = reader.ReadToEnd();
                            message = Extract(text, "message") ?? "密码不对";
                        }
                    }
                    catch { message = "密码不对"; }
                }
                else
                {
                    message = "连不上服务器（" + err.Status + "）";
                }
                return false;
            }
            catch (Exception err)
            {
                message = err.Message;
                return false;
            }
        }

        // ------------------------------ 哈希验证 ------------------------------

        /// <summary>验证 pbkdf2_sha256$迭代$盐hex$摘要hex（和客户端 security.py 一致）</summary>
        public static bool VerifyPbkdf2Sha256(string stored, string password)
        {
            try
            {
                var parts = stored.Split('$');
                if (parts.Length != 4 || parts[0] != "pbkdf2_sha256") return false;
                var iterations = int.Parse(parts[1], CultureInfo.InvariantCulture);
                var salt = HexToBytes(parts[2]);
                var expected = HexToBytes(parts[3]);
                if (iterations <= 0 || salt.Length == 0 || expected.Length == 0) return false;
                var actual = Pbkdf2(password, salt, iterations, expected.Length);
                return FixedTimeEquals(actual, expected);
            }
            catch { return false; }
        }

        /// <summary>验证本程序自己写的那种 {"salt":..,"hash":..,"iterations":..}</summary>
        public static bool VerifyOwnFormat(string stored, string password)
        {
            try
            {
                var salt = Convert.FromBase64String(Field(stored, "salt"));
                var hash = Convert.FromBase64String(Field(stored, "hash"));
                var iterations = int.Parse(Field(stored, "iterations"), CultureInfo.InvariantCulture);
                var actual = Pbkdf2(password, salt, iterations, hash.Length);
                return FixedTimeEquals(actual, hash);
            }
            catch { return false; }
        }

        static byte[] Pbkdf2(string password, byte[] salt, int iterations, int length)
        {
            using (var kdf = new Rfc2898DeriveBytes(password, salt, iterations, HashAlgorithmName.SHA256))
            {
                return kdf.GetBytes(length);
            }
        }

        static bool FixedTimeEquals(byte[] a, byte[] b)
        {
            if (a == null || b == null || a.Length != b.Length) return false;
            var diff = 0;
            for (var i = 0; i < a.Length; i++) diff |= a[i] ^ b[i];
            return diff == 0;
        }

        static byte[] HexToBytes(string hex)
        {
            var bytes = new byte[hex.Length / 2];
            for (var i = 0; i < bytes.Length; i++)
                bytes[i] = Convert.ToByte(hex.Substring(i * 2, 2), 16);
            return bytes;
        }

        // ------------------------------ 小工具 ------------------------------

        static string ReadAll(string path)
        {
            try { return File.Exists(path) ? File.ReadAllText(path, Encoding.UTF8) : null; }
            catch { return null; }
        }

        /// <summary>从 JSON 里取一个字符串字段（不引第三方库，够用就行）</summary>
        static string FieldOf(string path, string name)
        {
            var text = ReadAll(path);
            return text == null ? null : Field(text, name);
        }

        public static string Field(string json, string name)
        {
            var key = "\"" + name + "\"";
            var at = json.IndexOf(key, StringComparison.Ordinal);
            if (at < 0) return null;
            var colon = json.IndexOf(':', at + key.Length);
            if (colon < 0) return null;

            var i = colon + 1;
            while (i < json.Length && char.IsWhiteSpace(json[i])) i++;
            if (i >= json.Length) return null;

            // 字符串带引号；数字不带（iterations 就是数字），
            // 只认引号的话这里会返回 null，密码就永远验不过
            if (json[i] == '"')
            {
                var end = json.IndexOf('"', i + 1);
                return end <= i ? null : json.Substring(i + 1, end - i - 1);
            }
            var stop = i;
            while (stop < json.Length && json[stop] != ',' && json[stop] != '}') stop++;
            return json.Substring(i, stop - i).Trim();
        }

        static string Extract(string json, string name)
        {
            var value = Field(json, name);
            return string.IsNullOrEmpty(value) ? null : value;
        }

        static string Escape(string text)
        {
            return (text ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");
        }
    }
}
