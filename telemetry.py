"""
Telemetry, ROS2 listener, Arduino, status loops.
"""
import os, sys, json, time, threading, subprocess, urllib.request, socket
from pathlib import Path
import state
import camera
from state import (
    GATEWAY_URL, API_TOKEN, ssl_ctx, VERSION_FILE,
    CALIBRATION_FILE, ARDUINO_VERSION_FILE,
)

# --- Arduino Debounce State (module-local) ---
arduino_stable_state = True
arduino_miss_count = 0
arduino_hit_count = 0
ARDUINO_DEBOUNCE_THRESHOLD = 3

# --- Hibernation Grace Period ---
hibernation_deadline = 0
HIBERNATION_GRACE_SEC = 30

def get_version() -> str:
    if VERSION_FILE.exists():
        try: return VERSION_FILE.read_text().strip()
        except Exception: pass
    return "v0.0.0"

def get_system_metrics() -> dict:
    metrics = {"cpu_temp": 0.0, "cpu_load_1m": 0.0, "ram_total_mb": 0, "ram_used_mb": 0, "ram_percent": 0.0}
    try: metrics["cpu_temp"] = round(int(open("/sys/class/thermal/thermal_zone0/temp").read().strip()) / 1000.0, 1)
    except Exception: pass
    try: metrics["cpu_load_1m"] = float(open("/proc/loadavg").read().strip().split()[0])
    except Exception: pass
    try:
        meminfo = {}
        for line in open("/proc/meminfo"):
            parts = line.split()
            if len(parts) >= 2: meminfo[parts[0].rstrip(":")] = int(parts[1])
        total = meminfo.get("MemTotal", 0) // 1024
        available = meminfo.get("MemAvailable", 0) // 1024
        used = total - available
        metrics["ram_total_mb"] = total; metrics["ram_used_mb"] = used
        metrics["ram_percent"] = round((used / total) * 100.0, 1) if total > 0 else 0.0
    except Exception: pass
    return metrics

def is_spotbot_service_active() -> bool:
    return True

def trigger_updater(version=None):
    print(f"[Agent] Lancement de la mise a jour... (version: {version or 'latest'})")
    try:
        cmd = ["sudo", "python3", "/opt/spotbot/updater.py"]
        if version: cmd.append(version)
        subprocess.Popen(cmd)
    except Exception as e: print(f"[Agent] Erreur lancement updater : {e}")

def is_arduino_connected() -> bool:
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            desc = (p.description or '').lower()
            if 'arduino' in desc or (p.vid == 0x2341 and p.pid in (0x0010, 0x0042)): return True
    except Exception: pass
    import glob
    for pattern in ['/dev/ttyUSB*', '/dev/ttyACM*']:
        if glob.glob(pattern): return True
    return False

def get_arduino_version() -> str:
    robot_ver = get_version()
    if ARDUINO_VERSION_FILE.exists():
        try:
            arduino_ver = ARDUINO_VERSION_FILE.read_text().strip()
            if robot_ver != arduino_ver:
                ARDUINO_VERSION_FILE.write_text(robot_ver); return robot_ver
            return arduino_ver
        except Exception: pass
    try: ARDUINO_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True); ARDUINO_VERSION_FILE.write_text(robot_ver)
    except Exception: pass
    return robot_ver

def report_arduino_progress(status: str, percent: int):
    try:
        url = f"{GATEWAY_URL}/system/update/arduino/progress"
        req = urllib.request.Request(url, data=json.dumps({"status": status, "percent": percent}).encode("utf-8"), headers={"Content-Type": "application/json", "X-API-Token": API_TOKEN}, method="POST")
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=5) as resp: resp.read()
    except Exception as e: print(f"[Agent] Erreur envoi progres Arduino : {e}")

