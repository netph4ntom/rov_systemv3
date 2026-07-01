# core/routes.py
# REST API endpoints + SocketIO handlers.
# Sudah include: MAVLink control, telemetry, trajectory, screenshot, recording.
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
#
# ── SocketIO inbound (dari React) ────────────────────────────────
#   "cmd_arm", "cmd_disarm", "cmd_set_mode", "cmd_gripper",
#   "cmd_light", "cmd_rc_override"
#
# ── SocketIO outbound (ke React) ─────────────────────────────────
#   "telemetry_update", "trajectory_update", "qr_detected",
#   "dock_aligned", "dock_lost", "mavlink_status", "camera_result"

# core/routes.py

import logging
import threading
import multiprocessing
from datetime import datetime
from flask import Flask, jsonify, request
from flask_socketio import SocketIO
from flask_cors import CORS
from typing import List, Optional, Tuple

from core.websocket  import init_socketio, register_handlers, start_queue_drainer
from core.mavlink    import MAVLinkBridge
from core.telemetry  import TelemetryManager
from core.trajectory import TrajectoryEstimator
from config import (
    PORT_CORE_API,
    PORT_STREAM_FRONT,
    PORT_STREAM_BOTTOM,
    RC_NEUTRAL_PWM,
    JOYSTICK_SCALE_MS,
)

logger = logging.getLogger(__name__)

_qr_history: List[dict] = []
QR_HISTORY_MAX = 100

_mav:  Optional[MAVLinkBridge]      = None
_tele: Optional[TelemetryManager]   = None
_traj: Optional[TrajectoryEstimator]= None
_cmd_front:  Optional[multiprocessing.Queue] = None
_cmd_bottom: Optional[multiprocessing.Queue] = None


def _send_camera_cmd(camera: str, action: str) -> Tuple[bool, str]:
    queue = _cmd_front if camera == "front" else _cmd_bottom
    if queue is None:
        return False, f"cmd_queue untuk kamera '{camera}' belum tersedia"
    try:
        queue.put_nowait({"action": action})
        return True, f"Command '{action}' dikirim ke kamera {camera}"
    except Exception as e:
        return False, f"Gagal kirim command: {e}"


