# ============================================================================
# BCK Manager - Windows Installer / Updater (PowerShell)
# ============================================================================
# Run this script in an elevated PowerShell prompt to install or update
# BCK Manager on Windows.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File install.ps1
# ============================================================================

#Requires -RunAsAdministrator

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$APP_DIR = "$env:ProgramData\bck_manager"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path

# Detect whether this is a fresh install or an update
if ((Test-Path "$APP_DIR\bck_manager.py")) {
    $IsUpdate = $true
} else {
    $IsUpdate = $false
}

if ($IsUpdate) {
    Write-Host ""
    Write-Host "========================================================"
    Write-Host "              BCK Manager - Update"
    Write-Host "========================================================"
} else {
    Write-Host ""
    Write-Host "========================================================"
    Write-Host "              BCK Manager - Installation"
    Write-Host "========================================================"
}
Write-Host ""

# Check Python is installed
Write-Host "[1/4] Checking Python installation..."
try {
    $pythonVersion = & python --version 2>&1
    Write-Host "       OK: $pythonVersion"
} catch {
    Write-Host "[ERROR] Python is not installed or not in PATH."
    Write-Host "        Install Python 3.8+ from https://www.python.org/downloads/"
    Write-Host "        Make sure to check 'Add Python to PATH' during installation."
    exit 1
}

# Create application directory
Write-Host "[2/4] Preparing application directory: $APP_DIR"
if (-not (Test-Path $APP_DIR)) {
    New-Item -ItemType Directory -Path $APP_DIR -Force | Out-Null
}

# Copy application files
Write-Host "[3/4] Copying application files..."
$appFiles = @(
    "bck_manager.py",
    "config_loader.py",
    "s3_client.py",
    "backup.py",
    "restore.py",
    "retention.py",
    "docker_utils.py",
    "app_logger.py",
    "utils.py",
    "encryption.py",
    "notifier.py",
    "requirements.txt"
)

foreach ($file in $appFiles) {
    Copy-Item "$SCRIPT_DIR\$file" "$APP_DIR\" -Force
}
Write-Host "       OK: Application files updated."

# Handle config.yaml
if (-not (Test-Path "$APP_DIR\config.yaml")) {
    Copy-Item "$SCRIPT_DIR\config.yaml.example" "$APP_DIR\config.yaml"
    Write-Host "       OK: config.yaml created from example. EDIT IT with your actual parameters!"
} else {
    Write-Host ""
    Write-Host "  An existing config.yaml was found in $APP_DIR."
    Write-Host "  Overwriting it will ERASE your current credentials."
    $overwrite = Read-Host "  Overwrite config.yaml with the example template? [y/N]"
    if ($overwrite -match "^[Yy]$") {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        Copy-Item "$APP_DIR\config.yaml" "$APP_DIR\config.yaml.bak.$timestamp"
        Write-Host "       OK: Existing config backed up."
        Copy-Item "$SCRIPT_DIR\config.yaml.example" "$APP_DIR\config.yaml"
        Write-Host "       OK: config.yaml replaced with example template."
    } else {
        Write-Host "       config.yaml kept unchanged."
    }
    Write-Host ""
}

# Create / update virtual environment and install Python deps
Write-Host "[4/4] Updating virtual environment and Python dependencies..."
if (-not (Test-Path "$APP_DIR\venv")) {
    & python -m venv "$APP_DIR\venv"
}
& "$APP_DIR\venv\Scripts\pip.exe" install --quiet --upgrade pip
& "$APP_DIR\venv\Scripts\pip.exe" install --quiet -r "$APP_DIR\requirements.txt"
Write-Host "       OK: Python dependencies installed."

# Create a convenience batch file in the app directory
$batchContent = @"
@echo off
cd /d "$APP_DIR"
"$APP_DIR\venv\Scripts\python.exe" "$APP_DIR\bck_manager.py" %*
"@
Set-Content -Path "$APP_DIR\bck-manager.bat" -Value $batchContent -Encoding ASCII
Write-Host "       OK: Launcher script created at $APP_DIR\bck-manager.bat"

# Add to PATH if not already present
$currentPath = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($currentPath -notlike "*$APP_DIR*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$APP_DIR", "Machine")
    Write-Host "       OK: Added $APP_DIR to system PATH."
    Write-Host "           Open a NEW terminal for the PATH change to take effect."
} else {
    Write-Host "       OK: $APP_DIR is already in system PATH."
}

# Print completion banner
if ($IsUpdate) {
    $title = "Update complete!"
} else {
    $title = "Installation complete!"
}

Write-Host ""
Write-Host "========================================================"
Write-Host "  $title"
Write-Host "========================================================"
Write-Host ""
Write-Host "  App directory : $APP_DIR"
Write-Host "  Config file   : $APP_DIR\config.yaml"
Write-Host "  Log file      : $APP_DIR\bck_manager.log"
Write-Host ""
Write-Host "  USAGE:"
Write-Host "    bck-manager              # Interactive mode"
Write-Host "    bck-manager --run-all    # Run all backups"
Write-Host "    bck-manager --run-job X  # Single job"
Write-Host "    bck-manager --list-jobs  # List jobs"
Write-Host ""
if (-not $IsUpdate) {
    Write-Host "  NEXT STEPS:"
    Write-Host "  1. Edit $APP_DIR\config.yaml"
    Write-Host "  2. Set your S3 credentials and paths to back up"
    Write-Host "  3. Run: bck-manager"
    Write-Host ""
}
Write-Host "========================================================"
Write-Host ""
