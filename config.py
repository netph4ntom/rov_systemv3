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

MJPEG_QUALITY = 80                # kualitas JPEG 1–100 (makin kecil makin ringan)

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

# ──────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────
import os
LOG_DIR   = os.path.join(os.path.dirname(__file__), "logs")
LOG_LEVEL = "DEBUG"               # DEBUG | INFO | WARNING | ERROR

# ──────────────────────────────────────────────
# MAVLINK
# ──────────────────────────────────────────────
MAVLINK_CONNECTION_STRING  = "udp:0.0.0.0:14550"
MAVLINK_BAUD               = 115200
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