# camera_front/record.py
# VideoWriter wrapper untuk merekam stream kamera depan ke file .mp4
#
# Lifecycle:
#   start(filename) → buka VideoWriter, set _recording = True
#   write(frame)    → dipanggil setiap frame saat _recording == True
#   stop()          → flush + tutup VideoWriter, return path file
#
# Thread safety: semua method dipanggil dari satu thread (capture loop
# di stream_server.py), jadi tidak butuh lock.

import cv2
import os
import logging
from datetime import datetime
from typing import Optional
from config import (
    FRAME_WIDTH, FRAME_HEIGHT, FRAME_FPS,
    RECORD_DIR, RECORD_FOURCC,
)

logger = logging.getLogger(__name__)


class FrontRecorder:
    def __init__(self):
        self._writer:Optional[cv2.VideoWriter] = None
        self._filepath:Optional[str] = None
        self.is_recording: bool = False

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────
    def start(self, filename:Optional[str] = None) -> str:
        """
        Mulai rekaman. Return path file yang akan disimpan.
        Jika sudah recording, stop dulu lalu mulai baru.
        """
        if self.is_recording:
            logger.warning("[FrontRecorder] Sudah recording, stop dulu...")
            self.stop()

        os.makedirs(RECORD_DIR, exist_ok=True)

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"front_{timestamp}.mp4"

        self._filepath = os.path.join(RECORD_DIR, filename)

        fourcc = cv2.VideoWriter_fourcc(*RECORD_FOURCC)
        self._writer = cv2.VideoWriter(
            self._filepath,
            fourcc,
            FRAME_FPS,
            (FRAME_WIDTH, FRAME_HEIGHT)
        )

        if not self._writer.isOpened():
            logger.error(f"[FrontRecorder] Gagal buka VideoWriter: {self._filepath}")
            self._writer = None
            self._filepath = None
            return ""

        self.is_recording = True
        logger.info(f"[FrontRecorder] Recording dimulai → {self._filepath}")
        return self._filepath

    def write(self, frame) -> None:
        """Tulis satu frame ke file video. Dipanggil setiap frame dari capture loop."""
        if not self.is_recording or self._writer is None:
            return
        # Resize jika frame tidak sesuai ukuran writer
        h, w = frame.shape[:2]
        if w != FRAME_WIDTH or h != FRAME_HEIGHT:
            frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))
        self._writer.write(frame)

    def stop(self) ->Optional[str]:
        """
        Stop recording dan flush file.
        Return path file yang sudah disimpan, atau None jika tidak ada.
        """
        if not self.is_recording or self._writer is None:
            return None

        self._writer.release()
        self._writer = None
        self.is_recording = False
        saved_path = self._filepath
        self._filepath = None
        logger.info(f"[FrontRecorder] Recording selesai → {saved_path}")
        return saved_path

    def get_current_filepath(self) ->Optional[str]:
        return self._filepath if self.is_recording else None