using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

internal static class Native {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
}

internal sealed class DownloadButtonForm : Form {
    private const int W = 38;
    private readonly string home;
    private readonly Label glyph = new Label();
    private readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    private readonly ToolTip tip = new ToolTip();
    private IntPtr siteWindow;
    private string siteTitle = "";
    private bool dragging;
    private bool moved;
    private Point dragStart;
    private Point windowStart;
    private bool busy;
    private DateTime holdUntil = DateTime.MinValue;
    private bool customPosition;

    protected override bool ShowWithoutActivation { get { return true; } }

    public DownloadButtonForm(string home) {
        this.home = home;
        Text = "映格下载按钮";
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.Manual;
        ShowInTaskbar = false;
        TopMost = true;
        AutoScaleMode = AutoScaleMode.None;
        AutoSize = false;
        MinimumSize = new Size(W, W);
        MaximumSize = new Size(W, W);
        ClientSize = new Size(W, W);
        BackColor = Color.FromArgb(20, 107, 99);
        Opacity = 0.93;
        try {
            using (GraphicsPath path = new GraphicsPath()) {
                path.AddEllipse(0, 0, W, W);
                Region = new Region(path);
            }
        } catch { }
        glyph.Text = "↓";
        glyph.AutoSize = false;
        glyph.Dock = DockStyle.Fill;
        glyph.TextAlign = ContentAlignment.MiddleCenter;
        glyph.ForeColor = Color.White;
        glyph.BackColor = Color.Transparent;
        glyph.Font = new Font("Segoe UI Symbol", 17F, FontStyle.Bold);
        glyph.Enabled = false;
        Controls.Add(glyph);
        tip.SetToolTip(this, "下载当前抖音 / B 站视频");
        tip.SetToolTip(glyph, "下载当前抖音 / B 站视频");
        tip.ShowAlways = true;
        var menu = new ContextMenuStrip();
        menu.Items.Add("退出下载按钮", null, delegate { Close(); });
        ContextMenuStrip = menu;
        LoadPosition();
        MouseDown += OnDown;
        MouseMove += OnMove;
        MouseUp += OnUp;
        timer.Interval = 700;
        timer.Tick += delegate { UpdateVisibility(); };
    }

    public void StartWatch() {
        timer.Start();
        UpdateVisibility();
    }

    protected override void OnFormClosed(FormClosedEventArgs e) {
        timer.Stop();
        SavePosition();
        base.OnFormClosed(e);
    }

    private void LoadPosition() {
        int x = Screen.PrimaryScreen.WorkingArea.Right - W - 24;
        int y = Screen.PrimaryScreen.WorkingArea.Bottom - W - 24;
        try {
            string path = Path.Combine(home, "data", "download-button.json");
            if (File.Exists(path)) {
                Match mx = Regex.Match(File.ReadAllText(path, Encoding.UTF8), "\"x\"\\s*:\\s*(-?\\d+)");
                Match my = Regex.Match(File.ReadAllText(path, Encoding.UTF8), "\"y\"\\s*:\\s*(-?\\d+)");
                if (mx.Success && my.Success) { x = int.Parse(mx.Groups[1].Value); y = int.Parse(my.Groups[1].Value); customPosition = true; }
            }
        } catch { }
        Location = ClampToScreen(new Point(x, y));
    }

    private void SavePosition() {
        try {
            Directory.CreateDirectory(Path.Combine(home, "data"));
            string path = Path.Combine(home, "data", "download-button.json");
            File.WriteAllText(path, "{\"x\":" + Location.X + ",\"y\":" + Location.Y + "}", Encoding.UTF8);
        } catch { }
    }

    private Point ClampToScreen(Point p) {
        Screen screen = Screen.FromPoint(p);
        Rectangle r = screen.WorkingArea;
        int x = Math.Max(r.Left + 4, Math.Min(p.X, r.Right - W - 4));
        int y = Math.Max(r.Top + 4, Math.Min(p.Y, r.Bottom - W - 4));
        return new Point(x, y);
    }

