# core/mavlink.py

import threading
import logging
import time
from pymavlink import mavutil
from typing import Dict, Optional
from config import (
    MAVLINK_CONNECTION_STRING,
    MAVLINK_BAUD,
    MAVLINK_SOURCE_SYSTEM,
    MAVLINK_HEARTBEAT_INTERVAL,
    SERVO_GRIPPER_CHANNEL,
    SERVO_GRIPPER_OPEN_PWM,
    SERVO_GRIPPER_CLOSE_PWM,
    RELAY_LIGHT_INDEX,
    RC_NEUTRAL_PWM,
)

logger = logging.getLogger(__name__)

# Retry connect jika SITL belum ready
CONNECT_RETRY_INTERVAL = 5.0   # detik antar retry
CONNECT_MAX_RETRIES    = 12    # 12 × 5 detik = 1 menit


class MAVLinkBridge:
    def __init__(self):
        self._conn: Optional[mavutil.mavfile] = None
        self._lock = threading.Lock()
        self._connected = False
        self._reader_thread:    Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self.on_message_callback = None
        
        # Dipakai failsafe untuk cek apakah Pixhawk masih mengirim heartbeat
        self._last_heartbeat_time = None

    # ──────────────────────────────────────────
    # Koneksi
    # ──────────────────────────────────────────
    def connect(self) -> bool:
        """
        Connect ke Pixhawk/SITL dengan retry otomatis.
        SITL UDP  : set MAVLINK_CONNECTION_STRING = 'udpin:0.0.0.0:14550'
        Serial    : set MAVLINK_CONNECTION_STRING = '/dev/ttyUSB0'
        """
        for attempt in range(1, CONNECT_MAX_RETRIES + 1):
            logger.info(
                f"[MAVLink] Connect attempt {attempt}/{CONNECT_MAX_RETRIES} "
                f"{MAVLINK_CONNECTION_STRING}"
            )
            try:
                self._conn = mavutil.mavlink_connection(
                    MAVLINK_CONNECTION_STRING,
                    baud=MAVLINK_BAUD,
                    source_system=MAVLINK_SOURCE_SYSTEM,
                )
                logger.info("[MAVLink] Menunggu heartbeat (timeout=15s)...")
                hb = self._conn.wait_heartbeat(timeout=15)
                if hb is None:
                    raise TimeoutError("Heartbeat tidak datang dalam 15 detik")

                self._connected = True
                logger.info(
                    f"[MAVLink] Terhubung! "
                    f"system={self._conn.target_system} "
                    f"component={self._conn.target_component}"
                )
                self._start_reader()
                self._start_heartbeat()
                return True

            except Exception as e:
                logger.warning(f"[MAVLink] Gagal connect: {e}")
                if self._conn:
                    try: self._conn.close()
                    except: pass
                    self._conn = None
                if attempt < CONNECT_MAX_RETRIES:
                    logger.info(f"[MAVLink] Retry dalam {CONNECT_RETRY_INTERVAL}s...")
                    time.sleep(CONNECT_RETRY_INTERVAL)

        logger.error("[MAVLink] Semua retry habis. MAVLink tidak terhubung.")
        return False

    def disconnect(self):
        self._connected = False
        if self._conn:
            try: self._conn.close()
            except: pass
            self._conn = None
        logger.info("[MAVLink] Koneksi ditutup.")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._conn is not None
    
    @property
    def last_heartbeat_time(self):
        """
        Timestamp heartbeat terakhir dari Pixhawk.
        Dipakai oleh FailsafeWatchdog.
        """
        return self._last_heartbeat_time

    # ──────────────────────────────────────────
    # Background threads
    # ──────────────────────────────────────────
    def _start_reader(self):
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="MAVLinkReader"
        )
        self._reader_thread.start()

    def _start_heartbeat(self):
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="MAVLinkHeartbeat"
        )
        self._heartbeat_thread.start()

    def _reader_loop(self):
        logger.info("[MAVLink] Reader thread mulai")
        while self._connected and self._conn:
            try:
                # FIX: filter BAD_DATA agar tidak crash di callback
                msg = self._conn.recv_match(blocking=True, timeout=1.0)
                if msg is None:
                    continue
                # FIX: skip frame rusak sebelum sampai ke telemetry
                if msg.get_type() == "BAD_DATA":
                    logger.debug("[MAVLink] BAD_DATA frame diabaikan")
                    continue
                if msg.get_type() == "HEARTBEAT":
                    self._last_heartbeat_time = time.time()
                if self.on_message_callback:
                    try:
                        self.on_message_callback(msg)
                    except Exception as cb_err:
                        # Jangan biarkan crash di callback matikan reader thread
                        logger.warning(f"[MAVLink] Callback error: {cb_err}")
            except Exception as e:
                if self._connected:
                    logger.warning(f"[MAVLink] Error baca pesan: {e}")
                    time.sleep(0.5)

    def _heartbeat_loop(self):
        while self._connected:
            try:
                with self._lock:
                    if self._conn:
                        self._conn.mav.heartbeat_send(
                            mavutil.mavlink.MAV_TYPE_GCS,
                            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                            0, 0, 0
                        )
            except Exception as e:
                logger.warning(f"[MAVLink] Error heartbeat: {e}")
            time.sleep(MAVLINK_HEARTBEAT_INTERVAL)

    # ──────────────────────────────────────────
    # Command senders
    # ──────────────────────────────────────────
    def arm(self):
        logger.info("[MAVLink] ARM")
        self._command_long(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, param1=1)

    def disarm(self):
        logger.info("[MAVLink] DISARM")
        self._command_long(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, param1=0)

    def set_mode(self, mode_name: str):
        if not self._conn:
            return
        mode_id = self._conn.mode_mapping().get(mode_name.upper())
        if mode_id is None:
            logger.error(f"[MAVLink] Mode '{mode_name}' tidak dikenal")
            return
        logger.info(f"[MAVLink] SET_MODE -> {mode_name} (id={mode_id})")
        with self._lock:
            self._conn.mav.set_mode_send(
                self._conn.target_system,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                mode_id
            )

    def gripper(self, action: str):
        pwm = SERVO_GRIPPER_OPEN_PWM if action == "open" else SERVO_GRIPPER_CLOSE_PWM
        logger.info(f"[MAVLink] GRIPPER {action} -> PWM={pwm}")
        self._command_long(
            mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
            param1=float(SERVO_GRIPPER_CHANNEL),
            param2=float(pwm),
        )

    def light(self, state: bool):
        logger.info(f"[MAVLink] LIGHT {'ON' if state else 'OFF'}")
        self._command_long(
            mavutil.mavlink.MAV_CMD_DO_SET_RELAY,
            param1=float(RELAY_LIGHT_INDEX),
            param2=1.0 if state else 0.0,
        )

    def rc_override(self, channels: Dict[int, int]):
        if not self._conn or not self._connected:
            return
        UINT16_MAX = 65535
        # FIX: siapkan 18 channel (MAVLink v2), bukan hanya 8
        rc = [UINT16_MAX] * 18
        for ch, pwm in channels.items():
            if 1 <= ch <= 18:
                rc[ch - 1] = int(pwm)

        with self._lock:
            # Kirim 8 channel wajib + 10 channel extended (MAVLink v2)
            self._conn.mav.rc_channels_override_send(
                self._conn.target_system,
                self._conn.target_component,
                rc[0], rc[1], rc[2], rc[3],
                rc[4], rc[5], rc[6], rc[7],
            )

    def _command_long(self, command: int, **params):
        if not self._conn or not self._connected:
            logger.warning("[MAVLink] Tidak terhubung, command diabaikan.")
            return
        kwargs = {f"param{i}": params.get(f"param{i}", 0.0) for i in range(1, 8)}
        with self._lock:
            self._conn.mav.command_long_send(
                self._conn.target_system,
                self._conn.target_component,
                command, 0,
                kwargs["param1"], kwargs["param2"], kwargs["param3"],
                kwargs["param4"], kwargs["param5"], kwargs["param6"],
                kwargs["param7"],
            )