# core/autonomous.py
# ================================================================
# Semi-Autonomous Mission Controller - ROV Trajectory Replay
# ================================================================
#
# Alur misi semi-autonomous:
#   1. Operator drive manual Docking -> Target (trajectory direkam)
#   2. Operator klik "Set Target" -> traj.set_target_snapshot()
#   3. Operator klik "Start Autonomous" -> start_mission()
#   4. [REPLAYING]  ROV replay trajectory rekaman menuju target
#   5. [ALIGNING]   QR Detector aktif, koreksi posisi halus
#   6. [PICKUP]     Open gripper -> maju -> close gripper
#   7. [RETURNING]  ROV replay trajectory terbalik ke docking
#   8. [COMPLETE]   set_mode MANUAL, emit mission_complete
#
# Thread model:
#   - _mission_thread: satu thread background, aktif saat misi
#   - Control loop berjalan di AUTONOMOUS_LOOP_HZ (default 10Hz)
#
# Safety checks setiap loop iteration:
#   - fs.is_emergency_active -> abort jika True
#   - mav.is_connected -> abort jika False
#   - Per-state timeout cegah stuck

import threading
import time
import math
import logging
import multiprocessing
from datetime import datetime
from typing import Optional, Callable

from config import (
    RC_NEUTRAL_PWM,
    JOYSTICK_SCALE_MS,
    AUTONOMOUS_RC_CH_LATERAL,
    AUTONOMOUS_RC_CH_FORWARD,
    AUTONOMOUS_RC_CH_YAW,
    AUTONOMOUS_WAYPOINT_REACH_THRESHOLD_M,
    AUTONOMOUS_WAYPOINT_SKIP_THRESHOLD_M,
    AUTONOMOUS_WAYPOINT_TIMEOUT_S,
    AUTONOMOUS_REPLAY_SPEED_PWM,
    AUTONOMOUS_RETURN_SPEED_PWM,
    AUTONOMOUS_KP_YAW,
    AUTONOMOUS_MAX_YAW_CORRECTION,
    AUTONOMOUS_LOOP_HZ,
    AUTONOMOUS_ALIGN_THRESHOLD_PX,
    AUTONOMOUS_ALIGN_TIMEOUT_S,
    AUTONOMOUS_KP_ALIGN_LATERAL,
    AUTONOMOUS_KP_ALIGN_YAW,
    AUTONOMOUS_MAX_ALIGN_CORRECTION,
    AUTONOMOUS_PICKUP_ADVANCE_S,
    AUTONOMOUS_GRIPPER_WAIT_S,
    AUTONOMOUS_STOP_WAIT_S,
)

logger = logging.getLogger(__name__)

LOOP_INTERVAL = 1.0 / AUTONOMOUS_LOOP_HZ  # detik per iterasi kontrol


# ================================================================
# State
# ================================================================

class MissionState:
    IDLE      = "IDLE"
    REPLAYING = "REPLAYING"
    ALIGNING  = "ALIGNING"
    PICKUP    = "PICKUP"
    RETURNING = "RETURNING"
    COMPLETE  = "COMPLETE"
    ABORTING  = "ABORTING"


# ================================================================
# AutonomousController
# ================================================================

