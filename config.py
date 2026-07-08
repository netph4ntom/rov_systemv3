# config.py - Konfigurasi global untuk Project ROV Vision
# Semua konstanta dan setting dipusatkan di sini

# ──────────────────────────────────────────────
# KAMERA
# ──────────────────────────────────────────────
CAMERA_FRONT_INDEX   = 0          # index /dev/videoX untuk kamera depan
CAMERA_BOTTOM_INDEX  = 1          # index /dev/videoX untuk kamera bawah

FRAME_WIDTH   = 640
FRAME_HEIGHT  = 480
FRAME_FPS     = 20

MJPEG_QUALITY = 60                # kualitas JPEG 1–100 (makin kecil makin ringan)

# ------------------------------------------------
# IMAGE PROCESSING
# ------------------------------------------------

# ── Color correction (camera_front) ──────────────────────────
# Naikkan RED_BOOST jika gambar masih terlalu biru/hijau.
COLOR_CORRECTION_RED_BOOST   = 10   # range rekomendasi: 5–20
COLOR_CORRECTION_BLUE_REDUCE =  5   # range rekomendasi: 0–10

# ── CLAHE (camera_front & camera_bottom) ─────────────────────
CLAHE_CLIP_LIMIT = 1.5        # range rekomendasi kolam: 1.0–2.0
CLAHE_TILE_SIZE  = (8, 8)     # tile 8x8 cocok untuk resolusi 640x480


# ------------------------------------------------
# IMAGE PROCESSING
# ------------------------------------------------
SCREENSHOT_DIR  = "storage/screenshots"
RECORD_DIR      = "storage/recordings"

SCREENSHOT_QUALITY = 95

RECORD_FOURCC = "mp4v"

# ──────────────────────────────────────────────
# SERVER PORT
# ──────────────────────────────────────────────
# MJPEG stream
PORT_STREAM_FRONT   = 8001        # http://<ip>:8001/stream
PORT_STREAM_BOTTOM  = 8002        # http://<ip>:8002/stream

# Core API (Flask REST + WebSocket via flask-socketio)
PORT_CORE_API        = 8000       # http://<ip>:8000/

# ──────────────────────────────────────────────
# MULTIPROCESSING / IPC
# ──────────────────────────────────────────────
# Queue maxsize = 0 berarti unlimited; set ke N untuk back-pressure
QUEUE_MAXSIZE = 10

# ──────────────────────────────────────────────
# QR CODE / DOCKING
# ──────────────────────────────────────────────
QR_SCAN_INTERVAL_MS = 200         # scan QR setiap N ms (kurangi beban CPU)

# WeChat QR Code Detector model configurations
import os
WECHAT_QR_MODEL_DIR = os.path.join(os.path.dirname(__file__), "core", "wechat_models")
WECHAT_QR_DETECT_PROTOTXT = os.path.join(WECHAT_QR_MODEL_DIR, "detect.prototxt")
WECHAT_QR_DETECT_CAFFEMODEL = os.path.join(WECHAT_QR_MODEL_DIR, "detect.caffemodel")
WECHAT_QR_SR_PROTOTXT = os.path.join(WECHAT_QR_MODEL_DIR, "sr.prototxt")
WECHAT_QR_SR_CAFFEMODEL = os.path.join(WECHAT_QR_MODEL_DIR, "sr.caffemodel")

# URLs to download WeChat QR models if missing
WECHAT_QR_MODEL_URLS = {
    "detect.prototxt": "https://raw.githubusercontent.com/WeChatCV/opencv_3rdparty/wechat_qrcode/detect.prototxt",
    "detect.caffemodel": "https://raw.githubusercontent.com/WeChatCV/opencv_3rdparty/wechat_qrcode/detect.caffemodel",
    "sr.prototxt": "https://raw.githubusercontent.com/WeChatCV/opencv_3rdparty/wechat_qrcode/sr.prototxt",
    "sr.caffemodel": "https://raw.githubusercontent.com/WeChatCV/opencv_3rdparty/wechat_qrcode/sr.caffemodel"
}

# ──────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────
import os
LOG_DIR   = os.path.join(os.path.dirname(__file__), "logs")
LOG_LEVEL = "DEBUG"               # DEBUG | INFO | WARNING | ERROR

# ──────────────────────────────────────────────
# MAVLINK
# ──────────────────────────────────────────────
MAVLINK_CONNECTION_STRING  = os.getenv("MAVLINK_CONNECTION_STRING", "udp:0.0.0.0:14550")
MAVLINK_BAUD               = int(os.getenv("MAVLINK_BAUD", "115200"))
MAVLINK_SOURCE_SYSTEM      = 255       # GCS system ID
MAVLINK_HEARTBEAT_INTERVAL = 1.0      # detik antar heartbeat


# ──────────────────────────────────────────────
# SERVO
# ──────────────────────────────────────────────
SERVO_GRIPPER_CHANNEL   = 9          # AUX channel untuk servo gripper
SERVO_GRIPPER_OPEN_PWM  = 1900       # PWM saat gripper terbuka
SERVO_GRIPPER_CLOSE_PWM = 1100       # PWM saat gripper tertutup

