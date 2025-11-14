#!/usr/bin/env python3
"""
DCS-BIOS Controller Manager TUI
Interactive management interface for DCS-BIOS serial devices on Raspberry Pi
"""

import curses
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Dict
import signal
import sys
import glob
import re

# Import required modules
import socket
import struct
import serial

def sanitize_input(input_str):
    """Remove control characters and escape sequences from user input"""
    if input_str is None:
        return None
    
    # Remove control characters (ASCII 0-31) except tab (9) and newline (10, 13)
    sanitized = ''.join(char for char in input_str if ord(char) >= 32 or ord(char) in (9, 10, 13))
    
    # Remove escape sequences that might have been captured
    sanitized = sanitized.replace('\x1b', '')  # Remove escape character
    sanitized = sanitized.replace('\x00', '')  # Remove null character
    
    # Strip leading/trailing whitespace
    return sanitized.strip()

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
        self.devices: List[DeviceConfig] = []
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
    
    def serial_to_udp(self, device: DeviceConfig):
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
        
        self.add_message("DCS-BIOS manager started")
    
    def stop(self):
        if not self.running:
            return
            
        self.running = False
        self.add_message("Stopping DCS-BIOS manager...")
        time.sleep(1)
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except:
                pass
        for device in self.devices:
            device.status = "Stopped"
        self.threads = []
        self.add_message("DCS-BIOS manager stopped")