class AutonomousController:
    """
    State machine semi-autonomous untuk misi ROV.
    Satu instance, satu background thread saat aktif.
    """

    def __init__(self, mav, tele, traj, fs,
                 sio_emit: Callable,
                 qr_front_result_queue: Optional[multiprocessing.Queue] = None,
                 cmd_front_queue: Optional[multiprocessing.Queue] = None):
        """
        mav  : MAVLinkBridge
        tele : TelemetryManager
        traj : TrajectoryEstimator
        fs   : FailsafeWatchdog
        sio_emit        : lambda event, data -> None (SocketIO emit)
        qr_front_result_queue : queue hasil QR dari camera_front
        cmd_front_queue       : queue perintah ke camera_front
        """
        self._mav  = mav
        self._tele = tele
        self._traj = traj
        self._fs   = fs
        self._emit = sio_emit
        self._qr_queue    = qr_front_result_queue
        self._cmd_front_q = cmd_front_queue

        self._lock = threading.Lock()
        self._state      = MissionState.IDLE
        self._target_id  = ""
        self._start_time = 0.0
        self._abort_reason = ""
        self._mission_thread: Optional[threading.Thread] = None

        # Cache QR result terbaru dari queue (dibaca oleh _read_qr_result)
        self._latest_qr = None

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────
    def start_mission(self, target_id: str) -> dict:
        with self._lock:
            if self._state != MissionState.IDLE:
                return {"ok": False, "reason": "Misi sedang berjalan"}
            waypoints = self._traj.get_replay_waypoints()
            if not waypoints:
                return {"ok": False, "reason": "Tidak ada waypoint. Klik Set Target terlebih dahulu."}
            self._state     = MissionState.REPLAYING
            self._target_id = target_id
            self._start_time = time.time()
            self._abort_reason = ""
        self._mission_thread = threading.Thread(
            target=self._mission_loop,
            args=(waypoints,),
            daemon=True,
            name="AutonomousMission",
        )
        self._mission_thread.start()
        logger.info(f"[Autonomous] Misi dimulai: target='{target_id}' waypoints={len(waypoints)}")
        self._emit_event("mission_started", f"Misi autonomous dimulai menuju '{target_id}'")
        return {"ok": True, "waypoints": len(waypoints)}

    def stop_mission(self, reason: str = "operator_abort"):
        with self._lock:
            if self._state == MissionState.IDLE:
                return
            self._abort_reason = reason
            self._state = MissionState.ABORTING
        logger.info(f"[Autonomous] Misi dihentikan: {reason}")

    def get_status(self) -> dict:
        with self._lock:
            elapsed = round(time.time() - self._start_time, 1) if self._start_time else 0.0
            return {
                "state":       self._state,
                "target_id":   self._target_id,
                "elapsed_s":   elapsed,
                "abort_reason": self._abort_reason,
                "is_active":   self._state != MissionState.IDLE,
            }

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._state != MissionState.IDLE

    # ──────────────────────────────────────────
    # Mission loop (background thread)
    # ──────────────────────────────────────────
    def _mission_loop(self, waypoints: list):
        """
        Thread utama misi. Jalani semua fase berurutan.
        Setelah selesai atau abort, kembali ke IDLE.
        """
        success = False
        try:
            logger.info(f"[Autonomous] Fase REPLAYING ({len(waypoints)} waypoints)")
            if not self._phase_replay(waypoints):
                self._finalize(success=False)
                return

            self._set_state(MissionState.ALIGNING)
            logger.info("[Autonomous] Fase ALIGNING (QR detection)")
            if not self._phase_align():
                self._finalize(success=False)
                return

            self._set_state(MissionState.PICKUP)
            logger.info("[Autonomous] Fase PICKUP")
            self._phase_pickup()

            self._set_state(MissionState.RETURNING)
            logger.info("[Autonomous] Fase RETURNING")
            reversed_wp = list(reversed(waypoints))
            self._phase_replay(reversed_wp, is_return=True)

            success = True

        except Exception as e:
            logger.error(f"[Autonomous] Error tak terduga dalam mission_loop: {e}", exc_info=True)
            self._abort_reason = f"Exception: {e}"
        finally:
            self._finalize(success=success)

    # ──────────────────────────────────────────
    # Phases
    # ──────────────────────────────────────────
    def _phase_replay(self, waypoints: list, is_return: bool = False) -> bool:
        """
        Replay waypoints. Return True jika semua waypoint tercapai, False jika abort.
        is_return=True -> ROV berbalik arah menuju docking.
        """
        forward_pwm = AUTONOMOUS_RETURN_SPEED_PWM if is_return else AUTONOMOUS_REPLAY_SPEED_PWM
        phase_name  = "RETURNING" if is_return else "REPLAYING"

        for idx, waypoint in enumerate(waypoints):
            if not self._is_safe():
                return False

            wp_start_time = time.time()
            logger.debug(f"[Autonomous] [{phase_name}] Waypoint {idx+1}/{len(waypoints)}: "
                         f"({waypoint['x']:.2f}, {waypoint['y']:.2f})")
            self._emit_status(extra={"waypoint_index": idx + 1,
                                     "waypoint_total": len(waypoints)})

            # Loop sampai waypoint tercapai atau timeout
            while True:
                if not self._is_safe():
                    self._send_rc_neutral()
                    return False

                curr_pos = self._traj.get_current_pos()
                curr_yaw = self._traj.get_current_yaw()

                dx = waypoint["x"] - curr_pos["x"]
                dy = waypoint["y"] - curr_pos["y"]
                dist = math.sqrt(dx * dx + dy * dy)

                # Waypoint tercapai
                if dist < AUTONOMOUS_WAYPOINT_REACH_THRESHOLD_M:
                    logger.debug(f"[Autonomous] [{phase_name}] Waypoint {idx+1} tercapai (dist={dist:.3f}m)")
                    break

                # Timeout per waypoint
                if time.time() - wp_start_time > AUTONOMOUS_WAYPOINT_TIMEOUT_S:
                    logger.warning(f"[Autonomous] [{phase_name}] Waypoint {idx+1} timeout, skip.")
                    break

                # Heading menuju waypoint
                target_heading = math.degrees(math.atan2(dy, dx))
                yaw_error = self._normalize_angle(target_heading - curr_yaw)

                yaw_delta = int(_clamp(yaw_error * AUTONOMOUS_KP_YAW,
                                       -AUTONOMOUS_MAX_YAW_CORRECTION,
                                        AUTONOMOUS_MAX_YAW_CORRECTION))
                channels = {
                    AUTONOMOUS_RC_CH_FORWARD: forward_pwm,
                    AUTONOMOUS_RC_CH_YAW:     RC_NEUTRAL_PWM + yaw_delta,
                    AUTONOMOUS_RC_CH_LATERAL: RC_NEUTRAL_PWM,
                }
                self._send_rc(channels)
                time.sleep(LOOP_INTERVAL)

        self._send_rc_neutral()
        return True

    def _phase_align(self) -> bool:
        """
        Aktifkan QR Detector dan koreksi posisi hingga aligned.
        Return True jika aligned, False jika timeout/abort.
        """
        self._activate_qr_detector()
        # Flush queue lama
        self._flush_qr_queue()
        self._emit_event("qr_searching", "Mencari QR Code untuk alignment...")

        start = time.time()
        while True:
            if not self._is_safe():
                self._deactivate_qr_detector()
                self._send_rc_neutral()
                return False

            if time.time() - start > AUTONOMOUS_ALIGN_TIMEOUT_S:
                logger.warning("[Autonomous] ALIGNING timeout — abort")
                self._emit_event("align_timeout", "Alignment QR timeout")
                self._deactivate_qr_detector()
                self._send_rc_neutral()
                return False

            qr = self._read_qr_result()

            if qr is None:
                # QR belum terdeteksi, kirim RC netral, tunggu
                self._send_rc_neutral()
                time.sleep(LOOP_INTERVAL)
                continue

            if qr.get("aligned", False):
                logger.info(f"[Autonomous] ALIGNED: QR={qr.get('data','')} "
                            f"offset=({qr.get('offset_x',0):.1f}, {qr.get('offset_y',0):.1f})")
                self._emit_event("qr_aligned", "Posisi sejajar dengan target QR")
                self._deactivate_qr_detector()
                self._send_rc_neutral()
                time.sleep(AUTONOMOUS_STOP_WAIT_S)
                return True

            # Koreksi proporsional
            offset_x = qr.get("offset_x", 0.0)
            lat_delta = int(_clamp(offset_x * AUTONOMOUS_KP_ALIGN_LATERAL,
                                   -AUTONOMOUS_MAX_ALIGN_CORRECTION,
                                    AUTONOMOUS_MAX_ALIGN_CORRECTION))
            yaw_delta = int(_clamp(offset_x * AUTONOMOUS_KP_ALIGN_YAW,
                                   -AUTONOMOUS_MAX_ALIGN_CORRECTION,
                                    AUTONOMOUS_MAX_ALIGN_CORRECTION))
            channels = {
                AUTONOMOUS_RC_CH_FORWARD: RC_NEUTRAL_PWM,
                AUTONOMOUS_RC_CH_LATERAL: RC_NEUTRAL_PWM + lat_delta,
                AUTONOMOUS_RC_CH_YAW:     RC_NEUTRAL_PWM + yaw_delta,
            }
            self._send_rc(channels)
            self._emit_status(extra={"qr_offset_x": round(offset_x, 1),
                                     "qr_offset_y": round(qr.get("offset_y", 0), 1)})
            time.sleep(LOOP_INTERVAL)

    def _phase_pickup(self):
        """Open gripper -> maju perlahan -> close gripper."""
        logger.info("[Autonomous] Pickup: open gripper")
        self._emit_event("pickup_start", "Membuka gripper...")
        self._mav.gripper("open")
        time.sleep(AUTONOMOUS_GRIPPER_WAIT_S)

        logger.info("[Autonomous] Pickup: maju perlahan masuki gripper")
        self._emit_event("pickup_advance", "Maju perlahan memasukkan objek ke gripper...")
        end_time = time.time() + AUTONOMOUS_PICKUP_ADVANCE_S
        while time.time() < end_time:
            self._send_rc({
                AUTONOMOUS_RC_CH_FORWARD: AUTONOMOUS_REPLAY_SPEED_PWM,
                AUTONOMOUS_RC_CH_LATERAL: RC_NEUTRAL_PWM,
                AUTONOMOUS_RC_CH_YAW:     RC_NEUTRAL_PWM,
            })
            time.sleep(LOOP_INTERVAL)
        self._send_rc_neutral()
        time.sleep(AUTONOMOUS_STOP_WAIT_S)

        logger.info("[Autonomous] Pickup: close gripper")
        self._emit_event("pickup_close", "Menutup gripper, mengamankan objek...")
        self._mav.gripper("close")
        time.sleep(AUTONOMOUS_GRIPPER_WAIT_S)
        logger.info("[Autonomous] Pickup selesai")
        self._emit_event("pickup_done", "Objek berhasil diambil")

    # ──────────────────────────────────────────
    # Safety & finalize
    # ──────────────────────────────────────────
    def _is_safe(self) -> bool:
        """Return False jika ada kondisi darurat yang memerlukan abort."""
        if not self._mav.is_connected:
            self._abort_reason = "MAVLink terputus"
            return False
        with self._lock:
            if self._state == MissionState.ABORTING:
                return False
        if hasattr(self._fs, "is_emergency_active") and self._fs.is_emergency_active:
            self._abort_reason = "Emergency stop aktif"
            return False
        return True

    def _finalize(self, success: bool):
        """Bersihkan state setelah misi selesai atau abort."""
        self._send_rc_neutral()
        self._deactivate_qr_detector()
        self._mav.set_mode("MANUAL")

        duration = round(time.time() - self._start_time, 1) if self._start_time else 0.0

        with self._lock:
            self._state = MissionState.IDLE

        if success:
            logger.info(f"[Autonomous] Misi SELESAI: target='{self._target_id}' durasi={duration}s")
            self._emit("mission_complete", {
                "success":   True,
                "target_id": self._target_id,
                "duration_s": duration,
                "reason":    "success",
                "timestamp": datetime.utcnow().isoformat(),
            })
        else:
            reason = self._abort_reason or "unknown"
            logger.warning(f"[Autonomous] Misi ABORT: {reason} durasi={duration}s")
            self._emit("mission_complete", {
                "success":   False,
                "target_id": self._target_id,
                "duration_s": duration,
                "reason":    reason,
                "timestamp": datetime.utcnow().isoformat(),
            })

    # ──────────────────────────────────────────
    # RC helpers
    # ──────────────────────────────────────────
    def _send_rc(self, channels: dict):
        """
        Kirim RC override ke MAVLink dan update trajectory velocity
        agar dead reckoning tetap akurat selama autonomous.
        """
        self._mav.rc_override(channels)
        ch1 = channels.get(AUTONOMOUS_RC_CH_LATERAL, RC_NEUTRAL_PWM)
        ch2 = channels.get(AUTONOMOUS_RC_CH_FORWARD, RC_NEUTRAL_PWM)
        vel_x = ((ch1 - RC_NEUTRAL_PWM) / 500.0) * JOYSTICK_SCALE_MS
        vel_y = ((ch2 - RC_NEUTRAL_PWM) / 500.0) * JOYSTICK_SCALE_MS
        self._traj.update_velocity(vel_x, vel_y)

    def _send_rc_neutral(self):
        """Kirim semua channel ke posisi netral (stop semua motor lateral/forward)."""
        channels = {
            AUTONOMOUS_RC_CH_FORWARD: RC_NEUTRAL_PWM,
            AUTONOMOUS_RC_CH_LATERAL: RC_NEUTRAL_PWM,
            AUTONOMOUS_RC_CH_YAW:     RC_NEUTRAL_PWM,
        }
        self._mav.rc_override(channels)
        self._traj.update_velocity(0.0, 0.0)

    # ──────────────────────────────────────────
    # QR detector helpers
    # ──────────────────────────────────────────
    def _activate_qr_detector(self):
        if self._cmd_front_q is not None:
            try:
                self._cmd_front_q.put_nowait({"action": "qr_activate"})
                logger.debug("[Autonomous] Perintah qr_activate dikirim ke CameraFront")
            except Exception:
                pass

    def _deactivate_qr_detector(self):
        if self._cmd_front_q is not None:
            try:
                self._cmd_front_q.put_nowait({"action": "qr_deactivate"})
                logger.debug("[Autonomous] Perintah qr_deactivate dikirim ke CameraFront")
            except Exception:
                pass

    def _flush_qr_queue(self):
        if self._qr_queue is None:
            return
        while True:
            try:
                self._qr_queue.get_nowait()
            except Exception:
                break

    def _read_qr_result(self) -> Optional[dict]:
        if self._qr_queue is None:
            return None
        try:
            return self._qr_queue.get_nowait()
        except Exception:
            return None

    # ──────────────────────────────────────────
    # Emit helpers
    # ──────────────────────────────────────────
    def _set_state(self, new_state: str):
        with self._lock:
            self._state = new_state
        logger.info(f"[Autonomous] State -> {new_state}")
        self._emit_status()

    def _emit_status(self, extra: Optional[dict] = None):
        with self._lock:
            elapsed = round(time.time() - self._start_time, 1)
            payload = {
                "state":     self._state,
                "target_id": self._target_id,
                "elapsed_s": elapsed,
                "is_active": self._state != MissionState.IDLE,
            }
        if extra:
            payload.update(extra)
        self._emit("autonomous_status", payload)

    def _emit_event(self, event_type: str, message: str):
        self._emit("mission_event", {
            "type":      event_type,
            "message":   message,
            "timestamp": datetime.utcnow().isoformat(),
        })

    # ──────────────────────────────────────────
    # Utility
    # ──────────────────────────────────────────
    @staticmethod
    def _normalize_angle(angle: float) -> float:
        while angle >  180: angle -= 360
        while angle < -180: angle += 360
        return angle


def _clamp(val, lo, hi):
    return max(lo, min(hi, val))
