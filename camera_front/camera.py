# camera_front/camera.py
# Wrapper OpenCV untuk kamera depan ROV.
# Mengelola buka/tutup kamera, baca frame, dan retry otomatis jika kamera lepas.
#
# Perubahan dari versi sebelumnya:
#   - Context manager (__enter__/__exit__) untuk cleanup yang reliable
#   - Reconnect throttle (_RECONNECT_COOLDOWN_S) agar tidak blocking capture loop
#   - cap.set() divalidasi dan di-log jika gagal
#   - Type hint diperbaiki (np.ndarray | None)
#   - Titik ekstra di __del__ dihapus

import cv2
import time
import logging
import numpy as np

from config import CAMERA_FRONT_INDEX, FRAME_WIDTH, FRAME_HEIGHT, FRAME_FPS

logger = logging.getLogger(__name__)

# Inisialisasi GStreamer runtime via PyGObject
try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    logger.info(f"[GStreamer] Runtime inisialisasi sukses. Versi: {Gst.version()}")
except (ImportError, ValueError) as e:
    logger.warning(f"[GStreamer] Gagal inisialisasi PyGObject/GStreamer runtime: {e}")

_RECONNECT_COOLDOWN_S = 2.0


class FrontCamera:
    def __init__(self):
        self.index = CAMERA_FRONT_INDEX
        self.cap: cv2.VideoCapture | None = None
        self._last_reconnect: float = 0.0
        self._open()

    # ──────────────────────────────────────────
    # Context manager
    # ──────────────────────────────────────────
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.release()

    # ──────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────
    def _open(self):
        """Buka kamera menggunakan GStreamer."""
        logger.info(f"[FrontCamera] Membuka kamera index={self.index} via GStreamer")
        gst_pipeline = (
            f"v4l2src device=/dev/video{self.index} ! "
            f"videoconvert ! "
            f"videoscale ! video/x-raw, width={FRAME_WIDTH}, height={FRAME_HEIGHT} ! "
            f"videorate ! video/x-raw, framerate={FRAME_FPS}/1 ! "
            f"videoconvert ! appsink"
        )
        self.cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)

        if not self.cap.isOpened():
            logger.error("[FrontCamera] Gagal membuka kamera via GStreamer!")

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────
    def read_frame(self) -> tuple[bool, np.ndarray | None]:
        """
        Baca satu frame dari kamera.
        Reconnect di-throttle oleh _RECONNECT_COOLDOWN_S agar tidak
        blocking capture loop saat kamera benar-benar mati.
        """
        if self.cap is None or not self.cap.isOpened():
            now = time.monotonic()
            if now - self._last_reconnect < _RECONNECT_COOLDOWN_S:
                return False, None
            logger.warning("[FrontCamera] Kamera tidak terbuka, mencoba reconnect...")
            self._last_reconnect = now
            self._open()

        ret, frame = self.cap.read()
        if not ret:
            now = time.monotonic()
            if now - self._last_reconnect >= _RECONNECT_COOLDOWN_S:
                logger.warning("[FrontCamera] Gagal baca frame, reconnect...")
                self._last_reconnect = now
                self.release()
                self._open()
                ret, frame = self.cap.read()

        return ret, frame if ret else None

    def release(self):
        """Lepaskan resource kamera."""
        if self.cap and self.cap.isOpened():
            self.cap.release()
            logger.info("[FrontCamera] Kamera dilepas.")
        self.cap = None

    def __del__(self):
        self.release()