param([string]$CrawlerPython = '')
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot

function Run-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Executable" }
}

if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    Write-Host 'Creating the main Python environment...'
    Run-Checked -Executable 'py' -Arguments @('-3', '-m', 'venv', '.venv')
}
$mainPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
$mainRequirements = if (Test-Path 'requirements-lock.txt') { 'requirements-lock.txt' } else { 'requirements.txt' }
Run-Checked -Executable $mainPython -Arguments @('-m', 'pip', 'install', '-r', $mainRequirements)
if (-not (Test-Path -LiteralPath 'runtime-tools/ffprobe.exe')) {
    Run-Checked -Executable $mainPython -Arguments @('scripts/prepare_merge.py')
}

$upstreamCommit = '8f583999006d5b692272c608440e193ca282e73b'
$upstreamPath = Join-Path $projectRoot 'third_party/MediaCrawler'
if (-not (Test-Path -LiteralPath (Join-Path $upstreamPath '.git'))) {
    New-Item -ItemType Directory -Path $upstreamPath -Force | Out-Null
    Run-Checked -Executable 'git' -Arguments @('-C', $upstreamPath, 'init')
    Run-Checked -Executable 'git' -Arguments @('-C', $upstreamPath, 'remote', 'add', 'origin', 'https://github.com/NanmiCoder/MediaCrawler.git')
    Run-Checked -Executable 'git' -Arguments @('-C', $upstreamPath, 'fetch', '--depth', '1', 'origin', $upstreamCommit)
    Run-Checked -Executable 'git' -Arguments @('-C', $upstreamPath, 'checkout', '--detach', 'FETCH_HEAD')
} else {
    $currentCommit = & git -C $upstreamPath rev-parse HEAD
    if ($currentCommit.Trim() -ne $upstreamCommit) {
        throw 'Existing MediaCrawler checkout differs from the pinned version. Preserve your changes and select the documented version before setup.'
    }
}

if (-not (Test-Path -LiteralPath '.local/mediacrawler-venv/Scripts/python.exe')) {
    if (-not $CrawlerPython) {
        $bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
        if (Test-Path -LiteralPath $bundledPython) { $CrawlerPython = $bundledPython }
    }
    if ($CrawlerPython) { Run-Checked -Executable $CrawlerPython -Arguments @('-m', 'venv', '.local/mediacrawler-venv') }
    else { Run-Checked -Executable 'py' -Arguments @('-3.12', '-m', 'venv', '.local/mediacrawler-venv') }
}
$enginePython = Join-Path $projectRoot '.local/mediacrawler-venv/Scripts/python.exe'
$engineRequirements = if (Test-Path 'integrations/mediacrawler-requirements-lock.txt') { 'integrations/mediacrawler-requirements-lock.txt' } else { 'third_party/MediaCrawler/requirements.txt' }
Run-Checked -Executable $enginePython -Arguments @('-m', 'pip', 'install', '-r', $engineRequirements)
Run-Checked -Executable $enginePython -Arguments @('integrations/mediacrawler_bridge.py', '--check')
@{commit=$upstreamCommit; ready=$true} | ConvertTo-Json | Set-Content -LiteralPath '.local/mediacrawler-ready.json' -Encoding UTF8
Write-Host 'Both engines are ready. Run start.ps1 or double-click start.cmd.'

