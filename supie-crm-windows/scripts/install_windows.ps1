param(
    [switch]$ForceReinstall,
    [switch]$SkipServiceStart
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvDir = Join-Path $ProjectRoot ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$EnvFile = Join-Path $ProjectRoot ".env"
$EnvExample = Join-Path $ProjectRoot ".env.example"
$Requirements = Join-Path $ProjectRoot "requirements.txt"
$ServiceScript = Join-Path $ProjectRoot "ops\windows_service.py"
$ServiceName = "supie_crm"

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $PythonExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $PythonExe $($Arguments -join ' ')"
    }
}

Push-Location $ProjectRoot
try {
    if (-not (Test-Path $EnvFile)) {
        if (Test-Path $EnvExample) {
            Copy-Item $EnvExample $EnvFile -Force
            Write-Host "Created .env from .env.example. Please edit .env with real database settings before starting the service." -ForegroundColor Yellow
        }
        else {
            throw ".env and .env.example are both missing."
        }
    }

    if ($ForceReinstall -and (Test-Path $VenvDir)) {
        Remove-Item $VenvDir -Recurse -Force
    }

    if (-not (Test-Path $PythonExe)) {
        if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
            throw "Python is not available on PATH. Install Python first, then rerun this installer."
        }

        Write-Host "Creating virtual environment..." -ForegroundColor Cyan
        & python -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create virtual environment."
        }
    }

    New-Item -ItemType Directory -Path (Join-Path $ProjectRoot "logs") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $ProjectRoot "uploads") -Force | Out-Null

    Write-Host "Upgrading pip..." -ForegroundColor Cyan
    Invoke-Python -Arguments @("-m", "pip", "install", "--upgrade", "pip")

    Write-Host "Installing project dependencies..." -ForegroundColor Cyan
    Invoke-Python -Arguments @("-m", "pip", "install", "-r", $Requirements)

    $serviceInstalled = $false
    $existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($null -ne $existingService) {
        Write-Host "Existing service found. Reinstalling..." -ForegroundColor Yellow
        try {
            Invoke-Python -Arguments @($ServiceScript, "stop")
        }
        catch {
            Write-Warning "Service stop returned an error, continuing with reinstall."
        }

        try {
            Invoke-Python -Arguments @($ServiceScript, "remove")
        }
        catch {
            Write-Warning "Service remove returned an error, continuing with reinstall."
        }
    }

    Write-Host "Installing Windows service..." -ForegroundColor Cyan
    Invoke-Python -Arguments @($ServiceScript, "--startup", "auto", "install")
    $serviceInstalled = $true

    $envText = Get-Content $EnvFile -Raw
    $hasPlaceholderPassword = $envText -match '(?m)^\s*PG_PASSWORD\s*=\s*change_me\s*$'
    $shouldStartService = -not $SkipServiceStart -and -not $hasPlaceholderPassword

    if (-not $shouldStartService) {
        if ($hasPlaceholderPassword) {
            Write-Warning "The .env file still uses the placeholder database password. Edit .env before starting the service."
        }
        Write-Host "Installation finished. Start the service after database settings are ready." -ForegroundColor Green
        return
    }

    Write-Host "Starting Windows service..." -ForegroundColor Cyan
    Invoke-Python -Arguments @($ServiceScript, "start")

    Write-Host "Installation completed successfully." -ForegroundColor Green
    Write-Host "Service name: $ServiceName" -ForegroundColor Green
    Write-Host "Open http://127.0.0.1:3000 after login data and database are ready." -ForegroundColor Green
}
finally {
    Pop-Location
}