RELAY_LIGHT_INDEX = 0                # relay index untuk lampu utama

# ──────────────────────────────────────────────
# Joystick
# ──────────────────────────────────────────────
RC_CHANNEL_COUNT = 8                 # jumlah channel RC yang di-override
RC_NEUTRAL_PWM   = 1500              # PWM neutral (tidak bergerak)

JOYSTICK_SCALE_MS = 1.0              # Skala velocity untuk dead reckoning trajectory (m/s pada joystick full deflection)

# ──────────────────────────────────────────────
# TRAJECTORY
# ──────────────────────────────────────────────
TRAJECTORY_HISTORY_SIZE    = 500     # jumlah titik path yang disimpan
TRAJECTORY_UPDATE_INTERVAL = 0.1     # minimum interval emit ke React (detik)

# ──────────────────────────────────────────────
# FAILSAFE & WATCHDOG
# ──────────────────────────────────────────────

FS_CHECK_INTERVAL = 2.0
FS_MAVLINK_TIMEOUT = 5.0
FS_DASHBOARD_TIMEOUT = 30.0
FS_TELEMETRY_TIMEOUT = 5.0

FS_CAMERA_HEALTH_URL_FRONT  = f"http://localhost:{PORT_STREAM_FRONT}/health"
FS_CAMERA_HEALTH_URL_BOTTOM = f"http://localhost:{PORT_STREAM_BOTTOM}/health"

FS_CPU_WARN_PERCENT  = 85.0   # persen
FS_RAM_WARN_PERCENT  = 85.0   # persen
FS_TEMP_WARN_CELSIUS = 70.0   # derajat Celsius

FS_MAX_RECOVERY_ATTEMPTS = 3

# ──────────────────────────────────────────────
# AUTONOMOUS MISSION — Trajectory Replay
# ──────────────────────────────────────────────
# ArduSub Standard RC Channel Mapping:
#   CH1 = Lateral / Strafe (Roll)     → vel_x  (kanan positif)
#   CH2 = Forward / Backward (Pitch)  → vel_y  (maju positif)
#   CH3 = Throttle / Vertical
#   CH4 = Yaw (rotasi kanan positif)
AUTONOMOUS_RC_CH_LATERAL  = 1
AUTONOMOUS_RC_CH_FORWARD  = 2
AUTONOMOUS_RC_CH_THROTTLE = 3
AUTONOMOUS_RC_CH_YAW      = 4

# Trajectory Replay — toleransi dan kecepatan
AUTONOMOUS_WAYPOINT_REACH_THRESHOLD_M  = 0.20  # meter — waypoint dianggap tercapai
AUTONOMOUS_WAYPOINT_SKIP_THRESHOLD_M   = 0.50  # meter — skip waypoint jika estimasi drift terlalu jauh
AUTONOMOUS_WAYPOINT_TIMEOUT_S          = 8.0   # detik maks per waypoint sebelum skip
AUTONOMOUS_REPLAY_SPEED_PWM            = 1580  # PWM forward saat replay (maju ke target)
AUTONOMOUS_RETURN_SPEED_PWM            = 1580  # PWM forward saat return (balik ke docking, ROV berbalik arah)
AUTONOMOUS_KP_YAW                      = 3.0   # gain koreksi yaw: derajat error → PWM delta
AUTONOMOUS_MAX_YAW_CORRECTION          = 350   # maks delta PWM untuk koreksi yaw
AUTONOMOUS_LOOP_HZ                     = 10    # iterasi kontrol per detik (100ms/loop)

# QR Fine-Alignment
AUTONOMOUS_ALIGN_THRESHOLD_PX          = 30    # piksel — |offset_x| dan |offset_y| < nilai ini = aligned
AUTONOMOUS_ALIGN_TIMEOUT_S             = 25.0  # detik maks fase alignment sebelum abort
AUTONOMOUS_KP_ALIGN_LATERAL            = 0.60  # gain: offset_x (px) → CH1 (lateral correction)
AUTONOMOUS_KP_ALIGN_YAW               = 0.35  # gain: offset_x (px) → CH4 (yaw assist)
AUTONOMOUS_MAX_ALIGN_CORRECTION        = 180   # maks delta PWM saat alignment

# Pickup sequence
AUTONOMOUS_PICKUP_ADVANCE_S            = 0.8   # detik maju perlahan setelah gripper terbuka
AUTONOMOUS_GRIPPER_WAIT_S             = 1.2   # detik tunggu setelah perintah gripper (buka/tutup)
AUTONOMOUS_STOP_WAIT_S               = 0.5   # detik tunggu motor berhenti sebelum gripper

# Thinning waypoints untuk mengurangi overshoot di trajektori rekaman
# Ambil 1 dari N waypoint (subsample) — 1 = gunakan semua
AUTONOMOUS_WAYPOINT_SUBSAMPLE         = 3     # ambil setiap waypoint ke-3