# camera_bottom/camera.py
# Wrapper kamera bawah ROV — mirip FrontCamera tapi index berbeda
# dan ada exposure adjustment khusus untuk kondisi docking (close-range).

import cv2
import time
import logging
from config import CAMERA_BOTTOM_INDEX, FRAME_WIDTH, FRAME_HEIGHT, FRAME_FPS

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


class BottomCamera:
    def __init__(self):
        self.index = CAMERA_BOTTOM_INDEX
        self.cap: cv2.VideoCapture | None = None
        self._open()

    def _open(self):
        logger.info(f"[BottomCamera] Membuka kamera index={self.index} via GStreamer")
        gst_pipeline = (
            f"v4l2src device=/dev/video{self.index} ! "
            f"video/x-raw, width={FRAME_WIDTH}, height={FRAME_HEIGHT}, framerate={FRAME_FPS}/1 ! "
            f"videoconvert ! appsink"
        )
        self.cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)

        # Auto-exposure ON supaya adaptif saat ROV mendekati dock
        self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)  # 3 = auto, 1 = manual

        if not self.cap.isOpened():
            logger.error("[BottomCamera] Gagal membuka kamera via GStreamer!")

    def read_frame(self) -> tuple[bool, any]:
        """Baca frame dengan auto-reconnect jika kamera terputus."""
        if self.cap is None or not self.cap.isOpened():
            logger.warning("[BottomCamera] Reconnect kamera...")
            self._open()
            time.sleep(0.5)

        ret, frame = self.cap.read()
        if not ret:
            logger.warning("[BottomCamera] Gagal baca frame, reconnect...")
            self.release()
            time.sleep(1.0)
            self._open()
            ret, frame = self.cap.read()

        return ret, frame

    def release(self):
        if self.cap and self.cap.isOpened():
            self.cap.release()
            logger.info("[BottomCamera] Kamera dilepas.")
        self.cap = None

    def __del__(self):
        self.release()