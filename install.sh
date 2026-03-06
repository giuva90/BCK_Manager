#!/bin/bash
# ============================================================================
# BCK Manager - Installer / Updater
# ============================================================================
# Run this script as root on your Debian/Ubuntu server to install or update
# BCK Manager. When an existing installation is detected the script will
# update all application files and optionally overwrite config.yaml.
#
# Usage:
#   sudo bash install.sh
# ============================================================================

set -e

APP_DIR="/opt/bck_manager"
LOG_DIR="/var/log"
LOG_FILE="${LOG_DIR}/bck_manager.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect whether this is a fresh install or an update
if [ -d "${APP_DIR}" ] && [ -f "${APP_DIR}/bck_manager.py" ]; then
    IS_UPDATE=true
else
    IS_UPDATE=false
fi

if [ "${IS_UPDATE}" = true ]; then
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║              BCK Manager - Update                        ║"
    echo "╚══════════════════════════════════════════════════════════╝"
else
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║              BCK Manager - Installation                  ║"
    echo "╚══════════════════════════════════════════════════════════╝"
fi
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
echo "[2/5] Preparing application directory: ${APP_DIR}"
mkdir -p "${APP_DIR}"

# Copy application files
echo "[3/5] Copying application files..."
cp "${SCRIPT_DIR}/bck_manager.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/config_loader.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/s3_client.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/backup.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/restore.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/retention.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/docker_utils.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/app_logger.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/utils.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/encryption.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/requirements.txt" "${APP_DIR}/"
echo "       ✓ Application files updated."

# Handle config.yaml
if [ ! -f "${APP_DIR}/config.yaml" ]; then
    # Fresh install: copy the example config automatically
    cp "${SCRIPT_DIR}/config.yaml.example" "${APP_DIR}/config.yaml"
    echo "       ✓ config.yaml created from example. EDIT IT with your actual parameters!"
else
    # Existing config found: ask the user whether to overwrite (default: N)
    echo ""
    echo "  ┌─────────────────────────────────────────────────────┐"
    echo "  │  An existing config.yaml was found in ${APP_DIR}."
    echo "  │  Overwriting it will ERASE your current credentials."
    echo "  └─────────────────────────────────────────────────────┘"
    read -r -p "  Overwrite config.yaml with the example template? [y/N]: " OVERWRITE_CONFIG
    OVERWRITE_CONFIG="${OVERWRITE_CONFIG:-N}"
    if [[ "${OVERWRITE_CONFIG}" =~ ^[Yy]$ ]]; then
        # Backup the existing config before overwriting
        BACKUP_CONFIG="${APP_DIR}/config.yaml.bak.$(date +%Y%m%d_%H%M%S)"
        cp "${APP_DIR}/config.yaml" "${BACKUP_CONFIG}"
        echo "       ✓ Existing config backed up to: ${BACKUP_CONFIG}"
        cp "${SCRIPT_DIR}/config.yaml.example" "${APP_DIR}/config.yaml"
        echo "       ✓ config.yaml replaced with example template."
    else
        echo "       ⚠ config.yaml kept unchanged."
    fi
    echo ""
fi

# Create / update virtual environment and install Python deps
echo "[4/5] Updating virtual environment and Python dependencies..."
python3 -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${APP_DIR}/venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"
echo "       ✓ Python dependencies installed."

# Create / update convenience wrapper script
echo "[5/5] Updating launcher script..."

cat > /usr/local/bin/bck-manager << 'WRAPPER'
#!/bin/bash
# BCK Manager launcher
cd /opt/bck_manager
/opt/bck_manager/venv/bin/python3 /opt/bck_manager/bck_manager.py "$@"
WRAPPER

chmod +x /usr/local/bin/bck-manager

# Create log file if it does not exist yet
if [ ! -f "${LOG_FILE}" ]; then
    touch "${LOG_FILE}"
    chmod 640 "${LOG_FILE}"
fi

# Print completion banner
if [ "${IS_UPDATE}" = true ]; then
    COMPLETION_TITLE="Update complete!"
else
    COMPLETION_TITLE="Installation complete!"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  ${COMPLETION_TITLE}                              ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║                                                          ║"
echo "║  App directory : /opt/bck_manager                        ║"
echo "║  Config file   : /opt/bck_manager/config.yaml            ║"
echo "║  Log file      : /var/log/bck_manager.log                ║"
echo "║                                                          ║"
echo "║  USAGE:                                                  ║"
echo "║    sudo bck-manager              # Interactive mode      ║"
echo "║    sudo bck-manager --run-all    # Run all backups       ║"
echo "║    sudo bck-manager --run-job X  # Single job            ║"
echo "║    sudo bck-manager --list-jobs  # List jobs             ║"
echo "║                                                          ║"
if [ "${IS_UPDATE}" = false ]; then
echo "║  NEXT STEPS:                                             ║"
echo "║  1. Edit /opt/bck_manager/config.yaml                    ║"
echo "║  2. Set your S3 credentials and paths to back up         ║"
echo "║  3. Run: sudo bck-manager                                ║"
echo "║                                                          ║"
fi
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