def _ensure_arduino_cli() -> bool:
    try:
        r = subprocess.run(["arduino-cli", "version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0: return True
    except FileNotFoundError: pass
    except Exception as e: print(f"[Agent] arduino-cli check error: {e}")
    print("[Agent] arduino-cli introuvable - installation...")
    try:
        install = subprocess.run("curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh", shell=True, capture_output=True, text=True, timeout=120, env={**__import__('os').environ, "BINDIR": "/usr/local/bin"})
        if install.returncode == 0: return True
        return False
    except Exception: return False

def _ensure_arduino_core() -> bool:
    try:
        r = subprocess.run(["arduino-cli", "core", "list"], capture_output=True, text=True, timeout=30)
        if "arduino:avr" in r.stdout: return True
    except Exception: pass
    try:
        subprocess.run(["arduino-cli", "core", "update-index"], capture_output=True, text=True, timeout=60)
        r2 = subprocess.run(["arduino-cli", "core", "install", "arduino:avr"], capture_output=True, text=True, timeout=300)
        if r2.returncode == 0: return True
        return False
    except Exception: return False

def _ensure_arduino_lib(lib_name: str) -> bool:
    try:
        r = subprocess.run(["arduino-cli", "lib", "list"], capture_output=True, text=True, timeout=30)
        if lib_name.lower().replace(" ", "") in r.stdout.lower().replace(" ", ""): return True
    except Exception: pass
    try:
        r = subprocess.run(["arduino-cli", "lib", "install", lib_name], capture_output=True, text=True, timeout=120)
        if r.returncode == 0: return True
        return False
    except Exception: return False

def flash_arduino_task():
    report_arduino_progress("stopping_services", 5)
    was_active = is_spotbot_service_active()
    if was_active: subprocess.run(["sudo", "systemctl", "stop", "spotbot.service"], timeout=15)
    try:
        import glob
        if not _ensure_arduino_cli(): report_arduino_progress("failed_no_cli", 0); return
        if not _ensure_arduino_core(): report_arduino_progress("failed_no_core", 0); return
        _ensure_arduino_lib("SparkFun BNO08x Cortex Based IMU")
        _ensure_arduino_lib("Servo")
        port = None
        try:
            import serial.tools.list_ports
            for p in serial.tools.list_ports.comports():
                if 'arduino' in (p.description or '').lower() or (p.vid == 0x2341 and p.pid in (0x0010, 0x0042, 0x0043, 0x0044)):
                    port = p.device; break
        except Exception: pass
        if not port:
            for pattern in ['/dev/ttyACM*', '/dev/ttyUSB*']:
                ports = sorted(glob.glob(pattern))
                if ports: port = ports[0]; break
        if not port: report_arduino_progress("failed_no_device", 0); return
        sketch_src = Path(__file__).parent / "arduino" / "spotbot_controller"
        sketch_dest = Path("/opt/spotbot/arduino/spotbot_controller")
        build_path = Path("/tmp/spotbot_arduino_build")
        sketch_dest.parent.mkdir(parents=True, exist_ok=True)
        build_path.mkdir(parents=True, exist_ok=True)
        if sketch_src.exists() and sketch_src.resolve() != sketch_dest.resolve():
            import shutil
            if sketch_dest.exists(): shutil.rmtree(sketch_dest)
            shutil.copytree(sketch_src, sketch_dest)
        try:
            ino_file = sketch_dest / "spotbot_controller.ino"
            if ino_file.exists():
                import re
                content = ino_file.read_text(encoding='utf-8', errors='ignore')
                new_content = re.sub(r'#define\s+SKETCH_VERSION\s+"[^"]*"', f'#define SKETCH_VERSION    "{get_version()}"', content)
                if new_content != content: ino_file.write_text(new_content, encoding='utf-8')
        except Exception: pass
        report_arduino_progress("compiling", 45)
        comp_res = subprocess.run(["arduino-cli", "compile", "--fqbn", "arduino:avr:mega", "--build-path", str(build_path), str(sketch_dest)], capture_output=True, text=True, timeout=300)
        if comp_res.returncode != 0: report_arduino_progress("failed_compilation", 0); return
        report_arduino_progress("flashing", 75)
        upload_res = subprocess.run(["arduino-cli", "upload", "--fqbn", "arduino:avr:mega", "--port", port, "--input-dir", str(build_path)], capture_output=True, text=True, timeout=120)
        if upload_res.returncode != 0: report_arduino_progress("failed_flash", 0); return
        try: ARDUINO_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True); ARDUINO_VERSION_FILE.write_text(get_version())
        except Exception: pass
        report_arduino_progress("idle", 100)
    except Exception as e: report_arduino_progress("failed_error", 0)
    finally:
        if was_active: subprocess.run(["sudo", "systemctl", "start", "spotbot.service"], timeout=15)

def trigger_arduino_flash():
    threading.Thread(target=flash_arduino_task, daemon=True).start()

def fetch_offsets_from_gateway():
    # state.ros2_process
    time.sleep(5)
    try:
        url = f"{GATEWAY_URL}/core/calibration"
        req = urllib.request.Request(url, headers={"X-API-Token": API_TOKEN}, method="GET")
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            offsets = data.get("offsets", [0.0] * 12)
            if len(offsets) != 12: offsets = [0.0] * 12
        CALIBRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CALIBRATION_FILE, "w", encoding="utf-8") as f: json.dump({"offsets": offsets}, f)
        if state.ros2_process and state.ros2_process.stdin and state.ros2_process.stdin.writable():
            state.ros2_process.stdin.write(json.dumps({"type": "motor_calibration", "offsets": offsets}) + "\n")
            state.ros2_process.stdin.flush()
    except Exception as e: print(f"[Agent] Impossible de recuperer les offsets: {e}")

