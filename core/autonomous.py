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
    AUTONOMOUS_RC_CH_THROTTLE,
    AUTONOMOUS_RC_CH_YAW,
    AUTONOMOUS_WAYPOINT_REACH_THRESHOLD_M,
    AUTONOMOUS_WAYPOINT_SKIP_THRESHOLD_M,
    AUTONOMOUS_WAYPOINT_TIMEOUT_S,
    AUTONOMOUS_REPLAY_SPEED_PWM,
    AUTONOMOUS_RETURN_SPEED_PWM,
    AUTONOMOUS_KP_YAW,
    AUTONOMOUS_MAX_YAW_CORRECTION,
    AUTONOMOUS_KP_DEPTH,
    AUTONOMOUS_KP_LATERAL,
    AUTONOMOUS_KP_XTE,
    AUTONOMOUS_MAX_DEPTH_CORRECTION,
    AUTONOMOUS_MAX_LATERAL_CORRECTION,
    AUTONOMOUS_XTE_THRESHOLD_M,
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
            last_depth = waypoints[-1]["depth"] if waypoints else None
            if not self._phase_align(target_depth=last_depth):
                self._finalize(success=False)
                return

            self._set_state(MissionState.PICKUP)
            logger.info("[Autonomous] Fase PICKUP")
            self._phase_pickup(target_depth=last_depth)

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
        Replay waypoints dengan kontrol 4 sumbu (Forward, Lateral, Yaw, Depth)
        serta koreksi Cross-Track Error (XTE) secara proporsional.
        Return True jika semua waypoint tercapai, False jika abort.
        """
        forward_pwm = AUTONOMOUS_RETURN_SPEED_PWM if is_return else AUTONOMOUS_REPLAY_SPEED_PWM
        phase_name  = "RETURNING" if is_return else "REPLAYING"

        # Rekam posisi awal sebagai referensi W_prev untuk waypoint pertama
        start_pos = self._traj.get_current_pos()
        start_yaw = self._traj.get_current_yaw()
        start_wp = {
            "x": start_pos["x"],
            "y": start_pos["y"],
            "depth": start_pos["depth"],
            "yaw": start_yaw
        }

        for idx, waypoint in enumerate(waypoints):
            if not self._is_safe():
                return False

            wp_start_time = time.time()
            prev_waypoint = start_wp if idx == 0 else waypoints[idx - 1]
            
            logger.debug(
                f"[Autonomous] [{phase_name}] Waypoint {idx+1}/{len(waypoints)}: "
                f"({waypoint['x']:.2f}, {waypoint['y']:.2f}, depth={waypoint.get('depth', 0.0):.2f})"
            )
            self._emit_status(extra={"waypoint_index": idx + 1,
                                     "waypoint_total": len(waypoints)})

            # Loop sampai waypoint tercapai atau timeout
            while True:
                if not self._is_safe():
                    self._send_rc_neutral()
                    return False

                curr_pos = self._traj.get_current_pos()
                curr_yaw = self._traj.get_current_yaw()

                # Perhitungan error posisi horizontal
                dx = waypoint["x"] - curr_pos["x"]
                dy = waypoint["y"] - curr_pos["y"]
                dist_2d = math.sqrt(dx * dx + dy * dy)

                # Perhitungan error posisi vertikal (kedalaman)
                target_depth = waypoint.get("depth", curr_pos["depth"])
                dz = target_depth - curr_pos["depth"]

                # Cek pencapaian waypoint menggunakan jarak 3D
                dist_3d = math.sqrt(dx * dx + dy * dy + dz * dz)
                if dist_3d < AUTONOMOUS_WAYPOINT_REACH_THRESHOLD_M:
                    logger.debug(f"[Autonomous] [{phase_name}] Waypoint {idx+1} tercapai (dist_3d={dist_3d:.3f}m)")
                    break

                # Timeout per waypoint
                if time.time() - wp_start_time > AUTONOMOUS_WAYPOINT_TIMEOUT_S:
                    logger.warning(f"[Autonomous] [{phase_name}] Waypoint {idx+1} timeout, skip.")
                    break

                # 1. Kontrol Vertikal (Depth Controller)
                # target_depth > curr_depth -> perlu menyelam -> kurangi PWM (PWM < 1500)
                depth_delta = int(dz * AUTONOMOUS_KP_DEPTH)
                depth_delta = _clamp(depth_delta, -AUTONOMOUS_MAX_DEPTH_CORRECTION, AUTONOMOUS_MAX_DEPTH_CORRECTION)
                throttle_pwm = RC_NEUTRAL_PWM - depth_delta

                # 2. Kontrol Rotasi (Yaw Controller)
                target_heading = math.degrees(math.atan2(dy, dx))
                yaw_error = self._normalize_angle(target_heading - curr_yaw)
                yaw_delta = int(_clamp(yaw_error * AUTONOMOUS_KP_YAW,
                                       -AUTONOMOUS_MAX_YAW_CORRECTION,
                                        AUTONOMOUS_MAX_YAW_CORRECTION))

                # 3. Kontrol Lateral (Cross-Track Error / XTE)
                lat_delta = 0
                ux = waypoint["x"] - prev_waypoint["x"]
                uy = waypoint["y"] - prev_waypoint["y"]
                u_mag_sq = ux * ux + uy * uy

                if u_mag_sq > 1e-6:
                    vx = curr_pos["x"] - prev_waypoint["x"]
                    vy = curr_pos["y"] - prev_waypoint["y"]
                    # Proyeksi t pada garis segmen
                    t = (vx * ux + vy * uy) / u_mag_sq
                    t = _clamp(t, 0.0, 1.0)
                    
                    proj_x = prev_waypoint["x"] + t * ux
                    proj_y = prev_waypoint["y"] + t * uy
                    
                    # Vektor error dari path proyeksi ke posisi ROV
                    err_x = curr_pos["x"] - proj_x
                    err_y = curr_pos["y"] - proj_y
                    
                    # Rotasikan vektor error global ke body frame ROV untuk dapat lateral error
                    yaw_rad = math.radians(curr_yaw)
                    lat_error = -err_x * math.sin(yaw_rad) + err_y * math.cos(yaw_rad)
                    xte_m = abs(lat_error)

                    if xte_m > AUTONOMOUS_XTE_THRESHOLD_M:
                        # lat_error > 0 -> ROV berada di kanan jalur -> gerak ke kiri (PWM < 1500)
                        lat_delta = int(-lat_error * AUTONOMOUS_KP_XTE)
                        lat_delta = _clamp(lat_delta, -AUTONOMOUS_MAX_LATERAL_CORRECTION, AUTONOMOUS_MAX_LATERAL_CORRECTION)

                # Kirim sinyal kontrol 4 sumbu
                channels = {
                    AUTONOMOUS_RC_CH_FORWARD:  forward_pwm,
                    AUTONOMOUS_RC_CH_YAW:      RC_NEUTRAL_PWM + yaw_delta,
                    AUTONOMOUS_RC_CH_LATERAL:  RC_NEUTRAL_PWM + lat_delta,
                    AUTONOMOUS_RC_CH_THROTTLE: throttle_pwm,
                }
                self._send_rc(channels)
                time.sleep(LOOP_INTERVAL)

        self._send_rc_neutral()
        return True

    def _phase_align(self, target_depth: Optional[float] = None) -> bool:
        """
        Aktifkan QR Detector dan koreksi posisi hingga aligned dengan target.
        Mempertahankan kedalaman jika target_depth diberikan.
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

            # Kontrol kedalaman konstan selama alignment
            throttle_pwm = RC_NEUTRAL_PWM
            if target_depth is not None:
                curr_pos = self._traj.get_current_pos()
                dz = target_depth - curr_pos["depth"]
                depth_delta = int(dz * AUTONOMOUS_KP_DEPTH)
                depth_delta = _clamp(depth_delta, -AUTONOMOUS_MAX_DEPTH_CORRECTION, AUTONOMOUS_MAX_DEPTH_CORRECTION)
                throttle_pwm = RC_NEUTRAL_PWM - depth_delta

            if qr is None:
                # QR belum terdeteksi, kirim RC netral (keep depth), tunggu
                self._send_rc({
                    AUTONOMOUS_RC_CH_FORWARD:  RC_NEUTRAL_PWM,
                    AUTONOMOUS_RC_CH_LATERAL:  RC_NEUTRAL_PWM,
                    AUTONOMOUS_RC_CH_YAW:      RC_NEUTRAL_PWM,
                    AUTONOMOUS_RC_CH_THROTTLE: throttle_pwm,
                })
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

            # Koreksi proporsional berdasarkan offset piksel QR
            offset_x = qr.get("offset_x", 0.0)
            lat_delta = int(_clamp(offset_x * AUTONOMOUS_KP_ALIGN_LATERAL,
                                   -AUTONOMOUS_MAX_ALIGN_CORRECTION,
                                    AUTONOMOUS_MAX_ALIGN_CORRECTION))
            yaw_delta = int(_clamp(offset_x * AUTONOMOUS_KP_ALIGN_YAW,
                                   -AUTONOMOUS_MAX_ALIGN_CORRECTION,
                                    AUTONOMOUS_MAX_ALIGN_CORRECTION))
            channels = {
                AUTONOMOUS_RC_CH_FORWARD:  RC_NEUTRAL_PWM,
                AUTONOMOUS_RC_CH_LATERAL:  RC_NEUTRAL_PWM + lat_delta,
                AUTONOMOUS_RC_CH_YAW:      RC_NEUTRAL_PWM + yaw_delta,
                AUTONOMOUS_RC_CH_THROTTLE: throttle_pwm,
            }
            self._send_rc(channels)
            self._emit_status(extra={"qr_offset_x": round(offset_x, 1),
                                     "qr_offset_y": round(qr.get("offset_y", 0), 1)})
            time.sleep(LOOP_INTERVAL)

    def _phase_pickup(self, target_depth: Optional[float] = None):
        """Open gripper -> maju perlahan (depth-hold) -> close gripper."""
        logger.info("[Autonomous] Pickup: open gripper")
        self._emit_event("pickup_start", "Membuka gripper...")
        self._mav.gripper("open")
        time.sleep(AUTONOMOUS_GRIPPER_WAIT_S)

        logger.info("[Autonomous] Pickup: maju perlahan masuki gripper")
        self._emit_event("pickup_advance", "Maju perlahan memasukkan objek ke gripper...")
        end_time = time.time() + AUTONOMOUS_PICKUP_ADVANCE_S
        while time.time() < end_time:
            # Kontrol kedalaman konstan selama maju perlahan
            throttle_pwm = RC_NEUTRAL_PWM
            if target_depth is not None:
                curr_pos = self._traj.get_current_pos()
                dz = target_depth - curr_pos["depth"]
                depth_delta = int(dz * AUTONOMOUS_KP_DEPTH)
                depth_delta = _clamp(depth_delta, -AUTONOMOUS_MAX_DEPTH_CORRECTION, AUTONOMOUS_MAX_DEPTH_CORRECTION)
                throttle_pwm = RC_NEUTRAL_PWM - depth_delta

            self._send_rc({
                AUTONOMOUS_RC_CH_FORWARD:  AUTONOMOUS_REPLAY_SPEED_PWM,
                AUTONOMOUS_RC_CH_LATERAL:  RC_NEUTRAL_PWM,
                AUTONOMOUS_RC_CH_YAW:      RC_NEUTRAL_PWM,
                AUTONOMOUS_RC_CH_THROTTLE: throttle_pwm,
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
        Kirim manual control ke MAVLink dan update trajectory velocity
        agar dead reckoning tetap akurat selama autonomous.
        """
        ch_lat = channels.get(AUTONOMOUS_RC_CH_LATERAL, RC_NEUTRAL_PWM)
        ch_fwd = channels.get(AUTONOMOUS_RC_CH_FORWARD, RC_NEUTRAL_PWM)
        ch_thr = channels.get(AUTONOMOUS_RC_CH_THROTTLE, RC_NEUTRAL_PWM)
        ch_yaw = channels.get(AUTONOMOUS_RC_CH_YAW, RC_NEUTRAL_PWM)

        # Convert to MANUAL_CONTROL ranges (-1000 to 1000, throttle 0 to 1000)
        y = int((ch_lat - RC_NEUTRAL_PWM) * 2.5)
        x = int((ch_fwd - RC_NEUTRAL_PWM) * 2.5)
        z = int(500 + (ch_thr - RC_NEUTRAL_PWM) * 1.25)
        r = int((ch_yaw - RC_NEUTRAL_PWM) * 2.5)

        # Clamp values
        x = max(-1000, min(1000, x))
        y = max(-1000, min(1000, y))
        z = max(0, min(1000, z))
        r = max(-1000, min(1000, r))

        self._mav.manual_control(x, y, z, r)

        vel_x = ((ch_lat - RC_NEUTRAL_PWM) / 500.0) * JOYSTICK_SCALE_MS
        vel_y = ((ch_fwd - RC_NEUTRAL_PWM) / 500.0) * JOYSTICK_SCALE_MS
        self._traj.update_velocity(vel_x, vel_y)

    def _send_rc_neutral(self):
        """Kirim semua channel ke posisi netral (stop semua motor)."""
        channels = {
            AUTONOMOUS_RC_CH_FORWARD:  RC_NEUTRAL_PWM,
            AUTONOMOUS_RC_CH_LATERAL:  RC_NEUTRAL_PWM,
            AUTONOMOUS_RC_CH_YAW:      RC_NEUTRAL_PWM,
            AUTONOMOUS_RC_CH_THROTTLE: RC_NEUTRAL_PWM,
        }
        self._send_rc(channels)

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
