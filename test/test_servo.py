# test/test_servo.py
# ==============================================================================
# ROV Gripper Servo Standalone Test
# ==============================================================================
#
# Script ini menguji gripper servo secara langsung via MAVLink tanpa
# menjalankan seluruh sistem ROV. Berguna untuk verifikasi hardware
# setelah pemasangan servo atau perubahan konfigurasi.
#
# Usage:
#   python test/test_servo.py                       # pakai default dari config.py
#   python test/test_servo.py --port COM3           # serial langsung
#   python test/test_servo.py --port /dev/ttyUSB0   # Linux serial
#   python test/test_servo.py --port udp:0.0.0.0:14550  # UDP
#
# Config yang dipakai (dari config.py):
#   SERVO_GRIPPER_CHANNEL   = channel AUX servo
#   SERVO_GRIPPER_OPEN_PWM  = nilai PWM saat buka
#   SERVO_GRIPPER_CLOSE_PWM = nilai PWM saat tutup
#   MAVLINK_BAUD            = baud rate serial
# ==============================================================================

import os
import sys
import time
import argparse

# Pastikan project root ada di sys.path untuk import config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from pymavlink import mavutil
except ImportError:
    print("[ERROR] pymavlink tidak terinstall. Jalankan: pip install pymavlink")
    sys.exit(1)

try:
    from config import (
        SERVO_GRIPPER_CHANNEL,
        SERVO_GRIPPER_OPEN_PWM,
        SERVO_GRIPPER_CLOSE_PWM,
        MAVLINK_BAUD,
        MAVLINK_CONNECTION_STRING,
    )
except ImportError as e:
    print(f"[ERROR] Gagal import config.py: {e}")
    sys.exit(1)

# ── Terminal Colors ─────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"

if sys.platform == "win32":
    os.system("")  # aktifkan ANSI di Windows


def log_info(msg):
    print(f"  {CYAN}[INFO]{RESET}  {msg}")

def log_ok(msg):
    print(f"  {GREEN}[OK]{RESET}    {msg}")

def log_warn(msg):
    print(f"  {YELLOW}[WARN]{RESET}  {msg}")

def log_err(msg):
    print(f"  {RED}[ERROR]{RESET} {msg}")


def send_servo(conn, channel: int, pwm: int) -> bool:
    """
    Kirim perintah MAV_CMD_DO_SET_SERVO dan tunggu ACK (timeout 3 detik).
    Return True jika ACK diterima (ACCEPTED atau IN_PROGRESS), False jika gagal/timeout.
    """
    conn.mav.command_long_send(
        conn.target_system,
        conn.target_component,
        mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
        0,            # confirmation
        float(channel),   # param1: servo channel number
        float(pwm),       # param2: PWM value (microseconds)
        0, 0, 0, 0, 0     # param3-7: unused
    )

    # Tunggu COMMAND_ACK
    ack = conn.recv_match(type="COMMAND_ACK", blocking=True, timeout=3.0)
    if ack is None:
        log_warn("Tidak ada ACK diterima dalam 3 detik (koneksi lambat atau firmware tidak mengirim ACK).")
        return False

    # Result codes: 0=ACCEPTED, 1=TEMPORARILY_REJECTED, 2=DENIED, 3=UNSUPPORTED, 4=FAILED, 5=IN_PROGRESS
    RESULT_ACCEPTED    = 0
    RESULT_IN_PROGRESS = 5
    result = ack.result

    if result in (RESULT_ACCEPTED, RESULT_IN_PROGRESS):
        log_ok(f"ACK diterima → result={result} (ACCEPTED/IN_PROGRESS)")
        return True
    else:
        log_warn(f"ACK diterima tapi command ditolak → result={result}")
        return False