    private void OnDown(object sender, MouseEventArgs e) {
        if (e.Button != MouseButtons.Left) return;
        dragging = true; moved = false;
        dragStart = Cursor.Position; windowStart = Location;
        Capture = true;
    }

    private void OnMove(object sender, MouseEventArgs e) {
        if (!dragging) return;
        Point now = Cursor.Position;
        if (Math.Abs(now.X - dragStart.X) + Math.Abs(now.Y - dragStart.Y) > 3) moved = true;
        Location = ClampToScreen(new Point(windowStart.X + now.X - dragStart.X, windowStart.Y + now.Y - dragStart.Y));
    }

    private void OnUp(object sender, MouseEventArgs e) {
        if (!dragging) return;
        dragging = false; Capture = false;
        if (moved) { customPosition = true; SavePosition(); }
        else BeginDownload();
    }

    private static string TitleOf(IntPtr hwnd) {
        try {
            StringBuilder sb = new StringBuilder(1024);
            Native.GetWindowText(hwnd, sb, sb.Capacity);
            return sb.ToString();
        } catch { return ""; }
    }

    private static string ProcessOf(IntPtr hwnd) {
        try {
            uint pid;
            Native.GetWindowThreadProcessId(hwnd, out pid);
            return Process.GetProcessById((int)pid).ProcessName.ToLowerInvariant();
        } catch { return ""; }
    }

    private static bool IsBrowser(string process) {
        return process == "msedge" || process == "chrome" || process == "firefox" ||
               process == "brave" || process == "opera" || process == "vivaldi" ||
               process == "360se" || process == "360chrome" || process == "qqbrowser";
    }

    private static bool IsVideoSite(string title, string process) {
        string t = (title ?? "").ToLowerInvariant();
        return IsBrowser(process) && (t.Contains("抖音") || t.Contains("douyin") ||
               t.Contains("哔哩哔哩") || t.Contains("bilibili"));
    }

    private static bool IsFullscreen(IntPtr hwnd) {
        Native.RECT r;
        if (!Native.GetWindowRect(hwnd, out r)) return false;
        Screen screen = Screen.FromHandle(hwnd);
        Rectangle b = screen.Bounds;
        return r.Left <= b.Left + 2 && r.Top <= b.Top + 2 &&
               r.Right >= b.Right - 2 && r.Bottom >= b.Bottom - 2;
    }

    private void UpdateVisibility() {
        if (dragging || IsDisposed) return;
        IntPtr fg = Native.GetForegroundWindow();
        string title = TitleOf(fg);
        string process = ProcessOf(fg);
        if (IsVideoSite(title, process)) {
            siteWindow = fg; siteTitle = title;
            if (IsFullscreen(fg)) { Hide(); return; }
            if (!customPosition) {
                Rectangle work = Screen.FromHandle(fg).WorkingArea;
                Location = ClampToScreen(new Point(work.Right - W - 24, work.Bottom - W - 24));
            }
            if (!Visible) Show();
            return;
        }
        if (!busy && DateTime.Now >= holdUntil) Hide();
    }

    private void SetGlyph(string text, Color color) {
        if (IsDisposed) return;
        glyph.Text = text;
        glyph.ForeColor = color;
    }

    private string CaptureUrl(IntPtr hwnd) {
        try {
            Clipboard.Clear();
            Native.ShowWindow(hwnd, 9); // SW_RESTORE
            Native.SetForegroundWindow(hwnd);
            Thread.Sleep(100);
            SendKeys.SendWait("^l");
            Thread.Sleep(140);
            SendKeys.SendWait("^c");
            Thread.Sleep(320);
            string value = Clipboard.ContainsText() ? Clipboard.GetText().Trim() : "";
            SendKeys.SendWait("{ESC}");
            Uri uri;
            if (!Uri.TryCreate(value, UriKind.Absolute, out uri)) return "";
            string host = (uri.Host ?? "").ToLowerInvariant();
            if (!(host == "douyin.com" || host.EndsWith(".douyin.com") ||
                  host == "bilibili.com" || host.EndsWith(".bilibili.com") ||
                  host == "b23.tv" || host.EndsWith(".b23.tv"))) return "";
            return value;
        } catch { return ""; }
    }

