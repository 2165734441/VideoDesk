$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pidFile = Join-Path $projectRoot '.local/server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host 'No managed server is recorded.'; exit }
$serverProcessId = [int](Get-Content -LiteralPath $pidFile)
$info = Get-CimInstance Win32_Process -Filter "ProcessId=$serverProcessId" -ErrorAction SilentlyContinue
$expectedSource = Join-Path $projectRoot 'app.py'
if ($info -and $info.CommandLine -and $info.CommandLine.Contains($expectedSource)) {
    # This tree belongs to this tool; never kill other Python or browser processes.
    & taskkill /PID $serverProcessId /T /F
} elseif ($info) { throw 'Recorded process does not match this workspace. It was not stopped.' }
Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