def run_test(port: str, baud: int):
    print(f"\n{CYAN}{BOLD}{'=' * 60}")
    print("   ROV GRIPPER SERVO TEST")
    print(f"{'=' * 60}{RESET}")
    print(f"  Connection : {BOLD}{port}{RESET}")
    print(f"  Baud Rate  : {baud}")
    print(f"  Channel    : SERVO{SERVO_GRIPPER_CHANNEL} (AUX{SERVO_GRIPPER_CHANNEL - 8})")
    print(f"  PWM Open   : {SERVO_GRIPPER_OPEN_PWM} µs")
    print(f"  PWM Close  : {SERVO_GRIPPER_CLOSE_PWM} µs")
    print(f"{CYAN}{'-' * 60}{RESET}\n")

    # ── Step 1: Connect ────────────────────────────────────────────
    log_info(f"Connecting ke '{port}'...")
    try:
        master = mavutil.mavlink_connection(port, baud=baud)
    except Exception as e:
        log_err(f"Gagal membuka koneksi MAVLink: {e}")
        sys.exit(1)

    # ── Step 2: Tunggu Heartbeat ───────────────────────────────────
    log_info("Menunggu heartbeat dari flight controller (timeout=15s)...")
    hb = master.wait_heartbeat(timeout=15)
    if hb is None:
        log_err("Heartbeat tidak diterima dalam 15 detik. Periksa koneksi dan pastikan ArduSub aktif.")
        sys.exit(1)

    log_ok(f"Heartbeat diterima! system_id={master.target_system}, component_id={master.target_component}")
    time.sleep(0.5)

    # ── Step 3: Test OPEN ──────────────────────────────────────────
    print(f"\n  {BOLD}[TEST 1/2] GRIPPER OPEN — PWM={SERVO_GRIPPER_OPEN_PWM} µs{RESET}")
    log_info(f"Mengirim perintah OPEN ke SERVO{SERVO_GRIPPER_CHANNEL}...")
    ok = send_servo(master, SERVO_GRIPPER_CHANNEL, SERVO_GRIPPER_OPEN_PWM)
    if ok:
        log_ok("Perintah OPEN berhasil dikirim dan di-ACK.")
    else:
        log_warn("Perintah OPEN mungkin tidak diterima dengan benar.")

    log_info("Menunggu 2 detik agar servo bergerak penuh...")
    time.sleep(2.0)

    # ── Step 4: Test CLOSE ─────────────────────────────────────────
    print(f"\n  {BOLD}[TEST 2/2] GRIPPER CLOSE — PWM={SERVO_GRIPPER_CLOSE_PWM} µs{RESET}")
    log_info(f"Mengirim perintah CLOSE ke SERVO{SERVO_GRIPPER_CHANNEL}...")
    ok = send_servo(master, SERVO_GRIPPER_CHANNEL, SERVO_GRIPPER_CLOSE_PWM)
    if ok:
        log_ok("Perintah CLOSE berhasil dikirim dan di-ACK.")
    else:
        log_warn("Perintah CLOSE mungkin tidak diterima dengan benar.")

    log_info("Menunggu 2 detik agar servo bergerak penuh...")
    time.sleep(2.0)

    # ── Step 5: Selesai ────────────────────────────────────────────
    print(f"\n{CYAN}{BOLD}{'=' * 60}")
    print("   TEST SELESAI")
    print(f"{'=' * 60}{RESET}")
    print(f"  Cek visual gripper di hardware untuk verifikasi.")
    print(f"  Jika servo tidak bergerak:")
    print(f"    1. Pastikan SERVO{SERVO_GRIPPER_CHANNEL} di-assign ke Output di QGC")
    print(f"    2. Periksa supply power servo (5V rail)")
    print(f"    3. Cek nilai SERVO{SERVO_GRIPPER_CHANNEL}_MIN / MAX / TRIM di ArduSub params\n")


def main():
    parser = argparse.ArgumentParser(
        description="ROV Gripper Servo Standalone Test via MAVLink",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Contoh:
  python test/test_servo.py
  python test/test_servo.py --port COM3
  python test/test_servo.py --port /dev/ttyUSB0 --baud 57600
  python test/test_servo.py --port udp:0.0.0.0:14550
        """
    )
    parser.add_argument(
        "--port",
        default=None,
        help=(
            f"Connection string MAVLink. "
            f"Default: nilai MAVLINK_CONNECTION_STRING dari config.py ('{MAVLINK_CONNECTION_STRING}'). "
            "Contoh: COM3, /dev/ttyUSB0, udp:0.0.0.0:14550"
        )
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=MAVLINK_BAUD,
        help=f"Baud rate untuk koneksi serial. Default dari config.py: {MAVLINK_BAUD}"
    )

    args = parser.parse_args()

    # Tentukan connection string: CLI arg > MAVLINK_CONNECTION_STRING dari config
    port = args.port if args.port else MAVLINK_CONNECTION_STRING

    try:
        run_test(port=port, baud=args.baud)
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}Test dibatalkan oleh user (Ctrl+C).{RESET}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()