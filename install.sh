#!/bin/bash

# DCS-BIOS TUI Installation Script for Raspberry Pi
# This script installs the DCS-BIOS TUI as a systemd service

set -e  # Exit immediately if a command exits with a non-zero status

# Make this script executable if it isn't already
chmod +x "${BASH_SOURCE[0]}"


# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="dcsbios-tui"
SERVICE_FILE="dcsbios-tui.service"
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

# Check if user is pi or has sudo access
if [[ "$USER" != "pi" ]] && ! sudo -n true 2>/dev/null; then
    error "You must be 'pi' user or have passwordless sudo access to run this script."
    exit 1
fi

# Function to install dependencies
install_dependencies() {
    log "Installing Python dependencies..."
    
    # Check if pip is installed
    if ! command -v pip &> /dev/null && ! command -v pip3 &> /dev/null; then
        log "Installing pip..."
        sudo apt update
        sudo apt install -y python3-pip
    fi
    
    # Install Python requirements
    if [[ -f "requirements.txt" ]]; then
        log "Installing Python requirements..."
        pip3 install -r requirements.txt
    else
        warn "requirements.txt not found, installing pyserial..."
        pip3 install pyserial
    fi
}

# Function to setup directories
setup_directories() {
    log "Creating installation directories..."

    # Create installation directory
    mkdir -p "$INSTALL_DIR"

    # Copy necessary files to installation directory
    cp -f "$SCRIPT_DIR/dcsbios_tui.py" "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR/dcsbios_tui.py"  # Make the script executable
    cp -f "$SCRIPT_DIR/dcsbios_daemon.py" "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR/dcsbios_daemon.py"  # Make the daemon script executable
    cp -f "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/" 2>/dev/null || true
    cp -f "$SCRIPT_DIR/LICENSE" "$INSTALL_DIR/" 2>/dev/null || true
    cp -f "$SCRIPT_DIR/README.md" "$INSTALL_DIR/" 2>/dev/null || true

    # Add user to dialout group for serial port access
    log "Adding user to dialout group for serial port access..."
    sudo usermod -a -G dialout "$SERVICE_USER"

    # Set proper ownership
    sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
}

# Function to install the service
install_service() {
    log "Installing systemd service..."
    
    # Copy service file to system directory
    sudo cp "$SCRIPT_DIR/$SERVICE_FILE" "/etc/systemd/system/$SERVICE_FILE"
    
    # Set proper permissions for the service file
    sudo chmod 644 "/etc/systemd/system/$SERVICE_FILE"
    
    # Reload systemd to recognize the new service
    sudo systemctl daemon-reload
    
    log "Service installed successfully!"
}

# Function to start and enable the service
start_service() {
    log "Starting and enabling the service..."
    
    # Enable the service to start on boot
    sudo systemctl enable "$SERVICE_FILE"
    
    # Start the service
    sudo systemctl start "$SERVICE_FILE"
    
    # Check the status
    if sudo systemctl is-active --quiet "$SERVICE_FILE"; then
        log "Service is running successfully!"
    else
        warn "Service may have failed to start. Check status with: sudo systemctl status $SERVICE_FILE"
    fi
}

# Function to show service status
show_status() {
    log "Service status:"
    sudo systemctl status "$SERVICE_FILE" --no-pager -l
}

# Show installation summary
show_summary() {
    echo
    log "DCS-BIOS TUI Installation Complete!"
    echo
    log "Service management commands:"
    echo "  Start service:   sudo systemctl start $SERVICE_FILE"
    echo "  Stop service:    sudo systemctl stop $SERVICE_FILE"
    echo "  Restart service: sudo systemctl restart $SERVICE_FILE"
    echo "  Check status:    sudo systemctl status $SERVICE_FILE"
    echo "  View logs:       sudo journalctl -u $SERVICE_FILE -f"
    echo
    log "The service will automatically start on boot."
    echo
}

# Main execution
main() {
    log "Starting DCS-BIOS TUI installation..."
    
    install_dependencies
    setup_directories
    install_service
    start_service
    show_status
    show_summary
    
    log "Installation completed. The TUI is now running as a service!"
}

# Parse command line arguments
case "${1:-}" in
    --help|-h)
        echo "Usage: $0 [OPTIONS]"
        echo "Install DCS-BIOS TUI as a systemd service on Raspberry Pi"
        echo
        echo "Options:"
        echo "  --help, -h    Show this help message"
        echo "  --status      Show service status"
        echo "  --start       Start the service"
        echo "  --stop        Stop the service"
        echo "  --restart     Restart the service"
        echo "  --uninstall   Uninstall the service"
        exit 0
        ;;
    --status)
        if [[ -f "/etc/systemd/system/$SERVICE_FILE" ]]; then
            show_status
        else
            error "Service not installed"
            exit 1
        fi
        exit 0
        ;;
    --start)
        if [[ -f "/etc/systemd/system/$SERVICE_FILE" ]]; then
            sudo systemctl start "$SERVICE_FILE"
            log "Service started"
        else
            error "Service not installed"
            exit 1
        fi
        exit 0
        ;;
    --stop)
        if [[ -f "/etc/systemd/system/$SERVICE_FILE" ]]; then
            sudo systemctl stop "$SERVICE_FILE"
            log "Service stopped"
        else
            error "Service not installed"
            exit 1
        fi
        exit 0
        ;;
    --restart)
        if [[ -f "/etc/systemd/system/$SERVICE_FILE" ]]; then
            sudo systemctl restart "$SERVICE_FILE"
            log "Service restarted"
        else
            error "Service not installed"
            exit 1
        fi
        exit 0
        ;;
    --uninstall)
        log "Uninstalling DCS-BIOS TUI service..."
        if [[ -f "/etc/systemd/system/$SERVICE_FILE" ]]; then
            sudo systemctl stop "$SERVICE_FILE" 2>/dev/null || true
            sudo systemctl disable "$SERVICE_FILE" 2>/dev/null || true
            sudo rm -f "/etc/systemd/system/$SERVICE_FILE"
            sudo systemctl daemon-reload
            sudo systemctl reset-failed
            log "Service uninstalled"
        else
            error "Service not found"
        fi
        exit 0
        ;;
    "")
        main
        ;;
    *)
        error "Unknown option: $1"
        echo "Use --help for usage information."
        exit 1
        ;;
esac