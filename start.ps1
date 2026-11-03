param([int]$Port = 18966, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$appPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $appPython)) { $appPython=Join-Path $projectRoot '.local/build-exe/payload/runtime/main/python.exe' }
if (-not (Test-Path -LiteralPath $appPython)) { throw 'Please run the Windows EXE or setup.ps1 first.' }
$url = "http://127.0.0.1:$Port"
$isRunning = $false
try {
    $state = Invoke-RestMethod -Uri "$url/api/state" -TimeoutSec 2
    $isRunning = $state.application -eq 'video-desk' -and $state.version -match '^2\.'
} catch { }
if (-not $isRunning) {
    New-Item -ItemType Directory -Path '.local' -Force | Out-Null
    $env:VIDEO_DESK_PORT = "$Port"
    $bundledNode=Join-Path $projectRoot '.local/build-exe/payload/runtime/node/node.exe'
    if(Test-Path -LiteralPath $bundledNode){$env:VIDEO_DESK_NODE_EXE=$bundledNode}
    $appSource = Join-Path $projectRoot 'app.py'
    $process = Start-Process -FilePath $appPython -ArgumentList @('"' + $appSource + '"') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $projectRoot '.local/server.stdout.log') -RedirectStandardError (Join-Path $projectRoot '.local/server.stderr.log')
    $process.Id | Set-Content -LiteralPath '.local/server.pid' -Encoding ASCII
    for ($attempt=0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        try { $state = Invoke-RestMethod -Uri "$url/api/state" -TimeoutSec 1; $isRunning = $state.application -eq 'video-desk' -and $state.version -match '^2\.'; if ($isRunning) { break } } catch { }
        if ($process.HasExited) { break }
    }
    if (-not $isRunning) { throw 'Server did not start. Read .local/server.stderr.log, or choose a different port with -Port.' }
}
Write-Host "Video Desk: $url"
if (-not $NoBrowser) {
    $edgePath = Join-Path ${env:ProgramFiles(x86)} 'Microsoft/Edge/Application/msedge.exe'
    if (Test-Path -LiteralPath $edgePath) { Start-Process -FilePath $edgePath -ArgumentList @('--new-window', $url) -WindowStyle Normal }
    else { Start-Process $url }
}
