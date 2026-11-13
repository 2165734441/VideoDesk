using System;
using System.Collections;
using System.Diagnostics;

internal static class EnvironmentHarness {
    public static int Main() {
        ProcessEnvironment.Normalize();
        var info=new ProcessStartInfo("cmd.exe","/c exit 0") {UseShellExecute=false,CreateNoWindow=true};
        int proxies=0;
        foreach(DictionaryEntry pair in info.EnvironmentVariables)
            if(String.Equals((string)pair.Key,"HTTP_PROXY",StringComparison.OrdinalIgnoreCase))proxies++;
        if(proxies!=1)throw new Exception("proxy normalization count: "+proxies);
        if(!info.EnvironmentVariables.ContainsKey("PYTHONPATH"))throw new Exception("unrelated value lost before child isolation");
        Console.WriteLine("duplicate environment normalization passed");
        return 0;
    }
}
