# core/failsafe.py
# ═══════════════════════════════════════════════════════════════════════
# ROV Failsafe & Watchdog System
# ═══════════════════════════════════════════════════════════════════════
#
# Arsitektur dua lapis:
#   Layer 1 (file ini) → Raspberry Pi level: monitor semua subsistem,
#                         recovery otomatis, kirim notifikasi ke React
#   Layer 2            → Pixhawk level: failsafe bawaan flight controller
#
# ── Subsistem yang dimonitor ────────────────────────────────────────────
#   MAVLINK    : heartbeat Pixhawk (via _last_heartbeat_time di MAVLinkBridge)
#   DASHBOARD  : koneksi WebSocket dari React operator
#   TELEMETRY  : apakah data sensor masih fresh (tidak stale)
#   CAM_FRONT  : status kamera depan (via HTTP /health)
#   CAM_BOTTOM : status kamera bawah (via HTTP /health)
#   SYSTEM     : CPU, RAM, suhu CPU Raspberry Pi
#
# ── Severity Levels ─────────────────────────────────────────────────────
#   INFO     (0) → normal
#   WARNING  (1) → anomali, coba recovery otomatis
#   CRITICAL (2) → recovery gagal / bahaya → RC Override netral + MANUAL
#   EMERGENCY(3) → E-Stop operator atau multi-CRITICAL → DISARM + notif merah
#
# ── SocketIO events ke React ────────────────────────────────────────────
#   "failsafe_event"  → setiap anomali/recovery/eskalasi
#   "failsafe_status" → snapshot health seluruh subsistem (tiap loop)
#   "emergency_stop"  → khusus saat E-Stop aktif

import threading
import logging
import time
import psutil
import urllib.request
import json
from datetime import datetime
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Callable