    private void BeginDownload() {
        if (busy) return;
        IntPtr target = siteWindow;
        if (target == IntPtr.Zero || !IsVideoSite(siteTitle, ProcessOf(target))) {
            tip.Show("请先切到抖音或 B 站视频页", this, 0, -42, 3000);
            return;
        }
        busy = true;
        SetGlyph("…", Color.White);
        string url = CaptureUrl(target);
        if (String.IsNullOrEmpty(url)) {
            busy = false;
            SetGlyph("!", Color.FromArgb(255, 220, 120));
            tip.Show("没有取到视频链接，请确认页面已打开", this, 0, -42, 3000);
            holdUntil = DateTime.Now.AddSeconds(2);
            return;
        }
        Task.Run(() => {
            string message;
            try { message = DownloadClient.Post(home, url); }
            catch (Exception exc) { message = "下载失败：" + exc.Message; }
            BeginInvoke((Action)(delegate {
                busy = false;
                if (message.StartsWith("已")) {
                    SetGlyph("✓", Color.FromArgb(190, 255, 220));
                    tip.Show(message + "，可在映格工作台查看", this, 0, -42, 3500);
                } else {
                    SetGlyph("!", Color.FromArgb(255, 220, 120));
                    tip.Show(message, this, 0, -42, 4000);
                }
                holdUntil = DateTime.Now.AddSeconds(3);
            }));
        });
    }
}

internal static class DownloadClient {
    private static string ServerUrl(string home) {
        try {
            string text = File.ReadAllText(Path.Combine(home, "data", "server.json"), Encoding.UTF8);
            Match m = Regex.Match(text, "\"url\"\\s*:\\s*\"([^\"]+)\"");
            return m.Success ? m.Groups[1].Value.TrimEnd('/') : "";
        } catch { return ""; }
    }

    private static string Get(string url, string token) {
        HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
        req.Method = "GET"; req.Timeout = 8000; req.ReadWriteTimeout = 8000;
        req.Proxy = null;
        if (!String.IsNullOrEmpty(token)) req.Headers["X-App-Token"] = token;
        using (WebResponse resp = req.GetResponse())
        using (StreamReader reader = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
            return reader.ReadToEnd();
    }

    public static string Post(string home, string videoUrl) {
        string service = ServerUrl(home);
        if (String.IsNullOrEmpty(service)) return "映格工作台未启动";
        string state = Get(service + "/api/state", "");
        Match tm = Regex.Match(state, "\"token\"\\s*:\\s*\"([^\"]+)\"");
        if (!tm.Success) return "无法取得映格本地令牌";
        string token = tm.Groups[1].Value;
        string body = "{\"urls\":[\"" + Escape(videoUrl) + "\"],\"output\":\"\",\"login\":true}";
        HttpWebRequest req = (HttpWebRequest)WebRequest.Create(service + "/api/download");
        req.Method = "POST"; req.Timeout = 10000; req.ReadWriteTimeout = 10000;
        req.Proxy = null;
        req.ContentType = "application/json; charset=utf-8";
        req.Headers["X-App-Token"] = token;
        byte[] bytes = Encoding.UTF8.GetBytes(body);
        req.ContentLength = bytes.Length;
        using (Stream stream = req.GetRequestStream()) stream.Write(bytes, 0, bytes.Length);
        using (WebResponse resp = req.GetResponse())
        using (StreamReader reader = new StreamReader(resp.GetResponseStream(), Encoding.UTF8)) {
            string text = reader.ReadToEnd();
            return text.Contains("\"ids\"") ? "已加入下载队列" : "下载请求已发送";
        }
    }

    private static string Escape(string value) {
        return value.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "").Replace("\n", "");
    }
}

