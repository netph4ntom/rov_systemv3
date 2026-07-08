# core/websocket.py
# WebSocket handler menggunakan python-socketio ASGI server.
# Bertanggung jawab untuk:
#   1. Relay event QR code ke semua client React
#   2. Relay event docking (aligned / lost)
#   3. Relay hasil screenshot / recording dari proses kamera ke React
#   4. Mendengar ZMQ PUB socket dari proses kamera asinkron
#
# SocketIO events yang di-emit ke React (outbound):
#   "qr_detected"        → { data, aligned, timestamp }
#   "dock_aligned"       → { aligned, timestamp }
#   "dock_lost"          → { aligned, timestamp }
#   "camera_result"      → { camera, action, status, filepath, filename }
#     contoh action: "screenshot", "record_start", "record_stop"

import logging
import asyncio
import json
import socketio
import zmq
import zmq.asyncio
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# Create the AsyncServer instance
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

_qr_store_callback: Optional[Callable] = None
_qr_front_callback: Optional[Callable] = None


def register_handlers(qr_store_callback: Optional[Callable] = None, qr_front_callback: Optional[Callable] = None):
    """Register base event handlers."""
    global _qr_store_callback, _qr_front_callback
    _qr_store_callback = qr_store_callback
    _qr_front_callback = qr_front_callback

    @sio.on("ping_rov")
    async def on_ping(sid, data):
        await sio.emit("pong_rov", {"echo": data}, to=sid)


def start_zmq_listener(
    bottom_pub_port: int,
    front_pub_port: int,
):
    """Start ZMQ listener as an asyncio task."""
    loop = asyncio.get_event_loop()
    task = loop.create_task(_zmq_listener_loop(bottom_pub_port, front_pub_port))
    logger.info("[WS] ZMQ Listener asyncio task dijalankan")
    return task


async def _zmq_listener_loop(bottom_pub_port: int, front_pub_port: int):
    context = zmq.asyncio.Context()
    sub_socket = context.socket(zmq.SUB)
    sub_socket.connect(f"tcp://127.0.0.1:{bottom_pub_port}")
    sub_socket.connect(f"tcp://127.0.0.1:{front_pub_port}")
    sub_socket.subscribe("")  # Subscribe ke semua topic

    logger.info(f"[WS] ZMQ Listener terhubung ke Ports: bottom={bottom_pub_port}, front={front_pub_port}")

    while True:
        try:
            parts = await sub_socket.recv_multipart()
            if len(parts) >= 2:
                topic = parts[0].decode('utf-8')
                payload_str = parts[1].decode('utf-8')
                
                try:
                    payload = json.loads(payload_str)
                except ValueError:
                    payload = payload_str
                
                # Routing event berdasarkan topic
                if topic == "qr_result":
                    logger.debug(f"[WS] ZMQ QR: {payload.get('data', '')}")
                    if _qr_store_callback:
                        try:
                            _qr_store_callback(payload)
                        except Exception as e:
                            logger.warning(f"[WS] QR store error: {e}")
                    await sio.emit("qr_detected", payload)

                elif topic == "dock_event":
                    event_name = payload.get("type", "dock_event")
                    logger.debug(f"[WS] ZMQ Dock: {event_name}")
                    await sio.emit(event_name, payload)

                elif topic == "camera_result":
                    logger.info(f"[WS] ZMQ CameraResult: {payload.get('camera','?')}/{payload.get('action','?')} → {payload.get('status','?')}")
                    await sio.emit("camera_result", payload)

                elif topic == "qr_front_result":
                    # Teruskan hasil QR kamera depan ke controller otonom
                    if _qr_front_callback:
                        try:
                            _qr_front_callback(payload)
                        except Exception as e:
                            logger.warning(f"[WS] QR front callback error: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[WS] Error di ZMQ listener loop: {e}")
            await asyncio.sleep(1.0)