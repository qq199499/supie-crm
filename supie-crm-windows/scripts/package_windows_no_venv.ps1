param(
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $ProjectRoot "dist\windows-no-venv"
}
else {
    $OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
}

$StageDir = Join-Path $OutputDir "package"
$ZipPath = Join-Path $OutputDir "supie-crm-windows-no-venv.zip"

$IncludeItems = @(
    "app.py",
    "requirements.txt",
    "README.md",
    ".env.example",
    "install_windows.cmd",
    "start_windows.cmd",
    "ops",
    "scripts",
    "static",
    "templates",
    "docs"
)

if (Test-Path $StageDir) {
    Remove-Item $StageDir -Recurse -Force
}

if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}

New-Item -ItemType Directory -Path $StageDir -Force | Out-Null

foreach ($item in $IncludeItems) {
    $sourcePath = Join-Path $ProjectRoot $item
    if (Test-Path $sourcePath) {
        Copy-Item $sourcePath -Destination $StageDir -Recurse -Force
    }
    else {
        Write-Warning "Missing item: $item"
    }
}

New-Item -ItemType Directory -Path (Join-Path $StageDir "logs") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $StageDir "uploads") -Force | Out-Null

Compress-Archive -Path (Join-Path $StageDir "*") -DestinationPath $ZipPath -Force

Write-Host "Created package: $ZipPath"
