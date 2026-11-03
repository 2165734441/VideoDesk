"""Stage signed app-local CRT files from the Visual Studio redistributable set.

Never copy arbitrary DLLs from System32. The explicit --source option supports
another licensed build machine's VC/Redist/MSVC/.../x64/Microsoft.VC*.CRT folder.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('concrt140.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
         'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll', 'vccorlib140.dll',
         'vcruntime140.dll', 'vcruntime140_1.dll', 'vcruntime140_threads.dll')


def prepare(stage, source=None):
    stage = Path(stage)
    if source is None:
        base = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft Visual Studio'
        candidates = [p for p in base.glob('*/*/VC/Redist/MSVC/*/x64/Microsoft.VC*.CRT')
                      if all((p/n).is_file() for n in NAMES)]
        if not candidates:
            raise RuntimeError('VC redistributable directory not found; use prepare_vc_runtime.py --source ...')
        source = max(candidates, key=lambda p: tuple(int(x) for x in p.parent.parent.name.split('.')))
    source = Path(source).resolve()
    records = []
    for name in NAMES:
        path = source/name
        if not path.is_file():
            raise RuntimeError('Incomplete CRT distribution: '+name)
        # Verify the original Microsoft Authenticode signature before packaging.
        quoted = str(path).replace("'", "''")
        command = "$s=Get-AuthenticodeSignature -LiteralPath '"+quoted+"'; " + \
                  "if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notlike '*O=Microsoft Corporation*'){exit 1}; " + \
                  "[Console]::Write((Get-Item -LiteralPath '"+quoted+"').VersionInfo.FileVersion)"
        ps=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0'
        env={**os.environ,'PSModulePath':str(ps/'Modules')}
        result = subprocess.run([str(ps/'powershell.exe'),'-NoProfile','-NonInteractive','-Command',command],env=env,
                                capture_output=True, text=True, check=True,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        data = path.read_bytes()
        for runtime in ('main','crawler','node'):
            dest = stage/'runtime'/runtime/name
            dest.parent.mkdir(parents=True,exist_ok=True)
            # The build interpreter may itself use this identical staged CRT.
            # Verify signatures above, then avoid rewriting a loaded DLL.
            if not dest.is_file() or dest.read_bytes()!=data:shutil.copy2(path,dest)
        records.append({'name':name,'version':result.stdout.strip(),
                        'sha256':hashlib.sha256(data).hexdigest(),'signature':'Microsoft Corporation / Valid'})
    meta = stage/'runtime'/'vc';meta.mkdir(exist_ok=True)
    report = {'source':'Microsoft Visual Studio VC/Redist/MSVC/x64 CRT redistributable files',
              'documentation':'https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files',
              'files':records,'system_directory_modified':False}
    (meta/'provenance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (meta/'README.txt').write_text(
        'Microsoft Visual C++ Runtime — app-local x64 redistributable files.\n'
        'Copyright Microsoft Corporation. All rights reserved.\n'
        'Distributed with this application under the applicable Microsoft Visual Studio license terms.\n'
        'Original signed redistributable binaries are unchanged. No files are copied into Windows directories.\n'
        'License directory: https://visualstudio.microsoft.com/license-terms/\n'
        'Servicing: update this application, or install the official current supported Visual C++ x64 Redistributable.\n'
        'Official download: https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path)
    parser.add_argument('--stage',type=Path,default=ROOT/'.local/build-exe/payload')
    args=parser.parse_args();print(json.dumps(prepare(args.stage,args.source),indent=2))
