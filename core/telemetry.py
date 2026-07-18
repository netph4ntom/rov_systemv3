# core/telemetry.py

import threading
import logging
import time
from pymavlink import mavutil

logger = logging.getLogger(__name__)

ARDUSUB_MODES = {
    0:  "MANUAL",
    1:  "ACRO",
    2:  "LEARNING",
    3:  "STEERING",
    4:  "HOLD",
    5:  "GUIDED",
    6:  "INITIALIZE",
    7:  "AUTO",
    8:  "RTL",
    9:  "LOITER",
    16: "POSHOLD",
    19: "MANUAL",
    20: "ACRO",
    21: "STABILIZE",
    23: "DEPTH_HOLD",
}

# FIX: hanya proses heartbeat dari autopilot (component 1), bukan gimbal/dll
AUTOPILOT_COMPONENT_ID = 1


class TelemetryManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._state: dict = {
            "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
            "depth": 0.0,
            "battery_voltage": 0.0, "battery_current": 0.0, "battery_remaining": 100,
            "lat": 0.0, "lon": 0.0, "gps_fix": 0,
            "armed": False, "mode": "UNKNOWN",
            "accel_x": 0.0, "accel_y": 0.0, "accel_z": 0.0,
            "gyro_x":  0.0, "gyro_y":  0.0, "gyro_z":  0.0,
            "last_update": None,
        }
        self.on_telemetry_update = None

    def handle_message(self, msg):
        msg_type = msg.get_type()
        if   msg_type == "ATTITUDE":                          self._handle_attitude(msg)
        elif msg_type in ("SCALED_PRESSURE2", "SCALED_PRESSURE"): self._handle_depth(msg)
        elif msg_type == "BATTERY_STATUS":                    self._handle_battery_status(msg)
        elif msg_type == "SYS_STATUS":                        self._handle_sys_status(msg)
        elif msg_type == "GPS_RAW_INT":                       self._handle_gps(msg)
        elif msg_type == "HEARTBEAT":                         self._handle_heartbeat(msg)
        elif msg_type == "RAW_IMU":                           self._handle_imu(msg)
        elif msg_type == "VFR_HUD":                           self._handle_vfr_hud(msg)

    def get_state(self) -> dict:
        with self._lock:
            return dict(self._state)

    # ──────────────────────────────────────────
    # Handlers
    # ──────────────────────────────────────────
    def _handle_attitude(self, msg):
        import math
        with self._lock:
            self._state["roll"]  = round(math.degrees(msg.roll),  2)
            self._state["pitch"] = round(math.degrees(msg.pitch), 2)
            self._state["yaw"]   = round(math.degrees(msg.yaw),   2)
        # FIX: _notify() di LUAR lock agar tidak deadlock saat SocketIO emit
        self._notify()

    def _handle_depth(self, msg):
        SURFACE_PRESSURE_MBAR = 1013.25
        pressure_mbar = msg.press_abs
        depth_m = (pressure_mbar - SURFACE_PRESSURE_MBAR) / 100.0
        depth_m = max(0.0, round(depth_m, 3))
        # FIX: log press_abs supaya mudah debug saat SITL depth selalu 0
        logger.debug(f"[Telemetry] press_abs={pressure_mbar:.2f} mbar → depth={depth_m:.3f} m")
        with self._lock:
            # Low-pass filter (Alpha = 0.85) untuk meredam noise turbulensi air pada sensor tekanan
            prev_depth = self._state["depth"]
            if prev_depth == 0.0:
                self._state["depth"] = depth_m
            else:
                self._state["depth"] = round((prev_depth * 0.85) + (depth_m * 0.15), 3)
        self._notify()

    def _handle_battery_status(self, msg):
        with self._lock:
            if msg.voltages and msg.voltages[0] != 65535:
                total_mv = sum(v for v in msg.voltages if v != 65535)
                self._state["battery_voltage"] = round(total_mv / 1000.0, 2)
            if msg.current_battery != -1:
                self._state["battery_current"] = round(msg.current_battery / 100.0, 2)
            if msg.battery_remaining != -1:
                self._state["battery_remaining"] = msg.battery_remaining
        self._notify()

    def _handle_sys_status(self, msg):
        with self._lock:
            if self._state["battery_voltage"] == 0.0:
                self._state["battery_voltage"] = round(msg.voltage_battery / 1000.0, 2)
            if msg.current_battery != -1:
                self._state["battery_current"] = round(msg.current_battery / 100.0, 2)
        self._notify()

    def _handle_gps(self, msg):
        with self._lock:
            self._state["lat"]     = msg.lat / 1e7
            self._state["lon"]     = msg.lon / 1e7
            self._state["gps_fix"] = msg.fix_type
        self._notify()

    def _handle_heartbeat(self, msg):
        # FIX: filter — hanya proses HB dari autopilot (bukan gimbal/camera/dll)
        if msg.get_srcComponent() != AUTOPILOT_COMPONENT_ID:
            return

        armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        mode  = ARDUSUB_MODES.get(msg.custom_mode, f"MODE_{msg.custom_mode}")
        logger.debug(f"[Telemetry] HB: armed={armed} mode={mode} (custom_mode={msg.custom_mode})")

        with self._lock:
            self._state["armed"] = armed
            self._state["mode"]  = mode
        self._notify()

    def _handle_imu(self, msg):
        with self._lock:
            self._state["accel_x"] = round(msg.xacc  / 1000.0, 4)
            self._state["accel_y"] = round(msg.yacc  / 1000.0, 4)
            self._state["accel_z"] = round(msg.zacc  / 1000.0, 4)
            self._state["gyro_x"]  = round(msg.xgyro / 1000.0, 4)
            self._state["gyro_y"]  = round(msg.ygyro / 1000.0, 4)
            self._state["gyro_z"]  = round(msg.zgyro / 1000.0, 4)
        self._notify()

    def _handle_vfr_hud(self, msg):
        with self._lock:
            if self._state["depth"] == 0.0:
                self._state["depth"] = round(abs(msg.alt), 3)
        self._notify()

    def _notify(self):
        # FIX: update timestamp dan panggil callback di LUAR lock
        # untuk menghindari deadlock saat SocketIO emit blocking
        with self._lock:
            self._state["last_update"] = time.time()
        if self.on_telemetry_update:
            self.on_telemetry_update(self.get_state())