# camera_front/stream_server.py
# MJPEG streaming server untuk kamera depan.
# Update: sekarang juga consume cmd_queue untuk screenshot & recording.
#
# Thread model:
#   - Thread: Flask HTTP server (MJPEG /stream, /health)
#   - Thread: capture_loop → baca frame, jalankan processor, handle cmd_queue
#
# Command queue (dari core via shared_queue):
#   { "action": "screenshot" }
#   { "action": "record_start" }
#   { "action": "record_stop" }
#
# Result queue (ke core):
#   { "camera": "front", "action": "screenshot", "status": "ok", "filepath": "..." }
#   { "camera": "front", "action": "record_start", "status": "ok", "filepath": "..." }
#   { "camera": "front", "action": "record_stop",  "status": "ok", "filepath": "..." }

import cv2
import threading
import logging
import multiprocessing
import asyncio
from flask import Flask, Response, jsonify, request
from flask_cors import CORS

# WebRTC imports
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
import av

from camera_front.camera           import FrontCamera
from camera_front.image_processing import FrontImageProcessor
from camera_front.record           import FrontRecorder
from camera_front.screenshot       import FrontScreenshot
from camera_front.qr_detector      import QRDetector
from typing import Optional
from config import PORT_STREAM_FRONT, MJPEG_QUALITY, FRAME_FPS

logger = logging.getLogger(__name__)
app = Flask(__name__)
CORS(app)

# WebRTC global state
_async_loop = None
_pcs = set()


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
            # If no frame, sleep briefly and retry
            await asyncio.sleep(0.01)
            frame = self.get_frame_fn()
            if frame is None:
                # Still no frame, send blank frame to avoid error
                import numpy as np
                frame = np.zeros((480, 640, 3), dtype=np.uint8)

        # Convert OpenCV BGR array to PyAV VideoFrame
        video_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
        video_frame.pts = pts
        video_frame.time_base = time_base

        # Regulate speed to match expected FPS
        await asyncio.sleep(1 / FRAME_FPS)

        return video_frame


def _start_async_loop():
    global _async_loop
    _async_loop = asyncio.new_event_loop()
    t = threading.Thread(
        target=_async_loop.run_forever,
        daemon=True,
        name="FrontAsyncLoop"
    )
    t.start()


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


@app.route("/offer", methods=["POST"])
def offer():
    params = request.json
    if not params or "sdp" not in params or "type" not in params:
        return jsonify({"error": "Missing sdp or type in request"}), 400

    # Submit task to the running asyncio loop in the background thread
    coro = _negotiate_async(params)
    future = asyncio.run_coroutine_threadsafe(coro, _async_loop)
    try:
        # Wait for the negotiation to complete (timeout after 10s)
        answer = future.result(timeout=10.0)
        return jsonify(answer)
    except Exception as e:
        logger.exception("[WebRTC] Gagal negosiasi WebRTC")
        return jsonify({"error": str(e)}), 500


_camera    :Optional[FrontCamera]          = None
_processor :Optional[FrontImageProcessor] = None
_recorder  :Optional[FrontRecorder]       = None
_screenshotter:Optional[FrontScreenshot]  = None
_qr_detector  :Optional[QRDetector]       = None

# Queue references (di-set oleh run_front_stream_server)
_cmd_queue:Optional[multiprocessing.Queue]    = None
_result_queue:Optional[multiprocessing.Queue] = None

# Frame terbaru (RAW, sebelum processor overlay) untuk screenshot
# Frame processed untuk MJPEG stream
_raw_frame     = None
_display_frame = None
_frame_lock    = threading.Lock()


