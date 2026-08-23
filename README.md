# DCS-BIOS Controller Manager TUI

A Terminal User Interface (TUI) application for managing DCS-BIOS serial devices on Debian-based Linux hosts. This tool provides an interactive interface for configuring and controlling multiple serial devices used with DCS (Digital Combat Simulator).

## Features

- Interactive terminal-based user interface using curses
- Manage multiple DCS-BIOS serial devices simultaneously
- Enable/disable individual devices
- Configure serial port settings (baudrate, etc.)
- **Stable device identification** — the port picker prefers `/dev/serial/by-path` addresses so each panel stays pinned to its physical USB port; raw `ttyACM*` names are only offered when no stable alias exists
- UDP multicast communication for DCS-BIOS protocol
- Real-time status monitoring of devices
- Device configuration persistence
- Scheduled reboot functionality
- Auto-start configuration

## Requirements

- Python 3.9+
- Debian-based Linux host (curses and udevadm are standard); Raspberry Pi only needed for the USB power control feature
- Serial devices connected to the system (typically `/dev/serial/by-path/*` aliases of `/dev/ttyACM*` devices)
- DCS installed on another machine with DCS-BIOS configured

## Installation

The source repository is self-hosted Gitea and is private, so installs need a token exported as `GITEA_TOKEN`. One-line install:

```bash
GITEA_TOKEN=<your-token> bash -c 'curl -sSL -H "Authorization: token $GITEA_TOKEN" https://gitea.pitato.duckdns.org/pi/DCS-BIOS-TUI/raw/branch/main/install.sh | bash'
```

This downloads and runs the installation script directly without needing to clone the repository. The script will:
- Install required Python dependencies
- Set up the application as a systemd service
- Add the user to the `dialout` group for serial port access
- Start the service automatically
- Configure the service to start on boot

### Manual Installation

1. Clone the repository:
   ```bash
   git clone ssh://git@192.168.1.5:222/pi/DCS-BIOS-TUI.git
   cd DCS-BIOS-TUI
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   ```

## Usage

### As a Service (After Installation)

The application will run automatically in the background as a service, managing your DCS-BIOS serial connections.
The TUI interface is run interactively when you need to configure or monitor your devices.

To interact with the TUI interface, SSH into your host and run:
```bash
~/DCS-BIOS-TUI/venv/bin/python ~/DCS-BIOS-TUI/dcsbios_tui.py
```

The service (`dcsbios_daemon.py`) handles the actual data forwarding between DCS and your serial devices in the background. Run only ONE of daemon or web manager per host — both contend for the same serial ports.

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
- `devices`: list of configured devices (name, port, baudrate, enabled). Use stable `/dev/serial/by-path/...` ports so panels stay pinned to physical USB ports
- `dcs_pc_ip`: DCS PC IP address
- `udp_port`, `multicast_group`: DCS-BIOS UDP listener settings
- `auto_start`, `scheduled_reboot_time`: startup and maintenance preferences
- `max_reconnect_attempts`, `reconnect_delay_seconds`, `serial_open_spacing_seconds`: reconnect/startup behaviour
- `low_voltage_event_logging`, `last_low_voltage_detected_at`: Pi power-event tracking


## Troubleshooting

- If having serial port access issues, ensure you've run the installation script (which adds the user to the dialout group)
- Verify DCS-PC IP address is correctly configured
- Check that serial devices are properly connected and detected by the system
- Check service status with `sudo systemctl status dcsbios-tui.service`

## Contributing

Source lives on self-hosted Gitea (`gitea.pitato.duckdns.org/pi/DCS-BIOS-TUI`, private). Branch convention: `main` is the working branch; use feature branches and pull requests on Gitea.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
