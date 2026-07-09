# core/routes.py
# REST API endpoints + SocketIO handlers menggunakan FastAPI & python-socketio.
#
# ── REST endpoints ────────────────────────────────────────────────
#   GET  /api/status                      → status sistem + MAVLink
#   GET  /api/streams                     → URL MJPEG stream kamera
#   GET  /api/telemetry                   → snapshot telemetry
#   GET  /api/trajectory                  → snapshot trajectory + path
#   POST /api/trajectory/reset            → reset posisi ROV
#   GET  /api/qr/history                  → riwayat QR
#   DELETE /api/qr/history                → clear QR history
#   GET  /api/health                      → health check
#   POST /api/camera/<cam>/screenshot     → trigger screenshot
#   POST /api/camera/<cam>/record/start   → mulai recording
#   POST /api/camera/<cam>/record/stop    → stop recording
#     <cam> = "front" | "bottom"

import logging
import threading
import asyncio
import queue
from datetime import datetime
from typing import List, Optional, Tuple
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import socketio
import zmq

from core.logger import get_logger, setup_logging
from core.websocket import sio, register_handlers, start_zmq_listener
from core.mavlink import MAVLinkBridge
from core.telemetry import TelemetryManager
from core.trajectory import TrajectoryEstimator
from core.failsafe import FailsafeWatchdog
from core.autonomous import AutonomousController
from config import (
    PORT_CORE_API,
    PORT_STREAM_FRONT,
    PORT_STREAM_BOTTOM,
    RC_NEUTRAL_PWM,
    JOYSTICK_SCALE_MS,
    ZMQ_PORT_BOTTOM_PUB,
    ZMQ_PORT_FRONT_PUB,
    ZMQ_PORT_BOTTOM_CMD,
    ZMQ_PORT_FRONT_CMD,
)

logger = get_logger(__name__)

_qr_history: List[dict] = []
QR_HISTORY_MAX = 100

_mav: Optional[MAVLinkBridge] = None
_tele: Optional[TelemetryManager] = None
_traj: Optional[TrajectoryEstimator] = None
_fs: Optional[FailsafeWatchdog] = None
_autonomous: Optional[AutonomousController] = None

_active_clients = 0
_clients_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None

# Throttle untuk emisi telemetry ke WebSocket (10Hz)
_last_telemetry_emit_time = 0.0
_telemetry_emit_interval = 0.1  # detik
_telemetry_lock = threading.Lock()

# ZMQ socket references (Core binds PUSH sockets, cameras connect PULL sockets)
_zmq_ctx: Optional[zmq.Context] = None
_zmq_push_front: Optional[zmq.Socket] = None
_zmq_push_bottom: Optional[zmq.Socket] = None
_zmq_lock = threading.Lock()

# Local queue for autonomous commands to camera front
_autonomous_cmd_queue = queue.Queue()


def _send_camera_cmd(camera: str, action: str) -> Tuple[bool, str]:
    socket = _zmq_push_front if camera == "front" else _zmq_push_bottom
    if socket is None:
        return False, f"ZMQ PUSH socket untuk kamera '{camera}' belum tersedia"
    try:
        with _zmq_lock:
            socket.send_json({"action": action})
        return True, f"Command '{action}' dikirim ke kamera {camera}"
    except Exception as e:
        return False, f"Gagal kirim command: {e}"


def _drain_autonomous_cmd_queue():
    while True:
        try:
            cmd = _autonomous_cmd_queue.get()
            action = cmd.get("action")
            if action:
                _send_camera_cmd("front", action)
        except Exception as e:
            logger.error(f"[Routes] Error draining autonomous cmd queue: {e}")


def _store_qr_from_queue(data: dict):
    _qr_history.append({**data, "received_at": datetime.utcnow().isoformat()})
    if len(_qr_history) > QR_HISTORY_MAX:
        _qr_history.pop(0)


