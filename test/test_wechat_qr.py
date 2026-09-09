# test/test_wechat_qr.py
# WeChat QR Code Standalone Test + Browser Stream
#
# Jalankan di Raspberry Pi:
#   python3 test/test_wechat_qr.py
#
# Buka dari laptop:
#   http://IP_RASPBERRY_PI:8003

import os
import sys
import logging
import threading
import time

import cv2
import numpy as np
from flask import Flask, Response, render_template_string

# Ensure project root is in sys.path
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)

from core.wechat_model_downloader import ensure_wechat_models
from config import (
    WECHAT_QR_DETECT_PROTOTXT,
    WECHAT_QR_DETECT_CAFFEMODEL,
    WECHAT_QR_SR_PROTOTXT,
    WECHAT_QR_SR_CAFFEMODEL,
)


# ═══════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger("TestWeChatQR")


# ═══════════════════════════════════════════════
# Flask
# ═══════════════════════════════════════════════

app = Flask(__name__)


# ═══════════════════════════════════════════════
# Global camera state
# ═══════════════════════════════════════════════

camera = None
camera_lock = threading.Lock()

latest_frame = None
latest_frame_lock = threading.Lock()

latest_qr_result = []


# ═══════════════════════════════════════════════
# HTML
# ═══════════════════════════════════════════════

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>WeChat QR Test - ROV</title>

    <style>
        body {
            margin: 0;
            background: #111;
            color: white;
            font-family: Arial, sans-serif;
            text-align: center;
        }

        h1 {
            margin: 20px 0 10px 0;
        }

        .container {
            width: 90%;
            max-width: 1000px;
            margin: auto;
        }

        img {
            width: 100%;
            max-width: 960px;
            border: 2px solid #444;
            border-radius: 8px;
        }

        .status {
            margin-top: 15px;
            padding: 12px;
            background: #222;
            border-radius: 8px;
        }

        .qr {
            margin-top: 10px;
            font-size: 20px;
            font-weight: bold;
        }

        .green {
            color: #00ff88;
        }

        .gray {
            color: #aaa;
        }
    </style>
</head>

<body>

<div class="container">

    <h1>WeChat QR Code Test</h1>

    <img src="/stream">

    <div class="status">
        <div>
            Camera: <span class="green">RUNNING</span>
        </div>

        <div class="qr" id="qr">
            Menunggu QR...
        </div>
    </div>

</div>

<script>

async function updateQR() {

    try {

        const response = await fetch("/api/qr");
        const data = await response.json();

        const element = document.getElementById("qr");

        if (data.results && data.results.length > 0) {

            element.innerHTML =
                "QR TERDETEKSI: " +
                data.results.join("<br>");

            element.className = "qr green";

        } else {

            element.innerHTML = "Tidak ada QR terdeteksi";
            element.className = "qr gray";

        }

    } catch (error) {

        console.error(error);

    }

}

setInterval(updateQR, 500);
updateQR();

</script>