from config import (
    FS_CHECK_INTERVAL,
    FS_MAVLINK_TIMEOUT,
    FS_DASHBOARD_TIMEOUT,
    FS_TELEMETRY_TIMEOUT,
    FS_CAMERA_HEALTH_URL_FRONT,
    FS_CAMERA_HEALTH_URL_BOTTOM,
    FS_CPU_WARN_PERCENT,
    FS_RAM_WARN_PERCENT,
    FS_TEMP_WARN_CELSIUS,
    FS_MAX_RECOVERY_ATTEMPTS,
    RC_NEUTRAL_PWM,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# Enums & Dataclasses
# ═══════════════════════════════════════════════

class Severity(IntEnum):
    INFO      = 0
    WARNING   = 1
    CRITICAL  = 2
    EMERGENCY = 3


class Sub:
    """Nama subsistem sebagai konstanta string."""
    MAVLINK    = "mavlink"
    DASHBOARD  = "dashboard"
    TELEMETRY  = "telemetry"
    CAM_FRONT  = "camera_front"
    CAM_BOTTOM = "camera_bottom"
    SYSTEM     = "system"


@dataclass
class SubHealth:
    name:              str
    ok:                bool      = True
    severity:          Severity  = Severity.INFO
    message:           str       = "OK"
    last_ok_time:      float     = field(default_factory=time.time)
    recovery_attempts: int       = 0
    fault_since:       float | None = None


# ═══════════════════════════════════════════════
# Kelas utama
# ═══════════════════════════════════════════════

class FailsafeWatchdog:
    """
    Watchdog utama ROV. Diinisialisasi di routes.py setelah semua
    komponen siap, lalu .start() dipanggil sekali.

    Inject sebelum start():
        fs.get_client_connected = lambda: bool  # ada React client?
    """

    def __init__(self, mav, tele, sio_emit: Callable):
        self._mav    = mav
        self._tele   = tele
        self._emit   = sio_emit
        self._lock   = threading.RLock()
        self._running = False
        self._thread: threading.Thread | None = None

        # Emergency state
        self._emergency_active = False
        self._emergency_reason = ""

        # Callback: apakah ada React client yang terkoneksi?
        self.get_client_connected: Callable[[], bool] = lambda: False

        # Health per subsistem
        self._health: dict[str, SubHealth] = {
            Sub.MAVLINK:    SubHealth(name=Sub.MAVLINK),
            Sub.DASHBOARD:  SubHealth(name=Sub.DASHBOARD),
            Sub.TELEMETRY:  SubHealth(name=Sub.TELEMETRY),
            Sub.CAM_FRONT:  SubHealth(name=Sub.CAM_FRONT),
            Sub.CAM_BOTTOM: SubHealth(name=Sub.CAM_BOTTOM),
            Sub.SYSTEM:     SubHealth(name=Sub.SYSTEM),
        }

        self._last_dashboard_seen: float = time.time()
        self._event_history: list[dict]  = []
        self._EVENT_HISTORY_MAX = 200

    # ═══════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════

    def start(self):
        """Start background watchdog thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name="FailsafeWatchdog"
        )
        self._thread.start()
        logger.info("[Failsafe] Watchdog dimulai")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[Failsafe] Watchdog dihentikan")

    def notify_dashboard_active(self):
        """Dipanggil dari SocketIO 'connect' → reset timer timeout dashboard."""
        with self._lock:
            self._last_dashboard_seen = time.time()
            h = self._health[Sub.DASHBOARD]
            if not h.ok:
                h.ok = True
                h.severity = Severity.INFO
                h.message = "Dashboard reconnected"
                h.recovery_attempts = 0
                h.fault_since = None

    def trigger_emergency_stop(self, reason: str = "Operator E-Stop"):
        """
        Trigger Emergency Stop manual dari operator.
        Dipanggil dari SocketIO handler di routes.py.
        """
        with self._lock:
            if self._emergency_active:
                return
        logger.critical(f"[Failsafe] EMERGENCY STOP dipicu: {reason}")
        self._execute_emergency(reason)

    def clear_emergency(self):
        """
        Clear emergency state setelah operator konfirmasi aman.
        Dipanggil dari SocketIO 'cmd_clear_emergency'.
        """
        with self._lock:
            if not self._emergency_active:
                return
            self._emergency_active = False
            self._emergency_reason = ""

        logger.info("[Failsafe] Emergency state di-clear oleh operator")
        self._append_and_emit("failsafe_event", {
            "timestamp": _now(), "subsystem": "all",
            "severity":  Severity.INFO.name,
            "message":   "Emergency cleared oleh operator",
            "action":    "clear_emergency",
        })

    def get_status(self) -> dict:
        """Snapshot health seluruh subsistem untuk REST endpoint."""
        with self._lock:
            return {
                "emergency_active": self._emergency_active,
                "emergency_reason": self._emergency_reason,
                "subsystems": {
                    name: {
                        "ok":               h.ok,
                        "severity":         h.severity.name,
                        "message":          h.message,
                        "recovery_attempts": h.recovery_attempts,
                        "fault_since":      h.fault_since,
                    }
                    for name, h in self._health.items()
                },
                "event_count": len(self._event_history),
                "timestamp":   _now(),
            }

    def get_event_history(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(reversed(self._event_history[-limit:]))

    # ═══════════════════════════════════════════
    # Watchdog loop
    # ═══════════════════════════════════════════

    def _watchdog_loop(self):
        logger.info("[Failsafe] Watchdog loop berjalan")
        while self._running:
            try:
                self._check_mavlink()
                self._check_dashboard()
                self._check_telemetry()
                self._check_cameras()
                self._check_system()
                self._do_emit("failsafe_status", self.get_status())
            except Exception as e:
                logger.error(f"[Failsafe] Error di watchdog loop: {e}", exc_info=True)
            time.sleep(FS_CHECK_INTERVAL)

    # ═══════════════════════════════════════════
    # Checks
    # ═══════════════════════════════════════════

    def _check_mavlink(self):
        if not self._mav:
            return

        now         = time.time()
        connected   = self._mav.is_connected
        last_hb     = getattr(self._mav, "_last_heartbeat_time", None)
        hb_age      = (now - last_hb) if last_hb else float("inf")
        hb_timeout  = hb_age > FS_MAVLINK_TIMEOUT

        if not connected or hb_timeout:
            msg = (
                "MAVLink tidak terhubung" if not connected
                else f"Heartbeat timeout {hb_age:.1f}s > {FS_MAVLINK_TIMEOUT}s"
            )
            self._fault(Sub.MAVLINK, msg, Severity.CRITICAL)
        else:
            self._ok(Sub.MAVLINK, f"Heartbeat OK ({hb_age:.1f}s ago)")

    def _check_dashboard(self):
        now    = time.time()
        age    = now - self._last_dashboard_seen
        client = self.get_client_connected()

        if not client and age > FS_DASHBOARD_TIMEOUT:
            self._fault(Sub.DASHBOARD,
                        f"Dashboard tidak terhubung {age:.0f}s",
                        Severity.WARNING)
        else:
            self._ok(Sub.DASHBOARD, "Dashboard terhubung")

    def _check_telemetry(self):
        if not self._tele:
            return
        state       = self._tele.get_state()
        last_update = state.get("last_update")
        if last_update is None:
            self._fault(Sub.TELEMETRY, "Belum ada data telemetry", Severity.WARNING)
            return
        age = time.time() - last_update
        if age > FS_TELEMETRY_TIMEOUT:
            self._fault(Sub.TELEMETRY, f"Telemetry stale {age:.1f}s", Severity.WARNING)
        else:
            self._ok(Sub.TELEMETRY, f"Fresh ({age:.2f}s ago)")

    def _check_cameras(self):
        checks = [
            (Sub.CAM_FRONT,  FS_CAMERA_HEALTH_URL_FRONT),
            (Sub.CAM_BOTTOM, FS_CAMERA_HEALTH_URL_BOTTOM),
        ]
        for name, url in checks:
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    data   = json.loads(resp.read().decode())
                    status = data.get("status", "error")
                    if status == "ok":
                        self._ok(name, "Kamera OK")
                    else:
                        self._fault(name, f"Status kamera: {status}", Severity.WARNING)
            except Exception as e:
                self._fault(name, f"Health check gagal: {e}", Severity.WARNING)

    def _check_system(self):
        issues = []

        cpu_pct = psutil.cpu_percent(interval=None)
        if cpu_pct > FS_CPU_WARN_PERCENT:
            issues.append(f"CPU {cpu_pct:.0f}%")

        ram_pct = psutil.virtual_memory().percent
        if ram_pct > FS_RAM_WARN_PERCENT:
            issues.append(f"RAM {ram_pct:.0f}%")

        try:
            temps = psutil.sensors_temperatures()
            cpu_temp = None
            for key in ("cpu_thermal", "coretemp", "cpu-thermal"):
                if key in temps and temps[key]:
                    cpu_temp = temps[key][0].current
                    break
            if cpu_temp and cpu_temp > FS_TEMP_WARN_CELSIUS:
                issues.append(f"Suhu {cpu_temp:.1f}°C")
        except Exception:
            pass

        if issues:
            self._fault(Sub.SYSTEM, "Resource tinggi: " + ", ".join(issues), Severity.WARNING)
        else:
            self._ok(Sub.SYSTEM, f"CPU {cpu_pct:.0f}% | RAM {ram_pct:.0f}%")

    # ═══════════════════════════════════════════
    # State machine
    # ═══════════════════════════════════════════

    def _ok(self, name: str, message: str = "OK"):
        """Subsistem kembali normal."""
        with self._lock:
            h = self._health[name]
            was_fault = not h.ok
            h.ok                = True
            h.severity          = Severity.INFO
            h.message           = message
            h.last_ok_time      = time.time()
            h.recovery_attempts = 0
            h.fault_since       = None

        if was_fault:
            entry = {
                "timestamp": _now(), "subsystem": name,
                "severity":  Severity.INFO.name,
                "message":   f"{name} kembali normal: {message}",
                "action":    "auto_recovered",
            }
            self._append_and_emit("failsafe_event", entry)

    def _fault(self, name: str, message: str, severity: Severity):
        """
        Catat fault dan tentukan recovery action.
        State machine:
          attempt < MAX  → coba recovery sesuai subsistem
          attempt >= MAX → eskalasi severity
        """
        with self._lock:
            h   = self._health[name]
            now = time.time()

            if h.ok:
                h.fault_since = now
                logger.warning(f"[Failsafe] FAULT: {name} — {message}")

            h.ok       = False
            h.severity = severity
            h.message  = message

            action  = self._pick_action(name, severity, h.recovery_attempts)
            h.recovery_attempts += 1
            attempts = h.recovery_attempts

        entry = {
            "timestamp": _now(), "subsystem": name,
            "severity":  severity.name,
            "message":   message,
            "action":    action,
        }
        self._append_and_emit("failsafe_event", entry)
        self._run_action(name, severity, action, attempts)

    def _pick_action(self, name: str, severity: Severity, attempts: int) -> str:
        """Pilih string action berdasarkan subsistem, severity, dan attempts."""
        if severity == Severity.WARNING:
            if attempts < FS_MAX_RECOVERY_ATTEMPTS:
                return {
                    Sub.MAVLINK:    "reconnect_mavlink",
                    Sub.DASHBOARD:  "notify_operator",
                    Sub.TELEMETRY:  "wait_telemetry",
                    Sub.CAM_FRONT:  "log_camera_front",
                    Sub.CAM_BOTTOM: "log_camera_bottom",
                    Sub.SYSTEM:     "log_resources",
                }.get(name, "notify_only")
            else:
                return "escalate_critical"

        if severity == Severity.CRITICAL:
            return "rc_override_neutral"

        if severity == Severity.EMERGENCY:
            return "emergency_stop_full"

        return "notify_only"

    def _run_action(self, name: str, severity: Severity, action: str, attempts: int):
        """Eksekusi action di luar lock."""

        if action == "reconnect_mavlink":
            threading.Thread(
                target=self._do_reconnect_mavlink,
                daemon=True,
                name="MAVReconnect"
            ).start()

        elif action in ("log_camera_front", "log_camera_bottom"):
            logger.warning(f"[Failsafe] Kamera {name} fault (attempt #{attempts}) - perlu cek manual")

        elif action == "log_resources":
            logger.warning(f"[Failsafe] System resource tinggi: {self._health[name].message}")

        elif action == "notify_operator":
            logger.warning(f"[Failsafe] Dashboard timeout (attempt #{attempts})")

        elif action == "wait_telemetry":
            logger.warning(f"[Failsafe] Menunggu telemetry fresh (attempt #{attempts})")

        elif action == "escalate_critical":
            logger.error(f"[Failsafe] Max recovery untuk {name}, eskalasi ke CRITICAL")
            self._execute_critical(f"{name} max recovery: {self._health[name].message}")

        elif action == "rc_override_neutral":
            self._execute_critical(f"{name}: {self._health[name].message}")

        elif action == "emergency_stop_full":
            self._execute_emergency(f"{name}: {self._health[name].message}")

    # ═══════════════════════════════════════════
    # Eksekusi tindakan kritis
    # ═══════════════════════════════════════════

    def _do_reconnect_mavlink(self):
        """Jalankan di background thread: disconnect -> wait -> connect."""
        logger.info("[Failsafe] Mencoba reconnect MAVLink...")
        try:
            if self._mav:
                self._mav.disconnect()
                time.sleep(1.5)
                success = self._mav.connect()
                if success:
                    logger.info("[Failsafe] MAVLink reconnect berhasil")
                    with self._lock:
                        self._health[Sub.MAVLINK].recovery_attempts = 0
                else:
                    logger.error("[Failsafe] MAVLink reconnect gagal")
        except Exception as e:
            logger.error(f"[Failsafe] Reconnect error: {e}")

    def _execute_critical(self, reason: str):
        """
        CRITICAL: stop semua thruster (RC Override netral) + set MANUAL.
        Tidak DISARM - biarkan Pixhawk punya kendali penuh.
        """
        logger.error(f"[Failsafe] CRITICAL: {reason}")

        if self._mav and self._mav.is_connected:
            # Stop semua thruster
            try:
                self._mav.rc_override({ch: RC_NEUTRAL_PWM for ch in range(1, 9)})
                logger.info("[Failsafe] RC Override NEUTRAL dikirim")
            except Exception as e:
                logger.error(f"[Failsafe] RC Override gagal: {e}")

            # Ganti mode ke MANUAL
            try:
                self._mav.set_mode("MANUAL")
                logger.info("[Failsafe] Mode -> MANUAL")
            except Exception as e:
                logger.error(f"[Failsafe] Set mode gagal: {e}")

        self._append_and_emit("failsafe_event", {
            "timestamp": _now(), "subsystem": "system",
            "severity":  Severity.CRITICAL.name,
            "message":   reason,
            "action":    "rc_neutral + set_manual",
        })

    def _execute_emergency(self, reason: str):
        """
        EMERGENCY: RC Override netral + DISARM + emit event khusus ke React.
        Dashboard akan tampilkan alert merah + tombol "CLEAR EMERGENCY".
        """
        with self._lock:
            self._emergency_active = True
            self._emergency_reason = reason

        logger.critical(f"[Failsafe] ══ EMERGENCY STOP ══ {reason}")

        if self._mav and self._mav.is_connected:
            try:
                self._mav.rc_override({ch: RC_NEUTRAL_PWM for ch in range(1, 9)})
            except Exception as e:
                logger.error(f"[Failsafe] Emergency RC Override gagal: {e}")
            try:
                self._mav.disarm()
                logger.info("[Failsafe] DISARM terkirim ke Pixhawk")
            except Exception as e:
                logger.error(f"[Failsafe] DISARM gagal: {e}")

        payload = {
            "timestamp": _now(), "subsystem": "system",
            "severity":  Severity.EMERGENCY.name,
            "message":   reason,
            "action":    "rc_neutral + disarm",
            "requires_operator_clearance": True,
        }
        self._append_and_emit("emergency_stop",   payload)
        self._append_and_emit("failsafe_event",   payload)

    # ═══════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════

    def _append_and_emit(self, event_name: str, entry: dict):
        """Append ke history dan emit ke WebSocket."""
        with self._lock:
            self._event_history.append(entry)
            if len(self._event_history) > self._EVENT_HISTORY_MAX:
                self._event_history.pop(0)

        sev = entry.get("severity", "INFO")
        log_fn = {
            "INFO": logger.info, "WARNING": logger.warning,
            "CRITICAL": logger.error, "EMERGENCY": logger.critical,
        }.get(sev, logger.info)
        log_fn(
            f"[Failsafe] [{sev}] {entry.get('subsystem')}: "
            f"{entry.get('message')} -> {entry.get('action')}"
        )

        self._do_emit(event_name, entry)

    def _do_emit(self, event_name: str, payload: dict):
        try:
            self._emit(event_name, payload)
        except Exception as e:
            logger.warning(f"[Failsafe] Gagal emit '{event_name}': {e}")


# ═══════════════════════════════════════════════
# Utility
# ═══════════════════════════════════════════════

def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"