from pymavlink import mavutil

# ===========================
# Ubah sesuai koneksi
# ===========================
CONNECTION = "udp:0.0.0.0:14550"
# Contoh lain:
# CONNECTION = "/dev/ttyAMA0"
# CONNECTION = "/dev/serial0"
# CONNECTION = "tcp:127.0.0.1:5760"

print(f"Connecting ke {CONNECTION}...")

master = mavutil.mavlink_connection(CONNECTION)

print("Menunggu heartbeat...")
master.wait_heartbeat()

print("Heartbeat diterima!")
print(f"System ID    : {master.target_system}")
print(f"Component ID : {master.target_component}")
print()

while True:
    msg = master.recv_match(blocking=True)

    if msg is None:
        continue

    msg_type = msg.get_type()

    if msg_type == "HEARTBEAT":
        print("Heartbeat")

    elif msg_type == "SYS_STATUS":
        print(f"Battery : {msg.voltage_battery/1000:.2f} V")

    elif msg_type == "GLOBAL_POSITION_INT":
        print(f"Lat : {msg.lat/1e7:.7f}")
        print(f"Lon : {msg.lon/1e7:.7f}")
        print(f"Alt : {msg.relative_alt/1000:.2f} m")
        print()

    elif msg_type == "ATTITUDE":
        print(f"Roll  : {msg.roll:.2f}")
        print(f"Pitch : {msg.pitch:.2f}")
        print(f"Yaw   : {msg.yaw:.2f}")
        print()

    elif msg_type == "VFR_HUD":
        print(f"Ground Speed : {msg.groundspeed:.2f}")
        print(f"Heading      : {msg.heading}")
        print()