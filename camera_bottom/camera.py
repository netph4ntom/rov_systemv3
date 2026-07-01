# camera_bottom/camera.py
# Wrapper kamera bawah ROV — mirip FrontCamera tapi index berbeda
# dan ada exposure adjustment khusus untuk kondisi docking (close-range).

import cv2
import time
import logging
from config import CAMERA_BOTTOM_INDEX, FRAME_WIDTH, FRAME_HEIGHT, FRAME_FPS

logger = logging.getLogger(__name__)


class BottomCamera:
    def __init__(self):
        self.index = CAMERA_BOTTOM_INDEX
        self.cap: cv2.VideoCapture | None = None
        self._open()

    def _open(self):
        logger.info(f"[BottomCamera] Membuka kamera index={self.index}")
        self.cap = cv2.VideoCapture(self.index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS,          FRAME_FPS)

        # Auto-exposure ON supaya adaptif saat ROV mendekati dock
        self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)  # 3 = auto, 1 = manual

        if not self.cap.isOpened():
            logger.error("[BottomCamera] Gagal membuka kamera!")

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