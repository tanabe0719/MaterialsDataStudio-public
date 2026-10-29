param(
    [switch]$Package,
    [switch]$PortableZip,
    [switch]$SkipValidation,
    [switch]$Incremental,
    [switch]$RequireSignature
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install uv and run this script again."
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PreviousTaskTemp = $env:TEMP
$PreviousTaskTmp = $env:TMP
$PreviousProjectEnvironment = $env:UV_PROJECT_ENVIRONMENT
$PreviousUvLinkMode = $env:UV_LINK_MODE
$PreviousQtPlatform = $env:QT_QPA_PLATFORM
$PreviousBuildSearchPath = $env:PATH
$PreviousMdsSettingsDirectory = $env:MDS_SETTINGS_DIRECTORY
$PreviousMdsRecoveryDirectory = $env:MDS_RECOVERY_DIRECTORY
$PreviousSmokeFaultLog = $env:MDS_SMOKE_FAULT_LOG
Push-Location $ProjectRoot
try {
    $VersionText = Get-Content -LiteralPath "src/materials_data_studio/version.py" -Raw
    if ($VersionText -notmatch '__version__\s*=\s*"([0-9]+)\.([0-9]+)\.([0-9]+)"') {
        throw "Could not determine the application version."
    }
    $VersionTag = "v$($Matches[1])$($Matches[2])$($Matches[3])"
    $SemanticVersion = "$($Matches[1]).$($Matches[2]).$($Matches[3])"
    $BuildDirectory = Join-Path $ProjectRoot "build_$VersionTag"
    $DistDirectory = Join-Path $ProjectRoot "dist_$VersionTag"
    $BuildEnvironment = Join-Path $ProjectRoot ".build-envs\$VersionTag"
    $TaskTempDirectory = [System.IO.Path]::GetFullPath(
        (Join-Path $ProjectRoot ".test-tmp\build_$VersionTag")
    )
    $ResolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
    if (-not $TaskTempDirectory.StartsWith(
        $ResolvedProjectRoot + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "The build temporary directory resolved outside the project root."
    }
    New-Item -ItemType Directory -Force -Path $TaskTempDirectory | Out-Null
    $env:TEMP = $TaskTempDirectory
    $env:TMP = $TaskTempDirectory
    $env:UV_PROJECT_ENVIRONMENT = $BuildEnvironment
    # OneDrive files cannot always participate in hardlinks to the user-level
    # uv cache. Copy mode is deterministic across local and cloud-backed roots.
    $env:UV_LINK_MODE = "copy"
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:MDS_SETTINGS_DIRECTORY = Join-Path $TaskTempDirectory "settings"
    $env:MDS_RECOVERY_DIRECTORY = Join-Path $TaskTempDirectory "recovery"
    $env:MDS_SMOKE_FAULT_LOG = Join-Path $TaskTempDirectory "frozen-smoke-fault.log"
    # Qt uses Windows' unversioned ICU API. A PDF tool on PATH may also ship
    # icuuc.dll, but with version-suffixed exports. Resolve system DLLs first
    # during dependency collection; restore the caller's PATH in finally.
    $env:PATH = (Join-Path $env:SystemRoot "System32") + ";" + $PreviousBuildSearchPath

    uv sync --python 3.12 --frozen --extra dev
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE." }
    uv run python scripts/prepare_corresponding_sources.py
    if ($LASTEXITCODE -ne 0) { throw "Corresponding-source verification failed." }
    uv run python -m scripts.collect_licenses
    if ($LASTEXITCODE -ne 0) { throw "License collection failed." }
    if (-not $SkipValidation) {
    uv run pytest `
        --basetemp (Join-Path $TaskTempDirectory "pytest") `
        -o "cache_dir=$(Join-Path $TaskTempDirectory 'pytest-cache')" `
        --cov=materials_data_studio `
        --cov-report=term-missing `
        --cov-fail-under=80
    if ($LASTEXITCODE -ne 0) { throw "pytest failed with exit code $LASTEXITCODE." }
    uv run ruff check .
    if ($LASTEXITCODE -ne 0) { throw "ruff failed with exit code $LASTEXITCODE." }
    uv run pyright
    if ($LASTEXITCODE -ne 0) { throw "pyright failed with exit code $LASTEXITCODE." }
    uv run pyright --project pyright-tests.json
    if ($LASTEXITCODE -ne 0) { throw "test pyright failed with exit code $LASTEXITCODE." }
    }
    $PyInstallerArguments = @('-m', 'PyInstaller', '--noconfirm', '--workpath', $BuildDirectory,
        '--distpath', $DistDirectory)
    if (-not $Incremental) { $PyInstallerArguments += '--clean' }
    $PyInstallerArguments += 'packaging/materials_data_studio.spec'
    & (Join-Path $BuildEnvironment 'Scripts/python.exe') @PyInstallerArguments
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }

    $OutputDirectory = Join-Path $DistDirectory "MaterialsDataStudio"
    if (-not (Test-Path -LiteralPath $OutputDirectory)) {
        throw "PyInstaller did not create the expected output directory."
    }
    $Executable = Join-Path $OutputDirectory "MaterialsDataStudio.exe"
    $ExecutableVersion = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($Executable)
    if ($ExecutableVersion.ProductVersion -ne $SemanticVersion) {
        throw "The executable ProductVersion '$($ExecutableVersion.ProductVersion)' does not match $SemanticVersion."
    }
    $SmokeReport = Join-Path $TaskTempDirectory "frozen-smoke.txt"
    Remove-Item -LiteralPath $SmokeReport -Force -ErrorAction SilentlyContinue
    $SmokeProcess = Start-Process `
        -FilePath $Executable `
        -ArgumentList @("--packaging-smoke-test", ('"{0}"' -f $SmokeReport)) `
        -WindowStyle Hidden `
        -PassThru
    if (-not $SmokeProcess.WaitForExit(300000)) {
        Stop-Process -Id $SmokeProcess.Id -ErrorAction SilentlyContinue
        throw "Frozen dependency smoke test timed out before writing its report. Check startup DLL dependencies."
    }
    $SmokeProcess.Refresh()
    if ($SmokeProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $SmokeReport)) {
        throw "Frozen dependency smoke test failed with exit code $($SmokeProcess.ExitCode)."
    }
    $SmokeResult = Get-Content -LiteralPath $SmokeReport -Raw
    if ($SmokeResult -notmatch '^OK\r?\n') {
        throw "Frozen dependency smoke test failed:`n$SmokeResult"
    }
    Write-Host "Frozen dependency smoke test passed.`n$SmokeResult"

    $SigningCertificate = $env:MDS_SIGN_PFX_PATH
    $SigningPassword = $env:MDS_SIGN_PFX_PASSWORD
    $SigningRequested = -not [string]::IsNullOrWhiteSpace($SigningCertificate)
    if ($SigningRequested) {
        if (-not (Test-Path -LiteralPath $SigningCertificate)) {
            throw "MDS_SIGN_PFX_PATH does not identify an existing certificate file."
        }
        $SignTool = Get-Command signtool.exe -ErrorAction SilentlyContinue
        if ($null -eq $SignTool) {
            $KitsRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
            $SignTool = Get-ChildItem -LiteralPath $KitsRoot -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
                Sort-Object FullName -Descending |
                Select-Object -First 1
        }
        if ($null -eq $SignTool) {
            throw "signtool.exe is required to sign the release executable."
        }
        $TimestampUrl = if ([string]::IsNullOrWhiteSpace($env:MDS_TIMESTAMP_URL)) {
            "http://timestamp.digicert.com"
        } else {
            $env:MDS_TIMESTAMP_URL
        }
        $SignToolPath = if ($SignTool.PSObject.Properties.Name -contains "Source") {
            $SignTool.Source
        } else {
            $SignTool.FullName
        }
        & $SignToolPath sign /fd SHA256 /td SHA256 /tr $TimestampUrl /f $SigningCertificate /p $SigningPassword $Executable
        if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed with exit code $LASTEXITCODE." }
    } elseif ($RequireSignature) {
        throw "A release signature is required. Configure MDS_SIGN_PFX_PATH and MDS_SIGN_PFX_PASSWORD."
    }
    $Signature = Get-AuthenticodeSignature -LiteralPath $Executable
    if ($SigningRequested -and $Signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        throw "The executable signature is not valid: $($Signature.Status)."
    }

    if ($PortableZip) {
        $ReleaseDirectory = Join-Path $ProjectRoot "release_assets"
        New-Item -ItemType Directory -Force -Path $ReleaseDirectory | Out-Null
        $ArchiveName = "MaterialsDataStudio-v$SemanticVersion-windows-x64.zip"
        $ArchivePath = Join-Path $ReleaseDirectory $ArchiveName
        $ChecksumPath = "$ArchivePath.sha256"
        foreach ($GeneratedPath in @($ArchivePath, $ChecksumPath)) {
            $ResolvedParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $GeneratedPath))
            if (-not $ResolvedParent.StartsWith(
                $ResolvedProjectRoot + [System.IO.Path]::DirectorySeparatorChar,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
                throw "Release artifact path resolved outside the project root."
            }
            Remove-Item -LiteralPath $GeneratedPath -Force -ErrorAction SilentlyContinue
        }
        # Antivirus and cloud-sync providers can briefly retain a PyInstaller
        # output immediately after COLLECT. Retry only this exact validated
        # archive operation instead of requiring a second complete build.
        $ArchiveCreated = $false
        for ($Attempt = 1; $Attempt -le 6; $Attempt++) {
            try {
                Compress-Archive -LiteralPath $OutputDirectory -DestinationPath $ArchivePath -CompressionLevel Optimal
                $ArchiveCreated = $true
                break
            } catch {
                Remove-Item -LiteralPath $ArchivePath -Force -ErrorAction SilentlyContinue
                if ($Attempt -eq 6) { throw }
                Start-Sleep -Milliseconds 750
            }
        }
        if (-not $ArchiveCreated) { throw "Release ZIP was not created." }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $Archive = [System.IO.Compression.ZipFile]::OpenRead($ArchivePath)
        try {
            $RequiredEntry = "MaterialsDataStudio/MaterialsDataStudio.exe"
            $NormalizedEntries = $Archive.Entries.FullName | ForEach-Object { $_.Replace('\', '/') }
            if ($NormalizedEntries -notcontains $RequiredEntry) {
                throw "Release ZIP is missing $RequiredEntry."
            }
        } finally {
            $Archive.Dispose()
        }
        $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ArchivePath).Hash.ToLowerInvariant()
        [System.IO.File]::WriteAllText(
            $ChecksumPath,
            "$Hash  $ArchiveName`n",
            [System.Text.UTF8Encoding]::new($false)
        )
        Write-Host "Windows package completed: $ArchivePath"
    }
    if ($Package) {
        & (Join-Path $PSScriptRoot "build_installer.ps1") -PackDirectory $OutputDirectory `
            -Version $SemanticVersion -PythonExecutable (Join-Path $BuildEnvironment "Scripts/python.exe") `
            -RequireSignature:$RequireSignature
        & (Join-Path $BuildEnvironment "Scripts/python.exe") (Join-Path $ProjectRoot "scripts/verify_installer.py")
        if ($LASTEXITCODE -ne 0) { throw "Installer payload verification failed." }
    }
    Write-Host "Windows build completed: $OutputDirectory"
}
finally {
    $env:TEMP = $PreviousTaskTemp
    $env:TMP = $PreviousTaskTmp
    $env:UV_PROJECT_ENVIRONMENT = $PreviousProjectEnvironment
    $env:UV_LINK_MODE = $PreviousUvLinkMode
    $env:QT_QPA_PLATFORM = $PreviousQtPlatform
    $env:PATH = $PreviousBuildSearchPath
    $env:MDS_SETTINGS_DIRECTORY = $PreviousMdsSettingsDirectory
    $env:MDS_RECOVERY_DIRECTORY = $PreviousMdsRecoveryDirectory
    $env:MDS_SMOKE_FAULT_LOG = $PreviousSmokeFaultLog
    Pop-Location
}
