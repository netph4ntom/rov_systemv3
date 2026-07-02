# core/logger.py
# ═══════════════════════════════════════════════════════════════════════════════
# ROV Centralized Logging Service
# ═══════════════════════════════════════════════════════════════════════════════
#
# Modul ini merupakan layanan logging terpusat untuk seluruh sistem ROV.
# Bertanggung jawab mencatat setiap aktivitas, status, peringatan, dan
# kesalahan yang terjadi selama sistem berjalan.
#
# ── Arsitektur ────────────────────────────────────────────────────────────────
#   setup_logging()      → dipanggil sekali di main.py saat startup
#   get_logger(name)     → shortcut untuk logging.getLogger(name)
#   log_startup()        → banner startup dengan info sistem
#   log_shutdown()       → banner shutdown
#   ROVLogger           → wrapper kelas dengan context-aware log methods
#
# ── Output ────────────────────────────────────────────────────────────────────
#   Terminal : format berwarna (ANSI color codes), real-time
#   File     : logs/rov_YYYY-MM-DD.log  → harian, max 10MB, 30 file tersimpan
#              logs/rov_error.log       → khusus ERROR/CRITICAL, selalu append
#
# ── Format Log ────────────────────────────────────────────────────────────────
#   [TIMESTAMP] [LEVEL   ] [PROSES        ] [MODUL              ] PESAN
#   2024-01-15 08:23:11.452 [INFO    ] [CoreAPI       ] [core.mavlink       ] [MAVLink] Terhubung!
#
# ── Digunakan oleh ────────────────────────────────────────────────────────────
#   main.py, core/routes.py, core/mavlink.py, core/telemetry.py,
#   core/trajectory.py, core/failsafe.py, core/websocket.py,
#   camera_front/stream_server.py, camera_bottom/stream_server.py
#
# ── Cara pakai ────────────────────────────────────────────────────────────────
#   from core.logger import get_logger
#   logger = get_logger(__name__)
#   logger.info("Pesan info")
#   logger.warning("Pesan warning")
#   logger.error("Pesan error", exc_info=True)
# ═══════════════════════════════════════════════════════════════════════════════

import os
import sys
import logging
import logging.handlers
import platform
import threading
from datetime import datetime
from typing import Optional

# ── Import config dengan fallback aman ───────────────────────────────────────
# Fallback diperlukan karena logger.py mungkin di-import sebelum config tersedia
# (misal: saat unit testing atau saat proses kamera belum dapat config)
try:
    from config import LOG_DIR, LOG_LEVEL
except ImportError:
    LOG_DIR   = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    LOG_LEVEL = "DEBUG"

# ── Konstanta ─────────────────────────────────────────────────────────────────
_LOG_MAX_BYTES    = 10 * 1024 * 1024   # 10 MB per file
_LOG_BACKUP_COUNT = 30                  # simpan 30 file backup (≈ 1 bulan)
_LOG_ERROR_FILE   = "rov_error.log"     # file khusus ERROR dan CRITICAL
_SETUP_LOCK       = threading.Lock()
_IS_SETUP_DONE    = False


# ═══════════════════════════════════════════════════════════════════════════════
# ANSI Color Formatter (untuk terminal output)
# ═══════════════════════════════════════════════════════════════════════════════

