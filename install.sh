#!/bin/bash

# DCS-BIOS TUI Direct Installation Script
# This script can be run directly with curl to install DCS-BIOS TUI

set -e  # Exit immediately if a command exits with a non-zero status

echo "Starting DCS-BIOS TUI direct installation..."

# Configuration
TEMP_DIR=$(mktemp -d)
INSTALL_DIR="/home/pi/DCS-BIOS-TUI"
SERVICE_USER="pi"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [[ $EUID -eq 0 ]]; then
    error "This script should not be run as root. Please run as a regular user."
    exit 1
fi

# Check if user is pi or has sudo access
if [[ "$USER" != "pi" ]] && ! sudo -n true 2>/dev/null; then
    error "You must be 'pi' user or have passwordless sudo access to run this script."
    exit 1
fi

# Download the latest files from GitHub
log "Downloading latest DCS-BIOS TUI files..."
cd "$TEMP_DIR"
curl -sSL https://raw.githubusercontent.com/Biggus22/DCS-BIOS-TUI/main/dcsbios_tui.py -o dcsbios_tui.py
curl -sSL https://raw.githubusercontent.com/Biggus22/DCS-BIOS-TUI/main/dcsbios_daemon.py -o dcsbios_daemon.py
curl -sSL https://raw.githubusercontent.com/Biggus22/DCS-BIOS-TUI/main/requirements.txt -o requirements.txt
curl -sSL https://raw.githubusercontent.com/Biggus22/DCS-BIOS-TUI/main/dcsbios-tui.service -o dcsbios-tui.service

# Verify that all files were downloaded
REQUIRED_FILES=("dcsbios_tui.py" "dcsbios_daemon.py" "requirements.txt" "dcsbios-tui.service")
for file in "${REQUIRED_FILES[@]}"; do
    if [[ ! -f "$file" ]]; then
        error "Failed to download $file"
        exit 1
    fi
done

log "All files downloaded successfully!"

# Function to install dependencies
install_dependencies() {
    log "Installing Python dependencies..."

    # Check if pip is installed
    if ! command -v pip &> /dev/null && ! command -v pip3 &> /dev/null; then
        log "Installing pip..."
        sudo apt update
        sudo apt install -y python3-pip
    fi

    # Try installing via apt first (system package), then pip as fallback
    if ! sudo apt install -y python3-serial 2>/dev/null; then
        log "System package not available, using pip..."

        # Install Python requirements
        if [[ -f "requirements.txt" ]]; then
            log "Installing Python requirements..."
            # Try with --break-system-packages first (newer systems)
            if pip3 install --break-system-packages -r requirements.txt; then
                log "Requirements installed successfully"
            else
                # If that fails, try without the flag (older systems)
                log "Falling back to pip without --break-system-packages flag..."
                pip3 install -r requirements.txt
            fi
        else
            error "requirements.txt not found!"
            exit 1
        fi
    else
        log "python3-serial installed via apt"
    fi
}

# Function to setup directories
setup_directories() {
    log "Creating installation directories..."
    
    # Create installation directory
    mkdir -p "$INSTALL_DIR"
    
    # Copy necessary files to installation directory
    cp -f "$TEMP_DIR/dcsbios_tui.py" "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR/dcsbios_tui.py"  # Make the script executable
    cp -f "$TEMP_DIR/dcsbios_daemon.py" "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR/dcsbios_daemon.py"  # Make the daemon script executable
    cp -f "$TEMP_DIR/requirements.txt" "$INSTALL_DIR/" 2>/dev/null || true
    cp -f "$TEMP_DIR/dcsbios-tui.service" "/tmp/dcsbios-tui.service"
    
    # Set proper ownership
    sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
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
    sudo systemctl status "dcsbios-tui.service" --no-pager -l
}

# Add user to dialout group
add_to_dialout() {
    log "Adding user to dialout group for serial port access..."
    sudo usermod -a -G dialout "$SERVICE_USER"
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
    log "To use the TUI interface, connect via SSH and run:"
    echo "  python3 /home/pi/DCS-BIOS-TUI/dcsbios_tui.py"
    echo
    log "The service will automatically start on boot."
    echo
}

# Main execution
main() {
    log "Starting DCS-BIOS TUI direct installation..."
    
    install_dependencies
    add_to_dialout
    setup_directories
    install_service
    start_service
    show_status
    show_summary
    
    log "Installation completed. The DCS-BIOS service is now running!"
    log "Don't forget to reboot your system to ensure all changes take effect."
}

# Execute main function
main

# Cleanup
rm -rf "$TEMP_DIR"

log "Installation process finished!"