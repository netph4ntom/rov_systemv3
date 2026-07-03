# camera_front/qr_detector.py
# QR Code detector untuk kamera depan ROV.
# Digunakan dalam fase ALIGNING pada modul autonomous.py.
#
# Arsitektur:
#   - Instance hidup di proses CameraFront (Process 2)
#   - Diaktifkan / dinonaktifkan via cmd_front queue
#   - Hasil deteksi dikirim ke core via qr_front_result_queue
#   - process_frame() dipanggil dari capture_loop setiap frame saat aktif

import time
import logging
import threading
from dataclasses import dataclass, asdict
from typing import Optional

import cv2
import numpy as np

try:
    from pyzbar import pyzbar
    _PYZBAR_OK = True
except ImportError:
    _PYZBAR_OK = False

from config import AUTONOMOUS_ALIGN_THRESHOLD_PX

logger = logging.getLogger(__name__)


@dataclass
class QRResult:
    data:      str
    offset_x:  float
    offset_y:  float
    aligned:   bool
    timestamp: float


class QRDetector:
    def __init__(self, result_queue=None):
        self._lock = threading.Lock()
        self._active = False
        self._latest_result = None
        self._result_queue = result_queue
        self._last_send_time = 0.0
        self._SEND_INTERVAL  = 0.1
        if not _PYZBAR_OK:
            logger.error("[QRDetector-Front] pyzbar tidak dapat diimport.")

    def activate(self):
        with self._lock:
            self._active = True
            self._latest_result = None
        logger.info("[QRDetector-Front] Diaktifkan")

    def deactivate(self):
        with self._lock:
            self._active = False
            self._latest_result = None
        logger.info("[QRDetector-Front] Dinonaktifkan")

    @property
    def is_active(self):
        with self._lock:
            return self._active

    def process_frame(self, frame):
        if not self._active or not _PYZBAR_OK or frame is None:
            return frame
        h, w = frame.shape[:2]
        cx, cy = w // 2, h // 2
        frame = frame.copy()
        cv2.line(frame, (cx - 25, cy), (cx + 25, cy), (0, 255, 255), 1)
        cv2.line(frame, (cx, cy - 25), (cx, cy + 25), (0, 255, 255), 1)
        cv2.circle(frame, (cx, cy), AUTONOMOUS_ALIGN_THRESHOLD_PX, (0, 255, 255), 1)
        decoded = pyzbar.decode(frame)
        if not decoded:
            cv2.putText(frame, "SEARCHING QR...", (8, h - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 200), 1)
            with self._lock:
                self._latest_result = None
            return frame
        qr = max(decoded, key=lambda q: q.rect.width * q.rect.height)
        data = qr.data.decode("utf-8", errors="replace")
        rect = qr.rect
        qr_cx = rect.left + rect.width  // 2
        qr_cy = rect.top  + rect.height // 2
        offset_x = float(qr_cx - cx)
        offset_y = float(qr_cy - cy)
        aligned = (abs(offset_x) < AUTONOMOUS_ALIGN_THRESHOLD_PX and
                   abs(offset_y) < AUTONOMOUS_ALIGN_THRESHOLD_PX)
        result = QRResult(data=data, offset_x=offset_x, offset_y=offset_y,
                          aligned=aligned, timestamp=time.time())
        with self._lock:
            self._latest_result = result
        self._try_send_to_queue(result)
        color = (0, 255, 0) if aligned else (0, 200, 255)
        pts = np.array([[rect.left, rect.top],
                        [rect.left + rect.width, rect.top],
                        [rect.left + rect.width, rect.top + rect.height],
                        [rect.left, rect.top + rect.height]], dtype=np.int32)
        cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=2)
        cv2.circle(frame, (qr_cx, qr_cy), 4, color, -1)
        cv2.line(frame, (cx, cy), (qr_cx, qr_cy), color, 1)
        cv2.putText(frame, "QR: " + data[:14], (rect.left, max(rect.top - 8, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
        status_txt = "ALIGNED" if aligned else ("dX:" + str(int(offset_x)) + "px dY:" + str(int(offset_y)) + "px")
        cv2.putText(frame, status_txt, (8, h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
        return frame

    def get_latest_result(self):
        with self._lock:
            return self._latest_result

    def _try_send_to_queue(self, result):
        if self._result_queue is None:
            return
        now = time.time()
        if now - self._last_send_time < self._SEND_INTERVAL:
            return
        self._last_send_time = now
        try:
            self._result_queue.put_nowait({**asdict(result), "source": "front"})
        except Exception:
            pass
