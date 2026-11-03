using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

internal sealed class ComponentFailure : Exception {
    public readonly int ExitCode;
    public ComponentFailure(string message, int code) : base(message) { ExitCode = code; }
}

internal static class Compatibility {
    public static void VerifyMicrosoftSignature(string file) {
        string escaped=file.Replace("'","''");
        string code="$s=Get-AuthenticodeSignature -LiteralPath '"+escaped+"'; if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notlike '*O=Microsoft Corporation*'){exit 1}";
        string ps=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"WindowsPowerShell","v1.0");
        var verify=new ProcessStartInfo(Path.Combine(ps,"powershell.exe"),"-NoProfile -NonInteractive -EncodedCommand "+Convert.ToBase64String(Encoding.Unicode.GetBytes(code))) {
            UseShellExecute=false,CreateNoWindow=true
        };
        verify.EnvironmentVariables["PSModulePath"]=Path.Combine(ps,"Modules");
        using(Process process=Process.Start(verify)) {
            if(!process.WaitForExit(30000)) {try{process.Kill();}catch{}throw new TimeoutException("微软签名校验超时，未运行安装器。");}
            if(process.ExitCode!=0)throw new InvalidDataException("微软数字签名校验未通过，未运行安装器。");
        }
    }
    public static string Explain(int code, string error) {
        uint native = unchecked((uint)code);
        if (native == 0xc0000135 || error.IndexOf("DLL load failed", StringComparison.OrdinalIgnoreCase) >= 0 ||
            error.IndexOf("MSVCP140", StringComparison.OrdinalIgnoreCase) >= 0 || error.IndexOf("VCRUNTIME140", StringComparison.OrdinalIgnoreCase) >= 0)
            return "依赖运行库缺失或无法加载。请先检测并修复；仍失败可点击“安装系统运行库”。";
        if (native == 0xc000007b || native == 0xc000012f)
            return "组件文件损坏或架构不匹配。请检测并修复，并确认使用 Windows 10 / 11 x64。";
        if (native == 0xc000001d)
            return "组件遇到不支持的处理器指令。请先检测并修复；文件正常仍失败时，需要兼容构建或其他电脑。";
        if (native == 0xc0000005 || native == 0xc0000409)
            return "组件发生底层崩溃。请检测并修复；仍失败请导出启动诊断以核对系统与处理器兼容性。";
        if (code == -1) return "组件检查超时。请关闭占用资源的程序后重试，或检测并修复。";
        if (error.IndexOf("No module named", StringComparison.OrdinalIgnoreCase) >= 0)
            return "程序组件文件缺失。请检测并修复；如再次缺失，请查看安全软件的隔离记录。";
        return "组件未通过检查。请检测并修复；仍失败可导出启动诊断，查看具体错误。";
    }

    public static void InstallOfficialRuntime(string home, CancellationToken cancellation, Action<string,int> progress) {
        string root = Path.Combine(home, "repair"); Directory.CreateDirectory(root);
        string download = Path.Combine(root, ".vc-" + Guid.NewGuid().ToString("N") + ".exe");
        try {
            progress("正在从微软下载并校验系统运行库…", 5);
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
            var request = (HttpWebRequest)WebRequest.Create("https://aka.ms/vc14/vc_redist.x64.exe");
            request.Timeout = 30000; request.ReadWriteTimeout = 30000; request.MaximumAutomaticRedirections = 5;
            using (var response = (HttpWebResponse)request.GetResponse()) {
                string host = response.ResponseUri.Host;
                if (response.ResponseUri.Scheme != "https" || !(host == "aka.ms" || host.EndsWith(".microsoft.com", StringComparison.OrdinalIgnoreCase) || host.EndsWith(".visualstudio.microsoft.com", StringComparison.OrdinalIgnoreCase)))
                    throw new InvalidDataException("下载地址不是微软官方 HTTPS 地址。");
                using (var input = response.GetResponseStream()) using (var output = File.Create(download)) {
                    byte[] buffer = new byte[65536]; int read; long total = 0;
                    while ((read = input.Read(buffer,0,buffer.Length)) > 0) {
                        cancellation.ThrowIfCancellationRequested(); total += read;
                        if (total > 80L * 1024 * 1024) throw new InvalidDataException("运行库下载大小异常。");
                        output.Write(buffer,0,read);
                        progress("下载微软系统运行库：" + total / 1048576 + " MB", Math.Min(65,5+(int)(total*60/Math.Max(1,response.ContentLength))));
                    }
                }
            }
            cancellation.ThrowIfCancellationRequested();
            VerifyMicrosoftSignature(download);
            cancellation.ThrowIfCancellationRequested();
            progress("已验证微软签名。请在 Windows 权限提示中允许安装，程序不会自动重启电脑。",75);
            var install = new ProcessStartInfo(download, "/install /passive /norestart /log " + Core.QuoteArgument(Path.Combine(root,"vc-runtime-install.log"))) {
                UseShellExecute=true, Verb="runas"
            };
            using (Process process = Process.Start(install)) {
                while (!process.WaitForExit(500)) cancellation.ThrowIfCancellationRequested();
                if (process.ExitCode == 3010) { progress("系统运行库已安装，Windows 提示需要重启电脑后生效。",100); return; }
                if (process.ExitCode != 0 && process.ExitCode != 1638) throw new InvalidOperationException("微软安装器返回代码 " + process.ExitCode + "，详见程序目录 repair/vc-runtime-install.log。");
            }
            progress("系统运行库已准备，正在重新检测。",100);
        } catch (System.ComponentModel.Win32Exception exc) {
            if (exc.NativeErrorCode == 1223) throw new InvalidOperationException("已取消 Windows 安装授权，可以继续使用当前可用功能。");
            throw;
        } catch(WebException) {
            throw new InvalidOperationException("无法从微软下载运行库，请检查网络后重试。已内置的应用运行库和可用功能仍保留。");
        } finally {
            try { File.Delete(download); } catch (IOException) { } catch (UnauthorizedAccessException) { }
        }
    }
}
