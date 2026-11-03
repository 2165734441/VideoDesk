using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("映格视频工作台")]
[assembly: AssemblyDescription("自带运行环境的本地视频采集工作台")]
[assembly: AssemblyCompany("Video Desk")]
[assembly: AssemblyProduct("映格 Video Desk")]
[assembly: AssemblyVersion("2.5.0.0")]
[assembly: AssemblyFileVersion("2.5.0.0")]

internal sealed class Options {
    public bool SelfTest;
    public bool Background;
    public bool TrayTest;
    public bool Repair;
    public string Home = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "VideoDesk");
    public string Report = "";
    public string StopFile = "";
    public static Options Parse(string[] args) {
        Options o = new Options();
        for (int i = 0; i < args.Length; i++) {
            if (args[i] == "--self-test") o.SelfTest = true;
            else if (args[i] == "--repair") o.Repair = true;
            else if (args[i] == "--tray-test") { o.TrayTest = true; o.SelfTest = true; o.Background = true; }
            else if (args[i] == "--background") o.Background = true;
            else if (args[i] == "--home" && i + 1 < args.Length) o.Home = args[++i];
            else if (args[i] == "--report" && i + 1 < args.Length) o.Report = args[++i];
            else if (args[i] == "--stop-file" && i + 1 < args.Length) o.StopFile = args[++i];
            else throw new ArgumentException("Unknown argument: " + args[i]);
        }
        if (!Path.IsPathRooted(o.Home)) throw new ArgumentException("Application home must be an absolute path");
        o.Home = Path.GetFullPath(o.Home);
        return o;
    }
}

internal static class Json {
    public static JavaScriptSerializer Serializer() { return new JavaScriptSerializer { MaxJsonLength = 64 * 1024 * 1024 }; }
    public static Dictionary<string, object> Read(string path) {
        return Serializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8));
    }
    public static void Write(string path, object value) {
        string parent = Path.GetDirectoryName(path); if (!String.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
        string temporary = path + ".tmp";
        byte[] bytes = new UTF8Encoding(false).GetBytes(Serializer().Serialize(value));
        using (FileStream stream = new FileStream(temporary, FileMode.Create, FileAccess.Write, FileShare.None)) {
            stream.Write(bytes, 0, bytes.Length); stream.Flush(true);
        }
        if (File.Exists(path)) File.Replace(temporary, path, null);
        else File.Move(temporary, path);
    }
}

