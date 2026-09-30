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
    if (Test-Path -LiteralPath $ExternalTool5 -PathType Container) {
        # Source paths are loaded directly, without installing or modifying Tool5.
        $env:TOOL1_TOOL5_PATH = $ExternalTool5
    } else {
        Invoke-Checked $VenvPython @("-m", "pip", "install", $ExternalTool5)
    }
}
Invoke-Checked $VenvPython @("-m", "pip", "install", "-e", "$ProjectRoot[dashboard,benchmark]")
Invoke-Checked $VenvPython @("-c", "from acdcpf_pyflow_backend._bootstrap import backend_info; print(backend_info())")
if (-not $SkipSolverDownload) {
    $Idaes = Join-Path $ProjectRoot ".venv\Scripts\idaes.exe"
    Invoke-Checked $Idaes @("get-extensions")
}
Invoke-Checked $VenvPython @("-m", "acdcpf_opf.install_check", "--solve")
Write-Host "Installation verified. Start the dashboard with Tool1_Dashboard.bat."
