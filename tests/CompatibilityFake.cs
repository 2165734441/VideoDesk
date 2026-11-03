using System;
using System.Threading;
using System.Diagnostics;
internal static class CompatibilityFake {
    public static int Main(string[] args) {
        Console.WriteLine("probe output");
        if(args.Length>0 && args[0]=="--orphan") {
            var info=new ProcessStartInfo(System.Reflection.Assembly.GetExecutingAssembly().Location,"--sleep") {UseShellExecute=false,CreateNoWindow=true};
            using(var child=Process.Start(info))Console.WriteLine("child_pid="+child.Id);
            return 0;
        }
        if(args.Length>0)Thread.Sleep(10000);
        return unchecked((int)0xc000001d);
    }
}
