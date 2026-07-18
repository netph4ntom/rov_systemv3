# camera_front/stream_server.py
# MJPEG streaming server untuk kamera depan menggunakan FastAPI & ZMQ.
#
# Thread model:
#   - ASGI Event Loop: WebRTC negosiasi (/offer), health check (/health)
#   - Thread: capture_loop → baca frame, jalankan processor
#   - Thread: command_loop → baca dari ZMQ PULL socket, jalankan aksi (screenshot, record)

import cv2
import threading
import logging
import asyncio
import json
import time
import zmq
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# WebRTC imports
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
import av

from camera_front.camera import FrontCamera
from camera_front.image_processing import FrontImageProcessor
from camera_front.record import FrontRecorder
from camera_front.screenshot import FrontScreenshot
from camera_front.qr_detector import QRDetector
from typing import Optional, Any
from config import (
    PORT_STREAM_FRONT,
    MJPEG_QUALITY,
    FRAME_FPS,
    ZMQ_PORT_FRONT_PUB,
    ZMQ_PORT_FRONT_CMD,
)

logger = logging.getLogger(__name__)
app = FastAPI(title="Front Camera Stream Server")

# Setup CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_pcs = set()


class ZmqQueueWrapper:
    """
    Queue-like wrapper that publishes elements via a ZMQ PUB socket.
    Allows QRDetector and other components to put messages without code changes.
    """
    def __init__(self, pub_socket: zmq.Socket, topic: str):
        self.pub_socket = pub_socket
        self.topic = topic
        self._lock = threading.Lock()

    def put_nowait(self, item: Any):
        try:
            with self._lock:
                self.pub_socket.send_multipart([
                    self.topic.encode('utf-8'),
                    json.dumps(item).encode('utf-8')
                ])
        except Exception as e:
            logger.error(f"[ZmqQueueWrapper] Error sending on topic {self.topic}: {e}")


class CameraVideoTrack(MediaStreamTrack):
    """
    Video stream track reading from the display_frame buffer.
    """
    kind = "video"

    def __init__(self, get_frame_fn):
        super().__init__()
        self.get_frame_fn = get_frame_fn

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        
        # Get frame
        frame = self.get_frame_fn()
        if frame is None:
            await asyncio.sleep(0.01)
            frame = self.get_frame_fn()
            if frame is None:
                import numpy as np
                frame = np.zeros((480, 640, 3), dtype=np.uint8)

        # Convert OpenCV BGR array to PyAV VideoFrame
        video_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
        video_frame.pts = pts
        video_frame.time_base = time_base

        return video_frame


async def _negotiate_async(params):
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    pc = RTCPeerConnection()
    _pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"[WebRTC] Front connection state: {pc.connectionState}")
        if pc.connectionState in ["failed", "closed"]:
            await pc.close()
            _pcs.discard(pc)

    def get_frame():
        with _frame_lock:
            return _display_frame

    track = CameraVideoTrack(get_frame)
    pc.addTrack(track)

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    # Wait for ICE gathering to complete
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.01)

    return {
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    }


@app.post("/offer")
async def offer(request: Request):
    params = await request.json()
    if not params or "sdp" not in params or "type" not in params:
        return JSONResponse(status_code=400, content={"error": "Missing sdp or type in request"})

    try:
        answer = await _negotiate_async(params)
        return answer
    except Exception as e:
        logger.exception("[WebRTC] Gagal negosiasi WebRTC")
        return JSONResponse(status_code=500, content={"error": str(e)})


_camera: Optional[FrontCamera] = None
_processor: Optional[FrontImageProcessor] = None
_recorder: Optional[FrontRecorder] = None
_screenshotter: Optional[FrontScreenshot] = None
_qr_detector: Optional[QRDetector] = None

# ZMQ socket references
_zmq_ctx: Optional[zmq.Context] = None
_zmq_pub: Optional[zmq.Socket] = None
_zmq_pull_cmd: Optional[zmq.Socket] = None

# Wrapper for sending command results via ZMQ
_result_queue: Optional[ZmqQueueWrapper] = None

_raw_frame = None
_display_frame = None
_frame_id = 0
_frame_lock = threading.Lock()


def _capture_loop():
    global _raw_frame, _display_frame, _frame_id

    while True:
        if _camera is None or _processor is None:
            time.sleep(0.1)
            continue

        ret, frame = _camera.read_frame()
        if not ret or frame is None:
            time.sleep(0.01)
            continue

        raw = frame.copy()

        # Frame dengan HUD untuk stream
        display = _processor.process(frame)

        # QR overlay (hanya saat detektor aktif untuk autonomous alignment)
        if _qr_detector is not None and _qr_detector.is_active:
            display = _qr_detector.process_frame(display)

        with _frame_lock:
            _raw_frame = raw
            _display_frame = display
            _frame_id += 1

        # Tulis ke recorder jika sedang recording
        if _recorder is not None:
            _recorder.write(raw)


