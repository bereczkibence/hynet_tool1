param(
    [string]$Python = "python",
    [string]$Tool5Path = "",
    [switch]$SkipSolverDownload
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" }
}


if (-not (Test-Path -LiteralPath $VenvPython)) {
    Invoke-Checked $Python @("-m", "venv", (Join-Path $ProjectRoot ".venv"))
}
Invoke-Checked $VenvPython @("-m", "pip", "install", "--upgrade", "pip")
if ($Tool5Path) {
    $ExternalTool5 = (Resolve-Path -LiteralPath $Tool5Path -ErrorAction Stop).Path
    Invoke-Checked $VenvPython @("-m", "pip", "install", $ExternalTool5)
}
& $VenvPython -c "from importlib.metadata import version; import acdcpf; assert version('acdcpf') == '0.2.0+tool5.1'; assert acdcpf.capabilities()['api_version'].split('.')[0] == '1'"
if ($LASTEXITCODE -ne 0) {
    throw 'Compatible Tool5 is required separately. Run this installer with -Tool5Path pointing to its wheel or source checkout. See README.md.'
}
Invoke-Checked $VenvPython @("-m", "pip", "install", "$ProjectRoot[dashboard,benchmark]")
if (-not $SkipSolverDownload) {
    $Idaes = Join-Path $ProjectRoot ".venv\Scripts\idaes.exe"
    Invoke-Checked $Idaes @("get-extensions")
}
Invoke-Checked $VenvPython @("-m", "acdcpf_opf.install_check", "--solve")
Write-Host "Installation verified. Start the dashboard with Tool1_Dashboard.bat."
