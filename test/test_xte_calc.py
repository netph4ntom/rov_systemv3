# test/test_xte_calc.py
import unittest
import math

class TestXTECalculation(unittest.TestCase):
    def calculate_xte(self, prev_wp, next_wp, curr_pos, curr_yaw):
        ux = next_wp["x"] - prev_wp["x"]
        uy = next_wp["y"] - prev_wp["y"]
        u_mag_sq = ux * ux + uy * uy

        if u_mag_sq <= 1e-6:
            return 0.0, 0.0

        vx = curr_pos["x"] - prev_wp["x"]
        vy = curr_pos["y"] - prev_wp["y"]
        
        t = (vx * ux + vy * uy) / u_mag_sq
        t = max(0.0, min(1.0, t))
        
        proj_x = prev_wp["x"] + t * ux
        proj_y = prev_wp["y"] + t * uy
        
        err_x = curr_pos["x"] - proj_x
        err_y = curr_pos["y"] - proj_y
        
        yaw_rad = math.radians(curr_yaw)
        # Rotasi dari global (X=North, Y=East) ke body frame ROV
        lat_error = -err_x * math.sin(yaw_rad) + err_y * math.cos(yaw_rad)
        xte_m = abs(lat_error)
        
        return xte_m, lat_error

    def test_facing_north_drift_east(self):
        # Path: straight North along X-axis from (0,0) to (10,0)
        # ROV: at (5, 1) -> 1m East of the path
        # Yaw: 0 (Facing North)
        # Expected: XTE = 1.0, lateral error = +1.0 (positive/right)
        prev_wp = {"x": 0.0, "y": 0.0}
        next_wp = {"x": 10.0, "y": 0.0}
        curr_pos = {"x": 5.0, "y": 1.0}
        yaw = 0.0
        
        xte, lat_err = self.calculate_xte(prev_wp, next_wp, curr_pos, yaw)
        self.assertAlmostEqual(xte, 1.0)
        self.assertAlmostEqual(lat_err, 1.0)

    def test_facing_north_drift_west(self):
        # Path: straight North along X-axis from (0,0) to (10,0)
        # ROV: at (5, -1) -> 1m West of the path
        # Yaw: 0 (Facing North)
        # Expected: XTE = 1.0, lateral error = -1.0 (negative/left)
        prev_wp = {"x": 0.0, "y": 0.0}
        next_wp = {"x": 10.0, "y": 0.0}
        curr_pos = {"x": 5.0, "y": -1.0}
        yaw = 0.0
        
        xte, lat_err = self.calculate_xte(prev_wp, next_wp, curr_pos, yaw)
        self.assertAlmostEqual(xte, 1.0)
        self.assertAlmostEqual(lat_err, -1.0)

    def test_facing_east_drift_north(self):
        # Path: straight East along Y-axis from (0,0) to (0,10)
        # ROV: at (1, 5) -> 1m North of the path
        # Yaw: 90 (Facing East)
        # Expected: Since ROV is facing East (+Y), North (+X) is to its left.
        #           So XTE = 1.0, lateral error = -1.0 (negative/left)
        prev_wp = {"x": 0.0, "y": 0.0}
        next_wp = {"x": 0.0, "y": 10.0}
        curr_pos = {"x": 1.0, "y": 5.0}
        yaw = 90.0
        
        xte, lat_err = self.calculate_xte(prev_wp, next_wp, curr_pos, yaw)
        self.assertAlmostEqual(xte, 1.0)
        self.assertAlmostEqual(lat_err, -1.0)

    def test_facing_south_drift_east(self):
        # Path: straight South along X-axis from (0,0) to (-10,0)
        # ROV: at (-5, 1) -> 1m East of the path
        # Yaw: 180 (Facing South)
        # Expected: Since ROV is facing South (-X), East (+Y) is to its left.
        #           So XTE = 1.0, lateral error = -1.0 (negative/left)
        prev_wp = {"x": 0.0, "y": 0.0}
        next_wp = {"x": -10.0, "y": 0.0}
        curr_pos = {"x": -5.0, "y": 1.0}
        yaw = 180.0
        
        xte, lat_err = self.calculate_xte(prev_wp, next_wp, curr_pos, yaw)
        self.assertAlmostEqual(xte, 1.0)
        self.assertAlmostEqual(lat_err, -1.0)

if __name__ == "__main__":
    unittest.main()
