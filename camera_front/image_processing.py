# camera_front/image_processing.py
# Pipeline pre-processing frame kamera depan sebelum di-encode ke MJPEG.
#
# Perubahan dari versi sebelumnya:
#   - import datetime dipindahkan ke top-level (bukan di dalam loop per-frame)
#   - cv2.createCLAHE dibuat sekali di __init__, bukan tiap frame
#   - Color correction di-tune untuk kolam 1 meter (bukan laut dalam):
#       red boost dikurangi 30 → 10, blue reduce dikurangi 10 → 5
#   - clipLimit CLAHE diturunkan 2.0 → 1.5 (kolam terang, hindari over-enhance noise)
#   - Parameter color correction & CLAHE diambil dari config agar mudah di-tune
#   - HUD width tidak lagi hardcoded 220 — dihitung dari ukuran teks aktual

import datetime
import cv2
import numpy as np
import logging

from config import (
    COLOR_CORRECTION_RED_BOOST,
    COLOR_CORRECTION_BLUE_REDUCE,
    CLAHE_CLIP_LIMIT,
    CLAHE_TILE_SIZE,
)

logger = logging.getLogger(__name__)


class FrontImageProcessor:
    """
    Semua transformasi gambar untuk feed kamera depan.
    Method process() dipanggil oleh stream_server setiap frame.

    Tuning untuk kolam 1 meter:
      - Koreksi warna ringan (air dangkal, atenuasi merah minimal)
      - CLAHE clipLimit rendah (pencahayaan biasanya cukup baik)
    """

    def __init__(self, show_hud: bool = True):
        self.show_hud    = show_hud
        self.frame_count = 0

        # Buat CLAHE sekali — bukan tiap frame
        self._clahe = cv2.createCLAHE(
            clipLimit=CLAHE_CLIP_LIMIT,
            tileGridSize=CLAHE_TILE_SIZE,
        )

    # ──────────────────────────────────────────
    # Main entry point
    # ──────────────────────────────────────────
    def process(self, frame: np.ndarray) -> np.ndarray:
        """
        Terima raw frame, kembalikan frame yang sudah diproses.
        Pipeline:
          1. Color correction (kompensasi ringan untuk kolam 1m)
          2. CLAHE contrast enhancement
          3. HUD overlay
        """
        if frame is None:
            return frame

        self.frame_count += 1

        frame = self._color_correction(frame)
        frame = self._enhance_contrast(frame)

        if self.show_hud:
            frame = self._draw_hud(frame)

        return frame

    # ──────────────────────────────────────────
    # Pipeline steps
    # ──────────────────────────────────────────
    def _color_correction(self, frame: np.ndarray) -> np.ndarray:
        """
        Kompensasi white-balance ringan untuk kolam 1 meter.

        Di kolam dangkal 1m, atenuasi warna jauh lebih sedikit dibanding
        laut dalam — boost merah berlebihan malah membuat gambar kemerahan.
        Nilai default (dari config):
          RED_BOOST  = 10  (vs 30 yang terlalu agresif untuk kolam)
          BLUE_REDUCE = 5  (vs 10)

        cv2.add/subtract sudah saturate di 0–255, tidak perlu clipping manual.
        """
        b, g, r = cv2.split(frame)
        r = cv2.add(r, COLOR_CORRECTION_RED_BOOST)
        b = cv2.subtract(b, COLOR_CORRECTION_BLUE_REDUCE)
        return cv2.merge([b, g, r])

    def _enhance_contrast(self, frame: np.ndarray) -> np.ndarray:
        """
        CLAHE pada kanal L (LAB color space) untuk contrast lokal.
        CLAHE di-init sekali di __init__ untuk menghindari object allocation tiap frame.
        """
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = self._clahe.apply(l)
        lab = cv2.merge([l, a, b])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    def _draw_hud(self, frame: np.ndarray) -> np.ndarray:
        """
        Overlay teks: label kamera, timestamp, frame counter.
        Background width dihitung dari ukuran teks aktual (tidak hardcoded).
        """
        h, w = frame.shape[:2]
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        text = f"FRONT | {timestamp} | #{self.frame_count}"

        font       = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.5
        thickness  = 1

        # Hitung lebar background dari teks aktual
        (text_w, text_h), baseline = cv2.getTextSize(text, font, font_scale, thickness)
        pad = 6

        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (text_w + pad * 2, text_h + pad * 2), (0, 0, 0), -1)
        frame = cv2.addWeighted(overlay, 0.4, frame, 0.6, 0)

        cv2.putText(
            frame, text,
            (pad, text_h + pad),
            font, font_scale, (0, 255, 0), thickness
        )
        return frame