class _ColoredFormatter(logging.Formatter):
    """
    Custom formatter yang menambahkan warna ANSI pada output terminal.
    Warna dinonaktifkan otomatis jika terminal tidak mendukung (non-TTY,
    Windows CMD tanpa ANSI support, atau redirect ke file).
    """

    # Warna ANSI per level
    _LEVEL_COLORS = {
        logging.DEBUG:    "\033[38;5;245m",   # Abu-abu
        logging.INFO:     "\033[38;5;117m",   # Biru muda
        logging.WARNING:  "\033[38;5;220m",   # Kuning amber
        logging.ERROR:    "\033[38;5;203m",   # Merah coral
        logging.CRITICAL: "\033[38;5;201m",   # Magenta terang
    }

    # Warna label level (nama level yang ditampilkan)
    _LABEL_COLORS = {
        logging.DEBUG:    "\033[2;37m",        # Dim putih
        logging.INFO:     "\033[1;34m",        # Biru tebal
        logging.WARNING:  "\033[1;33m",        # Kuning tebal
        logging.ERROR:    "\033[1;31m",        # Merah tebal
        logging.CRITICAL: "\033[1;35m",        # Magenta tebal
    }

    _RESET = "\033[0m"
    _DIM   = "\033[2m"
    _CYAN  = "\033[36m"
    _GREEN = "\033[32m"

    def __init__(self, use_color: bool = True):
        super().__init__()
        # Deteksi apakah terminal mendukung warna
        self._use_color = (
            use_color
            and hasattr(sys.stdout, "isatty")
            and sys.stdout.isatty()
        )

    def format(self, record: logging.LogRecord) -> str:
        # Timestamp presisi milidetik
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        ms = f"{int(record.msecs):03d}"

        # Padding untuk alignment
        level_str = f"{record.levelname:<8}"
        name_str  = f"{record.name:<22}"
        proc_str  = f"{record.processName:<14}"

        if self._use_color:
            level_color = self._LABEL_COLORS.get(record.levelno, "")
            msg_color   = self._LEVEL_COLORS.get(record.levelno, "")
            R = self._RESET
            D = self._DIM
            C = self._CYAN
            G = self._GREEN

            header = (
                f"{D}{ts}.{ms}{R} "
                f"{level_color}[{level_str}]{R} "
                f"{D}[{C}{proc_str}{D}]{R} "
                f"{D}[{G}{name_str}{D}]{R} "
            )
            message = f"{msg_color}{record.getMessage()}{R}"
        else:
            header  = f"{ts}.{ms} [{level_str}] [{proc_str}] [{name_str}] "
            message = record.getMessage()

        output = header + message

        # Tambahkan traceback jika ada exception
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            if self._use_color:
                output += f"\n\033[38;5;203m{exc_text}{self._RESET}"
            else:
                output += f"\n{exc_text}"

        return output


# ═══════════════════════════════════════════════════════════════════════════════
# Plain Formatter (untuk file output)
# ═══════════════════════════════════════════════════════════════════════════════

class _PlainFormatter(logging.Formatter):
    """
    Formatter tanpa warna untuk output file.
    Format mudah di-parse oleh tools seperti grep, awk, atau ELK stack.
    """

    _FMT = (
        "%(asctime)s.%(msecs)03d "
        "[%(levelname)-8s] "
        "[%(processName)-14s] "
        "[%(name)-22s] "
        "%(message)s"
    )
    _DATEFMT = "%Y-%m-%d %H:%M:%S"

    def __init__(self):
        super().__init__(fmt=self._FMT, datefmt=self._DATEFMT)


# ═══════════════════════════════════════════════════════════════════════════════
# Setup utama
# ═══════════════════════════════════════════════════════════════════════════════

