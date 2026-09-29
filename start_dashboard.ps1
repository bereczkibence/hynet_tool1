$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"

if (Test-Path $VenvPython) {
    & $VenvPython -m acdcpf_opf.start_dashboard @args
} else {
    python -m acdcpf_opf.start_dashboard @args
}
exit $LASTEXITCODE
