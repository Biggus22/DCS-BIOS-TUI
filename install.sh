#!/bin/bash

# DCS-BIOS TUI Direct Installation Script
# Downloads the latest files from GitHub and installs DCS-BIOS TUI as a
# systemd service. Works on any Debian-based Linux host and user.
#
# Run directly:
#   bash install.sh
# or via curl:
#   curl -sSL https://raw.githubusercontent.com/Biggus22/DCS-BIOS-TUI/main/install.sh | bash

set -e  # Exit immediately if a command exits with a non-zero status

echo "Starting DCS-BIOS TUI direct installation..."

# Configuration
TEMP_DIR=$(mktemp -d)
INSTALL_DIR="$HOME/DCS-BIOS-TUI"
SERVICE_USER="$USER"
RAW_BASE="https://raw.githubusercontent.com/Biggus22/DCS-BIOS-TUI/main"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [[ $EUID -eq 0 ]]; then
    error "This script should not be run as root. Please run as a regular user."
    exit 1
fi

# Check that the user can use sudo
if ! sudo -n true 2>/dev/null; then
    error "This script needs sudo access to install packages and the systemd service."
    error "Run it as a user with sudo privileges."
    exit 1
fi

# Download the latest files from GitHub
log "Downloading latest DCS-BIOS TUI files..."
cd "$TEMP_DIR"
for file in dcsbios_tui.py dcsbios_daemon.py requirements.txt; do
    curl -fsSL "$RAW_BASE/$file" -o "$file" || {
        error "Failed to download $file from GitHub."
        exit 1
    }
done

# Verify that all files were downloaded
REQUIRED_FILES=("dcsbios_tui.py" "dcsbios_daemon.py" "requirements.txt")
for file in "${REQUIRED_FILES[@]}"; do
    if [[ ! -s "$file" ]]; then
        error "Missing or empty: $file"
        exit 1
    fi
done

log "All files downloaded successfully!"

# Function to install dependencies
install_dependencies() {
    log "Installing Python dependencies and creating virtual environment..."

    # Install required system packages
    sudo apt update
    sudo apt install -y python3 python3-pip python3-venv curl

    # Create Python virtual environment
    log "Creating Python virtual environment..."
    python3 -m venv "$INSTALL_DIR/venv"

    # Upgrade pip in virtual environment
    log "Upgrading pip in virtual environment..."
    "$INSTALL_DIR/venv/bin/pip" install --upgrade pip

    # Install Python requirements
    if [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
        log "Installing Python requirements in virtual environment..."
        "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
    else
        log "Installing default dependencies..."
        "$INSTALL_DIR/venv/bin/pip" install pyserial
    fi

    log "Virtual environment set up successfully"
}

# Function to setup directories
setup_directories() {
    log "Setting up installation directories..."

    # Create installation directory if it doesn't exist, otherwise backup config
    if [[ -d "$INSTALL_DIR" ]]; then
        log "Existing installation found, backing up config..."
        if [[ -f "$HOME/.dcsbios/config.json" ]]; then
            cp "$HOME/.dcsbios/config.json" "$HOME/.dcsbios/config.json.backup.$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
        fi
        # Stop service before updating
        if sudo systemctl is-active --quiet "dcsbios-tui.service"; then
            log "Stopping existing service..."
            sudo systemctl stop "dcsbios-tui.service"
        fi
    else
        log "Creating installation directory..."
        mkdir -p "$INSTALL_DIR"
    fi

    # Copy necessary files to installation directory
    cp -f "$TEMP_DIR/dcsbios_tui.py" "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR/dcsbios_tui.py"  # Make the script executable
    cp -f "$TEMP_DIR/dcsbios_daemon.py" "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR/dcsbios_daemon.py"  # Make the daemon script executable
    cp -f "$TEMP_DIR/requirements.txt" "$INSTALL_DIR/" 2>/dev/null || true

    # Generate the service file for this user and install location
    cat > "/tmp/dcsbios-tui.service" << EOF
[Unit]
Description=DCS-BIOS Controller Service (TUI daemon)
After=network.target
Wants=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/dcsbios_daemon.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
}

# Function to install the service
install_service() {
    log "Installing systemd service..."

    # Copy service file to system directory
    sudo cp "/tmp/dcsbios-tui.service" "/etc/systemd/system/dcsbios-tui.service"

    # Set proper permissions for the service file
    sudo chmod 644 "/etc/systemd/system/dcsbios-tui.service"

    # Reload systemd to recognize the new service
    sudo systemctl daemon-reload

    log "Service installed successfully!"
}

# Function to start and enable the service
start_service() {
    log "Starting and enabling the service..."

    # Enable the service to start on boot
    sudo systemctl enable "dcsbios-tui.service"

    # Start the service
    sudo systemctl start "dcsbios-tui.service"

    # Check the status
    if sudo systemctl is-active --quiet "dcsbios-tui.service"; then
        log "Service is running successfully!"
    else
        warn "Service may have failed to start. Check status with: sudo systemctl status dcsbios-tui.service"
    fi
}

# Function to show service status
show_status() {
    log "Service status:"
    sudo systemctl status "dcsbios-tui.service" --no-pager -l || true
}

# Add user to dialout group
add_to_dialout() {
    if groups "$SERVICE_USER" | grep -q '\bdialout\b'; then
        log "User $SERVICE_USER is already in the dialout group."
    else
        log "Adding user to dialout group for serial port access..."
        sudo usermod -a -G dialout "$SERVICE_USER"
        warn "Log out and back in for dialout group membership to take effect."
    fi
}

# Show installation summary
show_summary() {
    echo
    log "DCS-BIOS TUI Installation Complete!"
    echo
    log "Service management commands:"
    echo "  Start service:   sudo systemctl start dcsbios-tui.service"
    echo "  Stop service:    sudo systemctl stop dcsbios-tui.service"
    echo "  Restart service: sudo systemctl restart dcsbios-tui.service"
    echo "  Check status:    sudo systemctl status dcsbios-tui.service"
    echo "  View logs:       sudo journalctl -u dcsbios-tui.service -f"
    echo
    log "Live status (no login required): http://$(hostname -I | awk '{print $1}'):8080/api/status"
    echo
    log "To use the TUI interface, connect via SSH and run:"
    echo "  $INSTALL_DIR/venv/bin/python $INSTALL_DIR/dcsbios_tui.py"
    echo
    log "The service will automatically start on boot."
    echo
}

# Main execution
main() {
    install_dependencies
    setup_directories
    add_to_dialout
    install_service
    start_service
    show_status
    show_summary

    log "Installation/update completed. The DCS-BIOS service is now running!"
}

# Execute main function
main

# Cleanup
rm -rf "$TEMP_DIR"

log "Installation process finished!"