# ──────────────────────────────────────────────
# Capture loop (background thread)
# ──────────────────────────────────────────────
def _capture_loop():
    """
    Baca kamera tiap iterasi, proses frame, handle command queue.
    Berjalan di background thread agar tidak blocking Flask.
    """
    global _raw_frame, _display_frame

    while True:
        ret, frame = _camera.read_frame()
        if not ret or frame is None:
            continue

        # Simpan raw frame untuk screenshot (tanpa HUD overlay)
        raw = frame.copy()

        # Frame dengan HUD untuk stream
        display = _processor.process(frame.copy())

        # QR overlay (hanya saat detektor aktif untuk autonomous alignment)
        if _qr_detector and _qr_detector.is_active:
            display = _qr_detector.process_frame(display)

        with _frame_lock:
            _raw_frame     = raw
            _display_frame = display

        # Tulis ke recorder jika sedang recording (pakai raw frame)
        _recorder.write(raw)

        # Proses command dari core (non-blocking)
        _handle_commands()


def _handle_commands():
    """Drain cmd_queue secara non-blocking dan eksekusi perintah."""
    if _cmd_queue is None:
        return
    try:
        cmd = _cmd_queue.get_nowait()
    except Exception:
        return  # queue kosong, normal

    action = cmd.get("action", "")
    logger.info(f"[FrontStream] Command diterima: {action}")

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
        if _qr_detector:
            _qr_detector.activate()

    elif action == "qr_deactivate":
        if _qr_detector:
            _qr_detector.deactivate()

    else:
        logger.warning(f"[FrontStream] Unknown command: {action}")


def _send_result(action: str, filepath:Optional[str]):
    """Kirim hasil command ke result_queue untuk di-forward ke React oleh core."""
    if _result_queue is None:
        return
    status = "ok" if filepath else "error"
    payload = {
        "camera":   "front",
        "action":   action,
        "status":   status,
        "filepath": filepath or "",
        "filename": filepath.split("/")[-1] if filepath else "",
    }
    try:
        _result_queue.put_nowait(payload)
    except Exception:
        logger.warning("[FrontStream] result_queue penuh, result dibuang")


# ──────────────────────────────────────────────
# MJPEG generator
# ──────────────────────────────────────────────
def _generate_frames():
    while True:
        with _frame_lock:
            frame = _display_frame

        if frame is None:
            continue

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


# ──────────────────────────────────────────────
# Flask routes
# ──────────────────────────────────────────────
@app.route("/stream")
def stream():
    return Response(
        _generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/health")
def health():
    cam_ok = bool(_camera and _camera.cap and _camera.cap.isOpened())
    return jsonify({
        "camera":      "front",
        "status":      "ok" if cam_ok else "error",
        "recording":   _recorder.is_recording if _recorder else False,
        "record_file": _recorder.get_current_filepath() if _recorder else None,
    })


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
def run_front_stream_server(
    cmd_queue:    multiprocessing.Queue,
    result_queue: multiprocessing.Queue,
    qr_front_result_queue: Optional[multiprocessing.Queue] = None,
):
    global _camera, _processor, _recorder, _screenshotter, _qr_detector
    global _cmd_queue, _result_queue

    logging.basicConfig(level=logging.INFO)
    logger.info("[FrontStream] Proses dimulai")

    _cmd_queue    = cmd_queue
    _result_queue = result_queue

    _camera        = FrontCamera()
    _processor     = FrontImageProcessor(show_hud=True)
    _recorder      = FrontRecorder()
    _screenshotter = FrontScreenshot()
    _qr_detector   = QRDetector(result_queue=qr_front_result_queue)

    threading.Thread(
        target=_capture_loop,
        daemon=True,
        name="FrontCaptureThread"
    ).start()

    # Mulai thread background asyncio loop untuk WebRTC
    _start_async_loop()

    logger.info(f"[FrontStream] WebRTC + MJPEG server berjalan di port {PORT_STREAM_FRONT}")
    app.run(
        host="0.0.0.0",
        port=PORT_STREAM_FRONT,
        threaded=True,
        use_reloader=False
    )


if __name__ == "__main__":
    # Dummy queue untuk run standalone (testing)
    mgr = multiprocessing.Manager()
    run_front_stream_server(mgr.Queue(), mgr.Queue())