def _zmq_command_loop():
    """Background thread to pull ZMQ commands and execute them."""
    while True:
        try:
            if _zmq_pull_cmd is None:
                time.sleep(0.5)
                continue

            cmd = _zmq_pull_cmd.recv_json()
            action = cmd.get("action", "")
            logger.info(f"[FrontStream] Command diterima via ZMQ: {action}")

            if action == "screenshot":
                with _frame_lock:
                    frame = _raw_frame.copy() if _raw_frame is not None else None
                filepath = _screenshotter.take(frame)
                _send_result(action, filepath)

            elif action == "record_start":
                filepath = _recorder.start()
                _send_result(action, filepath)

            elif action == "record_stop":
                filepath = _recorder.stop()
                _send_result(action, filepath)

            elif action == "qr_activate":
                if _qr_detector is not None:
                    _qr_detector.activate()

            elif action == "qr_deactivate":
                if _qr_detector is not None:
                    _qr_detector.deactivate()

            else:
                logger.warning(f"[FrontStream] Unknown command: {action}")
        except Exception as e:
            logger.error(f"[FrontStream] Error di ZMQ command loop: {e}")
            time.sleep(0.1)


def _send_result(action: str, filepath: Optional[str]):
    if _result_queue is None:
        return
    import os
    status = "ok" if filepath else "error"
    payload = {
        "camera": "front",
        "action": action,
        "status": status,
        "filepath": filepath or "",
        "filename": os.path.basename(filepath) if filepath else "",
    }
    _result_queue.put_nowait(payload)


def _generate_frames():
    last_frame_id = -1
    while True:
        with _frame_lock:
            frame = _display_frame
            current_frame_id = _frame_id

        if frame is None:
            time.sleep(0.03)
            continue

        if current_frame_id == last_frame_id:
            time.sleep(0.01)  # Tunggu 10ms jika belum ada frame baru
            continue

        last_frame_id = current_frame_id

        ret, buffer = cv2.imencode(
            ".jpg", frame,
            [cv2.IMWRITE_JPEG_QUALITY, MJPEG_QUALITY]
        )
        if not ret:
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + buffer.tobytes()
            + b"\r\n"
        )


@app.get("/stream")
async def stream():
    return StreamingResponse(
        _generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/health")
async def health():
    cam_ok = bool(_camera and _camera.cap and _camera.cap.isOpened())
    return {
        "camera": "front",
        "status": "ok" if cam_ok else "error",
        "recording": _recorder.is_recording if _recorder else False,
        "record_file": _recorder.get_current_filepath() if _recorder else None,
    }


def run_front_stream_server():
    global _camera, _processor, _recorder, _screenshotter, _qr_detector
    global _zmq_ctx, _zmq_pub, _zmq_pull_cmd, _result_queue

    logging.basicConfig(level=logging.INFO)
    logger.info("[FrontStream] Proses dimulai")

    # Inisialisasi ZMQ sockets (Kamera mengikat PUB port, menghubungkan ke PULL port)
    _zmq_ctx = zmq.Context()
    
    # PUB socket untuk mengirim event QR dan camera command results
    _zmq_pub = _zmq_ctx.socket(zmq.PUB)
    _zmq_pub.bind(f"tcp://127.0.0.1:{ZMQ_PORT_FRONT_PUB}")
    logger.info(f"[FrontStream] ZMQ PUB Bound to {ZMQ_PORT_FRONT_PUB}")

    # PULL socket untuk menerima command screenshot/record dari core
    _zmq_pull_cmd = _zmq_ctx.socket(zmq.PULL)
    _zmq_pull_cmd.connect(f"tcp://127.0.0.1:{ZMQ_PORT_FRONT_CMD}")
    logger.info(f"[FrontStream] ZMQ PULL Connected to {ZMQ_PORT_FRONT_CMD}")

    # Buat queue wrappers yang meneruskan pesan ke ZMQ PUB
    qr_front_result_zmq = ZmqQueueWrapper(_zmq_pub, "qr_front_result")
    _result_queue = ZmqQueueWrapper(_zmq_pub, "camera_result")

    _camera = FrontCamera()
    _processor = FrontImageProcessor(show_hud=True)
    _recorder = FrontRecorder()
    _screenshotter = FrontScreenshot()
    _qr_detector = QRDetector(result_queue=qr_front_result_zmq)

    # Start capture thread
    threading.Thread(
        target=_capture_loop,
        daemon=True,
        name="FrontCaptureThread"
    ).start()

    # Start ZMQ command listener thread
    threading.Thread(
        target=_zmq_command_loop,
        daemon=True,
        name="FrontZmqCmdThread"
    ).start()

    logger.info(f"[FrontStream] WebRTC + MJPEG server berjalan di port {PORT_STREAM_FRONT}")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT_STREAM_FRONT, log_level="warning")


if __name__ == "__main__":
    run_front_stream_server()