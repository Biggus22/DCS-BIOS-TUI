#!/usr/bin/env python3
"""
DCS-BIOS Controller Daemon
Background service for managing DCS-BIOS serial devices on Raspberry Pi
"""

import datetime
import json
import os
import threading
import time
import socket
import struct
import subprocess
import serial

# Determine config file location in user's home directory
HOME_DIR = os.path.expanduser("~")
CONFIG_DIR = os.path.join(HOME_DIR, ".dcsbios")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Ensure config directory exists
os.makedirs(CONFIG_DIR, exist_ok=True)


def get_throttled_status():
    """Get Raspberry Pi throttled/undervoltage status via vcgencmd"""
    try:
        result = subprocess.run(
            ['vcgencmd', 'get_throttled'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode != 0:
            return None

        output = result.stdout.strip()
        if 'throttled=' not in output:
            return None

        hex_value = output.split('=', 1)[1].strip()
        parsed_hex = hex_value[2:] if hex_value.lower().startswith('0x') else hex_value
        value = int(parsed_hex, 16)

        return {
            'raw': hex_value,
            'undervoltage_now': bool(value & 0x1),
            'freq_capped_now': bool(value & 0x2),
            'throttled_now': bool(value & 0x4),
            'temp_limit_now': bool(value & 0x8),
            'undervoltage_occurred': bool(value & 0x10000),
            'freq_capped_occurred': bool(value & 0x20000),
            'throttled_occurred': bool(value & 0x40000),
            'temp_limit_occurred': bool(value & 0x80000)
        }
    except Exception:
        return None

class DeviceConfig:
    def __init__(self, name: str, port: str, baudrate: int = 250000, enabled: bool = True):
        self.name = name
        self.port = port
        self.baudrate = baudrate
        self.enabled = enabled
        self.status = "Stopped"
        self.last_activity = None

    def to_dict(self):
        return {
            "name": self.name,
            "port": self.port,
            "baudrate": self.baudrate,
            "enabled": self.enabled
        }

    @staticmethod
    def from_dict(data):
        return DeviceConfig(
            data.get("name", "Unknown"),
            data.get("port", ""),
            data.get("baudrate", 250000),
            data.get("enabled", True)
        )

class DCSBIOSManager:
    def __init__(self):
        self.devices = []
        self.running = False
        self.threads = []
        self.active_serial_ports = []
        self.udp_sock = None
        self.status_messages = []
        self.max_messages = 10

        # DCS-BIOS Configuration
        self.dcs_pc_ip = "192.168.1.2"
        self.udp_ip = "0.0.0.0"
        self.udp_port = 5010
        self.udp_dest_port = 7778
        self.multicast_group = "239.255.50.10"

        # Auto-start and scheduled reboot settings
        self.auto_start = False
        self.scheduled_reboot_time = None  # Format: "HH:MM"
        self.max_reconnect_attempts = 5
        self.reconnect_delay_seconds = 3
        self.serial_open_spacing_seconds = 0.5
        self.low_voltage_event_logging = False
        self.last_low_voltage_detected_at = None
        self.last_power_status = None
        self.serial_open_lock = threading.Lock()
        self.next_serial_open_time = 0.0
        self.power_monitor_interval_seconds = 5

        self.load_config()

    def add_message(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        self.status_messages.append(f"[{timestamp}] {msg}")
        if len(self.status_messages) > self.max_messages:
            self.status_messages.pop(0)

    def current_timestamp_iso(self) -> str:
        return datetime.datetime.now().astimezone().isoformat(timespec='seconds')

    def current_timestamp_label(self) -> str:
        return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    def update_power_status(self):
        power = get_throttled_status()
        if power is None:
            self.last_power_status = None
            return None

        previous_power = self.last_power_status or {}
        new_undervoltage_event = (
            (power.get('undervoltage_now') and not previous_power.get('undervoltage_now', False))
            or (power.get('undervoltage_occurred') and not previous_power.get('undervoltage_occurred', False))
        )

        if new_undervoltage_event:
            self.last_low_voltage_detected_at = self.current_timestamp_iso()
            if self.low_voltage_event_logging:
                self.add_message(
                    f"Low voltage detected at {self.current_timestamp_label()} ({power.get('raw', 'unknown')})"
                )
            self.save_config(emit_message=False)

        power['last_low_voltage_detected_at'] = self.last_low_voltage_detected_at
        power['low_voltage_event_logging'] = self.low_voltage_event_logging
        self.last_power_status = dict(power)
        return power

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.devices = [DeviceConfig.from_dict(d) for d in data.get("devices", [])]
                    self.dcs_pc_ip = data.get("dcs_pc_ip", self.dcs_pc_ip)
                    self.udp_port = data.get("udp_port", self.udp_port)
                    self.multicast_group = data.get("multicast_group", self.multicast_group)
                    self.auto_start = data.get("auto_start", False)
                    self.scheduled_reboot_time = data.get("scheduled_reboot_time", None)
                    self.max_reconnect_attempts = data.get("max_reconnect_attempts", self.max_reconnect_attempts)
                    self.reconnect_delay_seconds = data.get("reconnect_delay_seconds", self.reconnect_delay_seconds)
                    self.serial_open_spacing_seconds = data.get("serial_open_spacing_seconds", self.serial_open_spacing_seconds)
                    self.low_voltage_event_logging = data.get("low_voltage_event_logging", self.low_voltage_event_logging)
                    self.last_low_voltage_detected_at = data.get("last_low_voltage_detected_at")
                self.add_message(f"Loaded {len(self.devices)} devices from config")
            except Exception as e:
                self.add_message(f"Error loading config: {e}")
                self.init_default_devices()
        else:
            # Initialize with default devices
            self.init_default_devices()
            self.save_config()

    def init_default_devices(self):
        default_devices = [
            ("AFCS", "/dev/ttyACM0", True),
            ("ICS", "/dev/ttyACM1", True),
            ("FUEL", "/dev/ttyACM2", True),
            ("ENGINE_START", "/dev/ttyACM3", True),
            ("VOR/ILS", "/dev/ttyACM4", True),
            ("O2", "/dev/ttyACM5", True),
            ("UTILITY_PANEL", "/dev/ttyACM6", True),
            ("OUTBOARD_THROTTLE_PANEL", "/dev/ttyACM7", True),
            ("CMS", "/dev/ttyACM8", False),
            ("LEFT_SUBPANEL", "/dev/ttyACM9", False),
        ]
        for name, port, enabled in default_devices:
            self.devices.append(DeviceConfig(name, port, 250000, enabled))

    def save_config(self, emit_message: bool = True):
        try:
            data = {
                "devices": [d.to_dict() for d in self.devices],
                "dcs_pc_ip": self.dcs_pc_ip,
                "udp_port": self.udp_port,
                "multicast_group": self.multicast_group,
                "auto_start": self.auto_start,
                "scheduled_reboot_time": self.scheduled_reboot_time,
                "max_reconnect_attempts": self.max_reconnect_attempts,
                "reconnect_delay_seconds": self.reconnect_delay_seconds,
                "serial_open_spacing_seconds": self.serial_open_spacing_seconds,
                "low_voltage_event_logging": self.low_voltage_event_logging,
                "last_low_voltage_detected_at": self.last_low_voltage_detected_at
            }
            with open(CONFIG_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            if emit_message:
                self.add_message(f"Config saved to {CONFIG_FILE}")
        except Exception as e:
            self.add_message(f"Error saving config: {e}")

    def wait_for_serial_open_slot(self):
        spacing = max(0.0, float(self.serial_open_spacing_seconds))
        if spacing == 0:
            return self.running

        while self.running:
            with self.serial_open_lock:
                now = time.time()
                wait_time = self.next_serial_open_time - now
                if wait_time <= 0:
                    self.next_serial_open_time = now + spacing
                    return True
            time.sleep(min(wait_time, 0.05) if wait_time > 0 else 0.01)

        return False

    def open_serial_port(self, device: DeviceConfig):
        if not self.wait_for_serial_open_slot():
            raise RuntimeError("Manager stopped before serial port open")
        return serial.Serial(device.port, device.baudrate, timeout=0.1)

    def power_status_monitor(self):
        while self.running:
            self.update_power_status()
            time.sleep(self.power_monitor_interval_seconds)

    def setup_udp(self):
        try:
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_sock.bind((self.udp_ip, self.udp_port))
            self.udp_sock.settimeout(0.5)

            mreq = struct.pack("=4sl", socket.inet_aton(self.multicast_group), socket.INADDR_ANY)
            self.udp_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            self.add_message(f"UDP socket listening on port {self.udp_port}")
        except Exception as e:
            self.add_message(f"UDP setup error: {e}")

    def is_dcsbios_export_packet(self, data):
        return len(data) >= 4 and data[0] == 0x55 and data[1] == 0x55 and data[2] == 0x55 and data[3] == 0x55

    def serial_to_udp(self, device):
        if not device.enabled:
            return

        ser = None
        device.status = "Connecting"
        consecutive_failures = 0

        while self.running:
            try:
                if ser is None or not ser.is_open:
                    ser = self.open_serial_port(device)
                    consecutive_failures = 0
                    device.status = "Connected"
                    self.add_message(f"{device.name} connected on {device.port}")

                if ser.in_waiting:
                    data = ser.read(ser.in_waiting)
                    if data:
                        clean_data = data.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
                        self.udp_sock.sendto(clean_data, (self.dcs_pc_ip, self.udp_dest_port))
                        device.last_activity = time.time()
                        consecutive_failures = 0
                else:
                    time.sleep(0.005)

            except (serial.SerialException, PermissionError) as e:
                device.status = "Error"
                consecutive_failures += 1
                if ser and ser.is_open:
                    try:
                        ser.close()
                    except:
                        pass
                ser = None
                if consecutive_failures >= self.max_reconnect_attempts:
                    self.add_message(
                        f"{device.name} connection failed after {self.max_reconnect_attempts} attempts ({e}). Giving up."
                    )
                    break
                self.add_message(
                    f"{device.name} error ({e}). Reconnect attempt {consecutive_failures}/{self.max_reconnect_attempts} in {self.reconnect_delay_seconds}s"
                )
                time.sleep(self.reconnect_delay_seconds)
            except Exception as e:
                device.status = "Error"
                consecutive_failures += 1
                if ser and ser.is_open:
                    try:
                        ser.close()
                    except:
                        pass
                ser = None
                if consecutive_failures >= self.max_reconnect_attempts:
                    self.add_message(
                        f"{device.name} unexpected serial error after {self.max_reconnect_attempts} attempts ({e}). Giving up."
                    )
                    break
                self.add_message(
                    f"{device.name} unexpected serial error ({e}). Reconnect attempt {consecutive_failures}/{self.max_reconnect_attempts} in {self.reconnect_delay_seconds}s"
                )
                time.sleep(self.reconnect_delay_seconds)

        if ser and ser.is_open:
            try:
                ser.close()
            except:
                pass
        if not self.running:
            device.status = "Stopped"

    def udp_to_serial(self):
        self.active_serial_ports = []

        for device in self.devices:
            if device.enabled:
                self.active_serial_ports.append({
                    "name": device.name,
                    "port": None,
                    "device": device,
                    "failures": 0,
                    "next_retry": 0.0
                })

        while self.running:
            now = time.time()

            for entry in self.active_serial_ports:
                ser = entry["port"]
                device = entry["device"]

                if ser and ser.is_open:
                    continue

                if entry["failures"] >= self.max_reconnect_attempts:
                    continue

                if now < entry["next_retry"]:
                    continue

                try:
                    entry["port"] = self.open_serial_port(device)
                    entry["failures"] = 0
                    entry["next_retry"] = 0.0
                    device.status = "Connected"
                    self.add_message(f"Opened {device.name} for UDP forwarding")
                except Exception as e:
                    entry["failures"] += 1
                    device.status = "Error"
                    entry["next_retry"] = now + self.reconnect_delay_seconds
                    if entry["failures"] >= self.max_reconnect_attempts:
                        self.add_message(
                            f"Could not open {device.name} after {self.max_reconnect_attempts} attempts ({e}). Giving up."
                        )
                    else:
                        self.add_message(
                            f"Could not open {device.name} ({e}). Reconnect attempt {entry['failures']}/{self.max_reconnect_attempts} in {self.reconnect_delay_seconds}s"
                        )

            try:
                data, addr = self.udp_sock.recvfrom(1024)

                if addr[0] != self.dcs_pc_ip:
                    continue

                if not self.is_dcsbios_export_packet(data):
                    continue

                for entry in self.active_serial_ports:
                    ser = entry["port"]
                    device = entry["device"]
                    if ser and ser.is_open:
                        try:
                            ser.write(data)
                            device.last_activity = time.time()
                            entry["failures"] = 0
                        except Exception as e:
                            try:
                                ser.close()
                            except:
                                pass
                            entry["port"] = None
                            entry["failures"] += 1
                            device.status = "Error"
                            entry["next_retry"] = time.time() + self.reconnect_delay_seconds
                            if entry["failures"] >= self.max_reconnect_attempts:
                                self.add_message(
                                    f"UDP forwarding failed for {device.name} after {self.max_reconnect_attempts} attempts ({e}). Giving up."
                                )
                            else:
                                self.add_message(
                                    f"UDP forwarding error on {device.name} ({e}). Reconnect attempt {entry['failures']}/{self.max_reconnect_attempts} in {self.reconnect_delay_seconds}s"
                                )

            except socket.timeout:
                continue
            except Exception as e:
                time.sleep(1)

        # Cleanup
        for entry in self.active_serial_ports:
            if entry["port"] and entry["port"].is_open:
                try:
                    entry["port"].close()
                except:
                    pass

    def start(self):
        if self.running:
            self.add_message("Already running!")
            return

        self.running = True
        with self.serial_open_lock:
            self.next_serial_open_time = time.time()
        self.setup_udp()

        if self.serial_open_spacing_seconds > 0:
            self.add_message(
                f"Pacing serial open attempts by {self.serial_open_spacing_seconds:g}s"
            )

        # Start UDP to serial thread
        udp_thread = threading.Thread(target=self.udp_to_serial, daemon=True)
        udp_thread.start()
        self.threads.append(udp_thread)

        power_thread = threading.Thread(target=self.power_status_monitor, daemon=True)
        power_thread.start()
        self.threads.append(power_thread)

        # Start serial to UDP threads for each enabled device
        for device in self.devices:
            if device.enabled:
                thread = threading.Thread(target=self.serial_to_udp, args=(device,), daemon=True)
                thread.start()
                self.threads.append(thread)

        self.add_message("DCS-BIOS manager daemon started")

    def stop(self):
        if not self.running:
            return

        self.running = False
        self.add_message("Stopping DCS-BIOS manager daemon...")
        time.sleep(1)
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except:
                pass
        for device in self.devices:
            device.status = "Stopped"
        self.threads = []
        self.add_message("DCS-BIOS manager daemon stopped")

# Main execution
if __name__ == '__main__':
    manager = DCSBIOSManager()
    manager.start()
    
    try:
        while manager.running:
            time.sleep(1)
    except KeyboardInterrupt:
        manager.stop()