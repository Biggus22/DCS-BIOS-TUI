# DCS-BIOS Controller Manager TUI

A Terminal User Interface (TUI) application for managing DCS-BIOS serial devices, designed for Raspberry Pi systems. This tool provides an interactive interface for configuring and controlling multiple serial devices used with DCS (Digital Combat Simulator).

## Features

- Interactive terminal-based user interface using curses
- Manage multiple DCS-BIOS serial devices simultaneously
- Enable/disable individual devices
- Configure serial port settings (baudrate, etc.)
- UDP multicast communication for DCS-BIOS protocol
- Real-time status monitoring of devices
- Device configuration persistence
- USB power control (Raspberry Pi specific)
- Scheduled reboot functionality
- Auto-start configuration

## Requirements

- Python 3.7+
- Raspberry Pi (for USB control functionality)
- Serial devices connected to the system (typically /dev/ttyACM* devices)
- DCS installed on another machine with DCS-BIOS configured

## Installation

### Easy Installation (Recommended)

For Raspberry Pi systems, use the provided installation script to set up the application as a systemd service:

1. Clone the repository:
   ```bash
   git clone https://github.com/Biggus22/DCS-BIOS-TUI.git
   cd DCS-BIOS-TUI
   ```

2. Run the installation script:
   ```bash
   chmod +x install.sh
   ./install.sh
   ```

The installation script will:
- Install required Python dependencies
- Set up the application as a systemd service
- Add the user to the `dialout` group for serial port access
- Start the service automatically
- Configure the service to start on boot

### Manual Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/DCS-BIOS-TUI.git
   cd DCS-BIOS-TUI
   ```

2. Install required Python packages:
   ```bash
   pip install pyserial
   ```

## Usage

### As a Service (After Installation)

The application will run automatically in the background as a service, managing your DCS-BIOS serial connections.
The TUI interface is run interactively when you need to configure or monitor your devices.

To interact with the TUI interface, SSH into your Raspberry Pi and run:
```bash
python3 /home/pi/DCS-BIOS-TUI/dcsbios_tui.py
```

The service handles the actual data forwarding between DCS and your serial devices in the background.

### Service Management

The installation script provides commands to manage the service:
- Start service: `sudo systemctl start dcsbios-tui.service`
- Stop service: `sudo systemctl stop dcsbios-tui.service`
- Restart service: `sudo systemctl restart dcsbios-tui.service`
- Check status: `sudo systemctl status dcsbios-tui.service`
- View logs: `sudo journalctl -u dcsbios-tui.service -f`

## Controls

- Arrow keys: Navigate menu items
- Enter: Select highlighted item
- Space: Toggle device enable/disable
- D: Delete selected device
- Q: Quit the application
- ESC: Cancel dialogs

## Configuration

Configuration is stored in `~/.dcsbios/config.json` and includes:
- List of configured devices with name, port, and baudrate
- DCS-PC IP address
- UDP port settings
- Multicast group settings
- Auto-start preferences
- Scheduled reboot time


## Troubleshooting

- If having serial port access issues, ensure you've run the installation script (which adds the user to the dialout group)
- Verify DCS-PC IP address is correctly configured
- Check that serial devices are properly connected and detected by the system
- Check service status with `sudo systemctl status dcsbios-tui.service`

## Contributing

Feel free to submit issues and enhancement requests via the GitHub repository.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
