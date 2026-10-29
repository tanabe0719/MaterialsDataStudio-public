param([string]$PythonExecutable = "python")
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
& $PythonExecutable (Join-Path $ProjectRoot "scripts/publish_release.py") publish
if ($LASTEXITCODE -ne 0) { throw "Publication verification failed; existing releases were not overwritten." }
