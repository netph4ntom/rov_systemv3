# core/websocket.py
# WebSocket handler menggunakan Flask-SocketIO.
# Bertanggung jawab untuk:
#   1. Relay event QR code ke semua client React
#   2. Relay event docking (aligned / lost)
#   3. Relay hasil screenshot / recording dari proses kamera ke React
#   4. Background threads yang drain semua queue dari proses kamera
#
# SocketIO events yang di-emit ke React (outbound):
#   "qr_detected"        → { data, aligned, timestamp }
#   "dock_aligned"       → { aligned, timestamp }
#   "dock_lost"          → { aligned, timestamp }
#   "camera_result"      → { camera, action, status, filepath, filename }
#     contoh action: "screenshot", "record_start", "record_stop"
# core/websocket.py

import threading
import logging
import multiprocessing
from typing import Optional, Callable
from flask_socketio import SocketIO

logger = logging.getLogger(__name__)

socketio: Optional[SocketIO] = None
# FIX: callback untuk simpan QR ke history di routes.py
_qr_store_callback: Optional[Callable] = None


def init_socketio(sio: SocketIO):
    global socketio
    socketio = sio


def register_handlers(sio: SocketIO, qr_store_callback: Optional[Callable] = None):
    """Register base event handlers."""
    global _qr_store_callback
    _qr_store_callback = qr_store_callback

    @sio.on("ping_rov")
    def on_ping(data):
        sio.emit("pong_rov", {"echo": data})


def start_queue_drainer(
    qr_result_queue:     multiprocessing.Queue,
    dock_event_queue:    multiprocessing.Queue,
    result_camera_queue: multiprocessing.Queue,
):
    _start_drainer("QRQueueDrainer",     _drain_qr_queue,     qr_result_queue)
    _start_drainer("DockQueueDrainer",   _drain_dock_queue,   dock_event_queue)
    _start_drainer("ResultQueueDrainer", _drain_result_queue, result_camera_queue)
    logger.info("[WS] Queue drainer threads dimulai (QR, Dock, CameraResult)")


def _start_drainer(name: str, target, queue: multiprocessing.Queue):
    threading.Thread(target=target, args=(queue,), daemon=True, name=name).start()


def _drain_qr_queue(queue: multiprocessing.Queue):
    while True:
        try:
            payload = queue.get(timeout=1.0)
            logger.debug(f"[WS] QR: {payload.get('data', '')}")
            # FIX: simpan ke history routes via callback
            if _qr_store_callback:
                try: _qr_store_callback(payload)
                except Exception as e: logger.warning(f"[WS] QR store error: {e}")
            if socketio:
                socketio.emit("qr_detected", payload)
        except Exception:
            pass


def _drain_dock_queue(queue: multiprocessing.Queue):
    while True:
        try:
            payload = queue.get(timeout=1.0)
            event_name = payload.get("type", "dock_event")
            logger.debug(f"[WS] Dock: {event_name}")
            if socketio:
                socketio.emit(event_name, payload)
        except Exception:
            pass


def _drain_result_queue(queue: multiprocessing.Queue):
    while True:
        try:
            payload = queue.get(timeout=1.0)
            logger.info(f"[WS] CameraResult: {payload.get('camera','?')}/{payload.get('action','?')} → {payload.get('status','?')}")
            if socketio:
                socketio.emit("camera_result", payload)
        except Exception:
            pass