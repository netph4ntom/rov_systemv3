# core/trajectory.py
# Estimasi posisi dan trajectory ROV berdasarkan data telemetry dari Pixhawk.
#
# Karena ROV bawah air tidak punya GPS yang reliable (sinyal GPS tidak menembus air),
# posisi dihitung dengan dead reckoning sederhana:
#   posisi_baru = posisi_lama + (kecepatan × Δt)
#
# Data yang digunakan:
#   - Depth      : langsung dari pressure sensor (akurat)
#   - Yaw        : dari AHRS Pixhawk (akurat)
#   - X/Y        : estimasi dari velocity (jika ada) atau joystick intent
#
# Output ke React:
#   - path       : array titik {x, y, depth, timestamp} untuk visualisasi trail
#   - orientation: {roll, pitch, yaw} untuk 3D attitude indicator
#   - current_pos: posisi estimasi saat ini
#
# CATATAN: Ini adalah estimasi, bukan navigasi presisi.
# Untuk ROV kompetisi, dead reckoning sudah cukup untuk dashboard operator.

import time
import math
import logging
import threading
from collections import deque

from typing import Deque, Optional
from config import (
    TRAJECTORY_HISTORY_SIZE,
    TRAJECTORY_UPDATE_INTERVAL,
)

logger = logging.getLogger(__name__)


class TrajectoryEstimator:
    """
    Menghitung dan menyimpan estimasi posisi ROV dari waktu ke waktu.
    Method update() dipanggil setiap kali telemetry baru datang.
    """

    def __init__(self):
        self._lock = threading.Lock()

        # Posisi estimasi saat ini dalam meter (relatif dari titik start)
        self._pos_x: float = 0.0
        self._pos_y: float = 0.0
        self._pos_depth: float = 0.0

        # Velocity terakhir (dari thruster intent / joystick)
        # Unit: m/s (skala kasar)
        self._vel_x: float = 0.0
        self._vel_y: float = 0.0

        # Timestamp update terakhir untuk Δt
        self._last_update_time:Optional[float] = None

        # Riwayat posisi untuk trail di React
        # deque dengan maxlen otomatis buang titik lama
        self._path: Deque[dict] = deque(maxlen=TRAJECTORY_HISTORY_SIZE)

        # State attitude terbaru
        self._roll:  float = 0.0
        self._pitch: float = 0.0
        self._yaw:   float = 0.0

        # Callback ke websocket — di-set dari routes.py
        self.on_trajectory_update = None

        # Rate limiting: emit ke React tidak lebih dari 1x per interval
        self._last_emit_time: float = 0.0

    # ──────────────────────────────────────────
    # Update dari telemetry
    # ──────────────────────────────────────────
    def update_from_telemetry(self, telemetry: dict):
        """
        Dipanggil setiap kali TelemetryManager.on_telemetry_update terpicu.
        Update attitude, depth, dan kalkulasi posisi baru.
        """
        with self._lock:
            # Ambil attitude terbaru
            self._roll  = telemetry.get("roll",  0.0)
            self._pitch = telemetry.get("pitch", 0.0)
            self._yaw   = telemetry.get("yaw",   0.0)
            depth       = telemetry.get("depth", 0.0)

            # Hitung Δt sejak update terakhir
            now = time.time()
            if self._last_update_time is None:
                self._last_update_time = now

            dt = now - self._last_update_time
            self._last_update_time = now

            # Dead reckoning: integrasikan velocity ke posisi
            # Velocity di-set dari joystick (update_velocity dipanggil dari routes)
            yaw_rad = math.radians(self._yaw)

            # Thruster forward/lateral dirotasikan sesuai heading ROV
            forward = self._vel_y  # maju/mundur
            lateral = self._vel_x  # kanan/kiri

            dx = (forward * math.cos(yaw_rad) - lateral * math.sin(yaw_rad)) * dt
            dy = (forward * math.sin(yaw_rad) + lateral * math.cos(yaw_rad)) * dt

            self._pos_x     += dx
            self._pos_y     += dy
            self._pos_depth  = depth  # depth langsung dari sensor, tidak di-integrate

            # Simpan titik ke path history
            point = {
                "x":         round(self._pos_x, 3),
                "y":         round(self._pos_y, 3),
                "depth":     round(self._pos_depth, 3),
                "yaw":       round(self._yaw, 1),
                "timestamp": round(now, 3),
            }
            self._path.append(point)

        # Emit ke React dengan rate limiting
        self._maybe_emit()

    def update_velocity(self, vel_x: float, vel_y: float):
        """
        Terima velocity intent dari joystick (dikalibrasi ke m/s).
        Dipanggil dari routes.py setiap ada RC override command.
        vel_x: lateral (kanan positif)
        vel_y: forward (maju positif)
        """
        with self._lock:
            self._vel_x = vel_x
            self._vel_y = vel_y

    def reset_position(self):
        """Reset posisi ke (0,0,0) — dipanggil operator dari dashboard."""
        with self._lock:
            self._pos_x = 0.0
            self._pos_y = 0.0
            self._path.clear()
        logger.info("[Trajectory] Posisi di-reset ke origin")

    # ──────────────────────────────────────────
    # Getter
    # ──────────────────────────────────────────
    def get_state(self) -> dict:
        """Return snapshot state trajectory saat ini."""
        with self._lock:
            return {
                "current_pos": {
                    "x":     round(self._pos_x, 3),
                    "y":     round(self._pos_y, 3),
                    "depth": round(self._pos_depth, 3),
                },
                "orientation": {
                    "roll":  self._roll,
                    "pitch": self._pitch,
                    "yaw":   self._yaw,
                },
                "path": list(self._path),   # kirim seluruh trail ke React
                "timestamp": time.time(),
            }

    # ──────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────
    def _maybe_emit(self):
        """Emit state ke React maksimal 1x per TRAJECTORY_UPDATE_INTERVAL detik."""
        now = time.time()
        if (now - self._last_emit_time) < TRAJECTORY_UPDATE_INTERVAL:
            return
        self._last_emit_time = now

        if self.on_trajectory_update:
            self.on_trajectory_update(self.get_state())