# core/autonomous.py
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
    FRAME_WIDTH,
    FRAME_HEIGHT,
    VISION_SEARCH_YAW_SPEED_PWM,
    VISION_SEARCH_SWEEP_RANGE,
    VISION_SEARCH_SETTLE_TIME_S,
    VISION_SEARCH_TIMEOUT_S,
    VISION_KP_YAW,
    VISION_KP_HEAVE,
    VISION_MAX_YAW_CORRECTION,
    VISION_MAX_HEAVE_CORRECTION,
    VISION_ALIGNMENT_X_DEADZONE_PX,
    VISION_ALIGNMENT_Y_DEADZONE_PX,
    VISION_ALIGNMENT_TOLERANCE_X_PX,
    VISION_ALIGNMENT_TOLERANCE_Y_PX,
    VISION_ALIGNMENT_STABLE_FRAMES,
    VISION_ALIGN_TIMEOUT_S,
    VISION_TARGET_LOST_TIMEOUT_S,
    VISION_APPROACH_SPEED_PWM,
    VISION_APPROACH_TARGET_SIZE_PX,
    VISION_APPROACH_TIMEOUT_S,
    VISION_GRAB_STABILIZE_S,
    VISION_GRAB_OPEN_WAIT_S,
    VISION_GRAB_CLOSE_WAIT_S,
    VISION_VERIFY_BACKOFF_SPEED_PWM,
    VISION_VERIFY_BACKOFF_DURATION_S,
    VISION_VERIFY_TIMEOUT_S,
)

logger = logging.getLogger(__name__)

LOOP_INTERVAL = 1.0 / AUTONOMOUS_LOOP_HZ  # 10Hz

class MissionState:
    IDLE = "IDLE"
    NAV_TO_WAYPOINT = "NAV_TO_WAYPOINT"
    SEARCH = "SEARCH"
    ALIGN = "ALIGN"
    APPROACH = "APPROACH"
    GRAB = "GRAB"
    VERIFY_GRAB = "VERIFY_GRAB"
    RETURN = "RETURN"
    COMPLETE = "COMPLETE"
    FAILSAFE = "FAILSAFE"

