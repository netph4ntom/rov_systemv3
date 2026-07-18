# camera_bottom/qr_detector.py
# QR code detection menggunakan pyzbar + OpenCV.
# Sekaligus menghitung apakah ROV sudah aligned di atas dock (center check).
#
# Output:
#   - Decode string QR → dikirim via ZmqQueueWrapper (ZMQ PUB topik "qr_result")
#   - Dock alignment event → dikirim via ZmqQueueWrapper (ZMQ PUB topik "dock_event")
#
# Detail:
#   - scan() return 3 nilai: (qr_data, is_dock_aligned, bbox)
#   - bbox dikonversi dari pyzbar rect / WeChat points → np.ndarray shape (1, 4, 2)
#     agar kompatibel dengan cv2.polylines.

import time
import logging
import multiprocessing
import numpy as np
import cv2

try:
    from pyzbar import pyzbar
    _PYZBAR_OK = True
except ImportError:
    _PYZBAR_OK = False

from config import (
    QR_SCAN_INTERVAL_MS,
    WECHAT_QR_DETECT_PROTOTXT,
    WECHAT_QR_DETECT_CAFFEMODEL,
    WECHAT_QR_SR_PROTOTXT,
    WECHAT_QR_SR_CAFFEMODEL,
)
from core.wechat_model_downloader import ensure_wechat_models

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

        # Inisialisasi WeChat QR Code detector
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
                        logger.info("[QRDetector] WeChat QR Code Detector (dengan Super Resolution) berhasil diinisialisasi.")
                    except TypeError as te:
                        if "arguments" in str(te):
                            logger.warning(f"[QRDetector] Gagal dengan 4 argumen: {te}. Mencoba dengan 2 argumen (tanpa Super Resolution)...")
                            self.wechat_detector = cv2.wechat_qrcode_WeChatQRCode(
                                WECHAT_QR_DETECT_PROTOTXT,
                                WECHAT_QR_DETECT_CAFFEMODEL
                            )
                            logger.info("[QRDetector] WeChat QR Code Detector (tanpa Super Resolution) berhasil diinisialisasi.")
                        else:
                            raise te
                else:
                    logger.warning("[QRDetector] Modul WeChatQRCode tidak tersedia di OpenCV. Menggunakan pyzbar.")
            else:
                logger.warning("[QRDetector] Gagal mengunduh model WeChat QR. Menggunakan pyzbar.")
        except Exception as e:
            logger.error(f"[QRDetector] Gagal inisialisasi WeChat QR: {e}. Menggunakan pyzbar.")

    # ──────────────────────────────────────────
    # Main method — dipanggil tiap frame
    # ──────────────────────────────────────────
    def scan(
        self,
        frame: np.ndarray,
        processor: any,
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

        # Prapemrosesan dijalankan di sini, HANYA ketika interval scan terpenuhi
        if hasattr(processor, "preprocess_for_qr"):
            preprocessed = processor.preprocess_for_qr(frame)
        else:
            preprocessed = processor  # Fallback jika gambar preprocessed dilewatkan langsung (legacy/test)

        qr_data = None
        bbox = None

        # 1. Coba WeChat QR Detector jika tersedia (gunakan grayscale preprocessed untuk kecepatan CNN)
        if self.wechat_detector is not None:
            qr_data, bbox = self._decode_wechat(preprocessed)

        # 2. Fallback ke pyzbar jika WeChat gagal atau tidak tersedia
        if qr_data is None and _PYZBAR_OK:
            qr_data, bbox_raw = self._decode_pyzbar(preprocessed)
            if bbox_raw is not None:
                bbox = self._rect_to_ndarray(bbox_raw)

        # Snapshot state lama sebelum diupdate
        prev_qr_data = self._last_qr_data
        prev_aligned = self._dock_aligned

        # Update alignment berdasarkan posisi center QR vs center frame
        if bbox is not None:
            h, w = frame.shape[:2]
            self._dock_aligned = self._check_alignment_bbox(bbox, w, h)
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
    def _decode_wechat(self, frame: np.ndarray) -> tuple[str | None, np.ndarray | None]:
        """
        Decode QR menggunakan WeChat QR.
        Return (data_string, np_bbox) atau (None, None).
        """
        try:
            res, points = self.wechat_detector.detectAndDecode(frame)
            if res and len(res) > 0 and len(points) > 0:
                data = res[0]
                pts = points[0]  # numpy array shape (4, 2)
                bbox = np.array([pts], dtype=np.int32)
                return data, bbox
        except Exception as e:
            logger.error(f"[QRDetector] WeChat decode error: {e}")
        return None, None

    def _decode_pyzbar(self, frame: np.ndarray) -> tuple[str | None, any]:
        """
        Decode QR code menggunakan pyzbar.
        Return (data_string, pyzbar_rect) atau (None, None).
        """
        if not _PYZBAR_OK:
            return None, None
        try:
            decoded_objects = pyzbar.decode(frame)
            for obj in decoded_objects:
                if obj.type == "QRCODE":
                    data = obj.data.decode("utf-8", errors="replace")
                    return data, obj.rect
        except Exception as e:
            logger.error(f"[QRDetector] pyzbar decode error: {e}")
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

    def _check_alignment_bbox(self, bbox: np.ndarray, frame_w: int, frame_h: int) -> bool:
        """
        Cek apakah center QR code dekat center frame menggunakan bbox numpy array shape (1, 4, 2).
        """
        pts = bbox[0]
        qr_cx = int(np.mean(pts[:, 0]))
        qr_cy = int(np.mean(pts[:, 1]))
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