def create_app(
    mav: MAVLinkBridge,
    tele: TelemetryManager,
    traj: TrajectoryEstimator,
    fs: FailsafeWatchdog,
    autonomous: AutonomousController,
) -> Tuple[FastAPI, socketio.AsyncServer]:
    global _mav, _tele, _traj, _fs, _autonomous, _zmq_ctx, _zmq_push_front, _zmq_push_bottom
    _mav, _tele, _traj = mav, tele, traj
    _fs = fs
    _autonomous = autonomous

    # Setup ZMQ Context and Sockets
    _zmq_ctx = zmq.Context()
    _zmq_push_front = _zmq_ctx.socket(zmq.PUSH)
    _zmq_push_front.bind(f"tcp://127.0.0.1:{ZMQ_PORT_FRONT_CMD}")
    _zmq_push_bottom = _zmq_ctx.socket(zmq.PUSH)
    _zmq_push_bottom.bind(f"tcp://127.0.0.1:{ZMQ_PORT_BOTTOM_CMD}")
    logger.info(f"[Routes] ZMQ PUSH Bound: front={ZMQ_PORT_FRONT_CMD}, bottom={ZMQ_PORT_BOTTOM_CMD}")

    # Start background thread to listen to local autonomous commands
    threading.Thread(target=_drain_autonomous_cmd_queue, daemon=True, name="AutoCmdDrainer").start()

    fastapi_app = FastAPI(title="ROV Core API")

    @fastapi_app.on_event("startup")
    async def startup_event():
        global _loop
        _loop = asyncio.get_event_loop()
        logger.info(f"[CoreAPI] FastAPI startup: event loop captured: {_loop}")
        # Start ZMQ background listener in the active event loop
        start_zmq_listener(ZMQ_PORT_BOTTOM_PUB, ZMQ_PORT_FRONT_PUB)

    # Setup CORS middleware
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Bridge between sync background threads (MAVLink/Trajectory) and Async SocketIO
    def _emit_proxy(event, data):
        if _loop is not None:
            asyncio.run_coroutine_threadsafe(sio.emit(event, data), _loop)

    # Set callbacks
    def _on_telemetry(state: dict):
        # Trajectory Estimator tetap menerima data di full frequency untuk akurasi dead-reckoning
        traj.update_from_telemetry(state)
        
        # Batasi pengiriman telemetry ke WebSocket maksimal 10Hz
        global _last_telemetry_emit_time
        import time
        now = time.time()
        should_emit = False
        with _telemetry_lock:
            if now - _last_telemetry_emit_time >= _telemetry_emit_interval:
                _last_telemetry_emit_time = now
                should_emit = True
        
        if should_emit:
            _emit_proxy("telemetry_update", state)

    tele.on_telemetry_update = _on_telemetry

    def _on_trajectory(state: dict):
        _emit_proxy("trajectory_update", state)

    traj.on_trajectory_update = _on_trajectory
    mav.on_message_callback = tele.handle_message

    def _has_active_clients():
        with _clients_lock:
            return _active_clients > 0

    fs.get_client_connected = _has_active_clients

    # Register handlers
    def _on_qr_front_result(payload):
        # Forward to autonomous controller queue
        if autonomous and hasattr(autonomous, "_qr_queue") and autonomous._qr_queue is not None:
            try:
                autonomous._qr_queue.put_nowait(payload)
            except Exception:
                pass

    register_handlers(
        qr_store_callback=_store_qr_from_queue,
        qr_front_callback=_on_qr_front_result
    )

    # ── FastAPI REST Endpoints ─────────────────
    @fastapi_app.get("/api/status")
    async def status():
        return {
            "service": "ROV Core API",
            "status": "running",
            "timestamp": datetime.utcnow().isoformat(),
            "mavlink": {"connected": _mav.is_connected if _mav else False},
        }

    @fastapi_app.get("/api/streams")
    async def streams():
        return {
            "front": {
                "stream_url": f"http://localhost:{PORT_STREAM_FRONT}/stream",
                "webrtc_url": f"http://localhost:{PORT_STREAM_FRONT}/offer",
                "health_url": f"http://localhost:{PORT_STREAM_FRONT}/health"
            },
            "bottom": {
                "stream_url": f"http://localhost:{PORT_STREAM_BOTTOM}/stream",
                "webrtc_url": f"http://localhost:{PORT_STREAM_BOTTOM}/offer",
                "health_url": f"http://localhost:{PORT_STREAM_BOTTOM}/health"
            },
        }

    @fastapi_app.get("/api/telemetry")
    async def telemetry_snapshot():
        return _tele.get_state() if _tele else {}

    @fastapi_app.get("/api/trajectory")
    async def trajectory_snapshot():
        return _traj.get_state() if _traj else {}

    @fastapi_app.post("/api/trajectory/reset")
    async def trajectory_reset():
        if _traj:
            _traj.reset_position()
        return {"message": "Trajectory reset ke origin"}

    @fastapi_app.get("/api/qr/history")
    async def qr_history_get():
        return {"count": len(_qr_history), "history": _qr_history[-50:]}

    @fastapi_app.delete("/api/qr/history")
    async def qr_history_clear():
        _qr_history.clear()
        return {"message": "QR history cleared"}

    @fastapi_app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @fastapi_app.post("/api/camera/{cam}/screenshot")
    async def camera_screenshot(cam: str):
        if cam not in ("front", "bottom"):
            return JSONResponse(status_code=400, content={"error": "camera harus 'front' atau 'bottom'"})
        ok, msg = _send_camera_cmd(cam, "screenshot")
        return {"camera": cam, "action": "screenshot", "queued": ok, "message": msg}

    @fastapi_app.post("/api/camera/{cam}/record/start")
    async def camera_record_start(cam: str):
        if cam not in ("front", "bottom"):
            return JSONResponse(status_code=400, content={"error": "camera harus 'front' atau 'bottom'"})
        ok, msg = _send_camera_cmd(cam, "record_start")
        return {"camera": cam, "action": "record_start", "queued": ok, "message": msg}

    @fastapi_app.post("/api/camera/{cam}/record/stop")
    async def camera_record_stop(cam: str):
        if cam not in ("front", "bottom"):
            return JSONResponse(status_code=400, content={"error": "camera harus 'front' atau 'bottom'"})
        ok, msg = _send_camera_cmd(cam, "record_stop")
        return {"camera": cam, "action": "record_stop", "queued": ok, "message": msg}

    @fastapi_app.get("/api/failsafe/status")
    async def failsafe_status():
        return _fs.get_status() if _fs else {"error": "failsafe not initialized"}

    @fastapi_app.get("/api/failsafe/events")
    async def failsafe_events(limit: int = 50):
        return _fs.get_event_history(limit) if _fs else []

    @fastapi_app.get("/api/autonomous/status")
    async def autonomous_status_rest():
        return _autonomous.get_status() if _autonomous else {"state": "IDLE", "is_active": False}

    @fastapi_app.post("/api/trajectory/set_target")
    async def trajectory_set_target(request: Request):
        body = await request.json()
        target_id = body.get("target_id", "UNKNOWN")
        if not _traj:
            return JSONResponse(status_code=503, content={"error": "trajectory not initialized"})
        count = _traj.set_target_snapshot(target_id)
        return {
            "message": f"Target '{target_id}' snapshot disimpan",
            "target_id": target_id,
            "waypoints": count,
        }

    # ── SocketIO ASGI Handlers ──────────────────
    @sio.on("connect")
    async def on_connect(sid, environ, auth=None):
        logger.info(f"[Routes] React client terkoneksi: {sid}")
        global _active_clients
        with _clients_lock:
            _active_clients += 1
        if _fs:
            _fs.notify_dashboard_active()

        # Emit snapshots
        if _tele:
            await sio.emit("telemetry_update", _tele.get_state(), to=sid)
        if _traj:
            await sio.emit("trajectory_update", _traj.get_state(), to=sid)
        await sio.emit("mavlink_status", {"connected": _mav.is_connected if _mav else False}, to=sid)

    @sio.on("cmd_arm")
    async def on_arm(sid, data=None):
        if _mav:
            _mav.arm()

    @sio.on("cmd_disarm")
    async def on_disarm(sid, data=None):
        if _mav:
            _mav.disarm()

    @sio.on("cmd_set_mode")
    async def on_set_mode(sid, data: dict):
        if _mav:
            _mav.set_mode(data.get("mode", "MANUAL"))

    @sio.on("cmd_gripper")
    async def on_gripper(sid, data: dict):
        if _mav:
            _mav.gripper(data.get("action", "close"))

    @sio.on("cmd_light")
    async def on_light(sid, data: dict):
        if _mav:
            _mav.light(bool(data.get("state", False)))

    @sio.on("cmd_rc_override")
    async def on_rc_override(sid, data: dict):
        channels = {int(k): int(v) for k, v in data.get("channels", {}).items()}
        if _mav:
            _mav.rc_override(channels)
        if _traj:
            ch1 = channels.get(1, RC_NEUTRAL_PWM)
            ch2 = channels.get(2, RC_NEUTRAL_PWM)
            _traj.update_velocity(
                ((ch1 - RC_NEUTRAL_PWM) / 500.0) * JOYSTICK_SCALE_MS,
                ((ch2 - RC_NEUTRAL_PWM) / 500.0) * JOYSTICK_SCALE_MS,
            )

    @sio.on("cmd_emergency_stop")
    async def on_emergency_stop(sid, data=None):
        reason = (data or {}).get("reason", "Operator E-Stop")
        if _fs:
            _fs.trigger_emergency_stop(reason)

    @sio.on("cmd_clear_emergency")
    async def on_clear_emergency(sid, data=None):
        if _fs:
            _fs.clear_emergency()

    @sio.on("cmd_autonomous_start")
    async def on_autonomous_start(sid, data=None):
        if not _autonomous:
            await sio.emit("mission_complete", {"success": False, "reason": "Autonomous tidak diinisialisasi"}, to=sid)
            return
        target_id = (data or {}).get("target_id", "UNKNOWN")
        result = _autonomous.start_mission(target_id)
        if not result["ok"]:
            await sio.emit("mission_complete", {"success": False, "reason": result["reason"]}, to=sid)
        logger.info(f"[Routes] cmd_autonomous_start target='{target_id}' result={result}")

    @sio.on("cmd_autonomous_stop")
    async def on_autonomous_stop(sid, data=None):
        if _autonomous:
            reason = (data or {}).get("reason", "operator_abort")
            _autonomous.stop_mission(reason)
            logger.info(f"[Routes] cmd_autonomous_stop: {reason}")

    @sio.on("disconnect")
    async def on_disconnect(sid):
        logger.info(f"[Routes] React client disconnect: {sid}")
        global _active_clients
        with _clients_lock:
            _active_clients = max(0, _active_clients - 1)
        if _traj:
            _traj.update_velocity(0.0, 0.0)

    return fastapi_app, sio


