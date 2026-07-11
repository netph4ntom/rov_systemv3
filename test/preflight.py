# test/preflight.py
# ==============================================================================
# ROV Preflight Diagnostic and System Check Tool
# ==============================================================================
#
# This tool tests all critical ROV subsystems, including:
#   1. System Resources & Temperature
#   2. Software Dependencies
#   3. Vision & QR Model Readiness
#   4. Camera Stream Services (Front & Bottom)
#   5. Core API & WebSocket Server Connection
#   6. MAVLink Communications Link
#   7. Live Telemetry Streams (Battery, Orientation, Depth)
#   8. Interactive Hardware Controls (LED, Gripper, Motors)
#
# Usage:
#   python test/preflight.py
# ==============================================================================

import os
import sys
import time
import logging
import urllib.request
import json
from datetime import datetime
import numpy as np

# Enable virtual terminal processing for ANSI colors on Windows and fix encoding issues
if sys.platform == "win32":
    import io
    os.system("")
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Ensure project root is in sys.path for internal imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Terminal colors
COLOR_RESET = "\033[0m"
COLOR_BOLD = "\033[1m"
COLOR_RED = "\033[91m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_BLUE = "\033[94m"
COLOR_CYAN = "\033[96m"
COLOR_WHITE = "\033[97m"

SYM_PASS = f"{COLOR_GREEN}✔ [PASS]{COLOR_RESET}"
SYM_FAIL = f"{COLOR_RED}✘ [FAIL]{COLOR_RESET}"
SYM_WARN = f"{COLOR_YELLOW}⚠ [WARN]{COLOR_RESET}"
SYM_INFO = f"{COLOR_BLUE}ℹ [INFO]{COLOR_RESET}"

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")


class PreflightTester:
    def __init__(self):
        self.results = {}
        self.mav = None
        self.telemetry_received = []
        self.import_errors = []

    def print_banner(self):
        banner = f"""
{COLOR_CYAN}{COLOR_BOLD}======================================================================
               ____   ___  __     ____  ____  _____ _____ 
              |  _ \\\\ / _ \\\\ \\\\   / /  _ \\\\|  _ \\\\| ____|  ___|
              | |_) | | | | \\\\ \\\\ / /| |_) | |_) |  _| | |_   
              |  _ <| |_| |  \\\\ V / |  __/|  _ <| |___|  _|  
              |_| \\\\_\\\\\\\\___/    \\\\_/  |_|   |_| \\\\_\\\\_____|_|  
                                                          
                    SYSTEM PREFLIGHT DIAGNOSTICS
======================================================================{COLOR_RESET}
Date & Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
OS Platform: {sys.platform}
Python Exec: {sys.executable}
"""
        print(banner)

    # ──────────────────────────────────────────────────────────────────────────
    # Check 1: Software Environment & Dependencies
    # ──────────────────────────────────────────────────────────────────────────
    def run_check_dependencies(self):
        print(f"\n{COLOR_BOLD}[1/8] Checking Software Environment & Libraries...{COLOR_RESET}")
        
        # Verify Python version
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        print(f"  {SYM_INFO} Python version: {py_ver}")

        # List of required dependencies and description
        dependencies = [
            ("pymavlink", "MAVLink Communication Bridge"),
            ("pyzbar", "Classic QR Code Reader"),
            ("psutil", "System Resources Diagnostic"),
            ("aiortc", "WebRTC Video Engine"),
            ("fastapi", "FastAPI Core Web Framework"),
            ("zmq", "ZeroMQ Inter-process Communication"),
            ("cv2", "OpenCV Image Processing"),
            ("numpy", "NumPy Numerical Operations"),
        ]

        passed_deps = 0
        for lib, desc in dependencies:
            try:
                __import__(lib)
                print(f"  {SYM_PASS} {COLOR_BOLD}{lib:<12}{COLOR_RESET} - {desc}")
                passed_deps += 1
            except ImportError as e:
                print(f"  {SYM_FAIL} {COLOR_BOLD}{lib:<12}{COLOR_RESET} is NOT installed! (Error: {e})")
                self.import_errors.append(lib)

        if passed_deps == len(dependencies):
            self.results["dependencies"] = ("PASS", "All core libraries installed.")
        else:
            self.results["dependencies"] = ("FAIL", f"Missing {len(dependencies) - passed_deps} core libraries.")

    # ──────────────────────────────────────────────────────────────────────────
    # Check 2: Vision & QR Subsystem Readiness
    # ──────────────────────────────────────────────────────────────────────────
    def run_check_vision_engines(self):
        print(f"\n{COLOR_BOLD}[2/8] Checking Vision Modules & QR Detection Engines...{COLOR_RESET}")

        if "cv2" in self.import_errors:
            print(f"  {SYM_FAIL} OpenCV not available. Skipping vision engine tests.")
            self.results["vision"] = ("FAIL", "OpenCV dependency missing.")
            return

        # Check WeChat QR Model Directories & Files
        try:
            from config import (
                WECHAT_QR_DETECT_PROTOTXT,
                WECHAT_QR_DETECT_CAFFEMODEL,
                WECHAT_QR_SR_PROTOTXT,
                WECHAT_QR_SR_CAFFEMODEL,
            )
            
            models = {
                "Detect Prototxt": WECHAT_QR_DETECT_PROTOTXT,
                "Detect Caffemodel": WECHAT_QR_DETECT_CAFFEMODEL,
                "SR Prototxt": WECHAT_QR_SR_PROTOTXT,
                "SR Caffemodel": WECHAT_QR_SR_CAFFEMODEL,
            }

            model_files_ok = True
            for name, path in models.items():
                if os.path.exists(path):
                    size_kb = os.path.getsize(path) / 1024
                    print(f"  {SYM_PASS} {name:<17}: Found ({size_kb:.1f} KB)")
                else:
                    print(f"  {SYM_FAIL} {name:<17}: NOT FOUND at '{path}'")
                    model_files_ok = False

            # Check WeChat QR Code Initialization
            import cv2
            if hasattr(cv2, "wechat_qrcode_WeChatQRCode"):
                if model_files_ok:
                    try:
                        # Attempt 4-arg initialization (with Super Resolution)
                        cv2.wechat_qrcode_WeChatQRCode(
                            WECHAT_QR_DETECT_PROTOTXT,
                            WECHAT_QR_DETECT_CAFFEMODEL,
                            WECHAT_QR_SR_PROTOTXT,
                            WECHAT_QR_SR_CAFFEMODEL,
                        )
                        print(f"  {SYM_PASS} WeChat QR Code Engine (with Super Resolution): Initialized OK.")
                        self.results["vision_wechat"] = ("PASS", "WeChat QR + Super Resolution OK.")
                    except Exception as e:
                        print(f"  {SYM_WARN} WeChat QR 4-arg failed: {e}. Trying fallback...")
                        try:
                            # Attempt 2-arg initialization (without Super Resolution)
                            cv2.wechat_qrcode_WeChatQRCode(
                                WECHAT_QR_DETECT_PROTOTXT,
                                WECHAT_QR_DETECT_CAFFEMODEL,
                            )
                            print(f"  {SYM_PASS} WeChat QR Code Engine (without Super Resolution): Initialized OK.")
                            self.results["vision_wechat"] = ("WARN", "WeChat QR OK (no Super Resolution).")
                        except Exception as e2:
                            print(f"  {SYM_FAIL} WeChat QR initialization failed: {e2}")
                            self.results["vision_wechat"] = ("FAIL", "Failed to initialize WeChat QR detector.")
                else:
                    print(f"  {SYM_WARN} WeChat model files are incomplete. WeChat QR will not work.")
                    self.results["vision_wechat"] = ("FAIL", "WeChat QR models missing.")
            else:
                print(f"  {SYM_WARN} cv2.wechat_qrcode module is not compiled in this OpenCV installation.")
                self.results["vision_wechat"] = ("WARN", "OpenCV build has no WeChat QR module.")

            # Check OpenCV GStreamer Support
            build_info = cv2.getBuildInformation()
            has_gstreamer = False
            gstreamer_detail = ""
            for line in build_info.split('\n'):
                if "gstreamer" in line.lower() and "yes" in line.lower():
                    has_gstreamer = True
                    gstreamer_detail = line.strip()
                    break
            
            if has_gstreamer:
                print(f"  {SYM_PASS} OpenCV GStreamer support: Enabled ({gstreamer_detail})")
                self.results["vision_gstreamer"] = ("PASS", "OpenCV compiled with GStreamer support.")
            else:
                print(f"  {SYM_WARN} OpenCV GStreamer support: NOT ENABLED! Video streaming may fail on Raspberry Pi.")
                self.results["vision_gstreamer"] = ("WARN", "OpenCV not compiled with GStreamer support.")

            # Check pyzbar classic QR
            if "pyzbar" not in self.import_errors:
                from pyzbar import pyzbar
                print(f"  {SYM_PASS} Pyzbar classic QR library: Installed & functional.")
                self.results["vision_pyzbar"] = ("PASS", "Pyzbar library functional.")
            else:
                print(f"  {SYM_FAIL} Pyzbar classic QR library: NOT functional.")
                self.results["vision_pyzbar"] = ("FAIL", "Pyzbar library missing.")

            # Load image processors
            from camera_front.image_processing import FrontImageProcessor
            from camera_bottom.image_processing import BottomImageProcessor
            
            proc_front = FrontImageProcessor(show_hud=True)
            proc_bottom = BottomImageProcessor(show_hud=True)
            
            dummy = np.zeros((480, 640, 3), dtype=np.uint8)
            proc_front.process(dummy)
            proc_bottom.process(dummy)
            print(f"  {SYM_PASS} Image Processors (Front & Bottom): Instantiated & tested OK.")
            self.results["image_processors"] = ("PASS", "Front & Bottom processors instantiated OK.")

        except Exception as e:
            print(f"  {SYM_FAIL} Error during vision diagnostics: {e}")
            self.results["vision_general"] = ("FAIL", f"Vision check error: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # Check 3: Companion Computer Health & Resources
    # ──────────────────────────────────────────────────────────────────────────
    def run_check_system_resources(self):
        print(f"\n{COLOR_BOLD}[3/8] Checking System Resources & Temperature...{COLOR_RESET}")

        if "psutil" in self.import_errors:
            print(f"  {SYM_FAIL} psutil library not available. Skipping resource diagnostics.")
            self.results["system_resources"] = ("FAIL", "psutil library missing.")
            return

        import psutil
        try:
            # CPU Usage
            cpu_usage = psutil.cpu_percent(interval=1.0)
            cpu_cores = psutil.cpu_count(logical=True)
            
            # RAM Usage
            mem = psutil.virtual_memory()
            mem_pct = mem.percent
            mem_used_gb = mem.used / (1024**3)
            mem_total_gb = mem.total / (1024**3)

            # Disk Usage
            disk = psutil.disk_usage(".")
            disk_pct = disk.percent
            disk_free_gb = disk.free / (1024**3)

            # CPU Temperature (RPi/Linux specific)
            cpu_temp = None
            temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
            for key in ("cpu_thermal", "coretemp", "cpu-thermal"):
                if key in temps and temps[key]:
                    cpu_temp = temps[key][0].current
                    break

            # Print system resource status
            print(f"  {SYM_INFO} CPU Cores: {cpu_cores} Cores")
            
            # CPU threshold check
            from config import FS_CPU_WARN_PERCENT, FS_RAM_WARN_PERCENT, FS_TEMP_WARN_CELSIUS
            
            cpu_ok = cpu_usage <= FS_CPU_WARN_PERCENT
            cpu_color = COLOR_GREEN if cpu_ok else COLOR_YELLOW
            print(f"  {'  ' + SYM_PASS if cpu_ok else SYM_WARN} CPU Load : {cpu_color}{cpu_usage:.1f}%{COLOR_RESET} (Warn threshold: {FS_CPU_WARN_PERCENT}%)")

            # RAM threshold check
            ram_ok = mem_pct <= FS_RAM_WARN_PERCENT
            ram_color = COLOR_GREEN if ram_ok else COLOR_YELLOW
            print(f"  {'  ' + SYM_PASS if ram_ok else SYM_WARN} RAM Usage: {ram_color}{mem_pct:.1f}%{COLOR_RESET} ({mem_used_gb:.1f}/{mem_total_gb:.1f} GB, Warn threshold: {FS_RAM_WARN_PERCENT}%)")

            # Disk space check
            disk_ok = disk_pct <= 90
            disk_color = COLOR_GREEN if disk_ok else COLOR_YELLOW
            print(f"  {'  ' + SYM_PASS if disk_ok else SYM_WARN} Disk free: {disk_color}{disk_pct:.1f}%{COLOR_RESET} ({disk_free_gb:.1f} GB free)")

            # Temp threshold check
            if cpu_temp is not None:
                temp_ok = cpu_temp <= FS_TEMP_WARN_CELSIUS
                temp_color = COLOR_GREEN if temp_ok else COLOR_RED
                print(f"  {'  ' + SYM_PASS if temp_ok else SYM_FAIL} CPU Temp : {temp_color}{cpu_temp:.1f}°C{COLOR_RESET} (Warn threshold: {FS_TEMP_WARN_CELSIUS}°C)")
            else:
                print(f"  {SYM_INFO} CPU Temp : Temperature sensor not readable (Platform: {sys.platform})")

            # Overall system status decision
            if cpu_ok and ram_ok and disk_ok and (cpu_temp is None or cpu_temp <= FS_TEMP_WARN_CELSIUS):
                self.results["system_resources"] = ("PASS", f"CPU {cpu_usage:.0f}%, RAM {mem_pct:.0f}% OK.")
            else:
                self.results["system_resources"] = ("WARN", "One or more resources exceeded warning thresholds.")

        except Exception as e:
            print(f"  {SYM_FAIL} Failed to fetch system resources: {e}")
            self.results["system_resources"] = ("FAIL", f"System resource fetch error: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # Check 4: Camera Service Connections
    # ──────────────────────────────────────────────────────────────────────────
    def run_check_camera_services(self):
        print(f"\n{COLOR_BOLD}[4/8] Checking Camera Stream Services...{COLOR_RESET}")
        
        from config import (
            FS_CAMERA_HEALTH_URL_FRONT,
            FS_CAMERA_HEALTH_URL_BOTTOM,
            PORT_STREAM_FRONT,
            PORT_STREAM_BOTTOM,
        )

        checks = [
            ("Front Camera Stream Service", FS_CAMERA_HEALTH_URL_FRONT),
            ("Bottom Camera Stream Service", FS_CAMERA_HEALTH_URL_BOTTOM),
        ]

        failed_cams = 0
        for name, url in checks:
            print(f"  Connecting to health endpoint: {url}...")
            try:
                with urllib.request.urlopen(url, timeout=2.0) as resp:
                    data = json.loads(resp.read().decode())
                    status = data.get("status", "error")
                    if status == "ok":
                        print(f"  {SYM_PASS} {name}: Stream Server running & healthy.")
                    else:
                        print(f"  {SYM_WARN} {name}: Stream Server responded with status '{status}'")
                        failed_cams += 1
            except Exception as e:
                print(f"  {SYM_FAIL} {name}: Stream Server is OFFLINE or unreachable (Error: {e})")
                failed_cams += 1

        # Check WebRTC /offer signaling endpoint availability
        webrtc_checks = [
            ("Front Camera WebRTC Offer Endpoint", f"http://localhost:{PORT_STREAM_FRONT}/offer"),
            ("Bottom Camera WebRTC Offer Endpoint", f"http://localhost:{PORT_STREAM_BOTTOM}/offer"),
        ]

        print(f"  Checking WebRTC Signaling Endpoints...")
        for name, url in webrtc_checks:
            print(f"  Connecting to WebRTC signaling endpoint: {url}...")
            try:
                # Send a POST request with an empty body to trigger the route validation
                req = urllib.request.Request(
                    url,
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                try:
                    with urllib.request.urlopen(req, timeout=2.0) as resp:
                        print(f"  {SYM_PASS} {name}: Active & responding.")
                except urllib.error.HTTPError as he:
                    if he.code in (400, 422):
                        # 400 Bad Request is expected because the request body `{}` is missing "sdp"/"type" fields
                        print(f"  {SYM_PASS} {name}: Active & responding (Expected HTTP {he.code}).")
                    else:
                        print(f"  {SYM_WARN} {name}: Endpoint active, but returned unexpected code {he.code}")
                        failed_cams += 1
            except Exception as e:
                print(f"  {SYM_FAIL} {name}: Endpoint is OFFLINE or unreachable (Error: {e})")
                failed_cams += 1

        if failed_cams == 0:
            self.results["camera_services"] = ("PASS", "Both camera stream processes & WebRTC endpoints are online.")
        elif failed_cams < 4:
            self.results["camera_services"] = ("WARN", "Camera services are partially offline or degraded.")
        else:
            self.results["camera_services"] = ("FAIL", "Both camera services are offline. Verify camera streaming processes.")

    # ──────────────────────────────────────────────────────────────────────────
    # Check 5: Core API & WebSocket Server Connection Check
    # ──────────────────────────────────────────────────────────────────────────
    def run_check_core_api_websocket(self):
        print(f"\n{COLOR_BOLD}[5/8] Checking Core API and WebSocket (Socket.IO) Status...{COLOR_RESET}")
        
        from config import PORT_CORE_API
        
        core_url = f"http://localhost:{PORT_CORE_API}/api/health"
        sio_url = f"http://localhost:{PORT_CORE_API}"

        # 1. Check REST API Health
        print(f"  Connecting to REST API: {core_url}...")
        api_ok = False
        try:
            with urllib.request.urlopen(core_url, timeout=2.0) as resp:
                data = json.loads(resp.read().decode())
                if data.get("status") == "ok":
                    print(f"  {SYM_PASS} Core API REST Service: Online & healthy.")
                    api_ok = True
                else:
                    print(f"  {SYM_WARN} Core API REST Service: Responded with '{data}'")
        except Exception as e:
            print(f"  {SYM_FAIL} Core API REST Service: Offline or unreachable (Error: {e})")

        # 2. Check WebSocket (Socket.IO) Connection
        print(f"  Connecting to WebSocket (Socket.IO) at {sio_url}...")
        ws_ok = False
        
        try:
            import socketio
            import logging
            # Suppress socketio and engineio internal logging warnings
            logging.getLogger("socketio").setLevel(logging.CRITICAL)
            logging.getLogger("engineio").setLevel(logging.CRITICAL)
            logging.getLogger("urllib3").setLevel(logging.CRITICAL)
            
            sio_client = socketio.Client(logger=False, engineio_logger=False)
            
            pong_received = False
            
            @sio_client.on("pong_rov")
            def on_pong(data):
                nonlocal pong_received
                pong_received = True
                print(f"  {SYM_PASS} Received 'pong_rov' from Core API: {data}")

            try:
                # Try negotiating automatically
                sio_client.connect(sio_url, wait_timeout=2)
            except Exception:
                try:
                    # Fallback to explicit polling transport
                    sio_client.connect(sio_url, transports=["polling"], wait_timeout=2)
                except Exception as conn_err:
                    raise conn_err
            
            # Emit ping event
            sio_client.emit("ping_rov", {"test": "preflight"})
            
            # Wait up to 2.0s for pong response
            wait_start = time.time()
            while time.time() - wait_start < 2.0 and not pong_received:
                time.sleep(0.1)
                
            sio_client.disconnect()
            
            if pong_received:
                print(f"  {SYM_PASS} WebSocket (Socket.IO) Communication: Connected and verified.")
                ws_ok = True
            else:
                print(f"  {SYM_WARN} WebSocket (Socket.IO): Connected, but 'pong_rov' response timed out.")
                ws_ok = True # Connected but pong failed
        except ImportError:
            print(f"  {SYM_FAIL} WebSocket test: 'python-socketio' library not available for import.")
        except Exception as e:
            print(f"  {SYM_FAIL} WebSocket (Socket.IO) Connection: Failed (Error: {e})")

        # Record results
        if api_ok and ws_ok:
            self.results["core_api_and_websocket"] = ("PASS", f"Core API REST and Socket.IO online on Port {PORT_CORE_API}")
        elif api_ok or ws_ok:
            self.results["core_api_and_websocket"] = ("WARN", f"Core API REST/Socket.IO partially available on Port {PORT_CORE_API}")
        else:
            self.results["core_api_and_websocket"] = ("FAIL", f"Core API and WebSocket server are offline on Port {PORT_CORE_API}. Run main.py first.")

    # ──────────────────────────────────────────────────────────────────────────
    # Check 6: MAVLink Communication Bridge
    # ──────────────────────────────────────────────────────────────────────────
    def run_check_mavlink_link(self):
        print(f"\n{COLOR_BOLD}[6/8] Checking MAVLink Communication Link to Flight Controller...{COLOR_RESET}")

        if "pymavlink" in self.import_errors:
            print(f"  {SYM_FAIL} pymavlink library missing. Skipping MAVLink checks.")
            self.results["mavlink"] = ("FAIL", "pymavlink missing.")
            return

        from config import MAVLINK_CONNECTION_STRING
        from core.mavlink import MAVLinkBridge

        print(f"  Initializing MAVLinkBridge utilizing endpoint: '{MAVLINK_CONNECTION_STRING}'...")
        self.mav = MAVLinkBridge()

        # Temporary override warning to prevent blockages if connection is locked
        print(f"  Connecting (timeout=10s)... {COLOR_YELLOW}(Make sure other ROV software processes are NOT holding the port){COLOR_RESET}")
        
        # Modify MAVLink Bridge parameters to reduce retries specifically for the preflight diagnostic check
        import core.mavlink
        core.mavlink.CONNECT_MAX_RETRIES = 2
        core.mavlink.CONNECT_RETRY_INTERVAL = 1.0

        success = self.mav.connect()

        if success and self.mav.is_connected:
            print(f"  {SYM_PASS} MAVLink: Connection established successfully.")
            # Get target sys & component info
            sys_id = self.mav._conn.target_system
            comp_id = self.mav._conn.target_component
            print(f"  {SYM_INFO} Pixhawk target system ID: {sys_id}")
            print(f"  {SYM_INFO} Pixhawk target component ID: {comp_id}")
            self.results["mavlink"] = ("PASS", f"Connected to sys_id={sys_id} comp_id={comp_id}")
        else:
            print(f"  {SYM_FAIL} MAVLink: Connection failed. Flight Controller is unreachable.")
            print(f"          Troubleshoot: Verify connection cable (USB/Serial), ports in config.py, and ensure ArduSub SITL or Pixhawk is powered on.")
            self.results["mavlink"] = ("FAIL", "MAVLink connection failed.")
            self.mav = None

    # ──────────────────────────────────────────────────────────────────────────
    # Check 7: Live Telemetry Stream Validation
    # ──────────────────────────────────────────────────────────────────────────
    def run_check_telemetry(self):
        print(f"\n{COLOR_BOLD}[7/8] Validating Telemetry Messages Stream...{COLOR_RESET}")

        if not self.mav or not self.mav.is_connected:
            print(f"  {SYM_FAIL} MAVLink link not connected. Skipping telemetry stream validation.")
            self.results["telemetry"] = ("FAIL", "No active MAVLink link to listen for telemetry.")
            return

        print("  Listening to live MAVLink messages (3 seconds duration)...")
        received_types = set()
        battery_volts = None
        attitude = {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}
        
        start_time = time.time()
        # Temp local reader callback to capture diagnostics
        def temp_callback(msg):
            msg_type = msg.get_type()
            received_types.add(msg_type)
            
            nonlocal battery_volts, attitude
            if msg_type == "SYS_STATUS":
                battery_volts = msg.voltage_battery / 1000.0  # mV to Volts
            elif msg_type == "ATTITUDE":
                # Convert radians to degrees
                import math
                attitude["roll"] = math.degrees(msg.roll)
                attitude["pitch"] = math.degrees(msg.pitch)
                attitude["yaw"] = math.degrees(msg.yaw)

        # Inject callback
        self.mav.on_message_callback = temp_callback

        # Wait 3 seconds to gather telemetry messages
        time.sleep(3.0)

        # Cleanup callback
        self.mav.on_message_callback = None

        print(f"  {SYM_INFO} Message types captured: {', '.join(sorted(received_types)) if received_types else 'None'}")
        
        # Check critical message types
        critical_msgs = ["HEARTBEAT", "SYS_STATUS", "ATTITUDE"]
        missing_msgs = [m for m in critical_msgs if m not in received_types]

        for msg in critical_msgs:
            if msg in received_types:
                print(f"  {SYM_PASS} Telemetry: Message '{msg}' received.")
            else:
                print(f"  {SYM_FAIL} Telemetry: Message '{msg}' is MISSING in stream.")

        # Print current values parsed
        if battery_volts is not None:
            print(f"  {SYM_INFO} Battery Voltage : {COLOR_CYAN}{battery_volts:.2f} V{COLOR_RESET}")
        else:
            print(f"  {SYM_WARN} Battery Voltage : Could not read SYS_STATUS voltage.")

        print(f"  {SYM_INFO} ROV Orientation : Roll: {attitude['roll']:.1f}° | Pitch: {attitude['pitch']:.1f}° | Yaw: {attitude['yaw']:.1f}°")

        if len(missing_msgs) == 0:
            self.results["telemetry"] = ("PASS", "Telemetry stream active with correct telemetry frame updates.")
        else:
            self.results["telemetry"] = ("WARN", f"Active connection, but missing telemetry frames: {', '.join(missing_msgs)}")

    # ──────────────────────────────────────────────────────────────────────────
    # Check 8: Interactive Control & Actuators Check
    # ──────────────────────────────────────────────────────────────────────────
    def run_interactive_actuator_tests(self):
        print(f"\n{COLOR_BOLD}[8/8] Interactive Hardware Actuator Controls...{COLOR_RESET}")

        if not self.mav or not self.mav.is_connected:
            print(f"  {SYM_FAIL} MAVLink link not connected. Actuator control tests will be skipped.")
            self.results["actuators"] = ("SKIPPED", "No active MAVLink link to command actuators.")
            return

        # --- A. LED / Light Relay Test ---
        print(f"\n  {COLOR_BOLD}A. LED (Relay) Test{COLOR_RESET}")
        confirm = input("     Do you want to toggle the LED Lights? (y/N): ").strip().lower()
        if confirm == "y" or confirm == "yes":
            print("     -> Sending command: Turn LED ON...")
            self.mav.light(True)
            time.sleep(2.0)
            print("     -> Sending command: Turn LED OFF...")
            self.mav.light(False)
            time.sleep(1.0)
            led_status = input("     Did the LED turn ON and OFF? (y/N): ").strip().lower()
            if led_status == "y" or led_status == "yes":
                self.results["actuator_led"] = ("PASS", "LED relay triggered successfully.")
            else:
                self.results["actuator_led"] = ("FAIL", "LED relay failed to verify visually.")
        else:
            print("     LED test skipped.")
            self.results["actuator_led"] = ("SKIPPED", "User skipped LED test.")

        # --- B. Gripper Servo Test ---
        print(f"\n  {COLOR_BOLD}B. Gripper (Servo) Test{COLOR_RESET}")
        confirm = input("     Do you want to cycle the Gripper? (y/N): ").strip().lower()
        if confirm == "y" or confirm == "yes":
            print("     -> Sending command: OPEN GRIPPER...")
            self.mav.gripper("open")
            time.sleep(2.0)
            print("     -> Sending command: CLOSE GRIPPER...")
            self.mav.gripper("close")
            time.sleep(2.0)
            gripper_status = input("     Did the gripper open and close successfully? (y/N): ").strip().lower()
            if gripper_status == "y" or gripper_status == "yes":
                self.results["actuator_gripper"] = ("PASS", "Gripper servo triggered successfully.")
            else:
                self.results["actuator_gripper"] = ("FAIL", "Gripper failed to verify visually.")
        else:
            print("     Gripper test skipped.")
            self.results["actuator_gripper"] = ("SKIPPED", "User skipped gripper test.")

        # --- C. Thruster/Motor spin test ---
        print(f"\n  {COLOR_BOLD}C. Motor/Thrusters Spin Test{COLOR_RESET}")
        print(f"     {COLOR_RED}{COLOR_BOLD}================================================================={COLOR_RESET}")
        print(f"     {COLOR_RED}{COLOR_BOLD}⚠️  DANGER: PROPELLERS WILL SPIN FOR 1 SECOND EACH DURING TEST!{COLOR_RESET}")
        print(f"     {COLOR_RED}{COLOR_BOLD}KEEP HANDS, HAIR, AND LOOSE OBJECTS CLEAR OF ALL ROV THRUSTERS!{COLOR_RESET}")
        print(f"     {COLOR_RED}{COLOR_BOLD}================================================================={COLOR_RESET}")
        
        confirm = input("     Do you want to test the Motors/Thrusters? (y/N): ").strip().lower()
        if confirm == "y" or confirm == "yes":
            arm_confirm = input("     To arm the ROV, please type 'ARM' to proceed: ").strip()
            if arm_confirm == "ARM":
                try:
                    # Switch mode to MANUAL to allow motor inputs
                    print("     -> Set flight mode: MANUAL...")
                    self.mav.set_mode("MANUAL")
                    time.sleep(1.0)
                    
                    # Send Arm Command
                    print("     -> Sending: ARM ROV...")
                    self.mav.arm()
                    time.sleep(1.5)
                    
                    from config import (
                        AUTONOMOUS_RC_CH_LATERAL,
                        AUTONOMOUS_RC_CH_FORWARD,
                        AUTONOMOUS_RC_CH_YAW,
                        RC_NEUTRAL_PWM,
                    )
                    
                    # Spin test parameters: slight spin above neutral (e.g., 1540 PWM)
                    test_pwm = 1545

                    # 1. Lateral Motor spin
                    print("     [1/3] Spinning Lateral thrusters (Roll/Lateral)...")
                    self.mav.rc_override({AUTONOMOUS_RC_CH_LATERAL: test_pwm})
                    time.sleep(1.0)
                    self.mav.rc_override({AUTONOMOUS_RC_CH_LATERAL: RC_NEUTRAL_PWM})
                    time.sleep(0.5)

                    # 2. Forward Motor spin
                    print("     [2/3] Spinning Forward thrusters (Pitch/Forward)...")
                    self.mav.rc_override({AUTONOMOUS_RC_CH_FORWARD: test_pwm})
                    time.sleep(1.0)
                    self.mav.rc_override({AUTONOMOUS_RC_CH_FORWARD: RC_NEUTRAL_PWM})
                    time.sleep(0.5)

                    # 3. Yaw Motor spin
                    print("     [3/3] Spinning Yaw thrusters...")
                    self.mav.rc_override({AUTONOMOUS_RC_CH_YAW: test_pwm})
                    time.sleep(1.0)
                    self.mav.rc_override({AUTONOMOUS_RC_CH_YAW: RC_NEUTRAL_PWM})
                    time.sleep(0.5)

                    # Return all RC channels to neutral
                    print("     -> Neutralizing control channels...")
                    self.mav.rc_override({
                        AUTONOMOUS_RC_CH_LATERAL: RC_NEUTRAL_PWM,
                        AUTONOMOUS_RC_CH_FORWARD: RC_NEUTRAL_PWM,
                        AUTONOMOUS_RC_CH_YAW: RC_NEUTRAL_PWM
                    })

                    # Send Disarm Command
                    print("     -> Sending: DISARM ROV...")
                    self.mav.disarm()
                    time.sleep(1.5)

                    motor_status = input("     Did you hear/see the thrusters spin correctly? (y/N): ").strip().lower()
                    if motor_status == "y" or motor_status == "yes":
                        self.results["actuator_motors"] = ("PASS", "Thrusters spin test completed & verified.")
                    else:
                        self.results["actuator_motors"] = ("FAIL", "Motors spin test completed, but malfunction observed.")
                except Exception as e:
                    print(f"     {SYM_FAIL} Thruster test error: {e}")
                    # Ensure safety: Disarm
                    try:
                        self.mav.rc_override({
                            AUTONOMOUS_RC_CH_LATERAL: 1500,
                            AUTONOMOUS_RC_CH_FORWARD: 1500,
                            AUTONOMOUS_RC_CH_YAW: 1500
                        })
                        self.mav.disarm()
                    except:
                        pass
                    self.results["actuator_motors"] = ("FAIL", f"Thruster test error: {e}")
            else:
                print("     Arming validation failed. Thruster test aborted.")
                self.results["actuator_motors"] = ("SKIPPED", "User did not type 'ARM' correctly.")
        else:
            print("     Thruster/Motor test skipped.")
            self.results["actuator_motors"] = ("SKIPPED", "User skipped motor test.")

    # ──────────────────────────────────────────────────────────────────────────
    # Clean up MAVLink Connection
    # ──────────────────────────────────────────────────────────────────────────
    def cleanup(self):
        if self.mav:
            print(f"\nDisconnecting MAVLink link...")
            self.mav.disconnect()
            self.mav = None

    # ──────────────────────────────────────────────────────────────────────────
    # Generate Diagnostis Report
    # ──────────────────────────────────────────────────────────────────────────
    def generate_report(self):
        print(f"""\n{COLOR_CYAN}{COLOR_BOLD}======================================================================
                         PREFLIGHT REPORT
======================================================================{COLOR_RESET}""")

        pass_count = 0
        warn_count = 0
        fail_count = 0
        skipped_count = 0

        # Print all tests
        print(f"{COLOR_BOLD}{'Subsystem / Test Category':<35} | {'Status':<12} | {'Description'}{COLOR_RESET}")
        print("-" * 80)
        
        for cat, (status, desc) in self.results.items():
            if status == "PASS":
                status_str = f"{COLOR_GREEN}{status:<12}{COLOR_RESET}"
                pass_count += 1
            elif status == "WARN":
                status_str = f"{COLOR_YELLOW}{status:<12}{COLOR_RESET}"
                warn_count += 1
            elif status == "FAIL":
                status_str = f"{COLOR_RED}{status:<12}{COLOR_RESET}"
                fail_count += 1
            else:
                status_str = f"{COLOR_BLUE}{status:<12}{COLOR_RESET}"
                skipped_count += 1

            # Format the output category name
            cat_name = cat.replace("_", " ").title()
            print(f"{cat_name:<35} | {status_str} | {desc}")

        print("-" * 80)
        
        # Summary calculations
        total_tests = len(self.results)
        print(f"Total Diagnosed categories: {total_tests}")
        print(f" - {COLOR_GREEN}PASS   : {pass_count}{COLOR_RESET}")
        if warn_count > 0:
            print(f" - {COLOR_YELLOW}WARNING: {warn_count}{COLOR_RESET}")
        if fail_count > 0:
            print(f" - {COLOR_RED}FAIL   : {fail_count}{COLOR_RESET}")
        if skipped_count > 0:
            print(f" - {COLOR_BLUE}SKIPPED: {skipped_count}{COLOR_RESET}")

        print("-" * 80)

        # Final recommendation
        if fail_count > 0:
            print(f"\n{COLOR_RED}{COLOR_BOLD} RECOMMENDATION: DO NOT LAUNCH!{COLOR_RESET}")
            print(f"  There are critical failures ({fail_count} failures) in your ROV system components.")
            print("  Please review the detailed logs above and troubleshoot the failing categories.")
        elif warn_count > 0:
            print(f"\n{COLOR_YELLOW}{COLOR_BOLD} RECOMMENDATION: PROCEED WITH CAUTION!{COLOR_RESET}")
            print("  All systems are functioning, but warnings exist. Double check resources/models.")
        else:
            print(f"\n{COLOR_GREEN}{COLOR_BOLD} RECOMMENDATION: READY FOR DEPLOYMENT!{COLOR_RESET}")
            print("  All preflight checks have passed. Your ROV is ready to launch!")
            
        print(f"{COLOR_CYAN}{COLOR_BOLD}======================================================================{COLOR_RESET}\n")


def main():
    tester = PreflightTester()
    try:
        tester.print_banner()
        tester.run_check_dependencies()
        tester.run_check_vision_engines()
        tester.run_check_system_resources()
        tester.run_check_camera_services()
        tester.run_check_core_api_websocket()
        tester.run_check_mavlink_link()
        tester.run_check_telemetry()
        tester.run_interactive_actuator_tests()
    except KeyboardInterrupt:
        print(f"\n\n{COLOR_YELLOW}Preflight test aborted by user.{COLOR_RESET}")
    finally:
        tester.cleanup()
        tester.generate_report()


if __name__ == "__main__":
    main()