class AutonomousController:
    def __init__(self, mav, tele, traj, fs,
                 sio_emit: Callable,
                 qr_front_result_queue: Optional[multiprocessing.Queue] = None,
                 cmd_front_queue: Optional[multiprocessing.Queue] = None,
                 vision_queue: Optional[multiprocessing.Queue] = None):
        self._mav  = mav
        self._tele = tele
        self._traj = traj
        self._fs   = fs
        self._emit = sio_emit
        self._qr_queue    = qr_front_result_queue
        self._cmd_front_q = cmd_front_queue
        self._vision_queue = vision_queue

        self._lock = threading.Lock()
        self._state      = MissionState.IDLE
        self._target_id  = ""
        self._start_time = 0.0
        self._abort_reason = ""
        self._mission_thread: Optional[threading.Thread] = None

        self._latest_vision = None

    def start_mission(self, target_id: str) -> dict:
        with self._lock:
            if self._state != MissionState.IDLE:
                return {"ok": False, "reason": "Misi sedang berjalan"}
            waypoints = self._traj.get_replay_waypoints()
            self._state     = MissionState.NAV_TO_WAYPOINT
            self._target_id = target_id
            self._start_time = time.time()
            self._abort_reason = ""
            self._flush_vision_queue()
        
        self._mission_thread = threading.Thread(
            target=self._mission_loop,
            args=(waypoints,),
            daemon=True,
            name="AutonomousMission",
        )
        self._mission_thread.start()
        logger.info(f"[Autonomous] Misi dimulai: target='{target_id}'")
        self._emit_event("mission_started", f"Misi autonomous vision dimulai")
        return {"ok": True, "waypoints": len(waypoints) if waypoints else 0}

    def stop_mission(self, reason: str = "operator_abort"):
        with self._lock:
            if self._state == MissionState.IDLE:
                return
            self._abort_reason = reason
            self._state = MissionState.FAILSAFE
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

    # --- Vision Helpers ---
    def _activate_vision(self):
        if self._cmd_front_q:
            try:
                self._cmd_front_q.put_nowait({"action": "vision_activate"})
                logger.debug("Vision activated")
            except:
                pass
                
    def _deactivate_vision(self):
        if self._cmd_front_q:
            try:
                self._cmd_front_q.put_nowait({"action": "vision_deactivate"})
                logger.debug("Vision deactivated")
            except:
                pass
                
    def _flush_vision_queue(self):
        if not self._vision_queue: return
        while not self._vision_queue.empty():
            try:
                self._vision_queue.get_nowait()
            except:
                pass
                
    def _update_vision(self):
        if not self._vision_queue: return
        try:
            while not self._vision_queue.empty():
                self._latest_vision = self._vision_queue.get_nowait()
        except:
            pass
            
    def _get_payload_detection(self):
        if not self._latest_vision: return None
        # Age check
        if time.time() - self._latest_vision.get("timestamp", 0) > 1.0:
            return None # too old
        
        best = None
        for d in self._latest_vision.get("detections", []):
            if d["class_name"] == "payload":
                if best is None or d["confidence"] > best["confidence"]:
                    best = d
        return best
    
    # --- FSM ---
    def _mission_loop(self, waypoints: list):
        success = False
        try:
            if not self._phase_nav_to_waypoint(waypoints):
                self._finalize(False)
                return
            
            self._set_state(MissionState.SEARCH)
            if not self._phase_search():
                self._finalize(False)
                return
            
            self._set_state(MissionState.ALIGN)
            if not self._phase_align():
                self._finalize(False)
                return
            
            self._set_state(MissionState.APPROACH)
            if not self._phase_approach():
                self._finalize(False)
                return
            
            self._set_state(MissionState.GRAB)
            self._phase_grab()
            
            self._set_state(MissionState.VERIFY_GRAB)
            if not self._phase_verify_grab():
                self._finalize(False)
                return
            
            self._set_state(MissionState.RETURN)
            self._phase_return()
            
            success = True
        except Exception as e:
            logger.error(f"[Autonomous] Exception: {e}", exc_info=True)
            self._abort_reason = str(e)
            self._set_state(MissionState.FAILSAFE)
        finally:
            self._finalize(success)

    def _phase_nav_to_waypoint(self, waypoints) -> bool:
        self._emit_event("nav", "Navigasi ke area pencarian")
        if not waypoints: 
            return True # Langsung search
            
        target_wp = waypoints[-1]
        wp_start_time = time.time()
        
        while True:
            if not self._is_safe(): return False
            curr_pos = self._traj.get_current_pos()
            curr_yaw = self._traj.get_current_yaw()
            
            dx = target_wp["x"] - curr_pos["x"]
            dy = target_wp["y"] - curr_pos["y"]
            dist_2d = math.sqrt(dx*dx + dy*dy)
            
            if dist_2d < AUTONOMOUS_WAYPOINT_REACH_THRESHOLD_M:
                break
            if time.time() - wp_start_time > AUTONOMOUS_WAYPOINT_TIMEOUT_S * len(waypoints):
                break
                
            target_heading = math.degrees(math.atan2(dy, dx))
            yaw_error = self._normalize_angle(target_heading - curr_yaw)
            yaw_delta = int(_clamp(yaw_error * AUTONOMOUS_KP_YAW, -AUTONOMOUS_MAX_YAW_CORRECTION, AUTONOMOUS_MAX_YAW_CORRECTION))
            
            self._send_rc({
                AUTONOMOUS_RC_CH_FORWARD: AUTONOMOUS_REPLAY_SPEED_PWM,
                AUTONOMOUS_RC_CH_YAW: RC_NEUTRAL_PWM + yaw_delta
            })
            time.sleep(LOOP_INTERVAL)
            
        self._send_rc_neutral()
        return True

    def _phase_search(self) -> bool:
        self._activate_vision()
        self._emit_event("search", "Mencari payload via AI Vision...")
        start_time = time.time()
        
        sweep_dir = 1
        
        while time.time() - start_time < VISION_SEARCH_TIMEOUT_S:
            if not self._is_safe(): return False
            self._update_vision()
            target = self._get_payload_detection()
            if target:
                self._send_rc_neutral()
                self._emit_event("search_success", "Payload ditemukan!")
                return True
                
            yaw_pwm = RC_NEUTRAL_PWM + (VISION_SEARCH_YAW_SPEED_PWM * sweep_dir)
            self._send_rc({AUTONOMOUS_RC_CH_YAW: yaw_pwm})
            
            # Switch direction roughly every 3 seconds for basic sweep
            if int(time.time() - start_time) % 6 > 3:
                sweep_dir = -1
            else:
                sweep_dir = 1
                
            time.sleep(LOOP_INTERVAL)
            
        self._abort_reason = "Search timeout"
        return False
        
    def _phase_align(self) -> bool:
        self._emit_event("align", "Menyelaraskan posisi visual")
        start_time = time.time()
        stable_count = 0
        
        while time.time() - start_time < VISION_ALIGN_TIMEOUT_S:
            if not self._is_safe(): return False
            self._update_vision()
            target = self._get_payload_detection()
            
            if not target:
                self._send_rc_neutral()
                stable_count = 0
                time.sleep(LOOP_INTERVAL)
                continue
                
            error_x = target["center"]["x"] - (FRAME_WIDTH / 2)
            error_y = target["center"]["y"] - (FRAME_HEIGHT / 2)
            
            yaw_cmd = 0
            heave_cmd = 0
            
            if abs(error_x) > VISION_ALIGNMENT_X_DEADZONE_PX:
                yaw_cmd = int(error_x * VISION_KP_YAW)
                yaw_cmd = _clamp(yaw_cmd, -VISION_MAX_YAW_CORRECTION, VISION_MAX_YAW_CORRECTION)
                
            if abs(error_y) > VISION_ALIGNMENT_Y_DEADZONE_PX:
                # Assuming downward visual error (positive y) means payload is below center -> dive -> decrease throttle
                heave_cmd = int(-error_y * VISION_KP_HEAVE) 
                heave_cmd = _clamp(heave_cmd, -VISION_MAX_HEAVE_CORRECTION, VISION_MAX_HEAVE_CORRECTION)
                
            self._send_rc({
                AUTONOMOUS_RC_CH_YAW: RC_NEUTRAL_PWM + yaw_cmd,
                AUTONOMOUS_RC_CH_THROTTLE: RC_NEUTRAL_PWM + heave_cmd
            })
            
            if abs(error_x) <= VISION_ALIGNMENT_TOLERANCE_X_PX and abs(error_y) <= VISION_ALIGNMENT_TOLERANCE_Y_PX:
                stable_count += 1
            else:
                stable_count = 0
                
            if stable_count >= VISION_ALIGNMENT_STABLE_FRAMES:
                self._send_rc_neutral()
                self._emit_event("align_success", "Posisi terselaraskan")
                return True
                
            time.sleep(LOOP_INTERVAL)
            
        self._abort_reason = "Align timeout"
        return False
        
    def _phase_approach(self) -> bool:
        self._emit_event("approach", "Mendekati target")
        start_time = time.time()
        target_lost_time = None
        
        while time.time() - start_time < VISION_APPROACH_TIMEOUT_S:
            if not self._is_safe(): return False
            self._update_vision()
            target = self._get_payload_detection()
            
            if not target:
                if target_lost_time is None:
                    target_lost_time = time.time()
                elif time.time() - target_lost_time > VISION_TARGET_LOST_TIMEOUT_S:
                    self._abort_reason = "Target lost during approach"
                    return False
                self._send_rc_neutral()
                time.sleep(LOOP_INTERVAL)
                continue
            
            target_lost_time = None
            
            bbox_width = target["bbox"]["x2"] - target["bbox"]["x1"]
            if bbox_width >= VISION_APPROACH_TARGET_SIZE_PX:
                self._send_rc_neutral()
                self._emit_event("approach_success", "Target tercapai, ukuran bbox terpenuhi")
                return True
                
            error_x = target["center"]["x"] - (FRAME_WIDTH / 2)
            error_y = target["center"]["y"] - (FRAME_HEIGHT / 2)
            yaw_cmd = int(_clamp(error_x * VISION_KP_YAW, -VISION_MAX_YAW_CORRECTION, VISION_MAX_YAW_CORRECTION))
            heave_cmd = int(_clamp(-error_y * VISION_KP_HEAVE, -VISION_MAX_HEAVE_CORRECTION, VISION_MAX_HEAVE_CORRECTION))
            
            self._send_rc({
                AUTONOMOUS_RC_CH_FORWARD: RC_NEUTRAL_PWM + VISION_APPROACH_SPEED_PWM,
                AUTONOMOUS_RC_CH_YAW: RC_NEUTRAL_PWM + yaw_cmd,
                AUTONOMOUS_RC_CH_THROTTLE: RC_NEUTRAL_PWM + heave_cmd
            })
            time.sleep(LOOP_INTERVAL)
            
        self._abort_reason = "Approach timeout"
        return False

    def _phase_grab(self):
        self._emit_event("grab", "Menstabilkan dan Grab payload")
        self._send_rc_neutral()
        time.sleep(VISION_GRAB_STABILIZE_S)
        
        self._mav.gripper("open")
        time.sleep(VISION_GRAB_OPEN_WAIT_S)
        
        self._send_rc({AUTONOMOUS_RC_CH_FORWARD: RC_NEUTRAL_PWM + int(VISION_APPROACH_SPEED_PWM*0.5)})
        time.sleep(0.5)
        self._send_rc_neutral()
        
        self._mav.gripper("close")
        time.sleep(VISION_GRAB_CLOSE_WAIT_S)

    def _phase_verify_grab(self) -> bool:
        self._emit_event("verify", "Memverifikasi pengambilan payload")
        self._send_rc({AUTONOMOUS_RC_CH_FORWARD: RC_NEUTRAL_PWM + VISION_VERIFY_BACKOFF_SPEED_PWM})
        time.sleep(VISION_VERIFY_BACKOFF_DURATION_S)
        self._send_rc_neutral()
        return True
        
    def _phase_return(self):
        self._emit_event("return", "Kembali ke permukaan")
        self._send_rc({AUTONOMOUS_RC_CH_THROTTLE: RC_NEUTRAL_PWM + 200})
        time.sleep(3.0)
        self._send_rc_neutral()

    def _is_safe(self) -> bool:
        if not self._mav.is_connected:
            self._abort_reason = "MAVLink terputus"
            return False
        with self._lock:
            if self._state == MissionState.ABORTING or self._state == MissionState.FAILSAFE:
                return False
        if hasattr(self._fs, "is_emergency_active") and self._fs.is_emergency_active:
            self._abort_reason = "Emergency stop aktif"
            return False
        return True

    def _finalize(self, success: bool):
        self._send_rc_neutral()
        self._deactivate_vision()
        self._mav.set_mode("MANUAL")
        duration = round(time.time() - self._start_time, 1) if self._start_time else 0.0
        with self._lock:
            self._state = MissionState.IDLE
        if success:
            logger.info(f"[Autonomous] Misi SELESAI")
            self._emit("mission_complete", {"success": True, "reason": "success", "duration_s": duration})
        else:
            logger.warning(f"[Autonomous] Misi ABORT: {self._abort_reason}")
            self._emit("mission_complete", {"success": False, "reason": self._abort_reason, "duration_s": duration})

    def _send_rc(self, channels: dict):
        ch_lat = channels.get(AUTONOMOUS_RC_CH_LATERAL, RC_NEUTRAL_PWM)
        ch_fwd = channels.get(AUTONOMOUS_RC_CH_FORWARD, RC_NEUTRAL_PWM)
        ch_thr = channels.get(AUTONOMOUS_RC_CH_THROTTLE, RC_NEUTRAL_PWM)
        ch_yaw = channels.get(AUTONOMOUS_RC_CH_YAW, RC_NEUTRAL_PWM)

        y = int((ch_lat - RC_NEUTRAL_PWM) * 2.5)
        x = int((ch_fwd - RC_NEUTRAL_PWM) * 2.5)
        z = int(500 + (ch_thr - RC_NEUTRAL_PWM) * 1.25)
        r = int((ch_yaw - RC_NEUTRAL_PWM) * 2.5)

        x = max(-1000, min(1000, x))
        y = max(-1000, min(1000, y))
        z = max(0, min(1000, z))
        r = max(-1000, min(1000, r))

        self._mav.manual_control(x, y, z, r)

        vel_x = ((ch_lat - RC_NEUTRAL_PWM) / 500.0) * JOYSTICK_SCALE_MS
        vel_y = ((ch_fwd - RC_NEUTRAL_PWM) / 500.0) * JOYSTICK_SCALE_MS
        self._traj.update_velocity(vel_x, vel_y)

    def _send_rc_neutral(self):
        self._send_rc({})

    def _set_state(self, new_state: str):
        with self._lock:
            self._state = new_state
        logger.info(f"[Autonomous] State -> {new_state}")
        self._emit_status()

    def _emit_status(self, extra: Optional[dict] = None):
        with self._lock:
            payload = {
                "state":     self._state,
                "target_id": self._target_id,
                "elapsed_s": round(time.time() - self._start_time, 1),
                "is_active": self._state != MissionState.IDLE,
            }
        if extra: payload.update(extra)
        self._emit("autonomous_status", payload)

    def _emit_event(self, event_type: str, message: str):
        self._emit("mission_event", {
            "type":      event_type,
            "message":   message,
            "timestamp": datetime.utcnow().isoformat(),
        })

    @staticmethod
    def _normalize_angle(angle: float) -> float:
        while angle >  180: angle -= 360
        while angle < -180: angle += 360
        return angle

def _clamp(val, lo, hi):
    return max(lo, min(hi, val))
