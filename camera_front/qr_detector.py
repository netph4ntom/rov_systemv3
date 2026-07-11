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

from config import (
    AUTONOMOUS_ALIGN_THRESHOLD_PX,
    WECHAT_QR_DETECT_PROTOTXT,
    WECHAT_QR_DETECT_CAFFEMODEL,
    WECHAT_QR_SR_PROTOTXT,
    WECHAT_QR_SR_CAFFEMODEL,
)
from core.wechat_model_downloader import ensure_wechat_models

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
            logger.warning("[QRDetector-Front] pyzbar tidak dapat diimport.")

        self.wechat_detector = None
        self._init_wechat_detector()

    def _init_wechat_detector(self):
        try:
            if ensure_wechat_models():
                if hasattr(cv2, "wechat_qrcode_WeChatQRCode"):
                    try:
                        # Coba dengan 4 argumen (dengan Super Resolution)
                        self.wechat_detector = cv2.wechat_qrcode_WeChatQRCode(
                            WECHAT_QR_DETECT_PROTOTXT,
                            WECHAT_QR_DETECT_CAFFEMODEL,
                            WECHAT_QR_SR_PROTOTXT,
                            WECHAT_QR_SR_CAFFEMODEL
                        )
                        logger.info("[QRDetector-Front] WeChat QR Code Detector (dengan Super Resolution) berhasil diinisialisasi.")
                    except TypeError as te:
                        if "arguments" in str(te):
                            logger.warning(f"[QRDetector-Front] Gagal dengan 4 argumen: {te}. Mencoba dengan 2 argumen (tanpa Super Resolution)...")
                            self.wechat_detector = cv2.wechat_qrcode_WeChatQRCode(
                                WECHAT_QR_DETECT_PROTOTXT,
                                WECHAT_QR_DETECT_CAFFEMODEL
                            )
                            logger.info("[QRDetector-Front] WeChat QR Code Detector (tanpa Super Resolution) berhasil diinisialisasi.")
                        else:
                            raise te
                else:
                    logger.warning("[QRDetector-Front] Modul WeChatQRCode tidak tersedia di OpenCV. Menggunakan pyzbar.")
            else:
                logger.warning("[QRDetector-Front] Gagal mengunduh model WeChat QR. Menggunakan pyzbar.")
        except Exception as e:
            logger.error(f"[QRDetector-Front] Gagal inisialisasi WeChat QR: {e}. Menggunakan pyzbar.")

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
        if not self._active or frame is None:
            return frame
        if self.wechat_detector is None and not _PYZBAR_OK:
            return frame

        h, w = frame.shape[:2]
        cx, cy = w // 2, h // 2
        frame = frame.copy()
        cv2.line(frame, (cx - 25, cy), (cx + 25, cy), (0, 255, 255), 1)
        cv2.line(frame, (cx, cy - 25), (cx, cy + 25), (0, 255, 255), 1)
        cv2.circle(frame, (cx, cy), AUTONOMOUS_ALIGN_THRESHOLD_PX, (0, 255, 255), 1)

        qr_data = None
        pts = None

        # Konversi ke grayscale dan terapkan sharpening ringan untuk deteksi lebih andal & hemat CPU
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        kernel = np.array([[0, -1, 0],
                           [-1, 5,-1],
                           [0, -1, 0]], dtype=np.float32)
        sharpened = cv2.filter2D(gray, -1, kernel)

        # 1. Coba WeChat QR Detector jika tersedia
        if self.wechat_detector is not None:
            try:
                res, points = self.wechat_detector.detectAndDecode(sharpened)
                if res and len(res) > 0 and len(points) > 0:
                    qr_data = res[0]
                    pts = np.array(points[0], dtype=np.int32)  # shape (4, 2)
            except Exception as e:
                logger.error(f"[QRDetector-Front] WeChat decode error: {e}")

        # 2. Fallback ke pyzbar jika WeChat gagal atau tidak tersedia
        if qr_data is None and _PYZBAR_OK:
            try:
                decoded = pyzbar.decode(sharpened)
                if decoded:
                    qr = max(decoded, key=lambda q: q.rect.width * q.rect.height)
                    qr_data = qr.data.decode("utf-8", errors="replace")
                    rect = qr.rect
                    pts = np.array([[rect.left, rect.top],
                                    [rect.left + rect.width, rect.top],
                                    [rect.left + rect.width, rect.top + rect.height],
                                    [rect.left, rect.top + rect.height]], dtype=np.int32)
            except Exception as e:
                logger.error(f"[QRDetector-Front] pyzbar decode error: {e}")

        if qr_data is None or pts is None:
            cv2.putText(frame, "SEARCHING QR...", (8, h - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 200), 1)
            with self._lock:
                self._latest_result = None
            return frame

        # Hitung center dari pts
        qr_cx = int(np.mean(pts[:, 0]))
        qr_cy = int(np.mean(pts[:, 1]))
        offset_x = float(qr_cx - cx)
        offset_y = float(qr_cy - cy)
        aligned = (abs(offset_x) < AUTONOMOUS_ALIGN_THRESHOLD_PX and
                   abs(offset_y) < AUTONOMOUS_ALIGN_THRESHOLD_PX)

        result = QRResult(data=qr_data, offset_x=offset_x, offset_y=offset_y,
                          aligned=aligned, timestamp=time.time())
        with self._lock:
            self._latest_result = result
        self._try_send_to_queue(result)

        color = (0, 255, 0) if aligned else (0, 200, 255)
        cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=2)
        cv2.circle(frame, (qr_cx, qr_cy), 4, color, -1)
        cv2.line(frame, (cx, cy), (qr_cx, qr_cy), color, 1)

        # Cari sudut kiri atas untuk label teks
        left_x = int(np.min(pts[:, 0]))
        top_y = int(np.min(pts[:, 1]))
        cv2.putText(frame, "QR: " + qr_data[:14], (left_x, max(top_y - 8, 12)),
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