internal static class Program {
    [DllImport("kernel32.dll")] private static extern uint SetErrorMode(uint mode);
    [STAThread]
    public static int Main(string[] args) {
        Options options = null;
        try {
            // Native component failures must return an exit code, not a blocking OS dialog.
            SetErrorMode(0x0001 | 0x0002);
            AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling", false);
            AppContext.SetSwitch("Switch.System.IO.BlockLongPaths", false);
            int cliIndex = Array.IndexOf(args, "--cli");
            if (cliIndex >= 0) return RunCli(Options.Parse(args.Take(cliIndex).ToArray()), args.Skip(cliIndex + 1).ToArray());
            options = Options.Parse(args);
            if (!Environment.Is64BitOperatingSystem) throw new InvalidOperationException("本版本需要 Windows 10 / 11 的 64 位系统。");
            if (Environment.OSVersion.Version.Major < 10) throw new InvalidOperationException("当前 Windows 版本过旧。本版本需要 Windows 10 / 11 x64，请在受支持系统运行。");
            Directory.CreateDirectory(options.Home);
            string mutexName = "Local\\VideoDesk-" + Core.HashText(options.Home.ToLowerInvariant()).Substring(0, 20);
            using (Mutex mutex = new Mutex(false, mutexName)) {
                bool acquired;
                try { acquired = mutex.WaitOne(0); } catch (AbandonedMutexException) { acquired = true; }
                if (!acquired) {
                    if (options.Repair && Core.ExistingReady(options.Home)) {
                        File.WriteAllText(Path.Combine(options.Home, "data", "repair.request"), "repair");
                        return 0;
                    }
                    if (options.Background && Core.ExistingReady(options.Home)) {
                        if (!String.IsNullOrEmpty(options.Report)) Json.Write(options.Report, new { success = true, reused = true });
                        return 0;
                    }
                    if (!options.SelfTest && Core.OpenExisting(options.Home)) return 0;
                    if (!options.SelfTest) MessageBox.Show("映格正在启动或已经运行，请稍候。", "映格", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return 2;
                }
                try {
                    if (options.SelfTest && !options.TrayTest) return SelfTest(options);
                    Application.EnableVisualStyles();
                    Application.SetCompatibleTextRenderingDefault(false);
                    Application.Run(new LauncherForm(options));
                    return 0;
                } finally { mutex.ReleaseMutex(); }
            }
        } catch (Exception exc) {
            if (options != null && !String.IsNullOrEmpty(options.Report))
                Json.Write(options.Report, new { success = false, error = exc.ToString() });
            if (options == null || !options.SelfTest)
                MessageBox.Show("启动未完成：\r\n" + exc.Message + "\r\n\r\n诊断日志保存在应用数据目录中。", "映格", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    [DllImport("kernel32.dll")] private static extern bool AttachConsole(int processId);
    private static int RunCli(Options options, string[] args) {
        AttachConsole(-1); Console.OutputEncoding = new UTF8Encoding(false);
        if (!Core.ExistingReady(options.Home)) {
            Process.Start(new ProcessStartInfo(Assembly.GetExecutingAssembly().Location,
                "--background --home " + Core.QuoteArgument(options.Home)) { UseShellExecute = false, CreateNoWindow = true });
            Stopwatch watch = Stopwatch.StartNew();
            while (!Core.ExistingReady(options.Home) && watch.Elapsed.TotalMinutes < 5) Thread.Sleep(300);
            if (!Core.ExistingReady(options.Home)) { Console.Error.WriteLine("后台启动超时，请查看应用目录的 launcher.log"); return 1; }
        }
        var selected = Json.Read(Path.Combine(options.Home, "current.json"));
        string root = (string)selected["path"];
        var info = new ProcessStartInfo(Path.Combine(root, "runtime", "node", "node.exe"),
            Core.QuoteArgument(Path.Combine(root, "app", "bin", "video-desk.mjs")) + " " +
            String.Join(" ", args.Select(Core.QuoteArgument)) + " --home " + Core.QuoteArgument(options.Home));
        info.UseShellExecute = false;
        using (var process = Process.Start(info)) { process.WaitForExit(); return process.ExitCode; }
    }

    private static int SelfTest(Options options) {
        using (Core core = new Core(options, delegate(string message, int percent) { })) {
            try { core.Prepare(CancellationToken.None); }
            catch { core.SaveDiagnostics(); throw; }
            core.StartServer(CancellationToken.None);
            Json.Write(options.Report, new {
                success = true, version = BuildInfo.Version, home = options.Home,
                install = core.InstallRoot, url = core.Url, browser = core.Browser,
                main_python = core.MainPython, crawler_python = core.CrawlerPython,
                node = core.Node, installation_id = core.InstallationId,
                engine_check = core.EngineCheck, main_check = core.MainCheck,
                path = core.ChildPath, bundled_dependencies = true,
                degraded = core.Degraded, components = core.Components,
                diagnostics = Path.Combine(options.Home, "runtime-diagnostics.json")
            });
            if (!String.IsNullOrEmpty(options.StopFile)) {
                DateTime deadline = DateTime.UtcNow.AddMinutes(10);
                while (!File.Exists(options.StopFile) && DateTime.UtcNow < deadline) Thread.Sleep(200);
            }
        }
        return 0;
    }
}

internal sealed class LauncherForm : Form {
    private readonly Label heading = new Label();
    private readonly Label status = new Label();
    private readonly ProgressBar progress = new ProgressBar();
    private readonly TextBox details = new TextBox();
    private readonly Button open = new Button();
    private readonly Button folder = new Button();
    private readonly Button exit = new Button();
    private readonly Button repair = new Button();
    private readonly Button diagnostic = new Button();
    private readonly Button systemRuntime = new Button();
    private readonly CancellationTokenSource cancel = new CancellationTokenSource();
    private readonly Options options;
    private Core core;
    private bool working;
    private bool closeRequested;
    private bool exitRequested;
    private bool crashReported;
    private readonly NotifyIcon tray = new NotifyIcon();
    private readonly System.Windows.Forms.Timer monitor = new System.Windows.Forms.Timer();

    public LauncherForm(Options options) {
        this.options = options;
        Text = "映格 · 一键视频工作台";
        ClientSize = new Size(670, 490);
        MinimumSize = new Size(686, 529);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(247, 249, 248);
        Font = new Font("Microsoft YaHei UI", 9F);
        heading.Text = "映格  Video Desk";
        heading.Font = new Font(Font.FontFamily, 23F, FontStyle.Bold);
        heading.ForeColor = Color.FromArgb(20, 107, 99);
        heading.SetBounds(28, 24, 590, 44);
        Label subtitle = new Label();
        subtitle.Text = "自动准备运行环境，完成后打开本地网页。无需预装开发工具。";
        subtitle.ForeColor = Color.FromArgb(105, 120, 126);
        subtitle.SetBounds(30, 78, 605, 28);
        status.Text = "正在检查运行环境…"; status.SetBounds(30, 120, 605, 30);
        progress.SetBounds(30, 155, 605, 12);
        details.Multiline = true; details.ReadOnly = true; details.ScrollBars = ScrollBars.Vertical;
        details.BackColor = Color.White; details.BorderStyle = BorderStyle.FixedSingle;
        details.SetBounds(30, 186, 605, 155);
        open.Text = "打开工作台"; open.Enabled = false; open.BackColor = Color.FromArgb(20, 107, 99);
        open.ForeColor = Color.White; open.FlatStyle = FlatStyle.Flat; open.SetBounds(30, 355, 190, 39);
        folder.Text = "打开程序目录"; folder.SetBounds(234, 355, 180, 39);
        exit.Text = "退出并停止服务"; exit.SetBounds(430, 355, 205, 39);
        repair.Text = "检测并修复"; repair.SetBounds(30, 404, 190, 36);
        diagnostic.Text = "导出启动诊断"; diagnostic.SetBounds(234, 404, 180, 36);
        systemRuntime.Text = "安装系统运行库"; systemRuntime.SetBounds(430, 404, 205, 36);
        Label license = new Label();
        license.Text = "v" + BuildInfo.Version + " · MediaCrawler © relakkes · 仅限非商业学习研究";
        license.ForeColor = Color.FromArgb(140, 150, 155); license.SetBounds(30, 456, 610, 23);
        Controls.AddRange(new Control[] { heading, subtitle, status, progress, details, open, folder, exit, repair, diagnostic, systemRuntime, license });
        open.Click += async delegate { if (core != null && !core.ServerAlive) await Start(); else if (core != null) core.OpenBrowser(); };
        folder.Click += delegate { Process.Start(new ProcessStartInfo(options.Home) { UseShellExecute = true }); };
        exit.Click += delegate { RequestExit(); };
        repair.Click += async delegate { await Start(true); };
        diagnostic.Click += delegate { ExportDiagnostic(); };
        systemRuntime.Click += async delegate { await InstallSystemRuntime(); };
        var menu = new ContextMenuStrip();
        menu.Items.Add("打开工作台", null, delegate { if (core != null && core.ServerAlive) core.OpenBrowser(); else ShowStatus(); });
        menu.Items.Add("打开保存目录", null, delegate { if (core != null) Process.Start(new ProcessStartInfo(core.OutputDirectory) { UseShellExecute = true }); });
        menu.Items.Add("查看状态 / 日志", null, delegate { ShowStatus(); });
        menu.Items.Add("检测并修复", null, async delegate { ShowStatus(); await Start(true); });
        menu.Items.Add("导出启动诊断", null, delegate { ShowStatus(); ExportDiagnostic(); });
        menu.Items.Add("退出映格", null, delegate { RequestExit(); });
        this.Icon = System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        tray.Icon = this.Icon; tray.Text = "映格 · 本地视频工作台"; tray.ContextMenuStrip = menu;
        tray.DoubleClick += delegate { if (core != null && core.ServerAlive) core.OpenBrowser(); else ShowStatus(); };
        monitor.Interval = 2000;
        monitor.Tick += async delegate {
            if (core == null || working) return;
            if (core.RepairRequested) {
                core.ClearRepairRequest(); ShowStatus(); await Start(true); return;
            }
            if (options.TrayTest && !String.IsNullOrEmpty(options.StopFile) && File.Exists(options.StopFile)) { exitRequested = true; Close(); return; }
            if (core.ShutdownRequested) { exitRequested = true; Close(); return; }
            if (!core.ServerAlive && !crashReported) {
                crashReported = true; open.Text = "重新启动服务"; open.Enabled = true;
                UpdateProgress("后台服务已退出，点击重新启动。", 0);
                tray.ShowBalloonTip(5000, "映格服务已退出", "双击托盘图标查看状态并重新启动。", ToolTipIcon.Warning);
            }
        };
        Shown += async delegate { if (options.Background) HideToTray(); await Start(); };
        FormClosing += OnClosing;
        FormClosed += delegate { monitor.Dispose(); tray.Visible = false; tray.Dispose(); };
    }

    private void ShowStatus() { ShowInTaskbar = true; Show(); WindowState = FormWindowState.Normal; Activate(); }
    private void HideToTray() { tray.Visible = true; ShowInTaskbar = false; Hide(); }
    private void RequestExit() {
        if (core != null && core.ServerAlive && core.HasActiveJobs) {
            MessageBox.Show("还有任务或录制正在进行。请在工作台停止并保存录制，等待任务完成或取消任务后再退出。", "映格", MessageBoxButtons.OK, MessageBoxIcon.Information);
            core.OpenBrowser(); return;
        }
        exitRequested = true; Close();
    }

    private void UpdateProgress(string message, int value) {
        if (IsDisposed || Disposing) return;
        if (InvokeRequired) { try { BeginInvoke((Action)(() => UpdateProgress(message, value))); } catch (InvalidOperationException) { } return; }
        progress.Value = Math.Max(0, Math.Min(100, value)); status.Text = message;
        if (value == 0 || value == 100 || details.TextLength == 0 || !details.Text.EndsWith(message + "\r\n")) {
            if (message.IndexOf("释放组件") < 0) details.AppendText(message + "\r\n");
        }
    }

    private void ExportDiagnostic() {
        string source = Path.Combine(options.Home, "runtime-diagnostics.json");
        if (!File.Exists(source)) { MessageBox.Show("尚未生成诊断，请先启动或点击检测并修复。", "映格"); return; }
        using (SaveFileDialog dialog = new SaveFileDialog { Filter = "启动诊断 (*.json)|*.json", FileName = "映格启动诊断.json" }) {
            if (dialog.ShowDialog(this) != DialogResult.OK) return;
            try { if (!String.Equals(Path.GetFullPath(dialog.FileName), Path.GetFullPath(source), StringComparison.OrdinalIgnoreCase)) File.Copy(source, dialog.FileName, true); }
            catch (Exception exc) { MessageBox.Show("无法保存诊断：" + exc.Message, "映格"); }
        }
    }

    private bool CanRepair() {
        if (working) return false;
        if (core != null && core.ServerAlive && core.HasActiveJobs) {
            MessageBox.Show("还有任务正在进行。请等待完成或取消任务后再修复，账号和素材会保留。", "映格"); return false;
        }
        return true;
    }

    private async Task InstallSystemRuntime() {
        if (!CanRepair()) return;
        working = true; repair.Enabled = systemRuntime.Enabled = open.Enabled = false;
        try {
            await Task.Run(() => Compatibility.InstallOfficialRuntime(options.Home, cancel.Token, UpdateProgress));
            details.AppendText("系统运行库处理完成。正在重新检测…\r\n");
        } catch (Exception exc) { UpdateProgress("系统运行库尚未安装：" + exc.Message, 0); return; }
        finally {
            working = false; repair.Enabled = systemRuntime.Enabled = true; open.Enabled = core != null && core.ServerAlive;
            if (closeRequested) Close();
        }
        if (IsDisposed || Disposing) return;
        await Start();
    }

    private async Task Start(bool forceRepair = false) {
        if (!CanRepair()) return;
        working = true;
        open.Enabled = repair.Enabled = systemRuntime.Enabled = false;
        try {
            if (core != null && core.ServerAlive) await Task.Run(() => core.BeginRepair());
            if (core != null) core.Dispose();
            core = new Core(options, UpdateProgress);
            await Task.Run(() => { core.Prepare(cancel.Token, forceRepair || options.Repair); core.StartServer(cancel.Token); });
            options.Repair = false;
            open.Enabled = true;
            UpdateProgress("准备完成，工作台已启动。", 100);
            details.AppendText(core.Url + "\r\n后台在系统托盘运行。关闭网页不停止任务；录制时请保持采集页面打开。\r\n");
            if (core.Degraded) details.AppendText("部分组件暂不可用；工作台中可查看具体功能及修复入口。\r\n");
            open.Text = "打开工作台"; crashReported = false;
            if (!options.Background || forceRepair) core.OpenBrowser();
            HideToTray(); monitor.Start();
            if (core.Degraded) tray.ShowBalloonTip(6000, "工作台已启动，部分功能待修复", "点击托盘的“检测并修复”查看原因；其他功能仍可使用。", ToolTipIcon.Warning);
            if (options.TrayTest) Json.Write(options.Report, new { success = true, version = BuildInfo.Version,
                home = options.Home, url = core.Url, install = core.InstallRoot, tray_visible = tray.Visible,
                window_visible = Visible, show_in_taskbar = ShowInTaskbar, menu_items = tray.ContextMenuStrip.Items.Count });
        } catch (OperationCanceledException) {
            UpdateProgress("已取消启动，下次可以继续。", 0);
        } catch (Exception exc) {
            ShowStatus();
            if (core != null) { core.Log(exc.ToString()); core.SaveDiagnostics(); }
            open.Enabled = core != null && core.ServerAlive;
            UpdateProgress("启动未完成，请查看下方提示。", 0);
            details.AppendText(exc.Message + "\r\n诊断：" + Path.Combine(options.Home, "launcher.log") + "\r\n");
            if (options.TrayTest) { Json.Write(options.Report, new { success = false, error = exc.ToString() }); exitRequested = true; closeRequested = true; }
        } finally {
            working = false;
            repair.Enabled = systemRuntime.Enabled = true;
            if (closeRequested) Close();
        }
    }

    private void OnClosing(object sender, FormClosingEventArgs args) {
        if (!exitRequested && !closeRequested && !working && core != null && core.ServerAlive) { args.Cancel = true; HideToTray(); return; }
        cancel.Cancel();
        if (working) { closeRequested = true; args.Cancel = true; status.Text = "正在停止启动过程…"; return; }
        if (core != null) core.Dispose();
    }
}

internal sealed class Core : IDisposable {
    private readonly Options options;
    private readonly Action<string, int> progress;
    private readonly ProcessTree job = new ProcessTree();
    private readonly object logLock = new object();
    private Process server;
    private bool disposed;
    public string InstallRoot, Url, Browser, MainPython, CrawlerPython, Node, InstallationId, ChildPath;
    public string EngineCheck = "", MainCheck = "";
    public readonly List<Dictionary<string, object>> Components = new List<Dictionary<string, object>>();
    private readonly HashSet<string> disabled = new HashSet<string>();
    public bool Degraded { get { return disabled.Count > 0; } }
    public bool RepairRequested { get { return File.Exists(Path.Combine(Data, "repair.request")); } }
    public void ClearRepairRequest() { File.Delete(Path.Combine(Data, "repair.request")); }
    public bool ServerAlive { get { try { return server != null && !server.HasExited; } catch { return false; } } }
    public bool ShutdownRequested { get { return File.Exists(Path.Combine(Data, "shutdown.request")); } }
    public string OutputDirectory { get { try { return (string)Json.Read(Path.Combine(Data, "settings.json"))["output"]; } catch { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "映格素材"); } } }
    public bool HasActiveJobs {
        get {
            try {
                var jobs = Json.Serializer().Deserialize<List<Dictionary<string, object>>>(File.ReadAllText(Path.Combine(Data, "jobs.json"), Encoding.UTF8));
                return jobs.Any(j => new[] { "queued", "running", "waiting", "recording", "processing", "waiting_source" }.Contains((string)j["status"]));
            } catch { return ServerAlive; }
        }
    }
    private Dictionary<string, object> manifest;
    private string Data { get { return Path.Combine(options.Home, "data"); } }

    public Core(Options options, Action<string, int> progress) {
        this.options = options; this.progress = progress;
        Directory.CreateDirectory(options.Home); Directory.CreateDirectory(Data);
    }

    public static string HashText(string text) {
        using (SHA256 sha = SHA256.Create()) return Hex(sha.ComputeHash(Encoding.UTF8.GetBytes(text)));
    }
    private static string Hex(byte[] value) { return BitConverter.ToString(value).Replace("-", "").ToLowerInvariant(); }
    private static string HashFile(string file) { using (FileStream input = File.OpenRead(file)) using (SHA256 sha = SHA256.Create()) return Hex(sha.ComputeHash(input)); }
    private static string Quote(string value) { return "\"" + value.Replace("\"", "\\\"") + "\""; }
    public static string QuoteArgument(string value) {
        var result = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            result.Append('\\', c == '"' ? slashes * 2 + 1 : slashes); result.Append(c); slashes = 0;
        }
        return result.Append('\\', slashes * 2).Append('"').ToString();
    }

    public void Log(string message) {
        lock (logLock) {
            File.AppendAllText(Path.Combine(options.Home, "launcher.log"), DateTime.Now.ToString("s") + " " + message + "\r\n", new UTF8Encoding(false));
        }
    }
    private void Status(string message, int value) { Log(message); progress(message, value); }

    public void Prepare(CancellationToken cancellation, bool forceRepair = false) {
        Status("检查本机应用目录…", 1);
        ClearRepairRequest();
        string idPath = Path.Combine(Data, "installation.id");
        if (!File.Exists(idPath)) File.WriteAllText(idPath, Guid.NewGuid().ToString("N"), Encoding.ASCII);
        InstallationId = File.ReadAllText(idPath).Trim();
        InstallRoot = forceRepair || options.Repair ? null : ExistingInstallation();
        if (forceRepair || options.Repair) Status("正在重建程序组件；账号、设置与素材保留。", 2);
        if (InstallRoot == null) InstallRoot = Extract(cancellation);
        else Status("已检测到完整运行环境，复用本机组件。", 72);
        try {
            for (int attempt = 0; attempt < 2; attempt++) {
                ConfigureRuntime(); Components.Clear(); disabled.Clear();
                try {
                    Status("检查基础工作台与音视频组件…", 75);
                    MainCheck = Probe("base", "基础工作台", MainPython, ProbeArguments("base"), true, cancellation);
                    Probe("media", "音视频处理", MainPython, ProbeArguments("media"), true, cancellation);
                    Status("分项检查可选功能；识别组件异常不会阻止工作台启动。", 79);
                    Probe("download", "链接下载", MainPython, ProbeArguments("download"), false, cancellation);
                    Probe("node", "分镜与 YouTube 脚本", Node, "--version", false, cancellation);
                    Probe("browser", "登录浏览器", MainPython, ProbeArguments("browser"), false, cancellation);
                    Probe("dedup", "相似图片去重", MainPython, ProbeArguments("dedup"), false, cancellation);
                    Probe("ocr", "画面文字识别", MainPython, ProbeArguments("ocr"), false, cancellation);
                    Probe("asr", "本地语音识别", MainPython, ProbeArguments("asr"), false, cancellation);
                    EngineCheck = Probe("crawler", "MediaCrawler 平台采集", CrawlerPython,
                        Quote(Path.Combine(InstallRoot,"app","integrations","mediacrawler_bridge.py")) + " --check", false, cancellation);
                    if (!disabled.Contains("crawler") && !EngineCheck.Contains("\"ready\": true")) {
                        disabled.Add("crawler"); var entry=Components.Last(); entry["ready"]=false;
                        entry["message"]="平台引擎未准备完成，请检测并修复。";
                    }
                } catch (OperationCanceledException) { throw; }
                catch (Exception) {
                    if (attempt == 0 && DamagedFiles(cancellation)) { InstallRoot = Extract(cancellation); continue; }
                    throw;
                }
                if (Degraded && attempt == 0 && DamagedFiles(cancellation)) { InstallRoot = Extract(cancellation); continue; }
                break;
            }
            if (!options.SelfTest) InstallShortcut();
            Status(Degraded ? "基础工作台已就绪，部分可选功能待修复。" : "所有运行组件检查完成。", 90);
        } finally { SaveDiagnostics(); }
    }

    private void ConfigureRuntime() {
        manifest = Json.Read(Path.Combine(InstallRoot, "runtime-manifest.json"));
        MainPython = Path.Combine(InstallRoot, "runtime", "main", "python.exe");
        CrawlerPython = Path.Combine(InstallRoot, "runtime", "crawler", "python.exe");
        Node = Path.Combine(InstallRoot, "runtime", "node", "node.exe");
        Browser = ChooseBrowser(InstallRoot, manifest);
        ChildPath = Path.GetDirectoryName(Node) + ";" + Path.GetDirectoryName(MainPython) + ";" + Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32");
    }

    private string ProbeArguments(string component) {
        return "-u " + QuoteArgument(Path.Combine(InstallRoot,"app","integrations","runtime_probe.py")) + " " + component;
    }

    private string Probe(string id, string name, string executable, string arguments, bool required, CancellationToken cancellation) {
        cancellation.ThrowIfCancellationRequested();
        Status("正在检查：" + name + "…", 77 + Components.Count);
        var entry = new Dictionary<string,object> { {"id",id},{"name",name},{"required",required},{"ready",false} };
        Components.Add(entry); Stopwatch watch = Stopwatch.StartNew();
        try {
            string output = RunHidden(executable, arguments, id == "crawler" ? 90000 : 45000, cancellation);
            entry["ready"]=true; entry["message"]="已就绪"; entry["stdout"]=output; entry["exit_code"]=0; entry["exit_hex"]="0x00000000";
            return output;
        } catch (OperationCanceledException) { throw; }
        catch (Exception exc) {
            int code = exc is ComponentFailure ? ((ComponentFailure)exc).ExitCode : exc is TimeoutException ? -1 : exc.HResult;
            string hex="0x"+unchecked((uint)code).ToString("X8");
            string message = name + "检查失败（" + hex + "）。" + Compatibility.Explain(code,exc.Message);
            entry["exit_code"]=code; entry["exit_hex"]=hex; entry["message"]=message; entry["error"]=exc.Message;
            Log(message); progress(message,required ? 0 : 86);
            disabled.Add(id);
            if (required) throw new InvalidOperationException(message,exc);
            return "";
        } finally { entry["seconds"]=Math.Round(watch.Elapsed.TotalSeconds,2); }
    }

    public void SaveDiagnostics() {
        try {
            string cpu="未知";
            using (var key=Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"HARDWARE\DESCRIPTION\System\CentralProcessor\0")) {
                if(key!=null)cpu=Convert.ToString(key.GetValue("ProcessorNameString","未知"));
            }
            Json.Write(Path.Combine(options.Home,"runtime-diagnostics.json"),new {
                format_version=1, version=BuildInfo.Version, time=DateTime.UtcNow.ToString("o"),
                os=Environment.OSVersion.VersionString, x64=Environment.Is64BitOperatingSystem,
                cpu=cpu, processor_count=Environment.ProcessorCount, components=Components,
                degraded=Degraded, includes_accounts=false, includes_materials=false,
                hint="先检测并修复；运行库加载失败可安装微软官方系统运行库。"
            });
        } catch(Exception exc) { Log("保存诊断失败："+exc.Message); }
    }

    private bool DamagedFiles(CancellationToken cancellation) {
        Status("检测到组件异常，正在核对程序文件完整性…", 87);
        string prefix=Path.GetFullPath(InstallRoot)+Path.DirectorySeparatorChar;
        foreach(var pair in (Dictionary<string,object>)manifest["files"]) {
            cancellation.ThrowIfCancellationRequested();
            string file=Path.GetFullPath(Path.Combine(InstallRoot,pair.Key.Replace('/',Path.DirectorySeparatorChar)));
            if(!file.StartsWith(prefix,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("程序文件清单路径不合法。");
            try {
                if(!File.Exists(file) || HashFile(file)!=(string)pair.Value) { Status("发现缺失或损坏组件，正在自动恢复："+Path.GetFileName(file),2);return true; }
            } catch(IOException) { return true; } catch(UnauthorizedAccessException) { return true; }
        }
        Log("程序文件完整，保留可用功能并记录系统兼容性诊断。");return false;
    }

    private string ExistingInstallation() {
        try {
            Dictionary<string, object> selected = Json.Read(Path.Combine(options.Home, "current.json"));
            if ((string)selected["payload_hash"] != BuildInfo.PayloadHash) return null;
            string root = Path.GetFullPath((string)selected["path"]);
            string versions = Path.GetFullPath(Path.Combine(options.Home, "versions")) + Path.DirectorySeparatorChar;
            if (!root.StartsWith(versions, StringComparison.OrdinalIgnoreCase)) return null;
            Dictionary<string, object> complete = Json.Read(Path.Combine(root, ".complete.json"));
            if ((string)complete["payload_hash"] != BuildInfo.PayloadHash) return null;
            if (!complete.ContainsKey("manifest_sha256") || (string)complete["manifest_sha256"] != HashFile(Path.Combine(root,"runtime-manifest.json"))) return null;
            Dictionary<string, object> metadata = Json.Read(Path.Combine(root, "runtime-manifest.json"));
            string[] critical = { "runtime/main/python.exe", "runtime/crawler/python.exe", "runtime/node/node.exe",
                "runtime/main/msvcp140.dll", "runtime/main/msvcp140_1.dll", "runtime/crawler/msvcp140.dll", "runtime/node/msvcp140.dll",
                "app/integrations/runtime_probe.py", "app/integrations/runtime_support.py",
                "app/app.py", "app/integrations/mediacrawler_bridge.py", "app/integrations/local_sources.py", "app/integrations/batch_pipeline.py", "app/integrations/storage.py", "app/static/local_sources.js", "app/third_party/MediaCrawler/LICENSE", "app/runtime-tools/ffprobe.exe", "app/integrations/storyboard_bridge.mjs", (string)metadata["browser"] };
            if (critical.Any(relative => !File.Exists(Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar))))) return null;
            return root;
        } catch (Exception) { return null; }
    }

    private string Extract(CancellationToken cancellation) {
        string versions = Path.Combine(options.Home, "versions"); Directory.CreateDirectory(versions);
        string stage = Path.Combine(versions, ".stage-" + Guid.NewGuid().ToString("N").Substring(0, 10));
        Directory.CreateDirectory(stage);
        try {
            Status("校验安装包完整性…", 3);
            using (Stream payload = Assembly.GetExecutingAssembly().GetManifestResourceStream("VideoDesk.Payload")) {
                if (payload == null) throw new InvalidOperationException("安装包缺少组件资源。");
                using (SHA256 sha = SHA256.Create()) {
                    if (Hex(sha.ComputeHash(payload)) != BuildInfo.PayloadHash) throw new InvalidOperationException("安装包校验失败，请重新获取文件。");
                }
                payload.Position = 0;
                using (ZipArchive archive = new ZipArchive(payload, ZipArchiveMode.Read, true)) {
                    ZipArchiveEntry manifestEntry = archive.GetEntry("runtime-manifest.json");
                    if (manifestEntry == null) throw new InvalidOperationException("安装包缺少文件清单。");
                    Dictionary<string, object> metadata;
                    using (StreamReader reader = new StreamReader(manifestEntry.Open(), Encoding.UTF8))
                        metadata = Json.Serializer().Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
                    Dictionary<string, object> expected = (Dictionary<string, object>)metadata["files"];
                    if (archive.Entries.Count != expected.Count + 1) throw new InvalidOperationException("安装文件清单不一致。");
                    long total = archive.Entries.Sum(entry => entry.Length);
                    string driveRoot = Path.GetPathRoot(stage);
                    if (!String.IsNullOrEmpty(driveRoot) && new DriveInfo(driveRoot).AvailableFreeSpace < total + payload.Length + 200L * 1024 * 1024)
                        throw new IOException("应用所在磁盘空间不足，请至少预留 3 GB 可用空间。");
                    long processed = 0; int previous = -1;
                    string stagePrefix = Path.GetFullPath(stage) + Path.DirectorySeparatorChar;
                    foreach (ZipArchiveEntry entry in archive.Entries) {
                        cancellation.ThrowIfCancellationRequested();
                        string destination = Path.GetFullPath(Path.Combine(stage, entry.FullName.Replace('/', Path.DirectorySeparatorChar)));
                        if (!destination.StartsWith(stagePrefix, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("安装文件路径不合法。");
                        Directory.CreateDirectory(Path.GetDirectoryName(destination));
                        using (Stream input = entry.Open()) using (FileStream output = File.Create(destination)) using (SHA256 digest = SHA256.Create()) {
                            byte[] buffer = new byte[1024 * 1024]; int read;
                            while ((read = input.Read(buffer, 0, buffer.Length)) > 0) {
                                cancellation.ThrowIfCancellationRequested(); output.Write(buffer, 0, read);
                                digest.TransformBlock(buffer, 0, read, buffer, 0); processed += read;
                            }
                            digest.TransformFinalBlock(new byte[0], 0, 0);
                            if (entry.FullName != "runtime-manifest.json" && (!expected.ContainsKey(entry.FullName) || Hex(digest.Hash) != (string)expected[entry.FullName]))
                                throw new InvalidDataException("文件校验失败：" + entry.FullName);
                        }
                        int value = 5 + (int)(processed * 65 / Math.Max(1, total));
                        if (value != previous) { previous = value; progress("正在释放组件… " + (value - 5) * 100 / 65 + "%", value); }
                    }
                }
            }
            Json.Write(Path.Combine(stage, ".complete.json"), new { payload_hash = BuildInfo.PayloadHash, version = BuildInfo.Version,
                manifest_sha256 = HashFile(Path.Combine(stage,"runtime-manifest.json")) });
            string target = Path.Combine(versions, BuildInfo.Version + "-" + BuildInfo.PayloadHash.Substring(0, 10));
            if (Directory.Exists(target)) target += "-repair-" + Guid.NewGuid().ToString("N").Substring(0, 6);
            Directory.Move(stage, target);
            Json.Write(Path.Combine(options.Home, "current.json"), new { path = target, payload_hash = BuildInfo.PayloadHash });
            Status("独立运行环境已自动部署。", 72);
            return target;
        } catch {
            // Only recursively remove the exact random staging directory created above.
            string safeRoot = Path.GetFullPath(versions) + Path.DirectorySeparatorChar;
            string actual = Path.GetFullPath(stage);
            if (actual.StartsWith(safeRoot, StringComparison.OrdinalIgnoreCase) && Path.GetFileName(actual).StartsWith(".stage-", StringComparison.Ordinal) && Directory.Exists(actual)) {
                try { Directory.Delete(actual, true); } catch (IOException) { } catch (UnauthorizedAccessException) { }
            }
            throw;
        }
    }

    private ProcessStartInfo ChildInfo(string executable, string arguments) {
        ProcessStartInfo info = new ProcessStartInfo(executable, arguments);
        info.UseShellExecute = false; info.CreateNoWindow = true;
        info.WorkingDirectory = Path.Combine(InstallRoot, "app");
        foreach (string key in new[] { "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX" }) info.EnvironmentVariables.Remove(key);
        info.EnvironmentVariables["PATH"] = ChildPath;
        info.EnvironmentVariables["PYTHONUTF8"] = "1";
        info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        info.EnvironmentVariables["VIDEO_DESK_DATA"] = Data;
        info.EnvironmentVariables["VIDEO_DESK_DEFAULT_OUTPUT"] = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "映格素材");
        info.EnvironmentVariables["VIDEO_DESK_CRAWLER_PYTHON"] = CrawlerPython;
        info.EnvironmentVariables["VIDEO_DESK_BROWSER_EXE"] = Browser;
        info.EnvironmentVariables["VIDEO_DESK_NODE_EXE"] = Node;
        info.EnvironmentVariables["VIDEO_DESK_BUNDLED"] = "1";
        info.EnvironmentVariables["VIDEO_DESK_VERSION"] = BuildInfo.Version;
        info.EnvironmentVariables["VIDEO_DESK_LAUNCHER"] = "1";
        info.EnvironmentVariables["VIDEO_DESK_INSTALLATION_ID"] = InstallationId;
        info.EnvironmentVariables["VIDEO_DESK_DISABLED_COMPONENTS"] = String.Join(",",disabled);
        info.EnvironmentVariables["VIDEO_DESK_RUNTIME_REPORT"] = Path.Combine(options.Home,"runtime-diagnostics.json");
        foreach(string key in new[]{"OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS"}) info.EnvironmentVariables[key]="1";
        info.EnvironmentVariables["PLAYWRIGHT_BROWSERS_PATH"] = Path.Combine(InstallRoot, "runtime", "browsers");
        info.EnvironmentVariables["MPLCONFIGDIR"] = Path.Combine(Data, "matplotlib");
        return info;
    }

    private string RunHidden(string executable, string arguments, int timeout, CancellationToken cancellation) {
        ProcessStartInfo info = ChildInfo(executable, arguments);
        info.RedirectStandardOutput = true; info.RedirectStandardError = true;
        info.StandardOutputEncoding = Encoding.UTF8; info.StandardErrorEncoding = Encoding.UTF8;
        using (ProcessTree componentJob = new ProcessTree()) using (Process process = Process.Start(info)) {
            componentJob.Assign(process);
            Task<string> stdout = process.StandardOutput.ReadToEndAsync();
            Task<string> stderr = process.StandardError.ReadToEndAsync();
            Stopwatch watch = Stopwatch.StartNew();
            while (!process.WaitForExit(100)) {
                if (cancellation.IsCancellationRequested || watch.ElapsedMilliseconds > timeout) {
                    try { process.Kill(); } catch (InvalidOperationException) { }
                    cancellation.ThrowIfCancellationRequested(); throw new TimeoutException("运行组件检查超时，请查看诊断日志。");
                }
            }
            // Terminate orphaned probe children before awaiting redirected EOF.
            // A crashed browser/driver must not hold these pipe handles forever.
            componentJob.Dispose();
            string output = stdout.Result, error = stderr.Result;
            if(output.Length>8192)output=output.Substring(output.Length-8192);
            if(error.Length>8192)error=error.Substring(error.Length-8192);
            string result="退出码 "+process.ExitCode+" / 0x"+unchecked((uint)process.ExitCode).ToString("X8");
            Log(Path.GetFileName(executable) + " " + result + "\r\n标准输出：" + output + "\r\n错误输出：" + error);
            if (process.ExitCode != 0) throw new ComponentFailure(result+"\r\n标准输出："+output+"\r\n错误输出："+error,process.ExitCode);
            return output;
        }
    }

    public void StartServer(CancellationToken cancellation) {
        string shutdown = Path.Combine(Data, "shutdown.request"); if (File.Exists(shutdown)) File.Delete(shutdown);
        cancellation.ThrowIfCancellationRequested(); Status("启动本机网页服务…", 94);
        int port = FreePort(); Url = "http://127.0.0.1:" + port;
        ProcessStartInfo info = ChildInfo(MainPython, Quote(Path.Combine(InstallRoot, "app", "app.py")));
        info.EnvironmentVariables["VIDEO_DESK_PORT"] = port.ToString();
        info.RedirectStandardOutput = true; info.RedirectStandardError = true;
        info.StandardOutputEncoding = Encoding.UTF8; info.StandardErrorEncoding = Encoding.UTF8;
        server = new Process { StartInfo = info };
        server.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) Log("server " + e.Data); };
        server.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) Log("server " + e.Data); };
        server.Start(); job.Assign(server); server.BeginOutputReadLine(); server.BeginErrorReadLine();
        Stopwatch watch = Stopwatch.StartNew();
        while (watch.Elapsed < TimeSpan.FromSeconds(45)) {
            cancellation.ThrowIfCancellationRequested();
            if (server.HasExited) throw new InvalidOperationException("网页服务启动后退出，请查看 launcher.log。");
            if (Healthy(Url, InstallationId)) {
                Json.Write(Path.Combine(Data, "server.json"), new { url = Url, pid = server.Id, installation_id = InstallationId, browser = Browser });
                Status("工作台已启动。", 100); return;
            }
            Thread.Sleep(200);
        }
        throw new TimeoutException("网页服务启动超时，请查看 launcher.log。");
    }

    public void BeginRepair() {
        var stateRequest=(HttpWebRequest)WebRequest.Create(Url+"/api/state");
        stateRequest.Proxy=null;stateRequest.Timeout=5000;stateRequest.ReadWriteTimeout=5000;
        Dictionary<string,object> state;
        using(var response=stateRequest.GetResponse())using(var reader=new StreamReader(response.GetResponseStream(),Encoding.UTF8))
            state=Json.Serializer().Deserialize<Dictionary<string,object>>(reader.ReadToEnd());
        var request=(HttpWebRequest)WebRequest.Create(Url+"/api/runtime-repair");
        request.Proxy=null;request.Method="POST";request.Timeout=5000;request.ReadWriteTimeout=5000;
        request.ContentType="application/json";request.Headers["X-App-Token"]=(string)state["token"];
        byte[] bytes=Encoding.UTF8.GetBytes("{}");request.ContentLength=bytes.Length;
        using(var stream=request.GetRequestStream())stream.Write(bytes,0,bytes.Length);
        try {using(var response=request.GetResponse()){} }
        catch(WebException exc) {
            if(exc.Response!=null)using(var reader=new StreamReader(exc.Response.GetResponseStream(),Encoding.UTF8)) {
                var error=Json.Serializer().Deserialize<Dictionary<string,object>>(reader.ReadToEnd());
                throw new InvalidOperationException(Convert.ToString(error["detail"]));
            }
            throw new InvalidOperationException("无法确认任务状态，暂未重启服务。请稍后重试。",exc);
        }
        ClearRepairRequest();
    }

    private static int FreePort() {
        TcpListener listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start(); int port = ((IPEndPoint)listener.LocalEndpoint).Port; listener.Stop(); return port;
    }
    private static bool Healthy(string url, string id) {
        try {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url + "/api/state");
            request.Proxy = null; request.Timeout = 1500; request.ReadWriteTimeout = 1500;
            using (WebResponse response = request.GetResponse()) using (StreamReader reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8)) {
                Dictionary<string, object> state = Json.Serializer().Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
                return (string)state["application"] == "video-desk" && (string)state["installation_id"] == id;
            }
        } catch (Exception) { return false; }
    }

    private static string ChooseBrowser(string root, Dictionary<string, object> metadata) {
        string[] candidates = {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "Microsoft", "Edge", "Application", "msedge.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Microsoft", "Edge", "Application", "msedge.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Google", "Chrome", "Application", "chrome.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Google", "Chrome", "Application", "chrome.exe"),
            Path.Combine(root, ((string)metadata["browser"]).Replace('/', Path.DirectorySeparatorChar))
        };
        string browser = candidates.FirstOrDefault(File.Exists);
        if (browser == null) throw new FileNotFoundException("未找到可用的浏览器组件。");
        return browser;
    }

    public void OpenBrowser() {
        OpenBrowserAt(Browser, Url, Data);
    }
    private static void OpenBrowserAt(string browser, string url, string data) {
        ProcessStartInfo info = new ProcessStartInfo(browser, "--no-first-run --new-window --user-data-dir=" + Quote(Path.Combine(data, "workbench-browser")) + " " + Quote(url));
        info.UseShellExecute = false; info.CreateNoWindow = false;
        Process.Start(info);
    }
    public static bool OpenExisting(string home) {
        try {
            string data = Path.Combine(home, "data");
            Dictionary<string, object> serverInfo = Json.Read(Path.Combine(data, "server.json"));
            string url = (string)serverInfo["url"], id = (string)serverInfo["installation_id"];
            if (!Healthy(url, id)) return false;
            OpenBrowserAt((string)serverInfo["browser"], url, data); return true;
        } catch (Exception) { return false; }
    }
    public static bool ExistingReady(string home) {
        try { var info = Json.Read(Path.Combine(home, "data", "server.json")); return Healthy((string)info["url"], (string)info["installation_id"]); } catch { return false; }
    }

    private void InstallShortcut() {
        try {
            string executable = Assembly.GetExecutingAssembly().Location;
            string installed = Path.Combine(options.Home, "VideoDesk.exe");
            if (!String.Equals(executable, installed, StringComparison.OrdinalIgnoreCase)) {
                if (!File.Exists(installed) || new FileInfo(installed).Length != new FileInfo(executable).Length || FileVersionInfo.GetVersionInfo(installed).FileVersion != BuildInfo.Version + ".0") File.Copy(executable, installed, true);
            }
            string shortcut = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "映格视频工作台.lnk");
            {
                Type type = Type.GetTypeFromProgID("WScript.Shell");
                object shell = Activator.CreateInstance(type);
                object link = type.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { shortcut });
                Type linkType = link.GetType();
                linkType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, link, new object[] { installed });
                linkType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, link, new object[] { options.Home });
                linkType.InvokeMember("Description", BindingFlags.SetProperty, null, link, new object[] { "映格本地视频采集工作台" });
                linkType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, link, new object[] { installed + ",0" });
                linkType.InvokeMember("Save", BindingFlags.InvokeMethod, null, link, null);
                Marshal.FinalReleaseComObject(link); Marshal.FinalReleaseComObject(shell);
            }
        } catch (Exception exc) { Log("Shortcut: " + exc.Message); }
    }

    public void Dispose() {
        if (disposed) return; disposed = true;
        job.Dispose();
        if (server != null) {
            try { if (!server.HasExited) { server.Kill(); server.WaitForExit(5000); } } catch (InvalidOperationException) { }
            server.Dispose();
        }
        try {
            string info = Path.Combine(Data, "server.json");
            if (File.Exists(info) && (string)Json.Read(info)["installation_id"] == InstallationId) File.Delete(info);
        } catch (Exception) { }
    }
}

