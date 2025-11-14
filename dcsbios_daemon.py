#!/usr/bin/env python3
"""
DCS-BIOS Controller Daemon
Background service for managing DCS-BIOS serial devices on Raspberry Pi
"""

import json
import os
import threading
import time
import socket
import struct
import serial

# Determine config file location in user's home directory
HOME_DIR = os.path.expanduser("~")
CONFIG_DIR = os.path.join(HOME_DIR, ".dcsbios")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Ensure config directory exists
os.makedirs(CONFIG_DIR, exist_ok=True)

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

        self.load_config()

    def add_message(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        self.status_messages.append(f"[{timestamp}] {msg}")
        if len(self.status_messages) > self.max_messages:
            self.status_messages.pop(0)

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

    def save_config(self):
        try:
            data = {
                "devices": [d.to_dict() for d in self.devices],
                "dcs_pc_ip": self.dcs_pc_ip,
                "udp_port": self.udp_port,
                "multicast_group": self.multicast_group,
                "auto_start": self.auto_start,
                "scheduled_reboot_time": self.scheduled_reboot_time
            }
            with open(CONFIG_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            self.add_message(f"Config saved to {CONFIG_FILE}")
        except Exception as e:
            self.add_message(f"Error saving config: {e}")

    def setup_udp(self):
        try:
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_sock.bind((self.udp_ip, self.udp_port))

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

        while self.running:
            try:
                if ser is None or not ser.is_open:
                    ser = serial.Serial(device.port, device.baudrate, timeout=0.1)
                    device.status = "Connected"
                    self.add_message(f"{device.name} connected on {device.port}")

                if ser.in_waiting:
                    data = ser.read(ser.in_waiting)
                    if data:
                        clean_data = data.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
                        self.udp_sock.sendto(clean_data, (self.dcs_pc_ip, self.udp_dest_port))
                        device.last_activity = time.time()
                else:
                    time.sleep(0.005)

            except (serial.SerialException, PermissionError) as e:
                device.status = "Error"
                if ser and ser.is_open:
                    try:
                        ser.close()
                    except:
                        pass
                ser = None
                time.sleep(3)
            except Exception as e:
                device.status = "Error"
                time.sleep(5)

        if ser and ser.is_open:
            try:
                ser.close()
            except:
                pass
        device.status = "Stopped"

    def udp_to_serial(self):
        self.active_serial_ports = []

        # Open serial ports for all enabled devices
        for device in self.devices:
            if device.enabled:
                try:
                    ser = serial.Serial(device.port, device.baudrate, timeout=0.1)
                    self.active_serial_ports.append({
                        "name": device.name,
                        "port": ser,
                        "device": device
                    })
                    self.add_message(f"Opened {device.name} for UDP forwarding")
                except Exception as e:
                    self.add_message(f"Could not open {device.name}: {e}")

        while self.running:
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
                        except Exception:
                            pass

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
        self.setup_udp()

        # Start UDP to serial thread
        udp_thread = threading.Thread(target=self.udp_to_serial, daemon=True)
        udp_thread.start()
        self.threads.append(udp_thread)

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