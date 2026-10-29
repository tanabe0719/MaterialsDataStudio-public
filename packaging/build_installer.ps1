param(
    [Parameter(Mandatory=$true)][string]$PackDirectory,
    [Parameter(Mandatory=$true)][string]$Version,
    [string]$PythonExecutable = "python",
    [switch]$RequireSignature
)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Invalid application version." }
$Config = Get-Content -LiteralPath (Join-Path $PSScriptRoot "velopack.json") -Raw | ConvertFrom-Json
$SourceExecutable = Join-Path $PackDirectory $Config.mainExe
if (-not (Test-Path -LiteralPath $SourceExecutable)) { throw "The frozen application was not found." }
if ((Get-Item -LiteralPath $SourceExecutable).VersionInfo.ProductVersion -ne $Version) { throw "Frozen application version mismatch." }
$VpkDll = & $PythonExecutable (Join-Path $ProjectRoot "scripts/fetch_velopack.py")
if ($LASTEXITCODE -ne 0) { throw "Could not restore the pinned Velopack CLI." }
$InternalOutput = Join-Path $ProjectRoot ".test-tmp/velopack-v$Version"
$PublicOutput = Join-Path $ProjectRoot "release_assets"
New-Item -ItemType Directory -Force -Path $InternalOutput, $PublicOutput | Out-Null
$Arguments = @($VpkDll, "--skip-updates", "--yes", "pack", "--packId", $Config.packId,
    "--packVersion", $Version, "--packDir", $PackDirectory, "--mainExe", $Config.mainExe,
    "--packTitle", $Config.packTitle, "--packAuthors", "Materials Data Studio contributors",
    "--runtime", $Config.runtime, "--channel", $Config.channel, "--delta", "None",
    "--noPortable", "--shortcuts", "Desktop,StartMenuRoot",
    "--icon", (Join-Path $ProjectRoot "resources/icons/materials-data-studio.ico"),
    "--outputDir", $InternalOutput)
& dotnet @Arguments
if ($LASTEXITCODE -ne 0) { throw "Velopack packaging failed." }
$ProducedFiles = @(Get-ChildItem -LiteralPath $InternalOutput -Filter "$($Config.packId)*Setup.exe")
if ($ProducedFiles.Count -ne 1) { throw "Expected exactly one Velopack Setup.exe." }
$Produced = $ProducedFiles[0].FullName
$Setup = Join-Path $PublicOutput "MaterialsDataStudio-v$Version-Setup.exe"
Copy-Item -LiteralPath $Produced -Destination $Setup
if ($RequireSignature -and (Get-AuthenticodeSignature -LiteralPath $Setup).Status -ne "Valid") {
    throw "The installer must be signed before a signed release can be published."
}
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Setup).Hash.ToLowerInvariant()
[IO.File]::WriteAllText("$Setup.sha256", "$Hash  $([IO.Path]::GetFileName($Setup))`n", [Text.UTF8Encoding]::new($false))
@{sha256=$Hash; authenticode=(Get-AuthenticodeSignature -LiteralPath $Setup).Status.ToString()} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PublicOutput "signature.json") -Encoding UTF8
# Publish a complete full-package feed now; acquisition inside MDS remains off.
# Keep the original package filename referenced by the Velopack feed.
foreach ($UpdateAsset in @("$($Config.packId)-$Version-full.nupkg", "releases.$($Config.channel).json")) {
    Copy-Item -LiteralPath (Join-Path $InternalOutput $UpdateAsset) -Destination (Join-Path $PublicOutput $UpdateAsset)
}
Write-Output "Windows installer completed: $Setup"