class TUI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.manager = DCSBIOSManager()
        self.selected_idx = 0
        self.menu_items = []
        self.refresh_menu()
        self.running = True
        self.needs_redraw = True
        self.last_status_hash = None
        self.reboot_check_thread = None
        
        # Colors
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Selected
        curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)  # Enabled
        curses.init_pair(3, curses.COLOR_RED, curses.COLOR_BLACK)    # Disabled
        curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK) # Header
        curses.init_pair(5, curses.COLOR_CYAN, curses.COLOR_BLACK)   # Status
        
        curses.curs_set(0)  # Hide cursor
        self.stdscr.nodelay(1)  # Non-blocking input
        
        # Setup a pad for double buffering
        self.pad = curses.newpad(1000, 1000)
        
        # Start scheduled reboot checker
        self.start_reboot_checker()
    
    def refresh_menu(self):
        self.menu_items = []
        for i, device in enumerate(self.manager.devices):
            self.menu_items.append(("device", i))
        self.menu_items.append(("separator", None))
        self.menu_items.append(("add", None))
        self.menu_items.append(("start_stop", None))
        self.menu_items.append(("usb_toggle", None))
        self.menu_items.append(("multicast_settings", None))
        self.menu_items.append(("reboot", None))
        self.menu_items.append(("schedule_reboot", None))
        self.menu_items.append(("settings", None))
        self.menu_items.append(("quit", None))
        self.needs_redraw = True
    
    def get_status_hash(self):
        """Generate hash of current status to detect changes"""
        status_data = []
        status_data.append(str(self.manager.running))
        status_data.append(str(self.selected_idx))
        for device in self.manager.devices:
            status_data.append(f"{device.name}:{device.enabled}:{device.status}")
        status_data.append(str(len(self.manager.status_messages)))
        if self.manager.status_messages:
            status_data.append(self.manager.status_messages[-1])
        return ''.join(status_data)
    
    def draw(self):
        # Only redraw if something changed
        current_hash = self.get_status_hash()
        if not self.needs_redraw and current_hash == self.last_status_hash:
            return
        
        self.last_status_hash = current_hash
        self.needs_redraw = False
        
        try:
            self.stdscr.erase()  # Use erase instead of clear to reduce flicker
            height, width = self.stdscr.getmaxyx()
            
            # Header
            header = "DCS-BIOS Controller Manager"
            self.stdscr.addstr(0, max(0, (width - len(header)) // 2), header, curses.color_pair(4) | curses.A_BOLD)
            
            # Status line
            status = "RUNNING" if self.manager.running else "STOPPED"
            status_color = curses.color_pair(2) if self.manager.running else curses.color_pair(3)
            status_line = f"Status: {status}"
            if self.manager.auto_start:
                status_line += " [AUTO]"
            self.stdscr.addstr(1, 2, status_line, status_color | curses.A_BOLD)
            
            dcs_str = f"DCS: {self.manager.dcs_pc_ip}"
            if width > 30:
                self.stdscr.addstr(1, min(width - len(dcs_str) - 2, width - 2), dcs_str[:width-4], curses.color_pair(5))
            
            # Separator
            if width > 0:
                self.stdscr.addstr(2, 0, "─" * width)
            
            # Device list
            row = 3
            for idx, (item_type, item_data) in enumerate(self.menu_items):
                if row >= height - 8:
                    break
                
                is_selected = idx == self.selected_idx
                
                if item_type == "device":
                    device = self.manager.devices[item_data]
                    prefix = "►" if is_selected else " "
                    enabled_str = "✓" if device.enabled else "✗"
                    enabled_color = curses.color_pair(2) if device.enabled else curses.color_pair(3)
                    
                    # Truncate strings to fit screen
                    max_name_len = max(20, (width - 45) if width > 45 else 15)
                    name_display = device.name[:max_name_len].ljust(max_name_len)
                    port_display = device.port[:15].ljust(15)
                    status_display = device.status[:12].ljust(12)
                    
                    line = f"{prefix} [{enabled_str}] {name_display} {port_display} {status_display}"
                    
                    if is_selected:
                        self.stdscr.addstr(row, 2, line[:width-4], curses.color_pair(1))
                    else:
                        self.stdscr.addstr(row, 2, f"{prefix} [", 0)
                        self.stdscr.addstr(enabled_str, enabled_color | curses.A_BOLD)
                        remaining = f"] {name_display} {port_display} {status_display}"
                        self.stdscr.addstr(remaining[:width-8], 0)
                    
                    row += 1
                
                elif item_type == "separator":
                    if width > 0:
                        self.stdscr.addstr(row, 2, "─" * (width - 4))
                    row += 1
                
                elif item_type == "add":
                    line = f"{'►' if is_selected else ' '} [+] Add New Device"
                    attr = curses.color_pair(1) if is_selected else curses.color_pair(2)
                    self.stdscr.addstr(row, 2, line[:width-4], attr)
                    row += 1
                
                elif item_type == "start_stop":
                    action = "Stop" if self.manager.running else "Start"
                    line = f"{'►' if is_selected else ' '} [{action}] DCS-BIOS Manager"
                    attr = curses.color_pair(1) if is_selected else curses.color_pair(5)
                    self.stdscr.addstr(row, 2, line[:width-4], attr)
                    row += 1
                
                elif item_type == "usb_toggle":
                    line = f"{'►' if is_selected else ' '} [USB] Turn USB OFF"
                    attr = curses.color_pair(1) if is_selected else curses.color_pair(5)
                    self.stdscr.addstr(row, 2, line[:width-4], attr)
                    row += 1
                
                elif item_type == "multicast_settings":
                    current_settings = f"{self.manager.multicast_group}:{self.manager.udp_port}"
                    line = f"{'►' if is_selected else ' '} [🌐] Multicast: {current_settings}"
                    attr = curses.color_pair(1) if is_selected else curses.color_pair(5)
                    self.stdscr.addstr(row, 2, line[:width-4], attr)
                    row += 1

                elif item_type == "reboot":
                    line = f"{'►' if is_selected else ' '} [⟳] Reboot Pi"
                    attr = curses.color_pair(1) if is_selected else curses.color_pair(3)
                    self.stdscr.addstr(row, 2, line[:width-4], attr)
                    row += 1
                
                elif item_type == "schedule_reboot":
                    if self.manager.scheduled_reboot_time:
                        line = f"{'►' if is_selected else ' '} [⏰] Scheduled Reboot: {self.manager.scheduled_reboot_time}"
                    else:
                        line = f"{'►' if is_selected else ' '} [⏰] Schedule Reboot"
                    attr = curses.color_pair(1) if is_selected else curses.color_pair(5)
                    self.stdscr.addstr(row, 2, line[:width-4], attr)
                    row += 1
                
                elif item_type == "settings":
                    line = f"{'►' if is_selected else ' '} [⚙] Settings"
                    attr = curses.color_pair(1) if is_selected else 0
                    self.stdscr.addstr(row, 2, line[:width-4], attr)
                    row += 1
                
                elif item_type == "quit":
                    line = f"{'►' if is_selected else ' '} [Q] Quit"
                    attr = curses.color_pair(1) if is_selected else curses.color_pair(3)
                    self.stdscr.addstr(row, 2, line[:width-4], attr)
                    row += 1
            
            # Separator
            if height > 7 and width > 0:
                self.stdscr.addstr(height - 7, 0, "─" * width)
            
            # Status messages
            if height > 6:
                self.stdscr.addstr(height - 6, 2, "Status Messages:", curses.color_pair(4))
                for i, msg in enumerate(self.manager.status_messages[-5:]):
                    if height - 5 + i < height - 1:
                        self.stdscr.addstr(height - 5 + i, 2, msg[:width-4], curses.color_pair(5))
            
            # Help
            if height > 0:
                help_text = "↑/↓:Nav ENTER:Select SPACE:Toggle D:Del Q:Quit"
                self.stdscr.addstr(height - 1, max(0, (width - len(help_text)) // 2), help_text[:width-2], curses.A_DIM)
            
            self.stdscr.refresh()
        except curses.error:
            # Ignore drawing errors (e.g., terminal too small)
            pass
    
    def run(self):
        last_update = time.time()
        update_interval = 0.5  # Update every 500ms
        
        while self.running:
            current_time = time.time()
            
            # Only draw if enough time has passed or redraw is needed
            if self.needs_redraw or (current_time - last_update) >= update_interval:
                self.draw()
                last_update = current_time
            
            try:
                key = self.stdscr.getch()
            except:
                key = -1
            
            if key == -1:  # No input
                time.sleep(0.05)  # Sleep to reduce CPU usage
                continue
            
            # Navigation
            if key == curses.KEY_UP:
                self.selected_idx = (self.selected_idx - 1) % len(self.menu_items)
                # Skip separator
                if self.menu_items[self.selected_idx][0] == "separator":
                    self.selected_idx = (self.selected_idx - 1) % len(self.menu_items)
                self.needs_redraw = True
            elif key == curses.KEY_DOWN:
                self.selected_idx = (self.selected_idx + 1) % len(self.menu_items)
                # Skip separator
                if self.menu_items[self.selected_idx][0] == "separator":
                    self.selected_idx = (self.selected_idx + 1) % len(self.menu_items)
                self.needs_redraw = True
            
            # Selection
            elif key in [curses.KEY_ENTER, 10, 13]:
                item_type, item_data = self.menu_items[self.selected_idx]
                
                if item_type == "add":
                    self.add_device_dialog()
                    self.needs_redraw = True
                elif item_type == "start_stop":
                    if self.manager.running:
                        self.manager.stop()
                    else:
                        self.manager.start()
                    self.needs_redraw = True
                elif item_type == "usb_toggle":
                    self.usb_toggle_submenu()
                    self.needs_redraw = True
                elif item_type == "multicast_settings":
                    self.multicast_settings_dialog()
                    self.needs_redraw = True
                elif item_type == "reboot":
                    self.reboot_dialog()
                    self.needs_redraw = True
                elif item_type == "schedule_reboot":
                    self.schedule_reboot_dialog()
                    self.needs_redraw = True
                elif item_type == "settings":
                    self.settings_dialog()
                    self.needs_redraw = True
                elif item_type == "quit":
                    self.running = False
            
            # Toggle device
            elif key == ord(' '):
                item_type, item_data = self.menu_items[self.selected_idx]
                if item_type == "device":
                    device = self.manager.devices[item_data]
                    device.enabled = not device.enabled
                    self.manager.save_config()
                    self.manager.add_message(f"{device.name}: {'Enabled' if device.enabled else 'Disabled'}")
                    self.needs_redraw = True
            
            # Delete device
            elif key in [ord('d'), ord('D')]:
                item_type, item_data = self.menu_items[self.selected_idx]
                if item_type == "device":
                    self.delete_device(item_data)
                    self.needs_redraw = True
            
            # Quit
            elif key in [ord('q'), ord('Q')]:
                self.running = False
        
        # Cleanup
        if self.manager.running:
            self.manager.stop()
    
    def usb_toggle_submenu(self):
        """Submenu for USB power toggle with warning"""
        height, width = self.stdscr.getmaxyx()
        dialog_height, dialog_width = 12, 50
        dialog_y = max(0, (height - dialog_height) // 2)
        dialog_x = max(0, (width - dialog_width) // 2)
        
        if width < 50:
            dialog_width = width - 4
        
        dialog = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
        dialog.keypad(True)
        dialog.nodelay(0)
        
        options = ["Turn USB OFF (requires reboot to restore)", "Cancel"]
        selected = 0
        
        while True:
            try:
                dialog.clear()
                dialog.box()
                dialog.addstr(0, 2, " USB Power Control ", curses.color_pair(4) | curses.A_BOLD)
                
                dialog.addstr(2, 2, "WARNING:", curses.color_pair(3) | curses.A_BOLD)
                dialog.addstr(3, 2, "Turning USB OFF will disable all", curses.color_pair(3))
                dialog.addstr(4, 2, "USB devices until Pi is rebooted!", curses.color_pair(3))
                
                for i, option in enumerate(options):
                    row = 6 + i
                    if i == selected:
                        dialog.addstr(row, 2, f"► {option}", curses.color_pair(1))
                    else:
                        dialog.addstr(row, 2, f"  {option}")
                
                dialog.addstr(dialog_height - 2, 2, "↑/↓:Nav ENTER:Select ESC:Cancel")
                dialog.refresh()
                
                key = dialog.getch()
                
                if key == curses.KEY_UP:
                    selected = (selected - 1) % len(options)
                elif key == curses.KEY_DOWN:
                    selected = (selected + 1) % len(options)
                elif key in [curses.KEY_ENTER, 10, 13]:
                    if selected == 0:  # Turn OFF
                        if self.manager.running:
                            self.manager.stop()
                            time.sleep(1)
                        self.toggle_usb_power_off()
                        break
                    else:  # Cancel
                        break
                elif key == 27:  # ESC
                    break
            except curses.error:
                pass
        
        self.needs_redraw = True
    
    def toggle_usb_power_off(self):
        """Turn USB power off - requires reboot to restore"""
        try:
            self.manager.add_message("Turning USB OFF...")
            self.needs_redraw = True
            self.draw()
            
            result = subprocess.run(
                ["sudo", "uhubctl", "-l", "1-1", "-p", "2", "-a", "0"],
                capture_output=True, text=True, timeout=5
            )
            
            self.manager.add_message("USB Port 2: OFF")
            self.manager.add_message("REBOOT REQUIRED to restore USB power")
            
            if result.returncode != 0 and result.stderr:
                self.manager.add_message(f"Error: {result.stderr.strip()[:50]}")
                
        except FileNotFoundError:
            self.manager.add_message("uhubctl not found. Install: sudo apt install uhubctl")
        except subprocess.TimeoutExpired:
            self.manager.add_message("USB toggle timeout")
        except Exception as e:
            self.manager.add_message(f"USB toggle error: {e}")
        
        self.needs_redraw = True
    
    def start_reboot_checker(self):
        """Start background thread to check for scheduled reboot"""
        def check_reboot():
            while self.running:
                if self.manager.scheduled_reboot_time:
                    current_time = time.strftime("%H:%M")
                    if current_time == self.manager.scheduled_reboot_time:
                        self.manager.add_message(f"Scheduled reboot at {current_time}")
                        if self.manager.running:
                            self.manager.stop()
                        # Turn off USB before reboot
                        self.manager.add_message("Turning off USB ports...")
                        try:
                            subprocess.run(
                                ["sudo", "uhubctl", "-l", "1-1", "-p", "2", "-a", "0"],
                                capture_output=True, timeout=5
                            )
                        except:
                            pass
                        time.sleep(2)
                        subprocess.run(["sudo", "reboot"])
                        break
                time.sleep(30)  # Check every 30 seconds
        
        self.reboot_check_thread = threading.Thread(target=check_reboot, daemon=True)
        self.reboot_check_thread.start()
    
    def schedule_reboot_dialog(self):
        """Dialog to schedule a reboot time"""
        height, width = self.stdscr.getmaxyx()
        dialog_height, dialog_width = 12, 50
        dialog_y = max(0, (height - dialog_height) // 2)
        dialog_x = max(0, (width - dialog_width) // 2)
        
        if width < 50:
            dialog_width = width - 4
        
        dialog = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
        dialog.keypad(True)
        dialog.nodelay(0)
        
        current_schedule = self.manager.scheduled_reboot_time or "Not set"
        options = [
            "Set Reboot Time",
            "Clear Scheduled Reboot",
            "Cancel"
        ]
        selected = 0
        
        while True:
            try:
                dialog.clear()
                dialog.box()
                dialog.addstr(0, 2, " Schedule Reboot ", curses.color_pair(4) | curses.A_BOLD)
                
                dialog.addstr(2, 2, f"Current: {current_schedule}", curses.color_pair(5))
                dialog.addstr(3, 2, "Pi will reboot at scheduled time", curses.A_DIM)
                
                for i, option in enumerate(options):
                    row = 5 + i
                    if i == selected:
                        dialog.addstr(row, 2, f"► {option}", curses.color_pair(1))
                    else:
                        dialog.addstr(row, 2, f"  {option}")
                
                dialog.addstr(dialog_height - 2, 2, "↑/↓:Nav ENTER:Select ESC:Cancel")
                dialog.refresh()
                
                key = dialog.getch()
                
                if key == curses.KEY_UP:
                    selected = (selected - 1) % len(options)
                elif key == curses.KEY_DOWN:
                    selected = (selected + 1) % len(options)
                elif key in [curses.KEY_ENTER, 10, 13]:
                    if selected == 0:  # Set time
                        new_time = self.get_time_input(dialog)
                        if new_time:
                            self.manager.scheduled_reboot_time = new_time
                            self.manager.save_config()
                            self.manager.add_message(f"Reboot scheduled for {new_time}")
                        break
                    elif selected == 1:  # Clear
                        self.manager.scheduled_reboot_time = None
                        self.manager.save_config()
                        self.manager.add_message("Scheduled reboot cleared")
                        break
                    else:  # Cancel
                        break
                elif key == 27:  # ESC
                    break
            except curses.error:
                pass
        
        self.needs_redraw = True
    
    def get_time_input(self, parent_dialog):
        """Get time input from user (HH:MM format)"""
        height, width = parent_dialog.getmaxyx()
        dialog_height, dialog_width = 8, 40
        dialog_y = max(0, (height - dialog_height) // 2)
        dialog_x = max(0, (width - dialog_width) // 2)
        
        dialog = curses.newwin(dialog_height, dialog_width, 
                              parent_dialog.getbegyx()[0] + dialog_y,
                              parent_dialog.getbegyx()[1] + dialog_x)
        dialog.box()
        dialog.addstr(0, 2, " Enter Time ", curses.color_pair(4))
        
        curses.echo()
        curses.curs_set(1)
        
        try:
            dialog.addstr(2, 2, "Enter time (HH:MM, 24-hour):")
            dialog.addstr(3, 2, "Example: 03:00 for 3am")
            dialog.addstr(5, 2, "Time: ")
            dialog.refresh()
            
            time_str = dialog.getstr(5, 8, 5).decode('utf-8').strip()
            
            # Validate format
            if len(time_str) == 5 and time_str[2] == ':':
                hour, minute = time_str.split(':')
                if hour.isdigit() and minute.isdigit():
                    h = int(hour)
                    m = int(minute)
                    if 0 <= h <= 23 and 0 <= m <= 59:
                        return time_str
            
            self.manager.add_message("Invalid time format. Use HH:MM")
            return None
            
        except Exception as e:
            self.manager.add_message(f"Error: {e}")
            return None
        finally:
            curses.noecho()
            curses.curs_set(0)

    def reboot_dialog(self):
        """Dialog to confirm reboot"""
        height, width = self.stdscr.getmaxyx()
        dialog_height, dialog_width = 10, 45
        dialog_y = max(0, (height - dialog_height) // 2)
        dialog_x = max(0, (width - dialog_width) // 2)
        
        if width < 45:
            dialog_width = width - 4
        
        dialog = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
        dialog.keypad(True)
        dialog.nodelay(0)
        
        options = ["Yes, Reboot Now", "No, Cancel"]
        selected = 1  # Default to No
        
        while True:
            try:
                dialog.clear()
                dialog.box()
                dialog.addstr(0, 2, " Reboot System ", curses.color_pair(4) | curses.A_BOLD)
                
                dialog.addstr(2, 2, "Are you sure you want to reboot?", curses.color_pair(3) | curses.A_BOLD)
                dialog.addstr(3, 2, "This will stop the manager and", curses.A_BOLD)
                dialog.addstr(4, 2, "restart the Raspberry Pi.", curses.A_BOLD)
                
                for i, option in enumerate(options):
                    row = 6 + i
                    if i == selected:
                        dialog.addstr(row, 2, f"► {option}", curses.color_pair(1))
                    else:
                        dialog.addstr(row, 2, f"  {option}")
                
                dialog.addstr(dialog_height - 2, 2, "↑/↓:Nav ENTER:Select ESC:Cancel")
                dialog.refresh()
                
                key = dialog.getch()
                
                if key == curses.KEY_UP:
                    selected = (selected - 1) % len(options)
                elif key == curses.KEY_DOWN:
                    selected = (selected + 1) % len(options)
                elif key in [curses.KEY_ENTER, 10, 13]:
                    if selected == 0:  # Reboot
                        if self.manager.running:
                            self.manager.stop()
                        self.manager.add_message("Rebooting system...")
                        self.needs_redraw = True
                        self.draw()
                        time.sleep(2)
                        subprocess.run(["sudo", "reboot"])
                        self.running = False
                        break
                    else:  # Cancel
                        break
                elif key == 27:  # ESC
                    break
            except curses.error:
                pass
        
        self.needs_redraw = True
    
    def add_device_dialog(self):
        """Dialog to add a new device"""
        # Pause automatic redraws during dialog
        old_needs_redraw = self.needs_redraw
        self.needs_redraw = False
        
        height, width = self.stdscr.getmaxyx()
        
        # First, show port selection menu
        available_ports = self.detect_serial_ports()
        
        if not available_ports:
            # No ports detected, show manual entry
            self.manual_add_device()
            self.needs_redraw = True
            return
        
        # Show port selection dialog
        selected_port = self.port_selection_dialog(available_ports)
        
        if selected_port is None:
            self.needs_redraw = True
            return  # User cancelled
        
        # Now get device name and baudrate
        dialog_height, dialog_width = 8, 60
        dialog_y = max(0, (height - dialog_height) // 2)
        dialog_x = max(0, (width - dialog_width) // 2)
        
        if width < 60:
            dialog_width = width - 4
        if height < 8:
            dialog_height = height - 2
        
        dialog = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
        dialog.box()
        dialog.addstr(0, 2, " Add New Device ", curses.color_pair(4) | curses.A_BOLD)
        
        curses.echo()
        curses.curs_set(1)
        
        try:
            # Show selected port
            dialog.addstr(2, 2, f"Port: {selected_port}")
            
            # Get device name
            dialog.addstr(3, 2, "Device Name:")
            dialog.refresh()
            name = dialog.getstr(3, 16, 30).decode('utf-8').strip()
            
            # Get baudrate
            dialog.addstr(5, 2, "Baudrate (250000):")
            dialog.refresh()
            baudrate_str = dialog.getstr(5, 22, 10).decode('utf-8').strip()
            baudrate = int(baudrate_str) if baudrate_str else 250000
            
            if name and selected_port:
                new_device = DeviceConfig(name, selected_port, baudrate, True)
                self.manager.devices.append(new_device)
                self.manager.save_config()
                self.refresh_menu()
                self.manager.add_message(f"Added device: {name}")
        except ValueError:
            self.manager.add_message("Invalid baudrate entered")
        except Exception as e:
            self.manager.add_message(f"Error adding device: {e}")
        finally:
            curses.noecho()
            curses.curs_set(0)
            self.needs_redraw = True
    
    def detect_serial_ports(self):
        """Detect available serial ports on the system"""
        ports = []
        
        # Common serial port patterns on Linux
        patterns = [
            '/dev/ttyACM*',
            '/dev/ttyUSB*',
            '/dev/ttyAMA*',
            '/dev/ttyS*',
        ]
        
        # Get all matching ports
        all_ports = []
        for pattern in patterns:
            all_ports.extend(glob.glob(pattern))
        
        # Sort ports naturally
        all_ports.sort()
        
        # Get list of already configured ports
        configured_ports = {device.port for device in self.manager.devices}
        
        # Check each port and get info
        for port in all_ports:
            try:
                # Try to get device info from udev
                info = self.get_port_info(port)
                
                # Mark if already configured
                status = "CONFIGURED" if port in configured_ports else "Available"
                
                ports.append({
                    'port': port,
                    'info': info,
                    'status': status
                })
            except Exception:
                # If we can't get info, still add it
                status = "CONFIGURED" if port in configured_ports else "Available"
                ports.append({
                    'port': port,
                    'info': 'Unknown device',
                    'status': status
                })
        
        return ports
    
    def get_port_info(self, port):
        """Get information about a serial port using udevadm"""
        try:
            result = subprocess.run(
                ['udevadm', 'info', '-q', 'property', '-n', port],
                capture_output=True,
                text=True,
                timeout=2
            )
            
            if result.returncode == 0:
                props = {}
                for line in result.stdout.split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        props[key] = value
                
                # Build a descriptive string
                vendor = props.get('ID_VENDOR', '')
                model = props.get('ID_MODEL', '')
                serial = props.get('ID_SERIAL_SHORT', '')
                
                if vendor and model:
                    info = f"{vendor} {model}"
                    if serial:
                        info += f" (S/N: {serial})"
                    return info
                elif 'ID_USB_INTERFACE_NUM' in props:
                    return "USB Serial Device"
                else:
                    return "Serial Device"
        except Exception:
            pass
        
        # Fallback: try to identify by port name
        if 'ACM' in port:
            return "USB CDC ACM Device (Arduino compatible)"
        elif 'USB' in port:
            return "USB to Serial Adapter"
        elif 'AMA' in port:
            return "Hardware Serial Port"
        else:
            return "Serial Port"
    
    def port_selection_dialog(self, available_ports):
        """Show a dialog to select from available ports"""
        height, width = self.stdscr.getmaxyx()
        
        # Calculate dialog size based on number of ports
        dialog_height = min(len(available_ports) + 6, height - 4)
        dialog_width = min(70, width - 4)
        dialog_y = max(0, (height - dialog_height) // 2)
        dialog_x = max(0, (width - dialog_width) // 2)
        
        dialog = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
        dialog.keypad(True)
        dialog.nodelay(0)  # Blocking mode for dialog
        
        selected = 0
        scroll_offset = 0
        max_visible = dialog_height - 5
        
        # Add manual entry option
        options = available_ports + [{'port': 'MANUAL', 'info': 'Enter port manually', 'status': ''}]
        
        while True:
            try:
                dialog.clear()
                dialog.box()
                dialog.addstr(0, 2, " Select Serial Port ", curses.color_pair(4) | curses.A_BOLD)
                dialog.addstr(1, 2, f"Found {len(available_ports)} port(s)", curses.color_pair(5))
                
                # Display ports
                for i in range(max_visible):
                    idx = i + scroll_offset
                    if idx >= len(options):
                        break
                    
                    port_info = options[idx]
                    port = port_info['port']
                    info = port_info['info']
                    status = port_info['status']
                    
                    row = i + 2
                    
                    if idx == selected:
                        dialog.addstr(row, 2, "►", curses.color_pair(1))
                    else:
                        dialog.addstr(row, 2, " ")
                    
                    # Color code based on status
                    if status == "CONFIGURED":
                        color = curses.color_pair(3)  # Red
                    elif port == "MANUAL":
                        color = curses.color_pair(2)  # Green
                    else:
                        color = 0
                    
                    # Format the line
                    if port == "MANUAL":
                        line = f" {info}"
                        dialog.addstr(row, 4, line[:dialog_width-6], color | curses.A_BOLD)
                    else:
                        line = f" {port:<16} {info[:35]}"
                        if status == "CONFIGURED":
                            line += " [CONFIGURED]"
                        dialog.addstr(row, 4, line[:dialog_width-6], color)
                
                # Help text
                help_y = dialog_height - 2
                dialog.addstr(help_y, 2, "↑/↓:Navigate  ENTER:Select  ESC:Cancel", curses.A_DIM)
                
                dialog.refresh()
                
                key = dialog.getch()
                
                if key == curses.KEY_UP:
                    selected = (selected - 1) % len(options)
                    # Adjust scroll
                    if selected < scroll_offset:
                        scroll_offset = selected
                elif key == curses.KEY_DOWN:
                    selected = (selected + 1) % len(options)
                    # Adjust scroll
                    if selected >= scroll_offset + max_visible:
                        scroll_offset = selected - max_visible + 1
                elif key in [curses.KEY_ENTER, 10, 13]:
                    selected_option = options[selected]
                    if selected_option['port'] == 'MANUAL':
                        curses.curs_set(0)
                        self.manual_add_device()
                        return None
                    else:
                        curses.curs_set(0)
                        return selected_option['port']
                elif key == 27:  # ESC
                    curses.curs_set(0)
                    return None
            except curses.error:
                pass
    
    def manual_add_device(self):
        """Manual device entry dialog"""
        height, width = self.stdscr.getmaxyx()
        dialog_height, dialog_width = 10, 60
        dialog_y = max(0, (height - dialog_height) // 2)
        dialog_x = max(0, (width - dialog_width) // 2)
        
        if width < 60:
            dialog_width = width - 4
        if height < 10:
            dialog_height = height - 2
        
        dialog = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
        dialog.box()
        dialog.addstr(0, 2, " Add New Device ", curses.color_pair(4) | curses.A_BOLD)
        
        curses.echo()
        curses.curs_set(1)
        
        try:
            # Get device name
            dialog.addstr(2, 2, "Device Name:")
            dialog.refresh()
            name = dialog.getstr(2, 16, 30).decode('utf-8').strip()
            
            # Get port
            dialog.addstr(4, 2, "Port (e.g. /dev/ttyACM0):")
            dialog.refresh()
            port = dialog.getstr(4, 28, 20).decode('utf-8').strip()
            
            # Get baudrate
            dialog.addstr(6, 2, "Baudrate (250000):")
            dialog.refresh()
            baudrate_str = dialog.getstr(6, 22, 10).decode('utf-8').strip()
            baudrate = int(baudrate_str) if baudrate_str else 250000
            
            if name and port:
                new_device = DeviceConfig(name, port, baudrate, True)
                self.manager.devices.append(new_device)
                self.manager.save_config()
                self.refresh_menu()
                self.manager.add_message(f"Added device: {name}")
        except ValueError:
            self.manager.add_message("Invalid baudrate entered")
        except Exception as e:
            self.manager.add_message(f"Error adding device: {e}")
        finally:
            curses.noecho()
            curses.curs_set(0)
    
    def delete_device(self, idx: int):
        """Delete a device"""
        device = self.manager.devices[idx]
        if self.manager.running and device.enabled:
            self.manager.add_message("Stop manager first or disable device")
            return
            
        self.manager.devices.pop(idx)
        self.manager.save_config()
        self.refresh_menu()
        if self.selected_idx >= len(self.menu_items):
            self.selected_idx = len(self.menu_items) - 1
        self.manager.add_message(f"Deleted device: {device.name}")
    
    def settings_dialog(self):
        """Settings dialog for DCS PC IP and auto-start"""
        height, width = self.stdscr.getmaxyx()
        dialog_height, dialog_width = 14, 60
        dialog_y = max(0, (height - dialog_height) // 2)
        dialog_x = max(0, (width - dialog_width) // 2)
        
        if width < 60:
            dialog_width = width - 4
        
        dialog = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
        dialog.keypad(True)
        dialog.nodelay(0)
        
        auto_start_str = "Enabled" if self.manager.auto_start else "Disabled"
        boot_service_status = self.check_boot_service()
        
        options = [
            f"DCS PC IP: {self.manager.dcs_pc_ip}",
            f"Auto-start Manager: {auto_start_str}",
            f"Run Headless on Boot: {boot_service_status}",
            "Done"
        ]
        selected = 0
        
        while True:
            try:
                dialog.clear()
                dialog.box()
                dialog.addstr(0, 2, " Settings ", curses.color_pair(4) | curses.A_BOLD)
                
                dialog.addstr(2, 2, "Select option to change:", curses.color_pair(5))
                
                # Update auto-start display
                auto_start_str = "Enabled" if self.manager.auto_start else "Disabled"
                boot_service_status = self.check_boot_service()
                options[1] = f"Auto-start Manager: {auto_start_str}"
                options[2] = f"Run Headless on Boot: {boot_service_status}"
                
                for i, option in enumerate(options):
                    row = 4 + i
                    if i == selected:
                        dialog.addstr(row, 2, f"► {option}", curses.color_pair(1))
                    else:
                        dialog.addstr(row, 2, f"  {option}")
                
                if selected == 2:
                    dialog.addstr(9, 2, "Requires auto-start to be enabled", curses.A_DIM)
                
                dialog.addstr(dialog_height - 2, 2, "↑/↓:Nav ENTER:Select ESC:Done")
                dialog.refresh()
                
                key = dialog.getch()
                
                if key == curses.KEY_UP:
                    selected = (selected - 1) % len(options)
                elif key == curses.KEY_DOWN:
                    selected = (selected + 1) % len(options)
                elif key in [curses.KEY_ENTER, 10, 13]:
                    if selected == 0:  # DCS PC IP
                        curses.echo()
                        curses.curs_set(1)
                        dialog.addstr(10, 2, "New IP: ")
                        dialog.clrtoeol()
                        dialog.refresh()
                        try:
                            new_ip_raw = dialog.getstr(10, 10, 30).decode('utf-8')
                            new_ip = sanitize_input(new_ip_raw)
                            if new_ip:
                                self.manager.dcs_pc_ip = new_ip
                                self.manager.save_config()
                                self.manager.add_message(f"DCS PC IP: {new_ip}")
                                options[0] = f"DCS PC IP: {new_ip}"
                        except:
                            pass
                        finally:
                            curses.noecho()
                            curses.curs_set(0)
                    elif selected == 1:  # Auto-start
                        self.manager.auto_start = not self.manager.auto_start
                        self.manager.save_config()
                        status = "enabled" if self.manager.auto_start else "disabled"
                        self.manager.add_message(f"Auto-start {status}")
                    elif selected == 2:  # Boot service
                        self.configure_boot_service()
                    else:  # Done
                        break
                elif key == 27:  # ESC
                    break
            except curses.error:
                pass
        
        self.needs_redraw = True
    
    def check_boot_service(self):
        """Check if boot service is installed"""
        service_file = "/etc/systemd/system/dcsbios.service"
        if os.path.exists(service_file):
            try:
                result = subprocess.run(
                    ["systemctl", "is-enabled", "dcsbios.service"],
                    capture_output=True, text=True
                )
                if result.returncode == 0 and "enabled" in result.stdout:
                    return "Enabled"
                else:
                    return "Installed (disabled)"
            except:
                return "Installed"
        return "Not Installed"
    
    def configure_boot_service(self):
        """Configure systemd service for boot"""
        if not self.manager.auto_start:
            self.manager.add_message("ERROR: Enable auto-start first!")
            return
        
        # Ask what to do
        height, width = self.stdscr.getmaxyx()
        dialog_height, dialog_width = 12, 55
        dialog_y = max(0, (height - dialog_height) // 2)
        dialog_x = max(0, (width - dialog_width) // 2)
        
        dialog = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
        dialog.keypad(True)
        dialog.nodelay(0)
        
        current_status = self.check_boot_service()
        
        if current_status == "Not Installed":
            options = ["Install Boot Service", "Cancel"]
        else:
            options = ["Enable Boot Service", "Disable Boot Service", "Uninstall Boot Service", "Cancel"]
        
        selected = 0
        
        while True:
            try:
                dialog.clear()
                dialog.box()
                dialog.addstr(0, 2, " Boot Service ", curses.color_pair(4) | curses.A_BOLD)
                
                dialog.addstr(2, 2, f"Status: {current_status}", curses.color_pair(5))
                dialog.addstr(3, 2, "Service will run headless on boot", curses.A_DIM)
                
                for i, option in enumerate(options):
                    row = 5 + i
                    if i == selected:
                        dialog.addstr(row, 2, f"► {option}", curses.color_pair(1))
                    else:
                        dialog.addstr(row, 2, f"  {option}")
                
                dialog.addstr(dialog_height - 2, 2, "↑/↓:Nav ENTER:Select ESC:Cancel")
                dialog.refresh()
                
                key = dialog.getch()
                
                if key == curses.KEY_UP:
                    selected = (selected - 1) % len(options)
                elif key == curses.KEY_DOWN:
                    selected = (selected + 1) % len(options)
                elif key in [curses.KEY_ENTER, 10, 13]:
                    action = options[selected]
                    
                    if action == "Install Boot Service":
                        self.install_boot_service()
                        break
                    elif action == "Enable Boot Service":
                        self.enable_boot_service()
                        break
                    elif action == "Disable Boot Service":
                        self.disable_boot_service()
                        break
                    elif action == "Uninstall Boot Service":
                        self.uninstall_boot_service()
                        break
                    else:  # Cancel
                        break
                elif key == 27:  # ESC
                    break
            except curses.error:
                pass
        
        self.needs_redraw = True
    
    def install_boot_service(self):
        """Install systemd service for boot"""
        script_path = os.path.abspath(__file__)
        service_content = f"""[Unit]
Description=DCS-BIOS Controller Manager
After=network.target

[Service]
Type=simple
User={os.getenv('USER')}
ExecStart=/usr/bin/python3 {script_path} --headless
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        
        try:
            # Write service file
            service_file = "/tmp/dcsbios.service"
            with open(service_file, 'w') as f:
                f.write(service_content)
            
            # Install service
            result = subprocess.run(
                ["sudo", "cp", service_file, "/etc/systemd/system/dcsbios.service"],
                capture_output=True, text=True
            )
            
            if result.returncode == 0:
                # Reload systemd
                subprocess.run(["sudo", "systemctl", "daemon-reload"])
                # Enable service
                subprocess.run(["sudo", "systemctl", "enable", "dcsbios.service"])
                
                self.manager.add_message("Boot service installed and enabled")
                self.manager.add_message("Manager will start on next boot")
            else:
                self.manager.add_message(f"Error: {result.stderr}")
                
        except Exception as e:
            self.manager.add_message(f"Install error: {e}")
    
    def enable_boot_service(self):
        """Enable boot service"""
        try:
            result = subprocess.run(
                ["sudo", "systemctl", "enable", "dcsbios.service"],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                self.manager.add_message("Boot service enabled")
            else:
                self.manager.add_message(f"Error: {result.stderr}")
        except Exception as e:
            self.manager.add_message(f"Enable error: {e}")
    
    def disable_boot_service(self):
        """Disable boot service"""
        try:
            result = subprocess.run(
                ["sudo", "systemctl", "disable", "dcsbios.service"],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                self.manager.add_message("Boot service disabled")
            else:
                self.manager.add_message(f"Error: {result.stderr}")
        except Exception as e:
            self.manager.add_message(f"Disable error: {e}")
    
    def uninstall_boot_service(self):
        """Uninstall boot service"""
        try:
            # Disable first
            subprocess.run(["sudo", "systemctl", "disable", "dcsbios.service"],
                         capture_output=True)
            # Stop if running
            subprocess.run(["sudo", "systemctl", "stop", "dcsbios.service"],
                         capture_output=True)
            # Remove file
            result = subprocess.run(
                ["sudo", "rm", "/etc/systemd/system/dcsbios.service"],
                capture_output=True, text=True
            )
            # Reload systemd
            subprocess.run(["sudo", "systemctl", "daemon-reload"])
            
            if result.returncode == 0:
                self.manager.add_message("Boot service uninstalled")
            else:
                self.manager.add_message(f"Error: {result.stderr}")
        except Exception as e:
            self.manager.add_message(f"Uninstall error: {e}")

    def multicast_settings_dialog(self):
        """Dialog to configure multicast IP and port"""
        height, width = self.stdscr.getmaxyx()
        dialog_height, dialog_width = 12, 60
        dialog_y = max(0, (height - dialog_height) // 2)
        dialog_x = max(0, (width - dialog_width) // 2)

        if width < 60:
            dialog_width = width - 4

        dialog = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
        dialog.keypad(True)
        dialog.nodelay(0)

        # Get current values
        current_multicast = self.manager.multicast_group
        current_port = str(self.manager.udp_port)

        # Input fields
        dialog.clear()
        dialog.box()
        dialog.addstr(0, 2, " Multicast Settings ", curses.color_pair(4) | curses.A_BOLD)

        dialog.addstr(2, 2, f"Current multicast IP: {current_multicast}")
        dialog.addstr(3, 2, f"Current UDP port: {current_port}")

        dialog.addstr(5, 2, "New multicast IP:")
        dialog.addstr(6, 2, "New UDP port:")

        # Show field values
        dialog.addstr(5, 25, current_multicast)
        dialog.addstr(6, 25, current_port)

        dialog.addstr(8, 2, "Note: Restart required after changes")
        dialog.addstr(dialog_height - 2, 2, "Press ENTER to save, ESC to cancel")
        dialog.refresh()

        curses.echo()
        curses.curs_set(1)

        try:
            # Get multicast IP
            dialog.addstr(5, 25, " " * 20)  # Clear the field
            dialog.addstr(5, 25, current_multicast)
            dialog.move(5, 25)
            new_multicast = dialog.getstr(5, 25, 20).decode('utf-8').strip()

            # If user pressed ESC, the string might be empty or have control characters
            if new_multicast == "":
                new_multicast = current_multicast  # Keep current value

            # Validate multicast IP format (basic validation)
            if new_multicast and not self.is_valid_multicast_ip(new_multicast):
                self.manager.add_message(f"Invalid multicast IP: {new_multicast}")
                new_multicast = current_multicast  # Keep current value

            # Get port
            dialog.addstr(6, 25, " " * 10)  # Clear the field
            dialog.addstr(6, 25, current_port)
            dialog.move(6, 25)
            new_port_str = dialog.getstr(6, 25, 10).decode('utf-8').strip()

            if new_port_str == "":
                new_port = self.manager.udp_port  # Keep current value
            else:
                try:
                    new_port = int(new_port_str)
                    if not (1 <= new_port <= 65535):
                        raise ValueError("Port out of range")
                except ValueError:
                    self.manager.add_message(f"Invalid port: {new_port_str}, keeping current: {self.manager.udp_port}")
                    new_port = self.manager.udp_port

            # Apply changes if valid
            if new_multicast != current_multicast or new_port != self.manager.udp_port:
                self.manager.multicast_group = new_multicast
                self.manager.udp_port = new_port
                self.manager.save_config()
                self.manager.add_message(f"Multicast settings updated: {new_multicast}:{new_port}")

        except Exception as e:
            self.manager.add_message(f"Error in multicast settings: {e}")
        finally:
            curses.noecho()
            curses.curs_set(0)
            self.needs_redraw = True

    def is_valid_multicast_ip(self, ip):
        """Basic validation for multicast IP address (224.0.0.0 to 239.255.255.255)"""
        try:
            parts = ip.split('.')
            if len(parts) != 4:
                return False
            nums = [int(x) for x in parts]
            if nums[0] < 224 or nums[0] > 239:
                return False
            if any(n < 0 or n > 255 for n in nums):
                return False
            return True
        except (ValueError, IndexError):
            return False

def main(stdscr):
    tui = TUI(stdscr)
    
    # Auto-start if enabled
    if tui.manager.auto_start and not tui.manager.running:
        tui.manager.start()
        tui.manager.add_message("Auto-started DCS-BIOS manager")
    
    tui.run()

def check_permissions():
    """Check if running with appropriate permissions"""
    if os.geteuid() != 0:
        print("WARNING: Not running as root. You may need sudo for:")
        print("  - Accessing serial ports (or add user to 'dialout' group)")
        print("  - Toggling USB power")
        print("\nTo add your user to dialout group:")
        print(f"  sudo usermod -a -G dialout {os.getenv('USER')}")
        print("  (then logout and login)")
        print("\nPress Enter to continue anyway, or Ctrl+C to exit...")
        try:
            input()
        except KeyboardInterrupt:
            sys.exit(0)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='DCS-BIOS Controller Manager')
    parser.add_argument('--headless', action='store_true', 
                       help='Run without TUI (requires auto-start enabled)')
    args = parser.parse_args()
    
    print(f"DCS-BIOS Controller Manager")
    print(f"Config location: {CONFIG_FILE}")
    print()
    
    if args.headless:
        # Headless mode - run manager without TUI
        print("Running in headless mode...")
        manager = DCSBIOSManager()
        
        if not manager.auto_start:
            print("ERROR: Auto-start is not enabled in config.")
            print("Run with TUI first and enable auto-start in Settings.")
            sys.exit(1)
        
        print("Starting DCS-BIOS manager...")
        manager.start()
        
        # Start scheduled reboot checker
        def check_reboot():
            while True:
                if manager.scheduled_reboot_time:
                    current_time = time.strftime("%H:%M")
                    if current_time == manager.scheduled_reboot_time:
                        print(f"Scheduled reboot at {current_time}")
                        if manager.running:
                            manager.stop()
                        # Turn off USB before reboot
                        print("Turning off USB ports...")
                        try:
                            subprocess.run(
                                ["sudo", "uhubctl", "-l", "1-1", "-p", "2", "-a", "0"],
                                capture_output=True, timeout=5
                            )
                        except:
                            pass
                        time.sleep(2)
                        subprocess.run(["sudo", "reboot"])
                        break
                time.sleep(30)
        
        reboot_thread = threading.Thread(target=check_reboot, daemon=True)
        reboot_thread.start()
        
        print("Manager running. Press Ctrl+C to stop...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping manager...")
            manager.stop()
            print("Exited.")
    else:
        # Normal TUI mode
        check_permissions()
        
        try:
            curses.wrapper(main)
        except KeyboardInterrupt:
            print("\nExiting...")
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
