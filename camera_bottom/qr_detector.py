# camera_bottom/qr_detector.py
# QR code detection menggunakan pyzbar + OpenCV.
# Sekaligus menghitung apakah ROV sudah aligned di atas dock (center check).
#
# Output:
#   - Decode string QR → masuk ke qr_result_queue
#   - Dock alignment event → masuk ke dock_event_queue
#
# Fix dari versi sebelumnya:
#   - scan() sekarang return 3 nilai: (qr_data, is_dock_aligned, bbox)
#   - bbox dikonversi dari pyzbar rect → np.ndarray shape (1, 4, 2)
#     agar kompatibel dengan cv2.polylines di _draw_qr_bbox

import time
import logging
import multiprocessing
import numpy as np
from pyzbar import pyzbar

from config import QR_SCAN_INTERVAL_MS

logger = logging.getLogger(__name__)

# Toleransi piksel untuk center alignment check
DOCK_CENTER_TOLERANCE_PX = 50


class QRDetector:
    def __init__(
        self,
        qr_result_queue:  multiprocessing.Queue,
        dock_event_queue: multiprocessing.Queue,
    ):
        self.qr_queue   = qr_result_queue
        self.dock_queue = dock_event_queue

        self._last_scan_time: float        = 0.0
        self._last_qr_data:   str | None   = None
        self._dock_aligned:   bool         = False
        self._last_bbox:      np.ndarray | None = None

        self._scan_interval = QR_SCAN_INTERVAL_MS / 1000.0

    # ──────────────────────────────────────────
    # Main method — dipanggil tiap frame
    # ──────────────────────────────────────────
    def scan(
        self,
        frame: np.ndarray,
        preprocessed: np.ndarray,
    ) -> tuple[str | None, bool, np.ndarray | None]:
        """
        Scan QR code dari frame.
        Throttled oleh QR_SCAN_INTERVAL_MS agar tidak membebani CPU.

        Returns:
            (qr_data, is_dock_aligned, bbox)
              qr_data        = string hasil decode, None jika tidak ada
              is_dock_aligned = True jika QR center dekat center frame
              bbox           = np.ndarray shape (1, 4, 2) untuk cv2.polylines,
                               None jika tidak terdeteksi
        """
        now = time.monotonic()
        if (now - self._last_scan_time) < self._scan_interval:
            return self._last_qr_data, self._dock_aligned, self._last_bbox

        self._last_scan_time = now

        qr_data, bbox_raw = self._decode(preprocessed)

        # Konversi bbox pyzbar (rect namedtuple) → np.ndarray (1, 4, 2)
        # agar kompatibel dengan cv2.polylines di image_processing._draw_qr_bbox
        bbox = self._rect_to_ndarray(bbox_raw) if bbox_raw is not None else None

        # Snapshot state lama sebelum diupdate
        prev_qr_data = self._last_qr_data
        prev_aligned = self._dock_aligned

        # Update alignment berdasarkan posisi center QR vs center frame
        if bbox_raw is not None:
            h, w = frame.shape[:2]
            self._dock_aligned = self._check_alignment(bbox_raw, w, h)
        else:
            self._dock_aligned = False

        self._last_qr_data = qr_data
        self._last_bbox    = bbox

        # Kirim QR event kalau ada data baru (termasuk setelah sempat None)
        if qr_data and qr_data != prev_qr_data:
            logger.info(f"[QRDetector] QR baru terdeteksi: {qr_data}")
            self._emit_qr_event(qr_data, now)

        # Kirim dock event kalau status aligned berubah
        if self._dock_aligned != prev_aligned:
            self._emit_dock_event(now)

        return self._last_qr_data, self._dock_aligned, self._last_bbox

    # ──────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────
    def _decode(self, frame: np.ndarray) -> tuple[str | None, any]:
        """
        Decode QR code menggunakan pyzbar.
        Return (data_string, pyzbar_rect) atau (None, None).
        """
        decoded_objects = pyzbar.decode(frame)
        for obj in decoded_objects:
            if obj.type == "QRCODE":
                data = obj.data.decode("utf-8", errors="replace")
                return data, obj.rect
        return None, None

    @staticmethod
    def _rect_to_ndarray(rect) -> np.ndarray:
        """
        Konversi pyzbar rect(left, top, width, height)
        → np.ndarray shape (1, 4, 2) yang kompatibel dengan cv2.polylines.

        Susunan sudut: top-left, top-right, bottom-right, bottom-left
        (searah jarum jam, sama dengan format OpenCV QRCodeDetector)
        """
        l, t, w, h = rect.left, rect.top, rect.width, rect.height
        corners = np.array([[
            [l,     t    ],   # top-left
            [l + w, t    ],   # top-right
            [l + w, t + h],   # bottom-right
            [l,     t + h],   # bottom-left
        ]], dtype=np.int32)   # shape: (1, 4, 2)
        return corners

    def _check_alignment(self, rect, frame_w: int, frame_h: int) -> bool:
        """
        Cek apakah center QR code dekat center frame.
        Digunakan sebagai indikator ROV sudah di atas dock.
        """
        qr_cx = rect.left + rect.width  // 2
        qr_cy = rect.top  + rect.height // 2
        frame_cx = frame_w // 2
        frame_cy = frame_h // 2

        dx = abs(qr_cx - frame_cx)
        dy = abs(qr_cy - frame_cy)
        return dx < DOCK_CENTER_TOLERANCE_PX and dy < DOCK_CENTER_TOLERANCE_PX

    # ──────────────────────────────────────────
    # Event emitters
    # ──────────────────────────────────────────
    def _emit_qr_event(self, qr_data: str, timestamp: float):
        payload = {
            "type":      "qr_detected",
            "data":      qr_data,
            "aligned":   self._dock_aligned,
            "timestamp": timestamp,
        }
        self._put_nowait(self.qr_queue, payload, "qr_queue")

    def _emit_dock_event(self, timestamp: float):
        payload = {
            "type":      "dock_aligned" if self._dock_aligned else "dock_lost",
            "aligned":   self._dock_aligned,
            "timestamp": timestamp,
        }
        self._put_nowait(self.dock_queue, payload, "dock_queue")

    @staticmethod
    def _put_nowait(queue: multiprocessing.Queue, payload: dict, name: str):
        try:
            queue.put_nowait(payload)
        except Exception as e:
            logger.debug(f"[QRDetector] {name} penuh, payload di-skip: {e}")