internal sealed class ProcessTree : IDisposable {
    private IntPtr handle;
    public ProcessTree() {
        handle = CreateJobObject(IntPtr.Zero, null);
        if (handle == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
        ExtendedLimit information = new ExtendedLimit(); information.Basic.LimitFlags = 0x00002000; // KILL_ON_JOB_CLOSE
        int size = Marshal.SizeOf(information); IntPtr pointer = Marshal.AllocHGlobal(size);
        try {
            Marshal.StructureToPtr(information, pointer, false);
            if (!SetInformationJobObject(handle, 9, pointer, (uint)size)) throw new System.ComponentModel.Win32Exception();
        } finally { Marshal.FreeHGlobal(pointer); }
    }
    public void Assign(Process process) {
        if (!AssignProcessToJobObject(handle, process.Handle) && !process.HasExited) throw new System.ComponentModel.Win32Exception();
    }
    public void Dispose() { if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; } }
    [StructLayout(LayoutKind.Sequential)] private struct BasicLimit {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit; public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize; public uint ActiveProcessLimit;
        public UIntPtr Affinity; public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)] private struct IoCounters { public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount, ReadTransferCount, WriteTransferCount, OtherTransferCount; }
    [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimit { public BasicLimit Basic; public IoCounters Io; public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetInformationJobObject(IntPtr job, int informationClass, IntPtr information, uint length);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
}
