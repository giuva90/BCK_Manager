#!/bin/bash
# ============================================================================
# BCK Manager - Installer
# ============================================================================
# Run this script as root on your Debian/Ubuntu server to install BCK Manager.
#
# Usage:
#   sudo bash install.sh
# ============================================================================

set -e

APP_DIR="/opt/bck_manager"
LOG_DIR="/var/log"
LOG_FILE="${LOG_DIR}/bck_manager.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║              BCK Manager - Installation                  ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "[ERROR] This script must be run as root."
    echo "        Use: sudo bash install.sh"
    exit 1
fi

# Install system dependencies
echo "[1/5] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv > /dev/null 2>&1
echo "       ✓ Python3 and pip installed."

# Create application directory
echo "[2/5] Creating application directory: ${APP_DIR}"
mkdir -p "${APP_DIR}"

# Copy application files
echo "[3/5] Copying application files..."
cp "${SCRIPT_DIR}/bck_manager.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/config_loader.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/s3_client.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/backup.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/restore.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/app_logger.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/utils.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/requirements.txt" "${APP_DIR}/"

# Copy config only if not already present (don't overwrite existing config)
if [ ! -f "${APP_DIR}/config.yaml" ]; then
    cp "${SCRIPT_DIR}/config.yaml" "${APP_DIR}/"
    echo "       ✓ config.yaml copied. EDIT IT with your actual parameters!"
else
    echo "       ⚠ config.yaml already present, not overwritten."
fi

echo "       ✓ Files copied to ${APP_DIR}."

# Create virtual environment and install Python deps
echo "[4/5] Creating virtual environment and installing Python dependencies..."
python3 -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${APP_DIR}/venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"
echo "       ✓ Python dependencies installed."

# Create convenience wrapper script
echo "[5/5] Creating launcher script..."

cat > /usr/local/bin/bck-manager << 'WRAPPER'
#!/bin/bash
# BCK Manager launcher
cd /opt/bck_manager
/opt/bck_manager/venv/bin/python3 /opt/bck_manager/bck_manager.py "$@"
WRAPPER

chmod +x /usr/local/bin/bck-manager

# Create log file
touch "${LOG_FILE}"
chmod 640 "${LOG_FILE}"

echo ""
echo "╔═════════════════════════════════════════════════════════╗"
echo "║              Installation complete!                     ║"
echo "╠═════════════════════════════════════════════════════════╣"
echo "║                                                         ║"
echo "║  App directory : /opt/bck_manager                       ║"
echo "║  Config file   : /opt/bck_manager/config.yaml           ║"
echo "║  Log file      : /var/log/bck_manager.log               ║"
echo "║                                                         ║"
echo "║  USAGE:                                                 ║"
echo "║    sudo bck-manager              # Interactive mode     ║"
echo "║    sudo bck-manager --run-all    # Run all backups      ║"
echo "║    sudo bck-manager --run-job X  # Single job           ║"
echo "║    sudo bck-manager --list-jobs  # List jobs            ║"
echo "║                                                         ║"
echo "║  NEXT STEPS:                                            ║"
echo "║  1. Edit /opt/bck_manager/config.yaml                   ║"
echo "║  2. Set your S3 credentials and paths to back up        ║"
echo "║  3. Run: sudo bck-manager                               ║"
echo "║                                                         ║"
echo "╚═════════════════════════════════════════════════════════╝"
echo ""