</body>
</html>
"""


# ═══════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════

@app.route("/")
def index():
    return render_template_string(HTML_PAGE)


@app.route("/api/qr")
def api_qr():

    return {
        "results": latest_qr_result
    }


@app.route("/health")
def health():

    return {
        "status": "ok",
        "camera": camera is not None
    }


@app.route("/stream")
def stream():

    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


# ═══════════════════════════════════════════════
# Frame generator
# ═══════════════════════════════════════════════

def generate_frames():

    while True:

        with latest_frame_lock:
            if latest_frame is None:
                frame = None
            else:
                frame = latest_frame.copy()

        if frame is None:
            time.sleep(0.1)
            continue

        success, buffer = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, 80]
        )

        if not success:
            time.sleep(0.1)
            continue

        frame_bytes = buffer.tobytes()

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + frame_bytes
            + b"\r\n"
        )

        # Kasih delay sedikit supaya tidak freeze browser & CPU (~30fps)
        time.sleep(0.033)


# ═══════════════════════════════════════════════
# Camera + QR processing
# ═══════════════════════════════════════════════

def camera_loop(detector):

    global camera
    global latest_frame
    global latest_qr_result

    logger.info("Opening camera index 0...")

    camera = cv2.VideoCapture(0, cv2.CAP_V4L2)

    if not camera.isOpened():

        logger.warning(
            "Camera index 0 gagal dibuka. Mencoba index 1..."
        )

        camera.release()

        camera = cv2.VideoCapture(1, cv2.CAP_V4L2)

    if not camera.isOpened():

        logger.error("Gagal membuka kamera index 0 maupun 1.")
        return

    logger.info("Camera berhasil dibuka.")

    # Optional camera settings
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    camera.set(cv2.CAP_PROP_FPS, 30)

    while True:

        ret, frame = camera.read()

        if not ret or frame is None:

            logger.warning("Gagal mengambil frame dari kamera.")

            time.sleep(0.1)
            continue

        # ═══════════════════════════════════════
        # QR Detection
        # ═══════════════════════════════════════

        try:

            res, points = detector.detectAndDecode(frame)

        except Exception as e:

            logger.error(
                f"WeChat QR detection error: {e}"
            )

            res = []
            points = None

        # Reset hasil QR
        detected_results = []

        if res:

            for i, text in enumerate(res):

                if text:

                    detected_results.append(text)

                    logger.info(
                        f"QR TERDETEKSI: {text}"
                    )

                # ═══════════════════════════════
                # Draw bounding box
                # ═══════════════════════════════

                if points is not None and i < len(points):

                    pts = np.array(
                        points[i],
                        dtype=np.int32
                    )

                    # Normalisasi bentuk array
                    pts = pts.reshape(-1, 2)

                    if len(pts) >= 4:

                        cv2.polylines(
                            frame,
                            [pts],
                            isClosed=True,
                            color=(0, 255, 0),
                            thickness=3
                        )

                        # Ambil titik pertama
                        x = int(pts[0][0])
                        y = int(pts[0][1])

                        # Label QR
                        label = text if text else "QR"

                        cv2.putText(
                            frame,
                            label,
                            (x, max(30, y - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.7,
                            (0, 255, 0),
                            2
                        )

        latest_qr_result = detected_results

        # ═══════════════════════════════════════
        # Tambahkan status di video
        # ═══════════════════════════════════════

        cv2.putText(
            frame,
            "WeChat QR Test",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (255, 255, 255),
            2
        )

        if detected_results:

            cv2.putText(
                frame,
                "QR DETECTED",
                (20, 75),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2
            )

        else:

            cv2.putText(
                frame,
                "Scanning...",
                (20, 75),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (255, 255, 255),
                2
            )

        # Simpan frame terbaru
        with latest_frame_lock:
            latest_frame = frame


# ═══════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════

def main():

    logger.info("=== WeChat QR Browser Test ===")

    # ═══════════════════════════════════════════
    # System information
    # ═══════════════════════════════════════════

    logger.info(f"Python Version: {sys.version}")
    logger.info(f"Executable: {sys.executable}")
    logger.info(f"OpenCV Version: {cv2.__version__}")

    # ═══════════════════════════════════════════
    # Model files
    # ═══════════════════════════════════════════

    logger.info("Checking model files...")

    if ensure_wechat_models():

        logger.info("Models are ready.")

    else:

        logger.error(
            "Failed to ensure models are downloaded."
        )

        return

    for name, path in [

        ("detect.prototxt", WECHAT_QR_DETECT_PROTOTXT),

        ("detect.caffemodel", WECHAT_QR_DETECT_CAFFEMODEL),

        ("sr.prototxt", WECHAT_QR_SR_PROTOTXT),

        ("sr.caffemodel", WECHAT_QR_SR_CAFFEMODEL),

    ]:

        if os.path.exists(path):

            logger.info(
                f" - {name}: {os.path.getsize(path)} bytes"
            )

        else:

            logger.error(
                f" - {name}: NOT FOUND at {path}"
            )

    # ═══════════════════════════════════════════
    # Initialize detector
    # ═══════════════════════════════════════════

    detector = None

    logger.info(
        "Initializing WeChatQRCode with Super Resolution..."
    )

    try:

        detector = cv2.wechat_qrcode_WeChatQRCode(
            WECHAT_QR_DETECT_PROTOTXT,
            WECHAT_QR_DETECT_CAFFEMODEL,
            WECHAT_QR_SR_PROTOTXT,
            WECHAT_QR_SR_CAFFEMODEL
        )

        logger.info(
            "SUCCESS: WeChatQRCode initialized with 4 arguments."
        )

    except Exception as e:

        logger.warning(
            f"4-argument initialization failed: {e}"
        )

        logger.info(
            "Trying 2-argument initialization..."
        )

        try:

            detector = cv2.wechat_qrcode_WeChatQRCode(
                WECHAT_QR_DETECT_PROTOTXT,
                WECHAT_QR_DETECT_CAFFEMODEL
            )

            logger.info(
                "SUCCESS: WeChatQRCode initialized without SR."
            )

        except Exception as e2:

            logger.error(
                f"FAILED: {e2}"
            )

            return

    # ═══════════════════════════════════════════
    # Start camera thread
    # ═══════════════════════════════════════════

    camera_thread = threading.Thread(
        target=camera_loop,
        args=(detector,),
        daemon=True,
        name="WeChatQR-Camera"
    )

    camera_thread.start()

    # ═══════════════════════════════════════════
    # Start browser server
    # ═══════════════════════════════════════════

    logger.info("Starting browser stream...")
    logger.info("")
    logger.info("Buka dari laptop:")
    logger.info("http://IP_RASPBERRY_PI:8003")
    logger.info("")
    logger.info("Contoh:")
    logger.info("http://192.168.1.100:8003")
    logger.info("")

    app.run(
        host="0.0.0.0",
        port=8003,
        threaded=True,
        debug=False,
        use_reloader=False
    )


if __name__ == "__main__":
    main()