def create_app(
    mav:        MAVLinkBridge,
    tele:       TelemetryManager,
    traj:       TrajectoryEstimator,
    cmd_front:  multiprocessing.Queue,
    cmd_bottom: multiprocessing.Queue,
) -> Tuple[Flask, SocketIO]:
    global _mav, _tele, _traj, _cmd_front, _cmd_bottom
    _mav, _tele, _traj = mav, tele, traj
    _cmd_front, _cmd_bottom = cmd_front, cmd_bottom

    app = Flask(__name__)
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    sio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

    init_socketio(sio)
    register_handlers(sio, _store_qr_from_queue)  # FIX: pass QR store callback

    def _on_telemetry(state: dict):
        traj.update_from_telemetry(state)
        sio.emit("telemetry_update", state)

    tele.on_telemetry_update = _on_telemetry

    def _on_trajectory(state: dict):
        sio.emit("trajectory_update", state)

    traj.on_trajectory_update = _on_trajectory
    mav.on_message_callback   = tele.handle_message

    # ── REST ──────────────────────────────────
    @app.route("/api/status")
    def status():
        return jsonify({
            "service":   "ROV Core API",
            "status":    "running",
            "timestamp": datetime.utcnow().isoformat(),
            "mavlink":   {"connected": _mav.is_connected if _mav else False},
        })

    @app.route("/api/streams")
    def streams():
        return jsonify({
            "front":  {"stream_url": f"http://localhost:{PORT_STREAM_FRONT}/stream",
                       "webrtc_url": f"http://localhost:{PORT_STREAM_FRONT}/offer",
                       "health_url": f"http://localhost:{PORT_STREAM_FRONT}/health"},
            "bottom": {"stream_url": f"http://localhost:{PORT_STREAM_BOTTOM}/stream",
                       "webrtc_url": f"http://localhost:{PORT_STREAM_BOTTOM}/offer",
                       "health_url": f"http://localhost:{PORT_STREAM_BOTTOM}/health"},
        })


    @app.route("/api/telemetry")
    def telemetry_snapshot():
        return jsonify(_tele.get_state() if _tele else {})

    @app.route("/api/trajectory")
    def trajectory_snapshot():
        return jsonify(_traj.get_state() if _traj else {})

    @app.route("/api/trajectory/reset", methods=["POST"])
    def trajectory_reset():
        if _traj: _traj.reset_position()
        return jsonify({"message": "Trajectory reset ke origin"})

    @app.route("/api/qr/history", methods=["GET"])
    def qr_history_get():
        return jsonify({"count": len(_qr_history), "history": _qr_history[-50:]})

    @app.route("/api/qr/history", methods=["DELETE"])
    def qr_history_clear():
        _qr_history.clear()
        return jsonify({"message": "QR history cleared"})

    @app.route("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.route("/api/camera/<cam>/screenshot", methods=["POST"])
    def camera_screenshot(cam: str):
        if cam not in ("front", "bottom"):
            return jsonify({"error": "camera harus 'front' atau 'bottom'"}), 400
        ok, msg = _send_camera_cmd(cam, "screenshot")
        return jsonify({"camera": cam, "action": "screenshot", "queued": ok, "message": msg}), (200 if ok else 503)

    @app.route("/api/camera/<cam>/record/start", methods=["POST"])
    def camera_record_start(cam: str):
        if cam not in ("front", "bottom"):
            return jsonify({"error": "camera harus 'front' atau 'bottom'"}), 400
        ok, msg = _send_camera_cmd(cam, "record_start")
        return jsonify({"camera": cam, "action": "record_start", "queued": ok, "message": msg}), (200 if ok else 503)

    @app.route("/api/camera/<cam>/record/stop", methods=["POST"])
    def camera_record_stop(cam: str):
        if cam not in ("front", "bottom"):
            return jsonify({"error": "camera harus 'front' atau 'bottom'"}), 400
        ok, msg = _send_camera_cmd(cam, "record_stop")
        return jsonify({"camera": cam, "action": "record_stop", "queued": ok, "message": msg}), (200 if ok else 503)

    # ── SocketIO ──────────────────────────────
    @sio.on("connect")
    def on_connect(auth=None):
        logger.info("[Routes] React client terkoneksi")
        # FIX: emit snapshot di background thread agar tidak blocking connect handler
        sid = request.sid
        def _emit_initial():
            if _tele: sio.emit("telemetry_update",  _tele.get_state(), to=sid)
            if _traj: sio.emit("trajectory_update",  _traj.get_state(), to=sid)
            sio.emit("mavlink_status", {"connected": _mav.is_connected if _mav else False}, to=sid)
        threading.Thread(target=_emit_initial, daemon=True).start()

    @sio.on("cmd_arm")
    def on_arm(data=None):
        if _mav: _mav.arm()

    @sio.on("cmd_disarm")
    def on_disarm(data=None):
        if _mav: _mav.disarm()

    @sio.on("cmd_set_mode")
    def on_set_mode(data: dict):
        if _mav: _mav.set_mode(data.get("mode", "MANUAL"))

    @sio.on("cmd_gripper")
    def on_gripper(data: dict):
        if _mav: _mav.gripper(data.get("action", "close"))

    @sio.on("cmd_light")
    def on_light(data: dict):
        if _mav: _mav.light(bool(data.get("state", False)))

    @sio.on("cmd_rc_override")
    def on_rc_override(data: dict):
        channels = {int(k): int(v) for k, v in data.get("channels", {}).items()}
        if _mav: _mav.rc_override(channels)
        if _traj:
            ch1 = channels.get(1, RC_NEUTRAL_PWM)
            ch2 = channels.get(2, RC_NEUTRAL_PWM)
            _traj.update_velocity(
                ((ch1 - RC_NEUTRAL_PWM) / 500.0) * JOYSTICK_SCALE_MS,
                ((ch2 - RC_NEUTRAL_PWM) / 500.0) * JOYSTICK_SCALE_MS,
            )

    @sio.on("disconnect")
    def on_disconnect():
        logger.info("[Routes] React client disconnect")
        if _traj: _traj.update_velocity(0.0, 0.0)

    return app, sio


# FIX: fungsi ini dipanggil dari websocket.py saat QR datang dari kamera,
# bukan dari React — sehingga history terisi dengan benar
def _store_qr_from_queue(data: dict):
    _qr_history.append({**data, "received_at": datetime.utcnow().isoformat()})
    if len(_qr_history) > QR_HISTORY_MAX:
        _qr_history.pop(0)


def run_core_server(
    qr_result_queue:     multiprocessing.Queue,
    dock_event_queue:    multiprocessing.Queue,
    cmd_front_queue:     multiprocessing.Queue,
    cmd_bottom_queue:    multiprocessing.Queue,
    result_camera_queue: multiprocessing.Queue,
):
    logging.basicConfig(level=logging.INFO)
    logger.info("[CoreAPI] Proses dimulai")

    mav  = MAVLinkBridge()
    tele = TelemetryManager()
    traj = TrajectoryEstimator()

    # Connect di background thread agar core API langsung bisa serve request
    # sementara menunggu SITL/Pixhawk siap
    def _connect_bg():
        if not mav.connect():
            logger.warning("[CoreAPI] MAVLink tidak terhubung")
    threading.Thread(target=_connect_bg, daemon=True, name="MAVLinkConnector").start()

    app, sio = create_app(mav, tele, traj, cmd_front_queue, cmd_bottom_queue)
    start_queue_drainer(qr_result_queue, dock_event_queue, result_camera_queue)

    logger.info(f"[CoreAPI] Server berjalan di port {PORT_CORE_API}")
    sio.run(app, host="0.0.0.0", port=PORT_CORE_API, use_reloader=False, log_output=True)