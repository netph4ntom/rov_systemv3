from pymavlink import mavutil

master = mavutil.mavlink_connection('COM3', baud=115200)

master.wait_heartbeat()

master.mav.command_long_send(
    master.target_system,
    master.target_component,
    mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
    0,
    9,      # Servo output (SERVO9 = AUX1)
    1100,
    0,0,0,0,0
)