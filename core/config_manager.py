import os
import json
import logging

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'storage', 'user_config.json')

DEFAULT_CONFIG = {
    "axisMap": {
        "forward": {"axisIdx": 1, "invert": True},
        "yaw": {"axisIdx": 0, "invert": False},
        "throttle": {"axisIdx": 3, "invert": True},
        "lateral": {"axisIdx": 2, "invert": False}
    },
    "btnMap": {
        "0": "mode_toggle",
        "1": "gripper_toggle",
        "2": "arm_toggle",
        "3": "light_toggle",
        "9": "emergency_stop"
    },
    "calib": {
        "forward": 1.0,
        "yaw": 1.0,
        "throttle": 1.0,
        "lateral": 1.0
    }
}

def load_config():
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    
    try:
        with open(CONFIG_PATH, 'r') as f:
            data = json.load(f)
            merged = DEFAULT_CONFIG.copy()
            for key in DEFAULT_CONFIG:
                if key in data:
                    if isinstance(DEFAULT_CONFIG[key], dict) and isinstance(data[key], dict):
                        merged[key] = {**DEFAULT_CONFIG[key], **data[key]}
                    else:
                        merged[key] = data[key]
            return merged
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return DEFAULT_CONFIG

def save_config(config_data):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config_data, f, indent=4)
        return True
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        return False
