param(
    [string]$Version = "current",
    [ValidateSet("all", "main", "graph")][string]$Manual = "all"
)
$ErrorActionPreference = "Stop"
$ManualRoot = Split-Path -Parent $PSScriptRoot
$PdfWork = Join-Path $ManualRoot "tmp/pdfs/$Version"
$PdfFinal = Join-Path $ManualRoot "output/pdf"
$Chrome = "C:/Program Files/Google/Chrome/Application/chrome.exe"
if (-not (Test-Path -LiteralPath $Chrome)) { throw "Chrome was not found; set the browser path before building PDFs." }
New-Item -ItemType Directory -Force -Path $PdfWork, $PdfFinal | Out-Null
$ManualStems = switch ($Manual) {
    "main" { @("Materials_Data_Studio_User_Manual") }
    "graph" { @("Materials_Data_Studio_Graph_Viewer_Manual") }
    default { @("Materials_Data_Studio_User_Manual", "Materials_Data_Studio_Graph_Viewer_Manual") }
}
foreach ($ManualStem in $ManualStems) {
foreach ($Language in @("EN", "JA")) {
    $Stem = if ($Language -eq "EN") { "${ManualStem}_EN" } else { $ManualStem }
    $HtmlInput = Join-Path $ManualRoot "dist_manual/$Stem.html"
    $PdfOutput = Join-Path $PdfWork "$Stem.pdf"
    $Profile = Join-Path $PdfWork "chrome-$Language-$([guid]::NewGuid().ToString('N'))"
    $Arguments = @("--headless=new", "--disable-gpu", "--disable-extensions", "--disable-background-networking", "--no-first-run", "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw", "--virtual-time-budget=10000", ('"--user-data-dir={0}"' -f $Profile), ('"--print-to-pdf={0}"' -f $PdfOutput), ('"{0}"' -f ([Uri]$HtmlInput).AbsoluteUri))
    $Process = Start-Process -FilePath $Chrome -ArgumentList $Arguments -WindowStyle Hidden -PassThru -Wait
    if ($Process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $PdfOutput)) { throw "Headless PDF build failed." }
    & pdfinfo $PdfOutput
    if ($LASTEXITCODE -ne 0) { throw "Generated PDF is invalid." }
    Copy-Item -LiteralPath $PdfOutput -Destination (Join-Path $PdfFinal "$Stem.pdf")
    Copy-Item -LiteralPath $PdfOutput -Destination (Join-Path $ManualRoot "dist_manual/$Stem.pdf")
    Write-Output "Generated $Language PDF: $PdfOutput"
}
}