def run_core_server():
    global _loop
    setup_logging()
    logger.info("[CoreAPI] Proses FastAPI dimulai")

    # Get asyncio event loop
    _loop = asyncio.get_event_loop()

    mav = MAVLinkBridge()
    tele = TelemetryManager()
    traj = TrajectoryEstimator()

    # Defer Socket.IO emit to proxy
    def _emit_proxy(event, data):
        if _loop is not None:
            asyncio.run_coroutine_threadsafe(sio.emit(event, data), _loop)

    fs = FailsafeWatchdog(mav=mav, tele=tele, sio_emit=_emit_proxy)

    # Local queues to bridge between ZMQ listener / routes and AutonomousController
    autonomous_qr_queue = queue.Queue()

    autonomous = AutonomousController(
        mav=mav,
        tele=tele,
        traj=traj,
        fs=fs,
        sio_emit=_emit_proxy,
        qr_front_result_queue=autonomous_qr_queue,
        cmd_front_queue=_autonomous_cmd_queue,
    )

    # Connect in background thread
    def _connect_bg():
        if not mav.connect():
            logger.warning("[CoreAPI] MAVLink tidak terhubung")

    threading.Thread(target=_connect_bg, daemon=True, name="MAVLinkConnector").start()

    fastapi_app, socketio_server = create_app(mav, tele, traj, fs, autonomous)

    # Mount Socket.IO ASGIApp on FastAPI
    app = socketio.ASGIApp(socketio_server, other_asgi_app=fastapi_app)

    # Start failsafe watchdog
    fs.start()

    # Note: ZMQ background listener starts on fastapi_app startup event to share the active event loop.

    logger.info(f"[CoreAPI] Uvicorn/ASGI Server berjalan di port {PORT_CORE_API}")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT_CORE_API, log_level="warning")