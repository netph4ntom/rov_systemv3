# main.py — Entry point utama Project ROV Vision
#
# Arsitektur multiprocessing:
# main.py
# ├── Process 1: core            → REST API + WebSocket (port 8000)
# │     ├── MAVLinkBridge        → serial ke Pixhawk (background thread)
# │     ├── TelemetryManager     → parse MAVLink → state ROV
# │     ├── TrajectoryEstimator  → dead reckoning posisi
# │     └── WebSocket            → push semua ke React
# ├── Process 2: camera_front    → MJPEG stream (port 8001)
# └── Process 3: camera_bottom   → MJPEG stream + QR scan (port 8002)

# IPC Queue (cross-process):
# camera_bottom ──(qr_result_queue)──► core ──► React
# camera_bottom ──(dock_event_queue)─► core ──► React
# In-process callback (threading, dalam core):
# MAVLinkBridge ──► TelemetryManager ──► TrajectoryEstimator ──► SocketIO emit
# ──────────────────────────────────────────────────────────
# UPDATE log line di dalam main():
# logger.info("  Core API  : http://localhost:8000")
# logger.info("  Telemetry : ws://localhost:8000  (event: telemetry_update)")
# logger.info("  Trajectory: ws://localhost:8000  (event: trajectory_update)")
# logger.info("  Stream F  : http://localhost:8001/stream")
# logger.info("  Stream B  : http://localhost:8002/stream")
#
# Cara jalankan:
#   python main.py
#
# Cara stop:
#   Ctrl+C → semua proses dihentikan bersih via SIGINT

import sys
import logging
import multiprocessing
import signal

from shared_queue import create_shared_queues
from core.routes import run_core_server
from camera_front.stream_server import run_front_stream_server
from camera_bottom.stream_server import run_bottom_stream_server
from config import LOG_DIR, LOG_LEVEL

# ──────────────────────────────────────────────
# Setup logging
# ──────────────────────────────────────────────
from typing import List
import os
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(processName)s] %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "rov.log")),
    ]
)
logger = logging.getLogger("main")


# ──────────────────────────────────────────────
# Graceful shutdown
# ──────────────────────────────────────────────
_processes: List[multiprocessing.Process] = []

def _shutdown(signum, frame):
    logger.info("Signal diterima, menghentikan semua proses...")
    for p in _processes:
        if p.is_alive():
            p.terminate()
    for p in _processes:
        p.join(timeout=3)
    logger.info("Semua proses dihentikan. Selamat tinggal!")
    sys.exit(0)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    # WAJIB: set start method ke 'spawn' supaya aman di Linux/macOS
    # 'fork' bisa menyebabkan deadlock dengan OpenCV dan Flask
    multiprocessing.set_start_method("spawn", force=True)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("=" * 60)
    logger.info("  ROV Vision System — Starting up")
    logger.info("=" * 60)

    # Buat shared Manager dan queues
    manager = multiprocessing.Manager()
    shared_queues = create_shared_queues(manager)

    qr_result_queue     = shared_queues["qr_result"]
    dock_event_queue    = shared_queues["dock_event"]
    cmd_front_queue     = shared_queues["cmd_front"]
    cmd_bottom_queue    = shared_queues["cmd_bottom"]
    result_camera_queue = shared_queues["result_camera"]

    # ── Spawn proses ──────────────────────────
    processes_config = [
        {
            "name":   "CoreAPI",
            "target": run_core_server,
            "args":   (
                qr_result_queue, 
                dock_event_queue,
                cmd_front_queue,
                cmd_bottom_queue,
                result_camera_queue,
                ),
        },
        {
            "name":   "CameraFront",
            "target": run_front_stream_server,
            "args":   (
                cmd_front_queue,
                result_camera_queue,
                ),
        },
        {
            "name":   "CameraBottom",
            "target": run_bottom_stream_server,
            "args":   (
                qr_result_queue,
                dock_event_queue,
                cmd_bottom_queue,
                result_camera_queue,
            ),
        },
    ]

    for cfg in processes_config:
        p = multiprocessing.Process(
            target=cfg["target"],
            args=cfg["args"],
            name=cfg["name"],
            daemon=False,   # False agar proses anak tidak mati saat parent selesai join
        )
        p.start()
        _processes.append(p)
        logger.info(f"  [+] Proses '{cfg['name']}' dimulai (PID={p.pid})")

    logger.info("")
    logger.info("  Semua proses berjalan. URL:")
    logger.info("  Core API  : http://localhost:8000")
    logger.info("  Stream F  : http://localhost:8001/stream")
    logger.info("  Stream B  : http://localhost:8002/stream")
    logger.info("  Screenshots : ./storage/screenshots/")
    logger.info("  Recordings  : ./storage/recordings/")
    logger.info("  Tekan Ctrl+C untuk berhenti.")
    logger.info("=" * 60)

    # Monitor proses — restart otomatis jika crash
    try:
        while True:
            for p in _processes:
                if not p.is_alive():
                    logger.warning(f"  [!] Proses '{p.name}' mati (exitcode={p.exitcode}), restart...")
                    # TODO: implementasi restart logic di sini jika perlu
            # Check setiap 5 detik
            import time; time.sleep(5)
    except KeyboardInterrupt:
        _shutdown(None, None)


if __name__ == "__main__":
    main()