def setup_logging(
    log_dir:      Optional[str] = None,
    log_level:    Optional[str] = None,
    process_name: Optional[str] = None,
) -> None:
    """
    Inisialisasi sistem logging ROV secara terpusat.

    Harus dipanggil SEKALI di awal setiap proses (main.py, camera process, dll).
    Panggilan berulang diabaikan secara aman (idempotent).

    Args:
        log_dir:      Path direktori untuk menyimpan file log.
                      Default: LOG_DIR dari config.py
        log_level:    Level logging string ('DEBUG'|'INFO'|'WARNING'|'ERROR'|'CRITICAL').
                      Default: LOG_LEVEL dari config.py
        process_name: Nama proses (untuk identifikasi di log multi-process).
                      Tidak dipakai langsung, Python multiprocessing sudah set ini.
    """
    global _IS_SETUP_DONE

    with _SETUP_LOCK:
        if _IS_SETUP_DONE:
            return

        # Resolve parameter
        _log_dir   = log_dir   or LOG_DIR
        _log_level = log_level or LOG_LEVEL
        _level     = getattr(logging, _log_level.upper(), logging.DEBUG)

        # Pastikan direktori logs ada
        os.makedirs(_log_dir, exist_ok=True)

        # ── Nama file log dengan tanggal ─────────────────────────────────────
        today_str      = datetime.now().strftime("%Y-%m-%d")
        daily_log_file = os.path.join(_log_dir, f"rov_{today_str}.log")
        error_log_file = os.path.join(_log_dir, _LOG_ERROR_FILE)

        # ── Root logger ───────────────────────────────────────────────────────
        root_logger = logging.getLogger()
        root_logger.setLevel(_level)

        # Hapus handler lama (hindari duplikasi saat restart proses)
        root_logger.handlers.clear()

        # ── Handler 1: Terminal (stdout) dengan warna ─────────────────────────
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(_level)
        console_handler.setFormatter(_ColoredFormatter(use_color=True))
        root_logger.addHandler(console_handler)

        # ── Handler 2: Daily rotating file (semua level) ─────────────────────
        # RotatingFileHandler untuk membatasi ukuran per file log
        file_handler = logging.handlers.RotatingFileHandler(
            filename=daily_log_file,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(_level)
        file_handler.setFormatter(_PlainFormatter())
        root_logger.addHandler(file_handler)

        # ── Handler 3: Error-only file (ERROR + CRITICAL saja) ────────────────
        # File terpisah untuk memudahkan analisis error pasca-misi
        error_handler = logging.handlers.RotatingFileHandler(
            filename=error_log_file,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=10,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(_PlainFormatter())
        root_logger.addHandler(error_handler)

        # ── Redam library noise ───────────────────────────────────────────────
        # Library pihak ketiga yang terlalu verbose pada level DEBUG
        _quiet_loggers = [
            "werkzeug",    # Flask dev server request log
            "engineio",    # Socket.IO engine transport
            "socketio",    # Socket.IO protocol layer
            "pymavlink",   # MAVLink library internal
            "urllib3",     # HTTP client connection pool
            "asyncio",     # Async event loop noise
        ]
        for name in _quiet_loggers:
            logging.getLogger(name).setLevel(logging.WARNING)

        _IS_SETUP_DONE = True

        # Konfirmasi setup berhasil
        _init_logger = logging.getLogger("core.logger")
        _init_logger.info(
            f"[Logger] Logging diinisialisasi | "
            f"level={_log_level} | "
            f"file={daily_log_file} | "
            f"error_file={error_log_file}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════════════════

def get_logger(name: str) -> logging.Logger:
    """
    Shortcut untuk mendapatkan logger dengan nama modul.

    Penggunaan standar di setiap modul:
        from core.logger import get_logger
        logger = get_logger(__name__)

    Args:
        name: Nama modul, biasanya __name__

    Returns:
        logging.Logger instance yang sudah dikonfigurasi
    """
    return logging.getLogger(name)


def log_startup(version: str = "1.0.0") -> None:
    """
    Cetak banner startup sistem ROV ke log.
    Dipanggil sekali dari main.py setelah setup_logging().

    Args:
        version: Versi sistem ROV
    """
    logger  = get_logger("core.logger")
    sep     = "=" * 68
    sysinfo = _get_system_info()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    logger.info(sep)
    logger.info("  ROV VISION SYSTEM — STARTUP")
    logger.info(sep)
    logger.info(f"  Versi      : {version}")
    logger.info(f"  Waktu      : {now_str}")
    logger.info(f"  OS         : {sysinfo['os']}")
    logger.info(f"  Python     : {sysinfo['python']}")
    logger.info(f"  CPU Cores  : {sysinfo['cpu_cores']}")
    logger.info(f"  RAM Total  : {sysinfo['ram_total']}")
    logger.info(f"  Log Dir    : {LOG_DIR}")
    logger.info(sep)


def log_shutdown(reason: str = "Normal shutdown") -> None:
    """
    Cetak banner shutdown ke log.
    Dipanggil dari signal handler di main.py saat sistem dihentikan.

    Args:
        reason: Alasan shutdown (misal: "Ctrl+C", "Signal SIGTERM", dll)
    """
    logger  = get_logger("core.logger")
    sep     = "=" * 68
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    logger.info(sep)
    logger.info("  ROV VISION SYSTEM — SHUTDOWN")
    logger.info(f"  Waktu  : {now_str}")
    logger.info(f"  Alasan : {reason}")
    logger.info(sep)


# ═══════════════════════════════════════════════════════════════════════════════
# ROVLogger — Context-aware wrapper class
# ═══════════════════════════════════════════════════════════════════════════════

class ROVLogger:
    """
    Wrapper logging yang menyediakan method kontekstual per subsistem ROV.

    Setiap event penting memiliki method khusus dengan prefix subsistem yang
    konsisten sehingga log mudah di-filter dan di-parse pasca misi.

    Penggunaan:
        from core.logger import ROVLogger
        rov_log = ROVLogger("camera_front")

        rov_log.camera_open(index=0, width=640, height=480, fps=20)
        rov_log.qr_detected(data="MISI_01", aligned=True)
        rov_log.failsafe_trigger("mavlink", "CRITICAL", "Heartbeat timeout")
    """

    def __init__(self, subsystem: str):
        """
        Args:
            subsystem: Nama subsistem (misal: 'mavlink', 'camera_front', 'failsafe')
        """
        self._subsystem = subsystem.upper()
        self._logger    = get_logger(f"rov.{subsystem}")

    # ── Generic ───────────────────────────────────────────────────────────────

    def debug(self, msg: str, **kwargs):
        """Log level DEBUG — detail teknis untuk troubleshooting."""
        self._logger.debug(f"[{self._subsystem}] {msg}", **kwargs)

    def info(self, msg: str, **kwargs):
        """Log level INFO — informasi operasional normal."""
        self._logger.info(f"[{self._subsystem}] {msg}", **kwargs)

    def warning(self, msg: str, **kwargs):
        """Log level WARNING — anomali yang tidak kritis."""
        self._logger.warning(f"[{self._subsystem}] {msg}", **kwargs)

    def error(self, msg: str, **kwargs):
        """Log level ERROR — kegagalan yang mempengaruhi fungsi sistem."""
        self._logger.error(f"[{self._subsystem}] {msg}", **kwargs)

    def critical(self, msg: str, **kwargs):
        """Log level CRITICAL — kegagalan fatal yang mengancam keselamatan."""
        self._logger.critical(f"[{self._subsystem}] {msg}", **kwargs)

    # ── Sistem & Startup ──────────────────────────────────────────────────────

    def system_start(self, component: str, **kwargs):
        """Log saat komponen sistem mulai berjalan."""
        self._logger.info(f"[{self._subsystem}] >> {component} dimulai", **kwargs)

    def system_stop(self, component: str, reason: str = "normal", **kwargs):
        """Log saat komponen sistem berhenti."""
        self._logger.info(
            f"[{self._subsystem}] -- {component} dihentikan | alasan={reason}",
            **kwargs
        )

    def system_error(self, component: str, error: Exception, **kwargs):
        """Log error fatal pada komponen sistem."""
        self._logger.error(
            f"[{self._subsystem}] XX {component} error: {error}",
            exc_info=True,
            **kwargs
        )

    # ── MAVLink / Pixhawk ─────────────────────────────────────────────────────

    def mavlink_connecting(self, conn_string: str, attempt: int, max_attempts: int):
        """Log percobaan koneksi MAVLink ke Pixhawk."""
        self._logger.info(
            f"[{self._subsystem}] Mencoba koneksi MAVLink "
            f"({attempt}/{max_attempts}) -> {conn_string}"
        )

    def mavlink_connected(self, system_id: int, component_id: int):
        """Log berhasil terhubung ke Pixhawk."""
        self._logger.info(
            f"[{self._subsystem}] OK Pixhawk terhubung | "
            f"system={system_id} component={component_id}"
        )

    def mavlink_disconnected(self, reason: str = ""):
        """Log saat koneksi MAVLink terputus."""
        self._logger.warning(
            f"[{self._subsystem}] MAVLink terputus"
            + (f" | {reason}" if reason else "")
        )

    def mavlink_reconnect(self, attempt: int):
        """Log percobaan reconnect MAVLink."""
        self._logger.warning(
            f"[{self._subsystem}] Reconnect MAVLink attempt #{attempt}"
        )

    def mavlink_heartbeat(self, age_s: float):
        """Log status heartbeat Pixhawk (DEBUG — tidak spam saat normal)."""
        self._logger.debug(
            f"[{self._subsystem}] Heartbeat | age={age_s:.2f}s"
        )

    def mavlink_command(self, command: str, params: str = ""):
        """Log perintah MAVLink yang dikirim ke Pixhawk."""
        self._logger.info(
            f"[{self._subsystem}] CMD {command}"
            + (f" | {params}" if params else "")
        )

    def mavlink_mode_change(self, old_mode: str, new_mode: str):
        """Log perubahan mode operasi ROV (MANUAL, STABILIZE, dll)."""
        self._logger.info(
            f"[{self._subsystem}] Mode: {old_mode} -> {new_mode}"
        )

    def mavlink_arm(self, armed: bool):
        """Log status ARM/DISARM ROV."""
        state = "ARM" if armed else "DISARM"
        fn    = self._logger.info if armed else self._logger.warning
        fn(f"[{self._subsystem}] {state}")

    # ── Kamera ────────────────────────────────────────────────────────────────

    def camera_open(self, index: int, width: int = 0, height: int = 0, fps: int = 0):
        """Log saat kamera berhasil dibuka."""
        self._logger.info(
            f"[{self._subsystem}] OK Kamera #{index} terbuka | "
            f"{width}x{height} @{fps}fps"
        )

    def camera_close(self, index: int):
        """Log saat kamera ditutup."""
        self._logger.info(f"[{self._subsystem}] Kamera #{index} ditutup")

    def camera_error(self, message: str, exc: Optional[Exception] = None):
        """Log error kamera."""
        self._logger.error(
            f"[{self._subsystem}] Kamera error: {message}",
            exc_info=exc is not None
        )

    def camera_frame_drop(self, consecutive: int = 1):
        """Log frame drop — WARNING jika berulang, DEBUG jika sekali."""
        fn = self._logger.warning if consecutive > 5 else self._logger.debug
        fn(f"[{self._subsystem}] Frame drop #{consecutive}")

    def camera_stream_start(self, port: int, url: str):
        """Log saat MJPEG/WebRTC stream mulai aktif."""
        self._logger.info(
            f"[{self._subsystem}] Stream aktif | port={port} url={url}"
        )

    def camera_screenshot(self, filepath: str, success: bool):
        """Log hasil screenshot — INFO jika sukses, ERROR jika gagal."""
        if success:
            self._logger.info(
                f"[{self._subsystem}] Screenshot tersimpan -> {filepath}"
            )
        else:
            self._logger.error(
                f"[{self._subsystem}] Screenshot gagal -> {filepath}"
            )

    def camera_record_start(self, filepath: str):
        """Log saat recording video dimulai."""
        self._logger.info(
            f"[{self._subsystem}] Recording dimulai -> {filepath}"
        )

    def camera_record_stop(self, filepath: str, duration_s: float = 0.0):
        """Log saat recording dihentikan dengan durasi total."""
        self._logger.info(
            f"[{self._subsystem}] Recording selesai -> {filepath} "
            f"({duration_s:.1f}s)"
        )

    # ── QR Code & Docking ─────────────────────────────────────────────────────

    def qr_detected(self, data: str, aligned: bool = False):
        """Log saat QR code terdeteksi oleh kamera bawah."""
        align_str = "ALIGNED" if aligned else "tidak aligned"
        self._logger.info(
            f"[{self._subsystem}] QR terdeteksi | data={data!r} | {align_str}"
        )

    def qr_scan_start(self, interval_ms: int):
        """Log saat QR scanner aktif."""
        self._logger.info(
            f"[{self._subsystem}] QR scanner aktif | interval={interval_ms}ms"
        )

    def dock_aligned(self, offset_x: float = 0.0, offset_y: float = 0.0):
        """Log saat ROV berhasil align dengan docking station."""
        self._logger.info(
            f"[{self._subsystem}] DOCK ALIGNED | "
            f"offset=({offset_x:.1f}, {offset_y:.1f})"
        )

    def dock_lost(self):
        """Log saat ROV kehilangan lock pada docking station."""
        self._logger.warning(
            f"[{self._subsystem}] DOCK LOST — target tidak terdeteksi"
        )

    # ── Telemetry ─────────────────────────────────────────────────────────────

    def telemetry_update(
        self,
        roll: float, pitch: float, yaw: float,
        depth: float, armed: bool, mode: str,
    ):
        """Log update telemetry — DEBUG agar tidak spam terminal."""
        self._logger.debug(
            f"[{self._subsystem}] "
            f"r={roll:.1f}d p={pitch:.1f}d y={yaw:.1f}d | "
            f"depth={depth:.2f}m | armed={armed} | mode={mode}"
        )

    def telemetry_stale(self, age_s: float):
        """Log saat data telemetry tidak diperbarui melebihi threshold."""
        self._logger.warning(
            f"[{self._subsystem}] Data telemetry stale | age={age_s:.1f}s"
        )

    # ── Autonomous ────────────────────────────────────────────────────────────

    def autonomous_start(self, mission: str):
        """Log saat mode autonomous dimulai."""
        self._logger.info(
            f"[{self._subsystem}] Autonomous START | misi={mission}"
        )

    def autonomous_step(self, step: int, action: str, result: str = ""):
        """Log setiap langkah dalam skenario autonomous."""
        self._logger.info(
            f"[{self._subsystem}] Step {step}: {action}"
            + (f" -> {result}" if result else "")
        )

    def autonomous_complete(self, mission: str, duration_s: float):
        """Log saat misi autonomous berhasil diselesaikan."""
        self._logger.info(
            f"[{self._subsystem}] Autonomous SELESAI | "
            f"misi={mission} | durasi={duration_s:.1f}s"
        )

    def autonomous_abort(self, mission: str, reason: str):
        """Log saat misi autonomous dibatalkan (oleh operator atau failsafe)."""
        self._logger.warning(
            f"[{self._subsystem}] Autonomous ABORT | "
            f"misi={mission} | alasan={reason}"
        )

    # ── Failsafe & Watchdog ───────────────────────────────────────────────────

    def failsafe_trigger(self, subsystem: str, severity: str, message: str):
        """
        Log saat failsafe aktif — level disesuaikan dengan severity.
          WARNING  → logger.warning
          CRITICAL → logger.error
          EMERGENCY → logger.critical
        """
        fn_map = {
            "WARNING":   self._logger.warning,
            "CRITICAL":  self._logger.error,
            "EMERGENCY": self._logger.critical,
        }
        fn = fn_map.get(severity.upper(), self._logger.warning)
        fn(
            f"[{self._subsystem}] FAILSAFE [{severity}] | "
            f"{subsystem}: {message}"
        )

    def failsafe_recovery(self, subsystem: str, action: str, success: bool):
        """Log hasil percobaan recovery otomatis failsafe."""
        result = "OK" if success else "GAGAL"
        fn     = self._logger.info if success else self._logger.error
        fn(
            f"[{self._subsystem}] Recovery {result} | "
            f"{subsystem}: {action}"
        )

    def emergency_stop(self, reason: str):
        """Log Emergency Stop — selalu CRITICAL."""
        self._logger.critical(
            f"[{self._subsystem}] EMERGENCY STOP | alasan: {reason}"
        )

    def emergency_clear(self):
        """Log saat Emergency Stop di-clear oleh operator."""
        self._logger.info(
            f"[{self._subsystem}] Emergency state CLEARED oleh operator"
        )

    # ── WebSocket / Dashboard ─────────────────────────────────────────────────

    def client_connect(self, sid: str = "", addr: str = ""):
        """Log saat React dashboard operator terhubung via WebSocket."""
        self._logger.info(
            f"[{self._subsystem}] Dashboard connected"
            + (f" | sid={sid[:8]}" if sid else "")
            + (f" | addr={addr}" if addr else "")
        )

    def client_disconnect(self, sid: str = "", reason: str = ""):
        """Log saat React dashboard operator terputus."""
        self._logger.warning(
            f"[{self._subsystem}] Dashboard disconnected"
            + (f" | sid={sid[:8]}" if sid else "")
            + (f" | {reason}" if reason else "")
        )

    def ws_emit(self, event: str, target: str = "broadcast"):
        """Log WebSocket emit event — DEBUG untuk menghindari spam."""
        self._logger.debug(
            f"[{self._subsystem}] emit '{event}' -> {target}"
        )

    # ── Trajectory ────────────────────────────────────────────────────────────

    def trajectory_reset(self, reason: str = "operator"):
        """Log saat trajectory ROV di-reset ke titik asal."""
        self._logger.info(
            f"[{self._subsystem}] Trajectory di-reset | oleh={reason}"
        )

    def trajectory_update(self, x: float, y: float, depth: float, yaw: float):
        """Log posisi trajectory terkini — DEBUG agar tidak spam."""
        self._logger.debug(
            f"[{self._subsystem}] pos=({x:.2f}, {y:.2f}) "
            f"depth={depth:.2f}m yaw={yaw:.1f}d"
        )

    # ── Image Processing ──────────────────────────────────────────────────────

    def image_processing_start(self, pipeline: str):
        """Log saat pipeline image processing (CLAHE, color correction, dll) aktif."""
        self._logger.info(
            f"[{self._subsystem}] Pipeline image processing: {pipeline}"
        )

    def image_processing_error(self, stage: str, error: Exception):
        """Log error pada tahap tertentu dari pipeline image processing."""
        self._logger.error(
            f"[{self._subsystem}] Error pada stage '{stage}': {error}",
            exc_info=True
        )

    # ── WebRTC ────────────────────────────────────────────────────────────────

    def webrtc_offer(self, camera: str):
        """Log saat WebRTC offer SDP diterima dari browser."""
        self._logger.info(
            f"[{self._subsystem}] WebRTC offer diterima | kamera={camera}"
        )

    def webrtc_connected(self, camera: str, peer: str = ""):
        """Log saat WebRTC peer connection berhasil terbentuk."""
        self._logger.info(
            f"[{self._subsystem}] WebRTC connected | kamera={camera}"
            + (f" | peer={peer}" if peer else "")
        )

    def webrtc_disconnected(self, camera: str):
        """Log saat WebRTC peer connection terputus."""
        self._logger.warning(
            f"[{self._subsystem}] WebRTC disconnected | kamera={camera}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Utility: System Info
# ═══════════════════════════════════════════════════════════════════════════════

def _get_system_info() -> dict:
    """Kumpulkan informasi sistem untuk ditampilkan di banner startup."""
    try:
        import psutil
        ram_gb  = psutil.virtual_memory().total / (1024 ** 3)
        ram_str = f"{ram_gb:.1f} GB"
    except ImportError:
        ram_str = "N/A"

    cpu_cores = os.cpu_count() or "N/A"

    return {
        "os":        f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python":    f"{sys.version.split()[0]}",
        "cpu_cores": cpu_cores,
        "ram_total": ram_str,
    }


def get_log_files() -> dict:
    """
    Kembalikan daftar file log yang ada di direktori logs/.
    Berguna untuk endpoint REST /api/logs/list agar operator dapat
    melihat dan mengunduh log pasca-misi dari dashboard.

    Returns:
        dict dengan keys: 'log_dir', 'files', 'total_count'
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    all_files = []

    for fname in sorted(os.listdir(LOG_DIR), reverse=True):
        if fname.endswith(".log"):
            fpath = os.path.join(LOG_DIR, fname)
            try:
                stat = os.stat(fpath)
                all_files.append({
                    "name":        fname,
                    "path":        fpath,
                    "size_bytes":  stat.st_size,
                    "size_human":  _human_size(stat.st_size),
                    "modified":    datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
            except OSError:
                continue

    return {
        "log_dir":     LOG_DIR,
        "files":       all_files,
        "total_count": len(all_files),
    }


def _human_size(size_bytes: int) -> str:
    """Convert bytes ke string yang human-readable (B, KB, MB, GB)."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes //= 1024
    return f"{size_bytes:.1f} TB"