# test/test_system_components.py
# Comprehensive standalone test script for all ROV vision and control modules.

import os
import sys
import time
import logging
import multiprocessing
import threading
from unittest.mock import MagicMock

# Ensure project root is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import dependencies
import cv2
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
logger = logging.getLogger("SystemTest")


# ──────────────────────────────────────────────────────────
# 1. Environment & OpenCV WeChat QR Check
# ──────────────────────────────────────────────────────────
def test_environment():
    logger.info("=== 1. TEST ENVIRONMENT ===")
    logger.info(f"Python Version: {sys.version}")
    logger.info(f"OpenCV Version: {cv2.__version__}")

    # Check dependencies
    dependencies = [
        ("pymavlink", "MAVLink Communication"),
        ("pyzbar", "Classic QR Scanner"),
        ("psutil", "System Failsafe Monitoring"),
        ("aiortc", "WebRTC Video Stream"),
        ("flask", "Flask Server"),
        ("flask_socketio", "WebSocket Engine"),
    ]

    for module_name, desc in dependencies:
        try:
            __import__(module_name)
            logger.info(f" - [OK] {module_name} ({desc})")
        except ImportError:
            logger.warning(f" - [WARNING] {module_name} ({desc}) is NOT installed!")

    # Check WeChat QR
    try:
        from core.wechat_model_downloader import ensure_wechat_models
        from config import (
            WECHAT_QR_DETECT_PROTOTXT,
            WECHAT_QR_DETECT_CAFFEMODEL,
            WECHAT_QR_SR_PROTOTXT,
            WECHAT_QR_SR_CAFFEMODEL,
        )

        logger.info("Checking WeChat QR models...")
        if ensure_wechat_models():
            logger.info(" - WeChat models ready on disk.")
        else:
            logger.error(" - Failed to download WeChat models.")

        if hasattr(cv2, "wechat_qrcode_WeChatQRCode"):
            try:
                # Test with 4 args
                _ = cv2.wechat_qrcode_WeChatQRCode(
                    WECHAT_QR_DETECT_PROTOTXT,
                    WECHAT_QR_DETECT_CAFFEMODEL,
                    WECHAT_QR_SR_PROTOTXT,
                    WECHAT_QR_SR_CAFFEMODEL
                )
                logger.info(" - [OK] WeChatQRCode (with Super Resolution) initialized successfully.")
            except TypeError:
                try:
                    # Test with 2 args fallback
                    _ = cv2.wechat_qrcode_WeChatQRCode(
                        WECHAT_QR_DETECT_PROTOTXT,
                        WECHAT_QR_DETECT_CAFFEMODEL
                    )
                    logger.info(" - [OK] WeChatQRCode (without Super Resolution) initialized successfully (fallback).")
                except Exception as e:
                    logger.error(f" - [FAIL] WeChatQRCode 2-arg constructor failed: {e}")
            except Exception as e:
                logger.error(f" - [FAIL] WeChatQRCode constructor failed: {e}")
        else:
            logger.warning(" - [WARNING] WeChatQRCode module is not present in this cv2 build.")
    except Exception as e:
        logger.error(f" - Error checking WeChat QR: {e}")


# ──────────────────────────────────────────────────────────
# 2. Image Processors (Front & Bottom)
# ──────────────────────────────────────────────────────────
def test_image_processors():
    logger.info("=== 2. TEST IMAGE PROCESSORS ===")
    
    # Front Image Processor
    try:
        from camera_front.image_processing import FrontImageProcessor
        proc_front = FrontImageProcessor(show_hud=True)
        dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        
        # Test color correction & CLAHE
        processed_front = proc_front.process(dummy_frame.copy())
        logger.info(f" - [OK] FrontImageProcessor processed frame successfully. Shape: {processed_front.shape}")
    except Exception as e:
        logger.error(f" - [FAIL] FrontImageProcessor error: {e}")

    # Bottom Image Processor
    try:
        from camera_bottom.image_processing import BottomImageProcessor
        proc_bottom = BottomImageProcessor(show_hud=True)
        dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        
        # Inject mock states
        proc_bottom.update_qr_data("MOCK_DOCK_A")
        proc_bottom.update_dock_status(True)
        proc_bottom.update_bbox(np.array([[[100, 100], [200, 100], [200, 200], [100, 200]]], dtype=np.int32))

        processed_bottom = proc_bottom.process(dummy_frame.copy())
        preprocessed = proc_bottom.preprocess_for_qr(dummy_frame)
        
        logger.info(f" - [OK] BottomImageProcessor processed frame successfully. Shape: {processed_bottom.shape}")
        logger.info(f" - [OK] preprocess_for_qr returned frame. Shape: {preprocessed.shape}")
    except Exception as e:
        logger.error(f" - [FAIL] BottomImageProcessor error: {e}")


