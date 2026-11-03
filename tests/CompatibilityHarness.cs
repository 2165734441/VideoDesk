using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Diagnostics;

internal static class CompatibilityHarness {
    private static void Check(bool condition,string message) { if(!condition)throw new Exception(message); }
    public static int Main(string[] args) {
        string home=args[0], fake=args[1]; Directory.CreateDirectory(home);
        Check(Compatibility.Explain(unchecked((int)0xc0000135),"").Contains("运行库"),"missing DLL message");
        Check(Compatibility.Explain(unchecked((int)0xc000001d),"").Contains("处理器"),"CPU message");
        Check(Compatibility.Explain(unchecked((int)0xc000007b),"").Contains("架构"),"architecture message");
        Check(Compatibility.Explain(-1,"").Contains("超时"),"timeout message");
        Check(Compatibility.Explain(1,"ImportError: DLL load failed").Contains("运行库"),"Python import message");
        var options=new Options { Home=home };
        using(var core=new Core(options,delegate(string m,int p){})) {
            core.InstallRoot=home;Directory.CreateDirectory(Path.Combine(home,"app"));
            core.Node=fake;core.MainPython=fake;core.CrawlerPython=fake;
            core.InstallationId="test";core.ChildPath=Environment.GetFolderPath(Environment.SpecialFolder.System);
            MethodInfo probe=typeof(Core).GetMethod("Probe",BindingFlags.Instance|BindingFlags.NonPublic);
            probe.Invoke(core,new object[]{"ocr","画面文字识别",fake,"",false,CancellationToken.None});
            Check(core.Degraded,"native failure must degrade");
            Check((string)core.Components[0]["exit_hex"]=="0xC000001D","native exit code must be preserved");
            Check(((string)core.Components[0]["error"]).Contains("probe output"),"stdout must survive native exit");
            try {probe.Invoke(core,new object[]{"base","基础工作台",fake,"",true,CancellationToken.None});throw new Exception("required failure ignored");}
            catch(TargetInvocationException e) {Check(e.InnerException.Message.Contains("基础工作台"),"required label");}
            MethodInfo run=typeof(Core).GetMethod("RunHidden",BindingFlags.Instance|BindingFlags.NonPublic);
            try {run.Invoke(core,new object[]{fake,"--sleep",50,CancellationToken.None});throw new Exception("timeout ignored");}
            catch(TargetInvocationException e) {Check(e.InnerException is TimeoutException,"bounded timeout");}
            string orphan=(string)run.Invoke(core,new object[]{fake,"--orphan",3000,CancellationToken.None});
            int pid=Int32.Parse(orphan.Split(new[]{"child_pid="},StringSplitOptions.None)[1].Trim());
            try {using(var child=Process.GetProcessById(pid))Check(child.HasExited,"orphaned probe child survived");}
            catch(ArgumentException){}
            core.SaveDiagnostics();var report=Json.Read(Path.Combine(home,"runtime-diagnostics.json"));
            Check(report.ContainsKey("cpu") && !(bool)report["includes_accounts"],"diagnostic fields");
            File.WriteAllText(Path.Combine(home,"data","repair.request"),"repair");
            Check(core.RepairRequested,"repair request");core.ClearRepairRequest();Check(!core.RepairRequested,"consume repair request");
        }
        using(var form=new LauncherForm(options)) {
            Check(form.Controls.Find("unused",false).Length==0,"control access");
            bool repair=false,export=false,install=false;
            foreach(System.Windows.Forms.Control c in form.Controls) {
                if(c.Text=="检测并修复")repair=c.Enabled;
                if(c.Text=="导出启动诊断")export=c.Enabled;
                if(c.Text=="安装系统运行库")install=c.Enabled;
            }
            Check(repair&&export&&install,"repair controls available before startup");
        }
        if(args.Length>=4) {
            string stage=args[2],stop=args[3];
            Compatibility.VerifyMicrosoftSignature(Path.Combine(stage,"runtime","main","msvcp140.dll"));
            try {Compatibility.VerifyMicrosoftSignature(fake);throw new Exception("unsigned installer accepted");}
            catch(InvalidDataException){}
            using(var core=new Core(options,delegate(string m,int p){})) {
                core.InstallRoot=stage;core.MainPython=Path.Combine(stage,"runtime","main","python.exe");
                core.CrawlerPython=Path.Combine(stage,"runtime","crawler","python.exe");
                core.Node=Path.Combine(stage,"runtime","node","node.exe");core.Browser="";core.InstallationId="native-isolation-test";
                core.ChildPath=Path.GetDirectoryName(core.Node)+";"+Path.GetDirectoryName(core.MainPython)+";"+Environment.GetFolderPath(Environment.SpecialFolder.System);
                MethodInfo probe=typeof(Core).GetMethod("Probe",BindingFlags.Instance|BindingFlags.NonPublic);
                foreach(string id in new[]{"base","media"}) probe.Invoke(core,new object[]{id,id,core.MainPython,
                    "-u "+Core.QuoteArgument(Path.Combine(stage,"app","integrations","runtime_probe.py"))+" "+id,true,CancellationToken.None});
                foreach(string id in new[]{"ocr","asr"}) probe.Invoke(core,new object[]{id,id=="ocr"?"画面文字识别":"本地语音识别",fake,"",false,CancellationToken.None});
                core.SaveDiagnostics();core.StartServer(CancellationToken.None);
                Check(core.ServerAlive,"native failures must not prevent server startup");
                string jobs=Path.Combine(home,"data","jobs.json");
                File.WriteAllText(jobs,"[{\"status\":\"waiting_source\"}]");Check(core.HasActiveJobs,"waiting sources block repair");
                File.WriteAllText(jobs,"[]");Check(!core.HasActiveJobs,"no active jobs");
                Json.Write(Path.Combine(home,"degraded-server.json"),new {url=core.Url,degraded=core.Degraded});
                Stopwatch watch=Stopwatch.StartNew();
                while(!File.Exists(stop) && watch.Elapsed.TotalMinutes<3)Thread.Sleep(100);
            }
        }
        Console.WriteLine("PASS: native exit, diagnostics, timeout, required/optional isolation, repair controls");return 0;
    }
}
