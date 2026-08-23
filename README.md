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
- Debian-based Linux host (curses and udevadm are standard)
- Serial devices connected to the system (typically `/dev/serial/by-path/*` aliases of `/dev/ttyACM*` devices)
- DCS installed on another machine with DCS-BIOS configured

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/Biggus22/DCS-BIOS-TUI.git
   cd DCS-BIOS-TUI
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   ```

3. Add your user to the `dialout` group for serial port access (log out and back in afterwards):
   ```bash
   sudo usermod -a -G dialout $USER
   ```

### Optional: run headless as a systemd service

The bundled `dcsbios-tui.service` runs `dcsbios_daemon.py` at boot so panels work without anyone logging in:

```bash
sudo cp dcsbios-tui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dcsbios-tui.service
```

## Usage

### As a Service (After Installation)

The service (`dcsbios_daemon.py`) runs in the background, managing your DCS-BIOS serial connections.
The TUI interface is run interactively when you need to configure or monitor your devices.

To interact with the TUI interface, SSH into your host and run:
```bash
~/DCS-BIOS-TUI/venv/bin/python ~/DCS-BIOS-TUI/dcsbios_tui.py
```

The service (`dcsbios_daemon.py`) handles the actual data forwarding between DCS and your serial devices in the background. Run only ONE of daemon or web manager per host — both contend for the same serial ports.

### Service Management

When installed as a systemd service:
- Start service: `sudo systemctl start dcsbios-tui.service`
- Stop service: `sudo systemctl stop dcsbios-tui.service`
- Restart service: `sudo systemctl restart dcsbios-tui.service`
- Check status: `sudo systemctl status dcsbios-tui.service`
- View logs: `sudo journalctl -u dcsbios-tui.service -f`

The daemon also serves live status at `http://<host>:8080/api/status` (configurable via `status_api_port` in the config, `0` disables it), so a headless host can be checked from any browser or with `curl`.

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

- If having serial port access issues, ensure your user is in the `dialout` group
- Verify DCS-PC IP address is correctly configured
- Check that serial devices are properly connected and detected by the system
- Check service status with `sudo systemctl status dcsbios-tui.service`
- If the TUI reports another manager is already running, the systemd daemon owns the serial ports — stop it (`sudo systemctl stop dcsbios-tui.service`) before running an interactive manager, or use it headlessly

## Contributing

Pull requests and issues are welcome via GitHub.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
