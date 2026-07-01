# shared_queue.py
# Shared multiprocessing queues untuk komunikasi antar proses.
#
# Queue map:
#
#   camera_bottom ──(qr_result)────► core → WebSocket → React
#   camera_bottom ──(dock_event)───► core → WebSocket → React
#   camera_bottom ──(frame_snapshot)► core (health check / snapshot API)
#
# Note: MAVLink, telemetry, dan trajectory TIDAK menggunakan multiprocessing queue
# karena semuanya berjalan dalam satu proses "core" yang sama.
# Komunikasi telemetry → websocket dilakukan via threading callback di dalam core.

import multiprocessing
from typing import Any

from config import QUEUE_MAXSIZE


def create_shared_queues(manager: Any) -> dict[str, Any]:
    """
    Buat semua queue yang dibutuhkan.
    Dipanggil sekali di main.py lalu di-pass ke masing-masing proses.
    """
    return {
        # Hasil decode QR code: camera_bottom → core
        "qr_result": manager.Queue(maxsize=QUEUE_MAXSIZE),

        # Event docking (aligned / lost): camera_bottom → core
        "dock_event": manager.Queue(maxsize=QUEUE_MAXSIZE),

        # Frame snapshot (opsional): camera_bottom → core
        "frame_snapshot": manager.Queue(maxsize=2),

        # Command queue dari core ke kamera depan
        "cmd_front": manager.Queue(maxsize=QUEUE_MAXSIZE),

        # Command queue dari core ke kamera bawah
        "cmd_bottom": manager.Queue(maxsize=QUEUE_MAXSIZE),

        # Hasil aksi kamera (screenshot/record) ke core
        "result_camera": manager.Queue(maxsize=QUEUE_MAXSIZE),
    }