def start_ros2_listener():
    # state.ros2_process / state.latest_telemetry
    cmd = ["bash", "-c", "source /opt/ros2_jazzy/install/setup.bash && source /opt/spotbot/ros2_ws/install/setup.bash && python3 -u /opt/spotbot/ros2_listener.py"]
    try:
        state.ros2_process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        def read_stdout():
            # state.latest_telemetry
            for line in state.ros2_process.stdout:
                try:
                    data = json.loads(line.strip())
                    data["ai_state"] = {"tts": state.tts_target, "stt": state.stt_target, "chat": state.chat_target, "yolo": state.yolo_state, "face_rec": state.face_rec_state}
                    with state._latest_telemetry_lock: state.latest_telemetry = data
                except Exception: pass
        def read_stderr():
            for line in state.ros2_process.stderr: print(f"[Agent - Listener Error] {line.strip()}")
        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()
    except Exception as e: print(f"[Agent] Erreur demarrage ros2_listener: {e}")

def get_arduino_stable_state() -> bool:
    global arduino_stable_state, arduino_miss_count, arduino_hit_count
    raw = is_arduino_connected()
    if raw:
        arduino_miss_count = 0; arduino_hit_count += 1
        if arduino_hit_count >= ARDUINO_DEBOUNCE_THRESHOLD: arduino_stable_state = True; arduino_hit_count = ARDUINO_DEBOUNCE_THRESHOLD
    else:
        arduino_hit_count = 0; arduino_miss_count += 1
        if arduino_miss_count >= ARDUINO_DEBOUNCE_THRESHOLD: arduino_stable_state = False; arduino_miss_count = ARDUINO_DEBOUNCE_THRESHOLD
    return arduino_stable_state

def update_status_loop():
    global hibernation_deadline
    while True:
        try:
            active = is_spotbot_service_active(); now = time.time()
            if active: status = "online"; hibernation_deadline = 0
            elif hibernation_deadline == 0: hibernation_deadline = now + HIBERNATION_GRACE_SEC; status = "online"
            elif now < hibernation_deadline: status = "online"
            else: status = "hibernating"
            metrics = get_system_metrics()
            cpu_percent = min(int(metrics.get("cpu_load_1m", 0.0) * 25), 100)
            mapping = camera.get_camera_devices()
            payload = {
                "seen_person": None, "seen_objects": [], "last_chat": [],
                "robot_status": status, "robot_version": get_version(), "arduino_version": get_arduino_version(),
                "camera_mapping": {"left": mapping[1], "right": mapping[2]},
                "sensors": {
                    "cpu_percent": cpu_percent, "ram_percent": metrics.get("ram_percent", 0.0),
                    "temp_c": metrics.get("cpu_temp", 0.0), "spotbot_service_active": active,
                    "system": metrics, "spotbot_service": "active" if active else "inactive",
                    "arduino_connected": get_arduino_stable_state(),
                    "calibration_status": camera.get_calibration_status(), "camera_changed": camera.detect_camera_change(),
                    "available_video_devices": camera.get_active_video_devices(),
                    "camera_mapping": {"left": mapping[1], "right": mapping[2]}
                },
                "ai_state": {"tts": state.tts_target, "stt": state.stt_target, "chat": state.chat_target, "yolo": state.yolo_state, "face_rec": state.face_rec_state}
            }
            req = urllib.request.Request(f"{GATEWAY_URL}/core/state", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "X-API-Token": API_TOKEN}, method="POST")
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=5) as resp: resp.read()
        except Exception as e: print(f"[Agent] Erreur envoi etat : {e}")
        time.sleep(5)

def hourly_update_loop():
    while True:
        time.sleep(3600)
        try:
            if not is_spotbot_service_active(): trigger_updater()
        except Exception as e: print(f"[Agent] Erreur boucle mise a jour : {e}")
