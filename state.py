"""
Agent temps-reel pour le robot Bastet (Pi 5).
Rapporte la version, l'etat des capteurs/systeme a la Gateway,
et ecoute le WebSocket pour declencher les mises a jour en direct.
"""
import os, json, threading, ssl
from pathlib import Path
socket.setdefaulttimeout(30.0)
GATEWAY_URL = "https://ha.arthonetwork.fr:44888"
WS_URL = "wss://ha.arthonetwork.fr:44888/ws/robot"
API_TOKEN = "bst_c9f28d3a1e4b85c7f0d4b9a2e6f1c3d5"
VERSION_FILE = Path("/opt/spotbot/version.txt")
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE
ros2_process = None
latest_telemetry = None
_latest_telemetry_lock = threading.Lock()
tts_target = "robot"
stt_target = "robot"
chat_target = "robot"
yolo_state = "robot"
face_rec_state = "robot"

# File path constants (shared across modules)
CAMERA_MAPPING_FILE = Path("/opt/spotbot/config/camera_mapping.json")
CALIB_STATUS_FILE = Path("/opt/spotbot/config/calib_status.json")
CALIBRATION_FILE = Path("/opt/spotbot/config/calibration.json")
CAMERA_CALIB_LEFT_FILE = Path("/opt/spotbot/config/camera_stereo_left.yaml")
CAMERA_CALIB_RIGHT_FILE = Path("/opt/spotbot/config/camera_stereo_right.yaml")
CAMERA_CALIB_MONO_FILE = Path("/opt/spotbot/config/camera_calibration.yaml")
ARDUINO_VERSION_FILE = Path("/opt/spotbot/arduino_version.txt")
