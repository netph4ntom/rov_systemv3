# camera_bottom/screenshot.py
# Screenshot handler untuk kamera bawah.
# Mengambil frame terbaru dari memori dan menyimpannya ke disk sebagai .jpg
#
# Tidak butuh akses kamera langsung — cukup frame yang sudah ada
# di _current_frame (di-share dari capture loop stream_server.py).

import cv2
import os
import logging
from datetime import datetime
from typing import Optional
from config import SCREENSHOT_DIR, SCREENSHOT_QUALITY

logger = logging.getLogger(__name__)


class BottomScreenshot:
    """Screenshot taker untuk kamera bawah."""

    def take(self, frame, filename:Optional[str] = None) ->Optional[str]:
        """
        Simpan frame sebagai file .jpg.

        Args:
            frame: numpy ndarray frame terbaru dari kamera
            filename: nama file opsional. Jika None, auto-generate dari timestamp.

        Returns:
            Path absolut file yang disimpan, atau None jika gagal.
        """
        if frame is None:
            logger.warning("[BottomScreenshot] Frame kosong, skip screenshot")
            return None

        os.makedirs(SCREENSHOT_DIR, exist_ok=True)

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # sampai milidetik
            filename = f"bottom_{timestamp}.jpg"

        # Pastikan extensi .jpg
        if not filename.lower().endswith(".jpg"):
            filename += ".jpg"

        filepath = os.path.join(SCREENSHOT_DIR, filename)

        success = cv2.imwrite(
            filepath,
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, SCREENSHOT_QUALITY]
        )

        if success:
            logger.info(f"[BottomScreenshot] Disimpan → {filepath}")
            return filepath
        else:
            logger.error(f"[BottomScreenshot] Gagal menyimpan → {filepath}")
            return None