# ──────────────────────────────────────────────────────────
# 3. QR Detectors Logic
# ──────────────────────────────────────────────────────────
def test_qr_detectors():
    logger.info("=== 3. TEST QR DETECTORS ===")
    manager = multiprocessing.Manager()
    
    # Front QR Detector
    try:
        from camera_front.qr_detector import QRDetector as FrontQRDetector
        q_front_res = manager.Queue()
        det_front = FrontQRDetector(result_queue=q_front_res)
        det_front.activate()
        
        dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        processed_frame = det_front.process_frame(dummy_frame)
        
        logger.info(f" - [OK] Front QRDetector processed frame. Active: {det_front.is_active}")
        det_front.deactivate()
    except Exception as e:
        logger.error(f" - [FAIL] Front QRDetector error: {e}")

    # Bottom QR Detector
    try:
        from camera_bottom.qr_detector import QRDetector as BottomQRDetector
        q_bottom_res = manager.Queue()
        q_bottom_dock = manager.Queue()
        det_bottom = BottomQRDetector(qr_result_queue=q_bottom_res, dock_event_queue=q_bottom_dock)
        
        dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        dummy_gray = np.zeros((480, 640), dtype=np.uint8)
        
        qr_data, is_dock_aligned, bbox = det_bottom.scan(dummy_frame, dummy_gray)
        logger.info(f" - [OK] Bottom QRDetector scan executed. Result: {qr_data}, Aligned: {is_dock_aligned}")
    except Exception as e:
        logger.error(f" - [FAIL] Bottom QRDetector error: {e}")


# ──────────────────────────────────────────────────────────
# 4. MAVLink Interface
# ──────────────────────────────────────────────────────────
def test_mavlink():
    logger.info("=== 4. TEST MAVLINK INTERFACE ===")
    try:
        from core.mavlink import MAVLinkBridge
        logger.info("Importing MAVLink module and trying to instantiate...")
        
        # MAVLinkBridge takes no arguments in its constructor
        mav = MAVLinkBridge()
        logger.info(" - [OK] MAVLinkBridge class instantiated successfully.")
    except Exception as e:
        logger.error(f" - [FAIL] MAVLink test error: {e}")


# ──────────────────────────────────────────────────────────
# 5. Failsafe Watchdog
# ──────────────────────────────────────────────────────────
def test_failsafe():
    logger.info("=== 5. TEST FAILSAFE WATCHDOG ===")
    try:
        from core.failsafe import FailsafeWatchdog, Sub
        import psutil
        
        # Mocks
        mav_mock = MagicMock()
        tele_mock = MagicMock()
        sio_mock = MagicMock()
        
        fs = FailsafeWatchdog(mav=mav_mock, tele=tele_mock, sio_emit=sio_mock)
        
        # Run system resources health check method
        fs._check_system()
        
        # Read system health status
        health = fs._health[Sub.SYSTEM]
        logger.info(f" - [OK] FailsafeWatchdog checked system health.")
        logger.info(f"   Status: {'OK' if health.ok else 'FAULT'}")
        logger.info(f"   Message: {health.message}")
    except Exception as e:
        logger.error(f" - [FAIL] FailsafeWatchdog test error: {e}")


# ──────────────────────────────────────────────────────────
# 6. Autonomous Controller State Machine
# ──────────────────────────────────────────────────────────
def test_autonomous_state_machine():
    logger.info("=== 6. TEST AUTONOMOUS STATE MACHINE ===")
    try:
        from core.autonomous import AutonomousController
        
        # Mocks
        mav_mock = MagicMock()
        tele_mock = MagicMock()
        traj_mock = MagicMock()
        fs_mock = MagicMock()
        sio_mock = MagicMock()
        q_front = multiprocessing.Queue()
        q_cmd = multiprocessing.Queue()
        
        # Set mock waypoints to allow mission start
        traj_mock.get_replay_waypoints.return_value = [{"x": 0.0, "y": 0.0, "yaw": 0.0}]
        
        controller = AutonomousController(
            mav=mav_mock, 
            tele=tele_mock, 
            traj=traj_mock, 
            fs=fs_mock, 
            sio_emit=sio_mock,
            qr_front_result_queue=q_front,
            cmd_front_queue=q_cmd
        )
        
        status = controller.get_status()
        logger.info(f" - [OK] AutonomousController created in state: {status['state']}")
        
        # Trigger mock waypoint replay start
        res = controller.start_mission(target_id="QR_DOCK_ZONE_B")
        logger.info(f" - [OK] start_mission call response: {res}")
        logger.info(f"   Current Controller State: {controller.get_status()['state']}")
        
        # Abort mission to clean up threads/state
        controller.stop_mission(reason="Test termination")
        logger.info(f" - [OK] stop_mission executed. State: {controller.get_status()['state']}")
    except Exception as e:
        logger.error(f" - [FAIL] AutonomousController error: {e}")


# ──────────────────────────────────────────────────────────
# Main Runner
# ──────────────────────────────────────────────────────────
def main():
    logger.info("Starting System Components Test Suite...")
    
    test_environment()
    print()
    test_image_processors()
    print()
    test_qr_detectors()
    print()
    test_mavlink()
    print()
    test_failsafe()
    print()
    test_autonomous_state_machine()
    
    logger.info("=== TEST SUITE COMPLETED ===")


if __name__ == "__main__":
    main()
