# camera_bottom/image_processing.py
# Pipeline image processing untuk kamera bawah ROV.
# Fokus pada:
#   1. Pre-processing untuk mempermudah deteksi QR code
#   2. Deteksi alignment/docking (apakah ROV sudah di atas dock marker)
#   3. HUD overlay informatif untuk operator

import cv2
import numpy as np
import logging

logger = logging.getLogger(__name__)

# Panjang maksimum teks QR yang ditampilkan di overlay bbox
QR_DISPLAY_MAX_LEN = 20


class BottomImageProcessor:
    def __init__(self, show_hud: bool = True):
        self.show_hud  = show_hud
        self.frame_count = 0
        self._last_qr_data: str | None = None   # injeksi dari qr_detector
        self._dock_aligned: bool = False         # injeksi dari docking logic
        self._last_bbox: np.ndarray | None = None  # injeksi dari qr_detector

        # Buat CLAHE sekali untuk menghindari object allocation tiap call
        from config import CLAHE_CLIP_LIMIT, CLAHE_TILE_SIZE
        self._clahe = cv2.createCLAHE(
            clipLimit=CLAHE_CLIP_LIMIT,
            tileGridSize=CLAHE_TILE_SIZE,
        )

    # ──────────────────────────────────────────
    # State injection (dipanggil dari qr_detector)
    # ──────────────────────────────────────────
    def update_qr_data(self, data: str | None):
        self._last_qr_data = data

    def update_dock_status(self, aligned: bool):
        self._dock_aligned = aligned

    def update_bbox(self, bbox: np.ndarray | None):
        self._last_bbox = bbox

    # ──────────────────────────────────────────
    # Main pipeline
    # ──────────────────────────────────────────
    def process(self, frame: np.ndarray) -> np.ndarray:
        if frame is None:
            return frame

        self.frame_count += 1

        # 1. Sharpen untuk bantu QR reader
        frame = self._sharpen(frame)

        # 2. Gambar bounding box jika QR code terdeteksi
        frame = self._draw_qr_bbox(frame)

        # 3. Gambar crosshair center sebagai panduan docking
        frame = self._draw_crosshair(frame)

        # 4. HUD
        if self.show_hud:
            frame = self._draw_hud(frame)

        return frame

    # ──────────────────────────────────────────
    # Preprocessing helper
    # ──────────────────────────────────────────
    def preprocess_for_qr(self, frame: np.ndarray) -> np.ndarray:
        """
        Kembalikan versi grayscale + CLAHE + Sharpening Ringan untuk mempermudah deteksi QR code.
        Dipanggil oleh qr_detector.py, BUKAN dipakai untuk stream.
        """
        # 1. Grayscale
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # 2. CLAHE (Contrast Limited Adaptive Histogram Equalization)
        enhanced = self._clahe.apply(gray)
        
        # 3. Sharpening Ringan (meningkatkan ketajaman tepi modul QR agar terbaca lebih jauh)
        kernel = np.array([[0, -1, 0],
                           [-1, 5,-1],
                           [0, -1, 0]], dtype=np.float32)
        sharpened = cv2.filter2D(enhanced, -1, kernel)
        
        return sharpened

    # ──────────────────────────────────────────
    # Drawing helpers
    # ──────────────────────────────────────────
    def _sharpen(self, frame: np.ndarray) -> np.ndarray:
        kernel = np.array([[0, -1, 0],
                           [-1, 5,-1],
                           [0, -1, 0]])
        cv2.filter2D(frame, -1, kernel, dst=frame)
        return frame

    def _draw_qr_bbox(self, frame: np.ndarray) -> np.ndarray:
        if self._last_bbox is not None and len(self._last_bbox) > 0:
            pts = np.array(self._last_bbox, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(frame, [pts], isClosed=True, color=(0, 255, 0), thickness=2)

            if self._last_qr_data:
                # boundingRect lebih reliable daripada argmin(x+y)
                # untuk QR code yang miring/rotated
                x, y, _, _ = cv2.boundingRect(pts)
                cv2.putText(
                    frame,
                    self._last_qr_data[:QR_DISPLAY_MAX_LEN],
                    (x, max(y - 8, 15)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    2
                )
        return frame

    def _draw_crosshair(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        cx, cy = w // 2, h // 2
        color = (0, 255, 0) if self._dock_aligned else (0, 100, 255)
        cv2.line(frame, (cx - 30, cy), (cx + 30, cy), color, 1)
        cv2.line(frame, (cx, cy - 30), (cx, cy + 30), color, 1)
        cv2.circle(frame, (cx, cy), 40, color, 1)
        return frame

    def _draw_hud(self, frame: np.ndarray) -> np.ndarray:
        import datetime
        h, w = frame.shape[:2]

        # Panel atas
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, 32), (0, 0, 0), -1)
        frame = cv2.addWeighted(overlay, 0.45, frame, 0.55, 0)

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        dock_label = "DOCKED ✓" if self._dock_aligned else "SEARCHING"
        qr_label   = f"QR: {self._last_qr_data[:16]}..." if self._last_qr_data else "QR: --"

        cv2.putText(frame, f"BOTTOM | {ts} | {dock_label} | {qr_label}",
                    (5, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        return frame