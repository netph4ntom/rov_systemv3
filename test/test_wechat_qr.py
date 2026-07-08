# test/test_wechat_qr.py
# Standalone test script for WeChat QR Code Detector.

import os
import sys
import logging

# Ensure project root is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import cv2
import numpy as np
from core.wechat_model_downloader import ensure_wechat_models
from config import (
    WECHAT_QR_DETECT_PROTOTXT,
    WECHAT_QR_DETECT_CAFFEMODEL,
    WECHAT_QR_SR_PROTOTXT,
    WECHAT_QR_SR_CAFFEMODEL,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TestWeChatQR")


def main():
    logger.info("=== WeChat QR Code Standalone Test ===")
    
    # 1. Check Python version and OpenCV version
    logger.info(f"Python Version: {sys.version}")
    logger.info(f"Executable: {sys.executable}")
    logger.info(f"OpenCV Version: {cv2.__version__}")
    
    # 2. Check model files
    logger.info("Checking model files...")
    if ensure_wechat_models():
        logger.info("Models are ready.")
    else:
        logger.error("Failed to ensure models are downloaded.")
        return

    # Print model file details
    for name, path in [
        ("detect.prototxt", WECHAT_QR_DETECT_PROTOTXT),
        ("detect.caffemodel", WECHAT_QR_DETECT_CAFFEMODEL),
        ("sr.prototxt", WECHAT_QR_SR_PROTOTXT),
        ("sr.caffemodel", WECHAT_QR_SR_CAFFEMODEL),
    ]:
        if os.path.exists(path):
            logger.info(f" - {name}: {os.path.getsize(path)} bytes")
        else:
            logger.error(f" - {name}: NOT FOUND at {path}")

    # 3. Instantiate detector
    detector = None
    logger.info("Attempting to initialize WeChatQRCode with 4 arguments (with Super Resolution)...")
    try:
        detector = cv2.wechat_qrcode_WeChatQRCode(
            WECHAT_QR_DETECT_PROTOTXT,
            WECHAT_QR_DETECT_CAFFEMODEL,
            WECHAT_QR_SR_PROTOTXT,
            WECHAT_QR_SR_CAFFEMODEL
        )
        logger.info("SUCCESS: Initialized with 4 arguments.")
    except Exception as e:
        logger.error(f"FAILED (4 args): {e} (Type: {type(e)})")
        
        logger.info("Attempting to initialize WeChatQRCode with 2 arguments (without Super Resolution)...")
        try:
            detector = cv2.wechat_qrcode_WeChatQRCode(
                WECHAT_QR_DETECT_PROTOTXT,
                WECHAT_QR_DETECT_CAFFEMODEL
            )
            logger.info("SUCCESS: Initialized with 2 arguments.")
        except Exception as e2:
            logger.error(f"FAILED (2 args): {e2} (Type: {type(e2)})")
            
    if detector is None:
        logger.error("Could not initialize WeChatQRCode detector.")
        return

    # 4. Perform detection
    if len(sys.argv) > 1:
        # Load image from argument
        img_path = sys.argv[1]
        logger.info(f"Loading image from: {img_path}")
        if not os.path.exists(img_path):
            logger.error("File does not exist.")
            return
        img = cv2.imread(img_path)
        if img is None:
            logger.error("Failed to load image.")
            return
        
        logger.info("Running detection...")
        res, points = detector.detectAndDecode(img)
        logger.info(f"Result (decoded strings): {res}")
        logger.info(f"Points (coordinates): {points}")
    else:
        # Open camera stream to test
        logger.info("No image provided. Attempting to test with camera stream...")
        logger.info("Opening camera index 0 (press 'q' in window to exit)...")
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            logger.warning("Failed to open camera index 0. Trying camera index 1...")
            cap = cv2.VideoCapture(1)
            
        if not cap.isOpened():
            logger.error("Failed to open any camera.")
            return
            
        cv2.namedWindow("WeChat QR Test", cv2.WINDOW_NORMAL)
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                logger.error("Failed to capture frame.")
                break
                
            res, points = detector.detectAndDecode(frame)
            if res:
                logger.info(f"Detected: {res}")
                for i, pts in enumerate(points):
                    pts = np.array(pts, dtype=np.int32)
                    cv2.polylines(frame, [pts], isClosed=True, color=(0, 255, 0), thickness=2)
                    # Label
                    cv2.putText(frame, res[i], (int(pts[0][0]), int(pts[0][1]) - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            
            cv2.imshow("WeChat QR Test", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
        cap.release()
        cv2.destroyAllWindows()
        logger.info("Camera testing finished.")


if __name__ == "__main__":
    main()
