using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;

internal static class Launcher281Harness {
    [STAThread]
    public static int Main(string[] args) {
        AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling",false);
        AppContext.SetSwitch("Switch.System.IO.BlockLongPaths",false);
        ProcessEnvironment.Normalize();
        string home=Path.GetFullPath(args[0]);
        var results=new List<object>();int failed=0;
        var options=Options.Parse(new[]{"--home",home});
        using(var core=new Core(options,delegate(string s,int p){})) {
            try {
                var extract=typeof(Core).GetMethod("Extract",BindingFlags.NonPublic|BindingFlags.Instance);
                string target=(string)extract.Invoke(core,new object[]{CancellationToken.None});
                string relative=args[1];string file=Path.Combine(target,relative.Replace('/',Path.DirectorySeparatorChar));
                if(!File.Exists(file) || File.ReadAllText(file)!="long-path-test")throw new Exception("extracted contents differ");
                results.Add(new {id="long_path_extract",pass=true,path_length=file.Length,home_length=home.Length});
            } catch(Exception e) {failed++;results.Add(new {id="long_path_extract",pass=false,error=e.ToString()});}
            foreach(string mode in new[]{"custom","default","self-test"}) {
                Options config=mode=="custom"?options:Options.Parse(mode=="default"?new string[0]:new[]{"--self-test"});
                string wanted=mode=="default"?Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),"映格素材"):Path.Combine(config.Home,"output");
                // Avoid creating a default install: ChildInfo only assembles a child process.
                var field=typeof(Core).GetField("options",BindingFlags.NonPublic|BindingFlags.Instance);field.SetValue(core,config);
                core.InstallRoot=home;core.ChildPath="";core.CrawlerPython="";core.Browser="";core.Node="";
                var child=typeof(Core).GetMethod("ChildInfo",BindingFlags.NonPublic|BindingFlags.Instance);
                var info=(ProcessStartInfo)child.Invoke(core,new object[]{"test.exe",""});
                bool pass=info.EnvironmentVariables["VIDEO_DESK_DEFAULT_OUTPUT"]==wanted;
                if(!pass)failed++;
                results.Add(new {id=mode+"_default_output",pass=pass});
            }
        }
        Json.Write(args[2],new {failed=failed,cases=results});
        Console.WriteLine("launcher regressions: "+(4-failed)+"/4 passed");return failed==0?0:1;
    }
}
