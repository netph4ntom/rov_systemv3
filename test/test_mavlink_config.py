import importlib
import os
import sys
import unittest

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class MAVLinkConfigTests(unittest.TestCase):
    def reload_config(self):
        sys.modules.pop("config", None)
        return importlib.import_module("config")

    def test_mavlink_connection_uses_environment(self):
        os.environ["MAVLINK_CONNECTION_STRING"] = "udp:127.0.0.1:14550"
        os.environ["MAVLINK_BAUD"] = "57600"

        config = self.reload_config()

        self.assertEqual(config.MAVLINK_CONNECTION_STRING, "udp:127.0.0.1:14550")
        self.assertEqual(config.MAVLINK_BAUD, 57600)


if __name__ == "__main__":
    unittest.main()
