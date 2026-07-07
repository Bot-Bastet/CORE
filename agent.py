"""
Agent temps-réel pour le robot Bastet (Pi 5).
Rapporte la version, l'état des capteurs/système à la Gateway,
et écoute le WebSocket pour déclencher les mises à jour en direct.
"""
import os
import sys
import json
import time
import urllib.request
import subprocess
import threading
import ssl
import re
from pathlib import Path

# NOTE: do NOT call socket.setdefaulttimeout() here. The websockets library
# (used in start_websocket_client) requires non-blocking sockets; a global
# default timeout silently freezes the asyncio event loop, preventing the
# agent from ever receiving WS messages (start_camera, stop_camera, etc.).
# The synchronous REST calls below already pass explicit timeout= args.

# Config
GATEWAY_URL = "https://ha.arthonetwork.fr:44888"
WS_URL = "wss://ha.arthonetwork.fr:44888/ws/robot"
API_TOKEN = "bst_c9f28d3a1e4b85c7f0d4b9a2e6f1c3d5"
VERSION_FILE = Path("/opt/spotbot/version.txt")

# SSL context for self-signed certificates if any
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False  # nosemgrep
ssl_ctx.verify_mode = ssl.CERT_NONE  # nosemgrep

# Global variables for ROS 2 subprocess telemetry
ros2_process = None
latest_telemetry = None

# AI Pipeline state variables
tts_target = "robot"
stt_target = "robot"
chat_target = "robot"
yolo_state = "robot"
face_rec_state = "robot"

def get_version() -> str:
    if VERSION_FILE.exists():
        try:
            return VERSION_FILE.read_text().strip()
        except Exception:
            pass
    return "v0.0.0"

def get_system_metrics() -> dict:
    metrics = {
        "cpu_temp": 0.0,
        "cpu_load_1m": 0.0,
        "ram_total_mb": 0,
        "ram_used_mb": 0,
        "ram_percent": 0.0
    }
    
    # Température CPU
    try:
        temp_raw = open("/sys/class/thermal/thermal_zone0/temp").read().strip()
        metrics["cpu_temp"] = round(int(temp_raw) / 1000.0, 1)
    except Exception:
        pass
        
    # Charge CPU (1 min)
    try:
        load_raw = open("/proc/loadavg").read().strip().split()
        metrics["cpu_load_1m"] = float(load_raw[0])
    except Exception:
        pass
        
    # Mémoire RAM
    try:
        meminfo = {}
        for line in open("/proc/meminfo"):
            parts = line.split()
            if len(parts) >= 2:
                meminfo[parts[0].rstrip(":")] = int(parts[1])
                
        total = meminfo.get("MemTotal", 0) // 1024
        available = meminfo.get("MemAvailable", 0) // 1024
        used = total - available
        
        metrics["ram_total_mb"] = total
        metrics["ram_used_mb"] = used
        metrics["ram_percent"] = round((used / total) * 100.0, 1) if total > 0 else 0.0
    except Exception:
        pass
        
    return metrics

def is_spotbot_service_active() -> bool:
    # The agent IS the service. If this code runs, the agent is alive.
    # Calling systemctl every 5s is slow and causes intermittent POST
    # failures to the Gateway, which makes the dashboard flicker.
    return True

def trigger_updater(version=None):
    print(f"[Agent] Lancement de la mise à jour... (version: {version or 'latest'})")
    try:
        cmd = ["sudo", "python3", "/opt/spotbot/updater.py"]
        if version:
            cmd.append(version)
        subprocess.Popen(cmd)
    except Exception as e:
        print(f"[Agent] Erreur lancement updater : {e}")

CAMERA_MAPPING_FILE = Path("/opt/spotbot/config/camera_mapping.json")

def get_camera_devices() -> dict:
    """Retourne le mapping des caméras avec leurs empreintes.
    Format: {1: {"device": "/dev/video0", "fingerprint": "ABC123"}, 2: ...}
    Compatible avec l'ancien format: {1: "/dev/video0", 2: "/dev/video2"}
    """
    default_mapping = {
        1: {"device": "/dev/video0", "fingerprint": None},
        2: {"device": "/dev/video2", "fingerprint": None}
    }
    if CAMERA_MAPPING_FILE.exists():
        try:
            data = json.loads(CAMERA_MAPPING_FILE.read_text())
            left = data.get("left")
            right = data.get("right")
            # Support both old format (string) and new format (dict with device/fingerprint)
            if left:
                if isinstance(left, dict):
                    default_mapping[1] = left
                else:
                    default_mapping[1] = {"device": left, "fingerprint": None}
            if right:
                if isinstance(right, dict):
                    default_mapping[2] = right
                else:
                    default_mapping[2] = {"device": right, "fingerprint": None}
        except Exception:
            pass
    # Fill in missing fingerprints
    for cam_id in [1, 2]:
        dev_info = default_mapping[cam_id]
        if isinstance(dev_info, dict):
            if not dev_info.get("fingerprint"):
                dev_info["fingerprint"] = get_camera_fingerprint(dev_info["device"])
        else:
            # Old format: just a string
            dev_path = dev_info
            default_mapping[cam_id] = {"device": dev_path, "fingerprint": get_camera_fingerprint(dev_path)}
    return default_mapping

def get_camera_fingerprint(device: str) -> str:
    """Retourne une empreinte unique pour une caméra (USB serial, VID:PID, ou port)."""
    try:
        import subprocess
        # Try udevadm for serial and VID:PID
        r = subprocess.run(
            ["udevadm", "info", "--query=property", "--name="+device],
            capture_output=True, text=True, timeout=3
        )
        if r.returncode == 0:
            props = {}
            for line in r.stdout.splitlines():
                if '=' in line:
                    k, v = line.split('=', 1)
                    props[k] = v
            # Primary: USB serial number
            serial = props.get('ID_SERIAL_SHORT', '')
            if serial:
                return serial
            # Fallback: VID:PID
            vid = props.get('ID_VENDOR_ID', '')
            pid = props.get('ID_MODEL_ID', '')
            if vid and pid:
                return f'{vid}:{pid}'
            # Last resort: devpath
            devpath = props.get('DEVPATH', '')
            if devpath:
                return devpath
    except Exception:
        pass
    # Ultimate fallback: device path itself
    return device

# ─── CALIBRATION STATUS & CAMERA CHANGE DETECTION ────────────────────────────
CALIB_STATUS_FILE = Path("/opt/spotbot/config/calib_status.json")

def get_calibration_status() -> dict:
    """Retourne le statut de calibration pour chaque caméra.
    Format: {1: {"calibrated": True, "fingerprint": "ABC123"}, 2: {...}}
    """
    default = {1: {"calibrated": False, "fingerprint": None}, 2: {"calibrated": False, "fingerprint": None}}
    if CALIB_STATUS_FILE.exists():
        try:
            data = json.loads(CALIB_STATUS_FILE.read_text())
            for cam_id in [1, 2]:
                if str(cam_id) in data:
                    default[cam_id] = data[str(cam_id)]
                elif cam_id in data:
                    default[cam_id] = data[cam_id]
        except Exception:
            pass
    return default

def save_calibration_status(cam_id: int, calibrated: bool, fingerprint: str = None):
    """Sauvegarde le statut de calibration pour une caméra."""
    status = get_calibration_status()
    status[cam_id] = {"calibrated": calibrated, "fingerprint": fingerprint}
    try:
        CALIB_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CALIB_STATUS_FILE.write_text(json.dumps({str(k): v for k, v in status.items()}))
    except Exception as e:
        print(f"[Agent] Erreur sauvegarde calib_status: {e}")

def detect_camera_change() -> dict:
    """Détecte si une caméra a été changée (fingerprint différent).
    Retourne {cam_id: changed_bool, ...} et invalide la calibration si changée.
    """
    status = get_calibration_status()
    mapping = get_camera_devices()
    result = {}
    for cam_id in [1, 2]:
        dev_info = mapping.get(cam_id, {})
        current_fp = dev_info.get("fingerprint") if isinstance(dev_info, dict) else None
        saved_fp = status.get(cam_id, {}).get("fingerprint")
        changed = False
        if current_fp and saved_fp and current_fp != saved_fp:
            changed = True
            # Invalider la calibration
            save_calibration_status(cam_id, False, current_fp)
            print(f"[Agent] ⚠️ Caméra {cam_id} changée (fingerprint: {saved_fp} → {current_fp}). Calibration invalidée.")
        elif current_fp and not saved_fp:
            # Première fois qu'on voit cette caméra — enregistrer le fingerprint
            save_calibration_status(cam_id, status.get(cam_id, {}).get("calibrated", False), current_fp)
        result[cam_id] = changed
    return result

def _list_physical_usb_video_devices() -> list[str]:
    """Return sorted /dev/videoN paths for physically-plugged USB UVC cameras.

    Reads /dev/v4l/by-id/ which only contains entries for cameras the kernel
    actually bound to a v4l2 driver. EXCLUDES UVC metadata endpoints
    ('video-index1', 'metadata' in name) -- those are NOT independent cameras:
    a single physical USB UVC camera creates 2 by-id entries (capture + metadata)
    and would otherwise be counted twice, causing the dashboard to falsely report
    2 cameras when only 1 is plugged.

    Shared by check_camera_connected() and get_active_video_devices() -- DO NOT
    diverge the filter rule between callers.
    """
    out = set()
    by_id_dir = "/dev/v4l/by-id"
    if not os.path.isdir(by_id_dir):
        return []
    try:
        for entry in os.listdir(by_id_dir):
            if not entry.startswith("usb-"):
                continue
            # Filter metadata endpoints (not real cameras)
            if "index1" in entry or "metadata" in entry:
                continue
            link = os.path.join(by_id_dir, entry)
            try:
                real = os.path.realpath(link)
                if real.startswith("/dev/video"):
                    out.add(real)
            except OSError:
                continue
    except OSError:
        pass
    return sorted(out)


def get_active_video_devices() -> list[str]:
    """Return sorted list of /dev/videoN paths bound to currently-plugged USB cameras.

    Reads /dev/v4l/by-id/* which only contains entries for cameras the kernel
    actually bound to a v4l2 driver. Resolves each symlink to its target
    /dev/videoN and dedupes. Returns an EMPTY list when nothing is plugged.
    """
    return _list_physical_usb_video_devices()


def check_camera_connected(cam_id: int) -> bool:
    """Verifie si une camera V4L2 est connectee au systeme.

    Source de verite canonique: _list_physical_usb_video_devices() qui filtre
    les endpoints metadata (video-index1) pour ne PAS compter 2 cameras
    quand une seule est branchee.
    """
    mapping = get_camera_devices()
    dev = mapping.get(cam_id)
    if dev and isinstance(dev, dict):
        dev = dev.get("device", dev)
    if dev and os.path.exists(dev):
        return True
    # Fallback: utiliser _list_physical_usb_video_devices() qui filtre
    # le endpoint metadata video-index1.
    physical_devs = _list_physical_usb_video_devices()
    return cam_id <= len(physical_devs)





def is_arduino_connected() -> bool:
    """Vérifie si le microcontrôleur Arduino Mega est physiquement connecté."""
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            desc = (p.description or '').lower()
            if 'arduino' in desc or (p.vid == 0x2341 and p.pid in (0x0010, 0x0042)):
                return True
    except Exception:
        pass
    import glob
    for pattern in ['/dev/ttyUSB*', '/dev/ttyACM*']:
        if glob.glob(pattern):
            return True
    return False


# ─── ARDUINO FIRMWARE ACTIONS ─────────────────────────────────────────────────

ARDUINO_VERSION_FILE = Path("/opt/spotbot/arduino_version.txt")

def get_arduino_version() -> str:
    robot_ver = get_version()
    if ARDUINO_VERSION_FILE.exists():
        try:
            arduino_ver = ARDUINO_VERSION_FILE.read_text().strip()
            # Si la version du robot est plus récente, forcer la synchro
            if robot_ver != arduino_ver:
                print(f"[Agent] Sync Arduino version: {arduino_ver} -> {robot_ver}")
                ARDUINO_VERSION_FILE.write_text(robot_ver)
                return robot_ver
            return arduino_ver
        except Exception:
            pass
    # Fichier inexistant : créer avec la version robot
    try:
        ARDUINO_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        ARDUINO_VERSION_FILE.write_text(robot_ver)
    except Exception:
        pass
    return robot_ver

def report_arduino_progress(status: str, percent: int):
    try:
        url = f"{GATEWAY_URL}/system/update/arduino/progress"
        req = urllib.request.Request(
            url,
            data=json.dumps({"status": status, "percent": percent}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-API-Token": API_TOKEN
            },
            method="POST"
        )
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=5) as resp:
            resp.read()
    except Exception as e:
        print(f"[Agent] Erreur envoi progrès Arduino : {e}")

def _ensure_arduino_cli() -> bool:
    """Vérifie que arduino-cli est disponible, l'installe sinon."""
    try:
        r = subprocess.run(["arduino-cli", "version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            print(f"[Agent] arduino-cli OK : {r.stdout.strip()}")
            return True
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[Agent] arduino-cli check error: {e}")

    print("[Agent] arduino-cli introuvable — installation...")
    try:
        install = subprocess.run(
            "curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh",
            shell=True, capture_output=True, text=True, timeout=120,  # nosemgrep
            env={**__import__('os').environ, "BINDIR": "/usr/local/bin"}
        )
        if install.returncode == 0:
            print("[Agent] arduino-cli installé.")
            return True
        print(f"[Agent] Echec install arduino-cli: {install.stderr}")
        return False
    except Exception as e:
        print(f"[Agent] Exception install arduino-cli: {e}")
        return False

def _ensure_arduino_core() -> bool:
    """Vérifie que le core arduino:avr est installé."""
    try:
        r = subprocess.run(
            ["arduino-cli", "core", "list"],
            capture_output=True, text=True, timeout=30
        )
        if "arduino:avr" in r.stdout:
            print("[Agent] Core arduino:avr déjà installé.")
            return True
    except Exception:
        pass

    print("[Agent] Installation du core arduino:avr...")
    try:
        r = subprocess.run(
            ["arduino-cli", "core", "update-index"],
            capture_output=True, text=True, timeout=60
        )
        r2 = subprocess.run(
            ["arduino-cli", "core", "install", "arduino:avr"],
            capture_output=True, text=True, timeout=300
        )
        if r2.returncode == 0:
            print("[Agent] Core arduino:avr installé.")
            return True
        print(f"[Agent] Echec install core: {r2.stderr}")
        return False
    except Exception as e:
        print(f"[Agent] Exception install core: {e}")
        return False

def _ensure_arduino_lib(lib_name: str) -> bool:
    """Vérifie qu'une librairie arduino est installée."""
    try:
        r = subprocess.run(
            ["arduino-cli", "lib", "list"],
            capture_output=True, text=True, timeout=30
        )
        if lib_name.lower().replace(" ", "") in r.stdout.lower().replace(" ", ""):
            print(f"[Agent] Lib '{lib_name}' déjà installée.")
            return True
    except Exception:
        pass

    print(f"[Agent] Installation librairie '{lib_name}'...")
    try:
        r = subprocess.run(
            ["arduino-cli", "lib", "install", lib_name],
            capture_output=True, text=True, timeout=120
        )
        if r.returncode == 0:
            print(f"[Agent] Lib '{lib_name}' installée.")
            return True
        print(f"[Agent] Echec install lib: {r.stderr}")
        return False
    except Exception as e:
        print(f"[Agent] Exception install lib: {e}")
        return False

def flash_arduino_task():
    print("[Agent] ═══ Début flash Arduino ═══")
    report_arduino_progress("stopping_services", 5)

    was_active = is_spotbot_service_active()
    if was_active:
        print("[Agent] Arrêt de spotbot.service...")
        subprocess.run(["sudo", "systemctl", "stop", "spotbot.service"], timeout=15)

    try:
        import glob

        # ── 1. Vérification arduino-cli ────────────────────────────────────
        report_arduino_progress("checking_tools", 10)
        if not _ensure_arduino_cli():
            report_arduino_progress("failed_no_cli", 0)
            return

        # ── 2. Core AVR ────────────────────────────────────────────────────
        report_arduino_progress("installing_core", 15)
        if not _ensure_arduino_core():
            report_arduino_progress("failed_no_core", 0)
            return

        # ── 3. Librairies requises ─────────────────────────────────────────
        report_arduino_progress("installing_libs", 20)
        _ensure_arduino_lib("SparkFun BNO08x Cortex Based IMU")
        _ensure_arduino_lib("Servo")

        # ── 4. Détection port Arduino ──────────────────────────────────────
        report_arduino_progress("detecting_device", 25)
        port = None
        try:
            import serial.tools.list_ports
            for p in serial.tools.list_ports.comports():
                desc = (p.description or '').lower()
                if 'arduino' in desc or (p.vid == 0x2341 and p.pid in (0x0010, 0x0042, 0x0043, 0x0044)):
                    port = p.device
                    print(f"[Agent] Arduino détecté via pyserial : {port} (VID={hex(p.vid) if p.vid else 'N/A'})")
                    break
        except Exception as e:
            print(f"[Agent] pyserial error: {e}")

        if not port:
            for pattern in ['/dev/ttyACM*', '/dev/ttyUSB*']:
                ports = sorted(glob.glob(pattern))
                if ports:
                    port = ports[0]
                    print(f"[Agent] Arduino détecté via glob : {port}")
                    break

        if not port:
            print("[Agent] ✗ Aucun Arduino trouvé. Vérifiez le câble USB.")
            report_arduino_progress("failed_no_device", 0)
            return

        # ── 5. Copie du sketch vers le Pi ──────────────────────────────────
        report_arduino_progress("preparing_sketch", 30)
        sketch_src  = Path(__file__).parent / "arduino" / "spotbot_controller"
        sketch_dest = Path("/opt/spotbot/arduino/spotbot_controller")
        build_path  = Path("/tmp/spotbot_arduino_build")

        sketch_dest.parent.mkdir(parents=True, exist_ok=True)
        build_path.mkdir(parents=True, exist_ok=True)

        if sketch_src.exists():
            if sketch_src.resolve() != sketch_dest.resolve():
                import shutil
                if sketch_dest.exists():
                    shutil.rmtree(sketch_dest)
                shutil.copytree(sketch_src, sketch_dest)
                print(f"[Agent] Sketch copié vers {sketch_dest}")
            else:
                print(f"[Agent] Le sketch est déjà à sa destination : {sketch_dest}")
        elif not sketch_dest.exists():
            print(f"[Agent] ✗ Sketch introuvable : {sketch_src} ni {sketch_dest}")
            report_arduino_progress("failed_no_sketch", 0)
            return
        else:
            print(f"[Agent] Utilisation du sketch existant sur {sketch_dest}")

        # Injection dynamique de la version du robot dans le sketch Arduino
        try:
            ino_file = sketch_dest / "spotbot_controller.ino"
            if ino_file.exists():
                version_str = get_version()
                content = ino_file.read_text(encoding='utf-8', errors='ignore')
                import re
                new_content = re.sub(
                    r'#define\s+SKETCH_VERSION\s+"[^"]*"',
                    f'#define SKETCH_VERSION    "{version_str}"',
                    content
                )
                if new_content != content:
                    ino_file.write_text(new_content, encoding='utf-8')
                    print(f"[Agent] Version {version_str} injectée dans {ino_file.name}")
        except Exception as e:
            print(f"[Agent] Erreur injection version sketch: {e}")

        # ── 6. Compilation ─────────────────────────────────────────────────
        report_arduino_progress("compiling", 45)
        print(f"[Agent] Compilation de {sketch_dest}...")
        comp_res = subprocess.run([
            "arduino-cli", "compile",
            "--fqbn", "arduino:avr:mega",
            "--build-path", str(build_path),
            str(sketch_dest)
        ], capture_output=True, text=True, timeout=300)

        if comp_res.returncode != 0:
            print(f"[Agent] ✗ Erreur compilation:\n{comp_res.stderr}")
            report_arduino_progress(f"failed_compilation: {comp_res.stderr[:200]}", 0)
            return
        print("[Agent] ✓ Compilation réussie.")

        # ── 7. Upload ──────────────────────────────────────────────────────
        report_arduino_progress("flashing", 75)
        print(f"[Agent] Upload sur {port}...")
        upload_res = subprocess.run([
            "arduino-cli", "upload",
            "--fqbn", "arduino:avr:mega",
            "--port", port,
            "--input-dir", str(build_path)
        ], capture_output=True, text=True, timeout=120)

        if upload_res.returncode != 0:
            print(f"[Agent] ✗ Erreur upload:\n{upload_res.stderr}")
            report_arduino_progress(f"failed_flash: {upload_res.stderr[:200]}", 0)
            return
        print("[Agent] ✓ Upload réussi.")

        # ── 8. Sauvegarde version ──────────────────────────────────────────
        version = get_version()
        try:
            ARDUINO_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            ARDUINO_VERSION_FILE.write_text(version)
            print(f"[Agent] Version Arduino enregistree : {version}")
        except Exception as e_write:
            print(f"[Agent] Erreur ecriture version Arduino: {e_write}")
        try:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')
            tmp.write(version)
            tmp.close()
            subprocess.run(['sudo', 'mv', tmp.name, str(ARDUINO_VERSION_FILE)], check=True, timeout=5)
            print(f"[Agent] Version Arduino ecrite via sudo: {version}")
        except Exception as e_sudo:
            print(f"[Agent] Echec ecriture version Arduino (meme via sudo): {e_sudo}")
        print(f"[Agent] ✓ Version Arduino enregistrée : {version}")

        report_arduino_progress("idle", 100)
        print("[Agent] ═══ Flash Arduino terminé avec succès ! ═══")

    except subprocess.TimeoutExpired as e:
        print(f"[Agent] ✗ Timeout : {e}")
        report_arduino_progress("failed_timeout", 0)
    except Exception as e:
        print(f"[Agent] ✗ Erreur générale flash : {e}")
        import traceback
        traceback.print_exc()
        report_arduino_progress("failed_error", 0)

    finally:
        if was_active:
            print("[Agent] Redémarrage de spotbot.service...")
            subprocess.run(["sudo", "systemctl", "start", "spotbot.service"], timeout=15)

def trigger_arduino_flash():
    threading.Thread(target=flash_arduino_task, daemon=True).start()

# ─── CALIBRATION OFFSETS SYNC ────────────────────────────────────────────────

CALIBRATION_FILE = Path("/opt/spotbot/config/calibration.json")

def fetch_offsets_from_gateway():
    """Au démarrage, récupère les offsets de calibration depuis la Gateway
    et les sauvegarde localement + les publie au ros2_listener.
    Ainsi, même si les offsets ont été sauvegardés via le dashboard
    pendant que le robot était éteint, ils sont appliqués au boot."""
    global ros2_process  # noqa: F824
    time.sleep(5)  # Attendre que ros2_listener soit prêt
    try:
        url = f"{GATEWAY_URL}/core/calibration"
        req = urllib.request.Request(
            url,
            headers={"X-API-Token": API_TOKEN},
            method="GET"
        )
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            offsets = data.get("offsets", [0.0] * 12)
            if len(offsets) != 12:
                offsets = [0.0] * 12

        # Sauvegarder localement
        CALIBRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CALIBRATION_FILE, "w", encoding="utf-8") as f:
            json.dump({"offsets": offsets}, f)
        print(f"[Agent] Offsets récupérés depuis la Gateway : {offsets}")

        # Publier au ros2_listener pour les appliquer immédiatement
        if ros2_process and ros2_process.stdin and ros2_process.stdin.writable():
            ros2_process.stdin.write(
                json.dumps({"type": "motor_calibration", "offsets": offsets}) + "\n"
            )
            ros2_process.stdin.flush()
            print("[Agent] Offsets transmis au ros2_listener pour application.")
    except Exception as e:
        print(f"[Agent] Impossible de récupérer les offsets depuis la Gateway : {e}")
        print("[Agent] Les offsets locaux existants seront utilisés (si présents).")

# ─── DEFAULT CALIBRATION CHECK ──────────────────────────────────────────────

def _is_default_camera_calib(calib: dict) -> bool:
    """Retourne True si la calibration est la valeur par défaut (non calibrée).
    Supporte les deux formats: dict {rows, cols, data} ET flat list."""
    cm = calib.get("camera_matrix", {})
    # Extraire fx, fy
    if isinstance(cm, list):
        fx = cm[0] if len(cm) > 0 else 600.0
        fy = cm[4] if len(cm) > 4 else 600.0
    elif isinstance(cm, dict):
        data = cm.get("data", [600.0, 0.0, 320.0, 0.0, 600.0, 240.0, 0.0, 0.0, 1.0])
        fx = data[0] if len(data) > 0 else 600.0
        fy = data[4] if len(data) > 4 else 600.0
    else:
        return True

    # Si fx/fy sont exactement 600.0 → suspect default
    if abs(fx - 600.0) < 0.1 and abs(fy - 600.0) < 0.1:
        # Vérifier aussi la distortion: si tout est à zéro → défaut confirmé
        dc = calib.get("distortion_coefficients", {})
        if isinstance(dc, list):
            ddata = dc
        elif isinstance(dc, dict):
            ddata = dc.get("data", [0.0, 0.0, 0.0, 0.0, 0.0])
        else:
            ddata = [0.0] * 5
        if all(abs(d) < 0.001 for d in ddata[:5]):
            return True
    return False


# ─── CAMERA CALIBRATION SYNC FROM GATEWAY ───────────────────────────────────

CAMERA_CALIB_LEFT_FILE = Path("/opt/spotbot/config/camera_stereo_left.yaml")
CAMERA_CALIB_RIGHT_FILE = Path("/opt/spotbot/config/camera_stereo_right.yaml")
CAMERA_CALIB_MONO_FILE = Path("/opt/spotbot/config/camera_calibration.yaml")

def _json_calib_to_yaml(calib: dict) -> str:
    """Convertit un dict JSON de calibration caméra en YAML ROS camera_info.
    Format multi-ligne indenté, compatible yaml-cpp (usb_cam)."""
    def fmt_num(x):
        """Formate un nombre: entier si pas de partie décimale, sinon float."""
        if isinstance(x, float) and x == int(x):
            return f"{int(x)}.0"
        return str(float(x)) if isinstance(x, (int, float)) else str(x)

    def fmt_matrix(rows, cols, data, indent=4):
        """Formate une matrice en multi-ligne indentée (style ROS usb_cam)."""
        lines = []
        for r in range(rows):
            row_data = [fmt_num(data[r * cols + c]) for c in range(cols)]
            prefix = " " * indent
            lines.append(f"{prefix}{', '.join(row_data)}")
        return ",\n".join(lines)

    lines = ["%YAML:1.0"]
    lines.append(f"image_width: {calib.get('image_width', 640)}")
    lines.append(f"image_height: {calib.get('image_height', 480)}")
    lines.append(f"camera_name: {calib.get('camera_name', 'usb_cam')}")
    lines.append("")

    # camera_matrix
    cm = calib.get("camera_matrix", {})
    lines.append("camera_matrix:")
    lines.append(f"  rows: {cm.get('rows', 3)}")
    lines.append(f"  cols: {cm.get('cols', 3)}")
    data = cm.get("data", [600.0, 0.0, 320.0, 0.0, 600.0, 240.0, 0.0, 0.0, 1.0])
    lines.append(f"  data: [{fmt_matrix(3, 3, data)}]")
    lines.append("")

    lines.append(f"distortion_model: {calib.get('distortion_model', 'plumb_bob')}")
    lines.append("")

    # distortion_coefficients
    dc = calib.get("distortion_coefficients", {})
    lines.append("distortion_coefficients:")
    lines.append(f"  rows: {dc.get('rows', 1)}")
    lines.append(f"  cols: {dc.get('cols', 5)}")
    ddata = dc.get("data", [0.0, 0.0, 0.0, 0.0, 0.0])
    lines.append(f"  data: [{fmt_matrix(1, 5, ddata)}]")
    lines.append("")

    # rectification_matrix
    rm = calib.get("rectification_matrix", {})
    lines.append("rectification_matrix:")
    lines.append(f"  rows: {rm.get('rows', 3)}")
    lines.append(f"  cols: {rm.get('cols', 3)}")
    rdata = rm.get("data", [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0])
    lines.append(f"  data: [{fmt_matrix(3, 3, rdata)}]")
    lines.append("")

    # projection_matrix
    pm = calib.get("projection_matrix", {})
    lines.append("projection_matrix:")
    lines.append(f"  rows: {pm.get('rows', 3)}")
    lines.append(f"  cols: {pm.get('cols', 4)}")
    pdata = pm.get("data", [600.0, 0.0, 320.0, 0.0, 0.0, 600.0, 240.0, 0.0, 0.0, 0.0, 1.0, 0.0])
    lines.append(f"  data: [{fmt_matrix(3, 4, pdata)}]")
    lines.append("")

    return "\n".join(lines)


def fetch_camera_cals_from_gateway():
    """Au démarrage, récupère les calibrations caméra depuis la Gateway
    et les sauvegarde en YAML sur le robot.
    Ainsi, les calibrations sauvegardées via le dashboard sont appliquées
    au prochain boot, même si le robot était éteint lors de la sauvegarde."""
    time.sleep(7)  # Attendre que le réseau + ros2_listener soient prêts

    # Caméra 1 : fetch une seule fois, écrit dans mono ET stereo_left
    try:
        url = f"{GATEWAY_URL}/core/camera/calibration/1"
        req = urllib.request.Request(
            url,
            headers={"X-API-Token": API_TOKEN},
            method="GET"
        )
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            calib1 = json.loads(resp.read().decode("utf-8"))

        yaml1 = _json_calib_to_yaml(calib1)
        for path in [CAMERA_CALIB_MONO_FILE, CAMERA_CALIB_LEFT_FILE]:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(yaml1)
        print(f"[Agent] Calibration caméra 1 récupérée → camera_calibration.yaml + camera_stereo_left.yaml")
        # Marquer caméra 1 comme calibrée (sauf si valeurs par défaut)
        is_default = _is_default_camera_calib(calib1)
        mapping = get_camera_devices()
        fp = mapping.get(1, {}).get("fingerprint") if isinstance(mapping.get(1), dict) else None
        save_calibration_status(1, not is_default, fp)
        if is_default:
            print("[Agent] Calibration caméra 1: valeurs par défaut → marquée NON calibrée.")
    except Exception as e:
        print(f"[Agent] Impossible de récupérer la calibration caméra 1: {e}")
        print(f"[Agent] La calibration locale existante sera utilisée (si présente).")

    # Caméra 2 : fetch séparé (peut avoir des paramètres différents)
    try:
        url = f"{GATEWAY_URL}/core/camera/calibration/2"
        req = urllib.request.Request(
            url,
            headers={"X-API-Token": API_TOKEN},
            method="GET"
        )
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            calib2 = json.loads(resp.read().decode("utf-8"))

        yaml2 = _json_calib_to_yaml(calib2)
        CAMERA_CALIB_RIGHT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CAMERA_CALIB_RIGHT_FILE, "w", encoding="utf-8") as f:
            f.write(yaml2)
        print(f"[Agent] Calibration caméra 2 récupérée → camera_stereo_right.yaml")
        # Marquer caméra 2 comme calibrée (sauf si valeurs par défaut)
        is_default = _is_default_camera_calib(calib2)
        mapping = get_camera_devices()
        fp = mapping.get(2, {}).get("fingerprint") if isinstance(mapping.get(2), dict) else None
        save_calibration_status(2, not is_default, fp)
        if is_default:
            print("[Agent] Calibration caméra 2: valeurs par défaut → marquée NON calibrée.")
    except Exception as e:
        print(f"[Agent] Impossible de récupérer la calibration caméra 2: {e}")
        print(f"[Agent] La calibration locale existante sera utilisée (si présente).")

    # Stéréo : fetch la calibration stéréo complète (R, T, baseline, etc.)
    try:
        url = f"{GATEWAY_URL}/core/camera/calibration/stereo"
        req = urllib.request.Request(url, headers={"X-API-Token": API_TOKEN}, method="GET")
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            stereo_calib = json.loads(resp.read().decode("utf-8"))
        if stereo_calib.get("is_calibrated"):
            try:
                from camera import _json_calib_to_yaml_stereo, CAMERA_CALIB_STEREO_FILE
                yml = _json_calib_to_yaml_stereo(stereo_calib)
                CAMERA_CALIB_STEREO_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(CAMERA_CALIB_STEREO_FILE, "w", encoding="utf-8") as f:
                    f.write(yml)
                print(f"[Agent] Calibration stéréo récupérée → {CAMERA_CALIB_STEREO_FILE}")
            except ImportError:
                pass
    except Exception as e:
        print(f"[Agent] Impossible de récupérer la calibration stéréo: {e}")

# ─── ROS 2 TELEMETRY SUBPROCESS ───────────────────────────────────────────────

def start_ros2_listener():
    global ros2_process, latest_telemetry  # noqa: F824
    cmd = [
        "bash", "-c",
        "source /opt/ros2_jazzy/install/setup.bash && source /opt/spotbot/ros2_ws/install/setup.bash && python3 -u /opt/spotbot/ros2_listener.py"
    ]
    try:
        print("[Agent] Démarrage du subprocess ros2_listener...")
        ros2_process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, # Capture stderr for debugging
            text=True,
            bufsize=1
        )
        
        def read_stdout():
            global latest_telemetry
            for line in ros2_process.stdout:
                try:
                    data = json.loads(line.strip())
                    # Only print once in a while to not flood the journal
                    if int(time.time()) % 10 == 0:
                        print(f"[Agent] Télémétrie reçue avec succès du listener: {list(data.keys())}")
                    data["ai_state"] = {
                        "tts": tts_target,
                        "stt": stt_target,
                        "chat": chat_target,
                        "yolo": yolo_state,
                        "face_rec": face_rec_state
                    }
                    latest_telemetry = data
                except Exception as e:
                    print(f"[Agent] Erreur décodage ligne de télémétrie: {e}. Ligne brute: {line.strip()[:100]}")
                    
        def read_stderr():
            for line in ros2_process.stderr:
                print(f"[Agent - Listener Error] {line.strip()}")
                
        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()
    except Exception as e:
        print(f"[Agent] Erreur démarrage ros2_listener: {e}")

# ─── WIFI UTILS ───────────────────────────────────────────────────────────────

def get_wifi_list() -> list:
    import re
    try:
        # Trigger scan and capture stdout
        subprocess.run(["sudo", "iwlist", "wlan0", "scan"], capture_output=True, text=True, timeout=10)
        res = subprocess.run(["sudo", "iwlist", "wlan0", "scan"], capture_output=True, text=True, timeout=10)
        
        networks = []
        current_network = {}
        
        for line in res.stdout.split('\n'):
            line = line.strip()
            if not line:
                continue
                
            cell_match = re.search(r'Cell \d+ - Address: ([0-9A-Fa-f:]+)', line)
            if cell_match:
                if current_network.get("ssid"):
                    networks.append(current_network)
                current_network = {
                    "bssid": cell_match.group(1),
                    "ssid": "",
                    "signal": 0,
                    "security": "Open"
                }
                continue
                
            if not current_network:
                continue
                
            essid_match = re.search(r'ESSID:"([^"]*)"', line)
            if essid_match:
                current_network["ssid"] = essid_match.group(1)
                continue
                
            signal_match = re.search(r'Quality=(\d+)/(\d+)', line)
            if signal_match:
                q_cur = int(signal_match.group(1))
                q_max = int(signal_match.group(2))
                current_network["signal"] = int((q_cur / q_max) * 100) if q_max > 0 else 0
                continue
                
            enc_match = re.search(r'Encryption key:(on|off)', line)
            if enc_match:
                if enc_match.group(1) == "off":
                    current_network["security"] = "Open"
                else:
                    current_network["security"] = "Secured"
                continue
                
            if "WPA2" in line or "802.11i" in line:
                current_network["security"] = "WPA2"
            elif "WPA" in line:
                if current_network["security"] != "WPA2":
                    current_network["security"] = "WPA"
                    
        if current_network.get("ssid"):
            networks.append(current_network)
            
        # Deduplicate SSIDs, keeping the strongest signal
        unique_networks = {}
        for net in networks:
            ssid = net["ssid"]
            if not ssid:
                continue
            if ssid not in unique_networks or net["signal"] > unique_networks[ssid]["signal"]:
                unique_networks[ssid] = net
                
        # Sort by signal strength (highest first)
        sorted_networks = sorted(list(unique_networks.values()), key=lambda x: x["signal"], reverse=True)
        return sorted_networks
    except Exception as e:
        print(f"[Agent] Erreur scan wifi wpa: {e}")
        return []

def connect_to_wifi(ssid: str, password: str) -> dict:
    try:
        if password and len(password) < 8:
            return {"status": "error", "message": "Le mot de passe WPA/WPA2 doit faire au moins 8 caractères."}
            
        conf_path = "/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"
        content = ""
        if os.path.exists(conf_path):
            with open(conf_path, "r") as f:
                content = f.read()
                
        # Check if SSID exists in config
        ssid_exists = False
        ssids = []
        for line in content.splitlines():
            line = line.strip()
            if "=" in line:
                parts = line.split("=", 1)
                if parts[0].strip() == "ssid":
                    ssids.append(parts[1].strip().strip("\"'"))
        if ssid in ssids:
            ssid_exists = True

        if not password and ssid_exists:
            # Reconfigure first to make sure wpa_supplicant knows about all configured networks
            subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "reconfigure"], check=True)
            # Find the network id from wpa_cli list_networks
            res_list = subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "list_networks"], capture_output=True, text=True)
            net_id = None
            for line in res_list.stdout.split("\n"):
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1].strip("\"'") == ssid:
                    net_id = parts[0]
                    break
            
            if net_id is not None:
                # Select the network to force association
                subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "select_network", net_id], check=True)
            else:
                subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "enable_network", "all"], capture_output=True)
                return {"status": "error", "message": f"Réseau enregistré '{ssid}' introuvable dans wpa_cli."}
        else:
            # Strip existing blocks for this SSID and write the new one
            blocks = content.split("network={")
            new_blocks = [blocks[0]]
            for block in blocks[1:]:
                brace_idx = block.find("}")
                if brace_idx != -1:
                    block_content = block[:brace_idx]
                    rest = block[brace_idx:]
                    if f'ssid="{ssid}"' in block_content or f"ssid='{ssid}'" in block_content:
                        new_blocks[0] += rest.lstrip("}").lstrip("\n")
                        continue
                new_blocks.append("network={" + block)
                
            new_content = "".join(new_blocks).strip() + "\n\n"
            if password:
                new_network = f'network={{\n\tssid="{ssid}"\n\tpsk="{password}"\n}}\n'
            else:
                new_network = f'network={{\n\tssid="{ssid}"\n\tkey_mgmt=NONE\n}}\n'
                
            new_content += new_network
            
            # Write to both configs
            for p in ["/etc/wpa_supplicant/wpa_supplicant-wlan0.conf", "/etc/wpa_supplicant/wpa_supplicant.conf"]:
                try:
                    with open(p, "w") as f:
                        f.write(new_content)
                    os.chmod(p, 0o600)
                except Exception as e_write:
                    print(f"[Agent] Erreur ecriture {p}: {e_write}")
                
            # Reconfigure wpa_supplicant
            subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "reconfigure"], check=True)
            
            # Find net_id to select it
            res_list = subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "list_networks"], capture_output=True, text=True)
            net_id = None
            for line in res_list.stdout.split("\n"):
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1].strip("\"'") == ssid:
                    net_id = parts[0]
                    break
            if net_id is not None:
                subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "select_network", net_id], check=True)
        
        # Wait up to 12s for connection to establish
        for _ in range(12):
            res = subprocess.run(["ip", "addr", "show", "wlan0"], capture_output=True, text=True)
            if "inet " in res.stdout:
                # Ensure we also enable all other configured networks so failover works later
                subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "enable_network", "all"], capture_output=True)
                return {"status": "success", "message": f"Connecté à {ssid} avec succès."}
            time.sleep(1)
            
        # Re-enable all networks if connection timed out
        subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "enable_network", "all"], capture_output=True)
        return {"status": "error", "message": f"Délai d'obtention IP dépassé pour {ssid}."}
    except Exception as e:
        subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "enable_network", "all"], capture_output=True)
        return {"status": "error", "message": str(e)}

def forget_wifi_network(ssid: str) -> dict:
    try:
        conf_path = "/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"
        content = ""
        if os.path.exists(conf_path):
            with open(conf_path, "r") as f:
                content = f.read()
                
        blocks = content.split("network={")
        new_blocks = [blocks[0]]
        removed = False
        for block in blocks[1:]:
            brace_idx = block.find("}")
            if brace_idx != -1:
                block_content = block[:brace_idx]
                rest = block[brace_idx:]
                if f'ssid="{ssid}"' in block_content or f"ssid='{ssid}'" in block_content:
                    new_blocks[0] += rest.lstrip("}").lstrip("\n")
                    removed = True
                    continue
            new_blocks.append("network={" + block)
            
        if not removed:
            return {"status": "error", "message": f"Réseau '{ssid}' non trouvé."}
            
        new_content = "".join(new_blocks).strip() + "\n"
        
        # Write to both configs
        for p in ["/etc/wpa_supplicant/wpa_supplicant-wlan0.conf", "/etc/wpa_supplicant/wpa_supplicant.conf"]:
            try:
                with open(p, "w") as f:
                    f.write(new_content)
                os.chmod(p, 0o600)
            except Exception as e_write:
                print(f"[Agent] Erreur ecriture {p}: {e_write}")
            
        # Reconfigure wpa_supplicant
        subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "reconfigure"], check=True)
        subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "enable_network", "all"], capture_output=True)
        
        return {"status": "success", "message": f"Réseau '{ssid}' oublié avec succès."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ─── MAIN LOOPS ───────────────────────────────────────────────────────────────

# ─── Arduino Debounce ──────────────────────────────────────────────────────
arduino_stable_state = True    # current stable state reported to dashboard
arduino_miss_count = 0         # consecutive checks where is_arduino_connected() returned False
arduino_hit_count = 0          # consecutive checks where it returned True (to come back online)
ARDUINO_DEBOUNCE_THRESHOLD = 3 # number of consecutive checks before flipping state

# ─── Hibernation Grace Period ─────────────────────────────────────────────
hibernation_deadline = 0  # timestamp after which hibernation is confirmed
HIBERNATION_GRACE_SEC = 30  # wait 30s before declaring hibernation

def get_arduino_stable_state() -> bool:
    """Return the debounced Arduino connection state.
    Uses hysteresis: requires ARDUINO_DEBOUNCE_THRESHOLD consecutive
    identical readings before flipping the stable state."""
    global arduino_stable_state, arduino_miss_count, arduino_hit_count
    raw = is_arduino_connected()
    if raw:
        arduino_miss_count = 0
        arduino_hit_count += 1
        if arduino_hit_count >= ARDUINO_DEBOUNCE_THRESHOLD:
            arduino_stable_state = True
            arduino_hit_count = ARDUINO_DEBOUNCE_THRESHOLD  # cap
    else:
        arduino_hit_count = 0
        arduino_miss_count += 1
        if arduino_miss_count >= ARDUINO_DEBOUNCE_THRESHOLD:
            arduino_stable_state = False
            arduino_miss_count = ARDUINO_DEBOUNCE_THRESHOLD  # cap
    return arduino_stable_state

def update_status_loop():
    global hibernation_deadline
    print("[Agent] Démarrage du rapport d'état périodique...")
    while True:
        try:
            active = is_spotbot_service_active()
            now = time.time()
            if active:
                status = "online"
                hibernation_deadline = 0  # reset grace period
            elif hibernation_deadline == 0:
                # Service just stopped → start grace period
                hibernation_deadline = now + HIBERNATION_GRACE_SEC
                status = "online"  # still show online during grace period
            elif now < hibernation_deadline:
                # Still within grace period
                status = "online"
            else:
                # Grace period expired → confirmed hibernation
                status = "hibernating"
            
            metrics = get_system_metrics()
            cpu_percent = min(int(metrics.get("cpu_load_1m", 0.0) * 25), 100)

            mapping = get_camera_devices()
            payload = {
                "seen_person": None,
                "seen_objects": [],
                "last_chat": [],
                "robot_status": status,
                "robot_version": get_version(),
                "arduino_version": get_arduino_version(),
                "camera_mapping": {
                    "left": mapping[1],
                    "right": mapping[2]
                },
                "sensors": {
                    "cpu_percent": cpu_percent,
                    "ram_percent": metrics.get("ram_percent", 0.0),
                    "temp_c": metrics.get("cpu_temp", 0.0),
                    "spotbot_service_active": active,
                    "system": metrics,
                    "spotbot_service": "active" if active else "inactive",
                    "cam1_connected": check_camera_connected(1),
                    "cam2_connected": check_camera_connected(2),
                    "arduino_connected": get_arduino_stable_state(),
                    "calibration_status": get_calibration_status(),
                    "camera_changed": detect_camera_change(),
                    "available_video_devices": get_active_video_devices()
                },
                "ai_state": {
                    "tts": tts_target,
                    "stt": stt_target,
                    "chat": chat_target,
                    "yolo": yolo_state,
                    "face_rec": face_rec_state
                }
            }
            
            # Log ce qui est sur le point d'être posté (DEBUG)
            print(f"[Agent] POST status={status}, active={active}, arduino={payload['sensors']['arduino_connected']}")
            
            req = urllib.request.Request(
                f"{GATEWAY_URL}/core/state",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-API-Token": API_TOKEN
                },
                method="POST"
            )
            
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=5) as resp:
                resp.read()
                print(f"[Agent] POST OK status={status}")
                
        except Exception as e:
            print(f"[Agent] Erreur envoi état : {e}")
            
        time.sleep(5)

def hourly_update_loop():
    print("[Agent] Démarrage de la surveillance des mises à jour (toutes les heures)...")
    while True:
        time.sleep(3600)
        try:
            if not is_spotbot_service_active():
                print("[Agent] Mode hibernation détecté. Vérification horaire de mise à jour...")
                trigger_updater()
        except Exception as e:
            print(f"[Agent] Erreur boucle mise à jour : {e}")

def start_websocket_client():
    try:
        import websockets
        import asyncio
    except ImportError:
        print("[Agent] Module 'websockets' absent. Tentative d'installation automatique...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "websockets"], check=True)
            import websockets
            import asyncio
        except Exception as e:
            print(f"[Agent] Impossible d'installer 'websockets' : {e}. Le WebSocket ne sera pas actif.")
            return

    async def ws_loop():
        global tts_target, stt_target, chat_target, yolo_state, face_rec_state, ros2_process  # noqa: F824
        uri = f"{WS_URL}?token={API_TOKEN}"
        while True:
            try:
                print(f"[Agent] Connexion WebSocket vers {WS_URL}...")
                async with websockets.connect(uri, ssl=ssl_ctx) as ws:
                    print("[Agent] Connecté au WebSocket de la Gateway.")
                    await ws.send(json.dumps({"type": "chat", "text": f"Bastet Agent {get_version()} connecté."}))
                    
                    # Concurrently broadcast telemetry data
                    async def send_telemetry_loop():
                        while True:
                            global latest_telemetry  # noqa: F824
                            if latest_telemetry:
                                try:
                                    # Inject fresh sensor booleans (overrides stale ROS-only payload so dashboard sees live state).
                                    # Probes run in worker threads so blocking V4L syscalls do NOT stall the asyncio loop.
                                    # return_exceptions=True → a single USB hiccup kills one probe only, the other probe still publishes.
                                    # No spread of prev_sensors: keeps us from resurrecting stale non-camera fields set by ros2.
                                    try:
                                        cam1_res, cam2_res = await asyncio.gather(
                                            asyncio.to_thread(check_camera_connected, 1),
                                            asyncio.to_thread(check_camera_connected, 2),
                                            return_exceptions=True,
                                        )
                                        cam1 = cam1_res if not isinstance(cam1_res, BaseException) else False
                                        cam2 = cam2_res if not isinstance(cam2_res, BaseException) else False
                                    except Exception as probe_err:
                                        print(f"[Agent - WS Send] Probes cam échouées: {probe_err}")
                                        cam1 = False
                                        cam2 = False
                                    # setdefault().update() instead of `[key] = {...}`:
                                    # the previous code wiped available_video_devices and
                                    # every other sensor field the REST POST loop set 5s
                                    # earlier. The new pattern only touches cam1/cam2 and
                                    # the canonical USB device list (which the gateway's
                                    # normalize_camera_manifest + _camera_manifest trust
                                    # over the booleans if present).
                                    latest_telemetry.setdefault("sensors", {}).update({
                                        "cam1_connected": bool(cam1),
                                        "cam2_connected": bool(cam2),
                                        "available_video_devices": get_active_video_devices(),
                                    })

                                    # Print debug once in a while
                                    if int(time.time()) % 10 == 0:
                                        print(f"[Agent - WS Send] Envoi télémétrie périodique: {list(latest_telemetry.keys())}")
                                    await ws.send(json.dumps(latest_telemetry))
                                except Exception as e:
                                    print(f"[Agent - WS Send] Erreur lors de l'envoi WebSocket: {e}")
                                    break
                            await asyncio.sleep(0.5)
                            
                    telemetry_task = asyncio.create_task(send_telemetry_loop())
                    
                    try:
                        while True:
                            msg = await ws.recv()
                            try:
                                data = json.loads(msg)
                                msg_type = data.get("type")
                                
                                if msg_type == "trigger_update":
                                    version = data.get("version")
                                    print(f"[Agent] Commande de mise à jour reçue ! (version: {version or 'latest'})")
                                    trigger_updater(version)
                                    
                                elif msg_type == "trigger_arduino_flash":
                                    print("[Agent] Commande de flash Arduino reçue !")
                                    trigger_arduino_flash()
                                    
                                elif msg_type == "start_robot":
                                    print("[Agent] Commande de démarrage du robot reçue !")
                                    subprocess.run(["sudo", "systemctl", "start", "spotbot.service"])
                                    
                                elif msg_type == "stop_robot":
                                    print("[Agent] Commande d'arrêt du robot reçue !")
                                    subprocess.run(["sudo", "systemctl", "stop", "spotbot.service"])
                                    
                                elif msg_type == "start_camera":
                                    cam = data.get("camera", 1)
                                    v_slam = data.get("v_slam", False)
                                    # V-SLAM gatekeeping: vérifier calibration
                                    if v_slam:
                                        cal_status = get_calibration_status()
                                        cam_cal = cal_status.get(cam, {})
                                        if not cam_cal.get("calibrated", False):
                                            await ws.send(json.dumps({
                                                "type": "vslam_blocked",
                                                "camera": cam,
                                                "reason": "Calibration requise avant V-SLAM. Calibrez la caméra dans Arduino & Calib."
                                            }))
                                            print(f"[Agent] ⚠️ V-SLAM bloqué pour caméra {cam}: calibration invalide.")
                                            continue
                                    if ros2_process and ros2_process.stdin:
                                        ros2_process.stdin.write(json.dumps(data) + "\n")
                                        ros2_process.stdin.flush()
                                        print(f"[Agent] Start camera {cam} déléguée au ros2_listener.")
                                    
                                elif msg_type == "stop_camera":
                                    cam = data.get("camera", 1)
                                    if ros2_process and ros2_process.stdin:
                                        ros2_process.stdin.write(json.dumps(data) + "\n")
                                        ros2_process.stdin.flush()
                                        print(f"[Agent] Stop camera {cam} déléguée au ros2_listener.")
                                elif msg_type == "query_camera_resolutions":
                                    cam = data.get("camera", 1)
                                    devices = get_camera_devices()
                                    dev = devices.get(cam)
                                    resolutions = []
                                    if dev and os.path.exists(dev):
                                        try:
                                            result = subprocess.run(
                                                ["v4l2-ctl", "--list-formats-ext", "-d", dev],
                                                capture_output=True, text=True, timeout=5
                                            )
                                            if result.returncode == 0:
                                                seen = set()
                                                for line in result.stdout.split(chr(10)):
                                                    m = re.search(r'Size: Discrete (\d+)x(\d+)', line)
                                                    if m:
                                                        w, h = m.groups()
                                                        fmt = f'{w}x{h}'
                                                        if fmt not in seen:
                                                            seen.add(fmt)
                                                            resolutions.append(fmt)
                                            if not resolutions:
                                                try:
                                                    r2 = subprocess.run(
                                                        ["ffprobe", "-f", "v4l2", "-list_formats", "all", "-i", dev],
                                                        capture_output=True, text=True, timeout=5
                                                    )
                                                    seen = set()
                                                    for line in r2.stderr.split(chr(10)):
                                                        m = re.search(r'(\d+)x(\d+)', line)
                                                        if m:
                                                            w, h = m.groups()
                                                            fmt = f'{w}x{h}'
                                                            if fmt not in seen:
                                                                seen.add(fmt)
                                                                resolutions.append(fmt)
                                                except:
                                                    pass
                                        except Exception as e:
                                            print(f'[Agent] Erreur détection résolutions cam {cam}: {e}')
                                    if not resolutions:
                                        resolutions = ["640x480", "1280x720", "1920x1080", "640x360", "320x240"]
                                    await ws.send(json.dumps({
                                        "type": "camera_resolutions",
                                        "camera": cam,
                                        "resolutions": resolutions
                                    }))
                                    
                                elif msg_type == "motor_calibration":
                                    print("[Agent] Commande de calibration reçue !")
                                    if ros2_process and ros2_process.stdin:
                                        ros2_process.stdin.write(json.dumps(data) + "\n")
                                        ros2_process.stdin.flush()

                                elif msg_type == "arduino_cmd":
                                    cmd = data.get("cmd", "")
                                    print(f"[Agent] Commande Arduino reçue : {cmd}")
                                    if ros2_process and ros2_process.stdin:
                                        # FIX: forward le message COMPLET (index, angle, etc.) au lieu de juste {"type":"arduino_cmd","cmd":cmd}
                                        ros2_process.stdin.write(json.dumps(data) + "\n")
                                        ros2_process.stdin.flush()
                                        
                                elif msg_type == "manual_joint_control":
                                    print("[Agent] Commande de contrôle manuel des articulations reçue !")
                                    if ros2_process and ros2_process.stdin:
                                        ros2_process.stdin.write(json.dumps(data) + "\n")
                                        ros2_process.stdin.flush()
                                        
                                elif msg_type == "scan_wifi":
                                    print("[Agent] Commande de scan WiFi reçue !")
                                    networks = get_wifi_list()
                                    known_ssids = []
                                    known_passwords = {}
                                    conf_path = "/etc/wpa_supplicant/wpa_supplicant.conf"
                                    if os.path.exists(conf_path):
                                        try:
                                            with open(conf_path, "r") as f:
                                                content = f.read()
                                            # Parse all network blocks to extract ssid + psk
                                            import re as _re
                                            for block in _re.findall(r'network\s*=\s*\{([^}]*)\}', content, _re.DOTALL):
                                                ssid_m = _re.search(r'ssid\s*=\s*"([^"]+)"', block)
                                                psk_m  = _re.search(r'psk\s*=\s*"([^"]+)"', block)
                                                if ssid_m:
                                                    s = ssid_m.group(1)
                                                    if s not in known_ssids:
                                                        known_ssids.append(s)
                                                    if psk_m:
                                                        known_passwords[s] = psk_m.group(1)
                                        except Exception as e_wpa:
                                            print(f"[Agent] Erreur lecture wpa_supplicant.conf : {e_wpa}")
                                    # Get current connected SSID
                                    current_ssid = ""
                                    try:
                                        res_cur = subprocess.run(
                                            ["sudo", "iwgetid", "wlan0", "--raw"],
                                            capture_output=True, text=True, timeout=5
                                        )
                                        current_ssid = res_cur.stdout.strip()
                                    except Exception:
                                        pass
                                    await ws.send(json.dumps({
                                        "type": "wifi_list",
                                        "networks": networks,
                                        "known_ssids": known_ssids,
                                        "known_passwords": known_passwords,
                                        "current_ssid": current_ssid
                                    }))
                                    
                                elif msg_type == "connect_wifi":
                                    ssid = data.get("ssid")
                                    password = data.get("password")
                                    print(f"[Agent] Connexion WiFi vers {ssid}...")
                                    res = connect_to_wifi(ssid, password)
                                    await ws.send(json.dumps({"type": "wifi_connect_result", **res}))
                                elif msg_type == "forget_wifi":
                                    ssid = data.get("ssid")
                                    print(f"[Agent] Commande d'oubli WiFi pour {ssid}...")
                                    res = forget_wifi_network(ssid)
                                    await ws.send(json.dumps({"type": "wifi_forget_result", "ssid": ssid, **res}))

                                elif msg_type == "save_camera_mapping":
                                    left = data.get("left")
                                    right = data.get("right")
                                    print(f"[Agent] Sauvegarde du mapping caméra: left={left}, right={right}")
                                    try:
                                        CAMERA_MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
                                        with open(str(CAMERA_MAPPING_FILE), "w") as f_map:
                                            json.dump({"left": left, "right": right}, f_map)
                                        # NOTE: do NOT restart spotbot.service here. With
                                        # KillMode=mixed the cgroup cascade would tear down
                                        # streaming_engine + every ros2 process every time
                                        # the dashboard saves a mapping. The mapping file
                                        # is now picked up by the engine on its next
                                        # _check_hotplug tick (or on next boot).
                                        await ws.send(json.dumps({"type": "camera_mapping_saved", "status": "ok"}))
                                    except Exception as e_map:
                                        print(f"[Agent] Erreur sauvegarde mapping camera : {e_map}")
                                        await ws.send(json.dumps({"type": "camera_mapping_saved", "status": "error", "message": str(e_map)}))

                                elif msg_type == "reset_calibration":
                                    print("[Agent] Commande de reinitialisation calibration recue !")
                                    try:
                                        if CALIB_STATUS_FILE.exists():
                                            CALIB_STATUS_FILE.unlink()
                                            print("[Agent] calib_status.json supprime.")
                                        # Effacer les YAML locaux
                                        for p in [CAMERA_CALIB_MONO_FILE, CAMERA_CALIB_LEFT_FILE, CAMERA_CALIB_RIGHT_FILE]:
                                            if p.exists():
                                                p.unlink()
                                                print(f"[Agent] {p.name} supprime.")
                                        # Effacer stereo YAML
                                        try:
                                            from camera import CAMERA_CALIB_STEREO_FILE
                                            if CAMERA_CALIB_STEREO_FILE.exists():
                                                CAMERA_CALIB_STEREO_FILE.unlink()
                                                print(f"[Agent] {CAMERA_CALIB_STEREO_FILE.name} supprime.")
                                        except ImportError:
                                            pass
                                        await ws.send(json.dumps({"type": "calibration_reset", "status": "ok"}))
                                    except Exception as e_reset:
                                        print(f"[Agent] Erreur reset calibration: {e_reset}")
                                        await ws.send(json.dumps({"type": "calibration_reset", "status": "error", "message": str(e_reset)}))

                                elif msg_type == "run_stereo_calib":
                                    print("[Agent] Commande de calibration stereo recue !")
                                    cols = data.get("chessboard_cols", 9)
                                    rows = data.get("chessboard_rows", 6)
                                    square_mm = data.get("square_size_mm", 25)
                                    num_pairs = data.get("num_pairs", 20)

                                    async def _send_progress(pct, msg_text):
                                        try:
                                            await ws.send(json.dumps({
                                                "type": "stereo_calib_progress",
                                                "progress": pct,
                                                "message": msg_text
                                            }))
                                        except Exception:
                                            pass

                                    _ws_ref = ws
                                    _loop = asyncio.get_event_loop()

                                    def _progress_sync(pct, msg_text):
                                        asyncio.run_coroutine_threadsafe(
                                            _send_progress(pct, msg_text), _loop
                                        )

                                    def _stereo_calib_task():
                                        try:
                                            import cv2
                                            import numpy as np

                                            _progress_sync(5, "Ouverture des cameras...")
                                            mapping = get_camera_devices()
                                            left_dev = mapping.get(1, {}).get("device", "/dev/video0") if isinstance(mapping.get(1), dict) else mapping.get(1, "/dev/video0")
                                            right_dev = mapping.get(2, {}).get("device", "/dev/video2") if isinstance(mapping.get(2), dict) else mapping.get(2, "/dev/video2")

                                            cap_l = cv2.VideoCapture(left_dev)
                                            cap_r = cv2.VideoCapture(right_dev)
                                            if not cap_l.isOpened():
                                                asyncio.run_coroutine_threadsafe(
                                                    _ws_ref.send(json.dumps({"type": "stereo_calib_result", "success": False, "message": "Camera gauche introuvable: " + left_dev})), _loop)
                                                if cap_r.isOpened(): cap_r.release()
                                                return
                                            if not cap_r.isOpened():
                                                cap_l.release()
                                                asyncio.run_coroutine_threadsafe(
                                                    _ws_ref.send(json.dumps({"type": "stereo_calib_result", "success": False, "message": "Camera droite introuvable: " + right_dev})), _loop)
                                                return
                                            cap_l.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                                            cap_l.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                                            cap_r.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                                            cap_r.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

                                            _progress_sync(10, "Capture des paires synchronisees...")
                                            pat = (cols, rows)
                                            sq_m = square_mm / 1000.0
                                            objp = np.zeros((cols * rows, 3), np.float32)
                                            objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * sq_m
                                            objpts, il, ir = [], [], []
                                            cap_n, warm = 0, 0
                                            STABILITY_FRAMES_S = 8
                                            STABILITY_PX_S = 3.0
                                            DIVERSITY_CENTROID_PX_S = 35.0
                                            stable_count_l, stable_count_r = 0, 0
                                            prev_centroid_l, prev_centroid_r = None, None
                                            collected_centroids_l, collected_centroids_r = [], []
                                            found_any_s = False
                                            for attempt in range(num_pairs * 8):
                                                rl, fl = cap_l.read()
                                                rr, fr = cap_r.read()
                                                if not rl or not rr: continue
                                                warm += 1
                                                if warm < 5: continue
                                                gl = cv2.cvtColor(fl, cv2.COLOR_BGR2GRAY)
                                                gr = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
                                                flags_cb = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE + cv2.CALIB_CB_FAST_CHECK
                                                fl_f, cl = cv2.findChessboardCorners(gl, pat, flags_cb)
                                                fr_f, cr = cv2.findChessboardCorners(gr, pat, flags_cb)
                                                
                                                if fl_f and fr_f:
                                                    found_any_s = True
                                                    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
                                                    cv2.cornerSubPix(gl, cl, (11, 11), (-1, -1), crit)
                                                    cv2.cornerSubPix(gr, cr, (11, 11), (-1, -1), crit)
                                                    
                                                    c_l = np.mean(cl.reshape(-1, 2), axis=0)
                                                    c_r = np.mean(cr.reshape(-1, 2), axis=0)
                                                    
                                                    if prev_centroid_l is not None:
                                                        mov_l = float(np.linalg.norm(c_l - prev_centroid_l))
                                                        mov_r = float(np.linalg.norm(c_r - prev_centroid_r))
                                                    else:
                                                        mov_l, mov_r = 999.0, 999.0
                                                    
                                                    prev_centroid_l, prev_centroid_r = c_l, c_r
                                                    
                                                    if mov_l < STABILITY_PX_S and mov_r < STABILITY_PX_S:
                                                        stable_count_l += 1
                                                        stable_count_r += 1
                                                    else:
                                                        stable_count_l, stable_count_r = 0, 0
                                                    
                                                    stable = (stable_count_l >= STABILITY_FRAMES_S and stable_count_r >= STABILITY_FRAMES_S)
                                                    
                                                    if stable:
                                                        is_diverse = True
                                                        for past_c in collected_centroids_l:
                                                            if float(np.linalg.norm(c_l - past_c)) < DIVERSITY_CENTROID_PX_S:
                                                                is_diverse = False
                                                                break
                                                        
                                                        if is_diverse or cap_n == 0:
                                                            objpts.append(objp); il.append(cl); ir.append(cr)
                                                            collected_centroids_l.append(c_l)
                                                            collected_centroids_r.append(c_r)
                                                            cap_n += 1
                                                            stable_count_l, stable_count_r = 0, 0
                                                            pct = 10 + int(30 * cap_n / num_pairs)
                                                            _progress_sync(pct, f"Damier stable ✓ — {cap_n}/{num_pairs} paires...")
                                                            if cap_n >= num_pairs: break
                                                    
                                                    if attempt % 20 == 0:
                                                        st = "✓ Stable" if stable else f"Stabilisation... {min(stable_count_l, stable_count_r)}/{STABILITY_FRAMES_S}"
                                                        _progress_sync(10, f"Damier detecte — {st}")
                                                else:
                                                    stable_count_l, stable_count_r = 0, 0
                                                    prev_centroid_l, prev_centroid_r = None, None
                                                    if attempt % 60 == 0:
                                                        _progress_sync(10, "En attente du damier sur les 2 cameras...")
                                            cap_l.release(); cap_r.release()
                                            if cap_n < 5:
                                                asyncio.run_coroutine_threadsafe(
                                                    _ws_ref.send(json.dumps({"type": "stereo_calib_result", "success": False, "message": f"Pas assez de paires ({cap_n})"})), _loop)
                                                return

                                            _progress_sync(45, "Calibration mono gauche...")
                                            _, ml, dl, _, _ = cv2.calibrateCamera(objpts, il, gl.shape[::-1], None, None)
                                            _progress_sync(55, "Calibration mono droite...")
                                            _, mr, dr, _, _ = cv2.calibrateCamera(objpts, ir, gr.shape[::-1], None, None)
                                            _progress_sync(65, "cv2.stereoCalibrate...")
                                            criteria_s = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-5)
                                            ret_s, ml, dl, mr, dr, R, T, E, F = cv2.stereoCalibrate(
                                                objpts, il, ir, ml, dl, mr, dr, gl.shape[::-1],
                                                criteria=criteria_s, flags=cv2.CALIB_FIX_INTRINSIC)
                                            _progress_sync(75, "Rectification stereo...")
                                            R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(ml, dl, mr, dr, gl.shape[::-1], R, T, alpha=0)
                                            baseline = float(np.linalg.norm(T))
                                            camera_bf = float(ml[0, 0]) * baseline

                                            _progress_sync(85, "Sauvegarde des resultats...")
                                            stereo_data = {
                                                "image_width": gl.shape[1], "image_height": gl.shape[0],
                                                "camera_name": "usb_cam",
                                                "camera_matrix": ml.flatten().tolist(),
                                                "distortion_coefficients": dl.flatten().tolist(),
                                                "camera2_matrix": mr.flatten().tolist(),
                                                "camera2_distortion": dr.flatten().tolist(),
                                                "R": R.flatten().tolist(), "T": T.flatten().tolist(),
                                                "E": E.flatten().tolist(), "F": F.flatten().tolist(),
                                                "R1": R1.flatten().tolist(), "R2": R2.flatten().tolist(),
                                                "P1": P1.flatten().tolist(), "P2": P2.flatten().tolist(),
                                                "Q": Q.flatten().tolist(),
                                                "baseline_m": baseline, "camera_bf": camera_bf,
                                                "th_depth": 40.0, "is_calibrated": True,
                                                "reprojection_error": float(ret_s),
                                                "num_sample_pairs": cap_n,
                                                "calibrated_at": time.strftime("%d/%m/%Y %H:%M:%S")
                                            }
                                            try:
                                                url = f"{GATEWAY_URL}/core/camera/calibration/stereo"
                                                req = urllib.request.Request(url,
                                                    data=json.dumps(stereo_data).encode("utf-8"),
                                                    headers={"Content-Type": "application/json", "X-API-Token": API_TOKEN},
                                                    method="POST")
                                                with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
                                                    resp.read()
                                                print("[Agent] Calibration stereo sauvegardee sur la Gateway.")
                                            except Exception as e_post:
                                                print(f"[Agent] Erreur sauvegarde stereo Gateway: {e_post}")

                                            # Save YAML locally
                                            try:
                                                from camera import _json_calib_to_yaml_stereo, CAMERA_CALIB_STEREO_FILE
                                                yml = _json_calib_to_yaml_stereo(stereo_data)
                                                CAMERA_CALIB_STEREO_FILE.parent.mkdir(parents=True, exist_ok=True)
                                                with open(CAMERA_CALIB_STEREO_FILE, "w", encoding="utf-8") as f:
                                                    f.write(yml)
                                            except ImportError:
                                                pass

                                            _progress_sync(100, "Calibration stereo terminee !")
                                            asyncio.run_coroutine_threadsafe(
                                                _ws_ref.send(json.dumps({
                                                    "type": "stereo_calib_result", "success": True,
                                                    "message": f"OK (reproj: {ret_s:.4f}, baseline: {baseline*1000:.1f}mm)",
                                                    "reprojection_error": float(ret_s),
                                                    "baseline_mm": round(baseline * 1000, 1),
                                                    "fx": round(float(ml[0, 0]), 2),
                                                    "num_pairs": cap_n
                                                })), _loop)
                                        except Exception as e:
                                            import traceback; traceback.print_exc()
                                            try:
                                                asyncio.run_coroutine_threadsafe(
                                                    _ws_ref.send(json.dumps({"type": "stereo_calib_result", "success": False, "message": str(e)})), _loop)
                                            except Exception:
                                                pass

                                    threading.Thread(target=_stereo_calib_task, daemon=True).start()
                                    

                                elif msg_type == "run_mono_calib":
                                    cam_id = data.get("camera", 1)
                                    cols = data.get("chessboard_cols", 9)
                                    rows = data.get("chessboard_rows", 6)
                                    square_mm = data.get("square_size_mm", 25)
                                    timeout_s = data.get("timeout_seconds", 300)
                                    
                                    _ws_ref_mono = ws
                                    _loop_mono = asyncio.get_event_loop()
                                    
                                    def _send_mono_progress(pct, msg_text):
                                        try:
                                            asyncio.run_coroutine_threadsafe(
                                                _ws_ref_mono.send(json.dumps({
                                                    "type": "mono_calib_progress",
                                                    "camera": cam_id,
                                                    "progress": pct,
                                                    "message": msg_text
                                                })), _loop_mono)
                                        except Exception:
                                            pass
                                    
                                    def _mono_calib_task():
                                        try:
                                            import cv2
                                            import numpy as np
                                            
                                            _send_mono_progress(2, "Arret du stream WebRTC...")
                                            time.sleep(1.5)
                                            
                                            mapping = get_camera_devices()
                                            dev_info = mapping.get(cam_id, {})
                                            dev = dev_info.get("device", f"/dev/video{2*(cam_id-1)}") if isinstance(dev_info, dict) else dev_info
                                            
                                            _send_mono_progress(5, f"Ouverture camera {dev}...")
                                            cap = cv2.VideoCapture(dev)
                                            if not cap.isOpened():
                                                asyncio.run_coroutine_threadsafe(
                                                    _ws_ref_mono.send(json.dumps({
                                                        "type": "mono_calib_result", "camera": cam_id,
                                                        "success": False, "message": f"Camera {dev} introuvable"
                                                    })), _loop_mono)
                                                return
                                            
                                            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                                            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                                            
                                            pat = (cols, rows)
                                            sq_m = square_mm / 1000.0
                                            objp = np.zeros((cols * rows, 3), np.float32)
                                            objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * sq_m
                                            objpts, imgpts = [], []
                                            
                                            STABILITY_FRAMES = 8
                                            STABILITY_PX = 3.0
                                            DIVERSITY_CENTROID_PX = 30.0
                                            MAX_FRAMES = 2000
                                            MIN_FRAMES = 10
                                            
                                            stable_count = 0
                                            prev_centroid = None
                                            collected_centroids = []
                                            cap_n = 0
                                            warm = 0
                                            start_time = time.time()
                                            found_any = False
                                            
                                            _send_mono_progress(8, "Recherche du damier... Presentez le damier face a la camera.")
                                            
                                            for attempt in range(MAX_FRAMES):
                                                if time.time() - start_time > timeout_s:
                                                    cap.release()
                                                    asyncio.run_coroutine_threadsafe(
                                                        _ws_ref_mono.send(json.dumps({
                                                            "type": "mono_calib_result", "camera": cam_id,
                                                            "success": False, "message": f"Timeout ({timeout_s}s) - damier non detecte"
                                                        })), _loop_mono)
                                                    return
                                                
                                                ret, frame = cap.read()
                                                if not ret: continue
                                                warm += 1
                                                if warm < 5: continue
                                                
                                                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                                                flags_cb = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE + cv2.CALIB_CB_FAST_CHECK
                                                found, corners = cv2.findChessboardCorners(gray, pat, flags_cb)
                                                
                                                if found:
                                                    found_any = True
                                                    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
                                                    cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), crit)
                                                    
                                                    centroid = np.mean(corners.reshape(-1, 2), axis=0)
                                                    
                                                    if prev_centroid is not None:
                                                        movement = float(np.linalg.norm(centroid - prev_centroid))
                                                    else:
                                                        movement = 999.0
                                                    
                                                    prev_centroid = centroid
                                                    
                                                    if movement < STABILITY_PX:
                                                        stable_count += 1
                                                    else:
                                                        stable_count = 0
                                                    
                                                    if stable_count >= STABILITY_FRAMES:
                                                        # Check diversity
                                                        is_diverse = True
                                                        for past_c in collected_centroids:
                                                            dist = float(np.linalg.norm(centroid - past_c))
                                                            if dist < DIVERSITY_CENTROID_PX:
                                                                is_diverse = False
                                                                break
                                                        
                                                        if is_diverse or cap_n == 0:
                                                            objpts.append(objp)
                                                            imgpts.append(corners)
                                                            collected_centroids.append(centroid)
                                                            cap_n += 1
                                                            stable_count = 0
                                                            
                                                            pct = 8 + int(52 * cap_n / max(MIN_FRAMES, 30))
                                                            status = "Stable" if stable_count < STABILITY_FRAMES else "✓ Capture"
                                                            _send_mono_progress(min(pct, 60), f"Damier detecte {status} — {cap_n} frames collectees...")
                                                            
                                                            if cap_n >= MIN_FRAMES:
                                                                break
                                                    
                                                    if attempt % 30 == 0:
                                                        st = "✓ Stable" if stable_count >= STABILITY_FRAMES else (f"Stabilisation... {stable_count}/{STABILITY_FRAMES}")
                                                        _send_mono_progress(8 + min(stable_count * 2, 20), f"Damier detecte — {st}")
                                                else:
                                                    stable_count = 0
                                                    prev_centroid = None
                                                    
                                                    if attempt % 60 == 0 and not found_any:
                                                        _send_mono_progress(8, "Recherche du damier... Placez le damier face a la camera.")
                                            
                                            cap.release()
                                            
                                            if cap_n < 5:
                                                asyncio.run_coroutine_threadsafe(
                                                    _ws_ref_mono.send(json.dumps({
                                                        "type": "mono_calib_result", "camera": cam_id,
                                                        "success": False, "message": f"Pas assez de frames ({cap_n})"
                                                    })), _loop_mono)
                                                return
                                            
                                            _send_mono_progress(65, f"Calibration avec {cap_n} frames...")
                                            
                                            gray_shape = gray.shape[::-1]
                                            ret_cal, mtx, dist, rvecs, tvecs = cv2.calibrateCamera(objpts, imgpts, gray_shape, None, None)
                                            
                                            _send_mono_progress(80, "Sauvegarde des resultats...")
                                            
                                            calib_data = {
                                                "image_width": gray_shape[0], "image_height": gray_shape[1],
                                                "camera_name": f"usb_cam_{cam_id}",
                                                "camera_matrix": {
                                                    "rows": 3, "cols": 3,
                                                    "data": mtx.flatten().tolist()
                                                },
                                                "distortion_model": "plumb_bob",
                                                "distortion_coefficients": {
                                                    "rows": 1, "cols": len(dist.flatten()),
                                                    "data": dist.flatten().tolist()
                                                },
                                                "rectification_matrix": {
                                                    "rows": 3, "cols": 3,
                                                    "data": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
                                                },
                                                "projection_matrix": {
                                                    "rows": 3, "cols": 4,
                                                    "data": mtx[0].tolist() + [0.0] + mtx[1].tolist() + [0.0] + mtx[2].tolist() + [0.0]
                                                },
                                                "is_calibrated": True,
                                                "reprojection_error": float(ret_cal),
                                                "num_sample_frames": cap_n,
                                                "fx": float(mtx[0, 0]), "fy": float(mtx[1, 1]),
                                                "cx": float(mtx[0, 2]), "cy": float(mtx[1, 2]),
                                                "calibrated_at": time.strftime("%d/%m/%Y %H:%M:%S")
                                            }
                                            
                                            # Save to Gateway
                                            try:
                                                url = f"{GATEWAY_URL}/core/camera/calibration/{cam_id}"
                                                req = urllib.request.Request(url,
                                                    data=json.dumps(calib_data).encode("utf-8"),
                                                    headers={"Content-Type": "application/json", "X-API-Token": API_TOKEN},
                                                    method="POST")
                                                with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
                                                    resp.read()
                                                print(f"[Agent] Calibration mono cam {cam_id} sauvegardee sur Gateway.")
                                            except Exception as e_post:
                                                print(f"[Agent] Erreur sauvegarde mono Gateway: {e_post}")
                                            
                                            # Update calibration status
                                            mapping = get_camera_devices()
                                            fp = mapping.get(cam_id, {}).get("fingerprint") if isinstance(mapping.get(cam_id), dict) else None
                                            save_calibration_status(cam_id, True, fp)
                                            
                                            _send_mono_progress(100, f"Calibration reussie ! (reproj: {ret_cal:.4f}, fx: {mtx[0,0]:.1f})")
                                            asyncio.run_coroutine_threadsafe(
                                                _ws_ref_mono.send(json.dumps({
                                                    "type": "mono_calib_result", "camera": cam_id,
                                                    "success": True,
                                                    "message": f"OK (reproj: {ret_cal:.4f}, fx: {mtx[0,0]:.1f})",
                                                    "reprojection_error": float(ret_cal),
                                                    "fx": round(float(mtx[0, 0]), 2),
                                                    "fy": round(float(mtx[1, 1]), 2),
                                                    "num_frames": cap_n
                                                })), _loop_mono)
                                        
                                        except Exception as e:
                                            import traceback; traceback.print_exc()
                                            try:
                                                asyncio.run_coroutine_threadsafe(
                                                    _ws_ref_mono.send(json.dumps({
                                                        "type": "mono_calib_result", "camera": cam_id,
                                                        "success": False, "message": str(e)
                                                    })), _loop_mono)
                                            except Exception:
                                                pass
                                    
                                    threading.Thread(target=_mono_calib_task, daemon=True).start()

                                elif msg_type == "feature_request":
                                    feature = data.get("feature")
                                    state = data.get("state")
                                    print(f"[Agent] feature_request reçue : {feature} -> {state}")
                                    
                                    if feature == "audio":
                                        if state:
                                            tts_target = "node"
                                            stt_target = "node"
                                            chat_target = "node"
                                        else:
                                            tts_target = "robot"
                                            stt_target = "robot"
                                            chat_target = "robot"
                                    elif feature == "yolo":
                                        yolo_state = "node" if state else "robot"
                                    elif feature == "face_rec":
                                        face_rec_state = "node" if state else "robot"
                                        
                                    if latest_telemetry and "ai_state" in latest_telemetry:
                                        latest_telemetry["ai_state"] = {
                                            "tts": tts_target,
                                            "stt": stt_target,
                                            "chat": chat_target,
                                            "yolo": yolo_state,
                                            "face_rec": face_rec_state
                                        }
                                        
                                    ack_state = state
                                    if feature == "yolo":
                                        ack_state = (yolo_state == "node")
                                    elif feature == "face_rec":
                                        ack_state = (face_rec_state == "node")
                                    elif feature == "audio":
                                        ack_state = (tts_target == "node" or stt_target == "node" or chat_target == "node")

                                    ack_msg = {
                                        "type": "feature_ack",
                                        "feature": feature,
                                        "state": ack_state,
                                        "status": "ok"
                                    }
                                    await ws.send(json.dumps(ack_msg))
                                    
                                elif msg_type == "ai_control":
                                    feature = data.get("feature")
                                    target = data.get("target")
                                    print(f"[Agent] Commande ai_control reçue de l'app: {feature} -> {target}")
                                    
                                    if feature == "tts":
                                        tts_target = target
                                    elif feature == "stt":
                                        stt_target = target
                                    elif feature == "chat":
                                        chat_target = target
                                    elif feature == "yolo":
                                        yolo_state = target
                                    elif feature == "face_rec":
                                        face_rec_state = target
                                        
                                    if latest_telemetry and "ai_state" in latest_telemetry:
                                        latest_telemetry["ai_state"] = {
                                            "tts": tts_target,
                                            "stt": stt_target,
                                            "chat": chat_target,
                                            "yolo": yolo_state,
                                            "face_rec": face_rec_state
                                        }
                                        
                                    # Send feature_ack to CORE-Node to sync its checkboxes
                                    if feature in ("tts", "stt", "chat"):
                                        audio_active = (tts_target == "node" or stt_target == "node" or chat_target == "node")
                                        ack_msg = {
                                            "type": "feature_ack",
                                            "feature": "audio",
                                            "state": audio_active,
                                            "status": "ok"
                                        }
                                        await ws.send(json.dumps(ack_msg))
                                    elif feature == "yolo":
                                        ack_msg = {
                                            "type": "feature_ack",
                                            "feature": "yolo",
                                            "state": (yolo_state == "node"),
                                            "status": "ok"
                                        }
                                        await ws.send(json.dumps(ack_msg))
                                    elif feature == "face_rec":
                                        ack_msg = {
                                            "type": "feature_ack",
                                            "feature": "face_rec",
                                            "state": (face_rec_state == "node"),
                                            "status": "ok"
                                        }
                                        await ws.send(json.dumps(ack_msg))
                                        
                            except json.JSONDecodeError:
                                pass
                    finally:
                        telemetry_task.cancel()
            except Exception as e:
                print(f"[Agent] Déconnexion WebSocket ({e}). Reconnexion dans 5s...")
                await asyncio.sleep(5)

    asyncio.run(ws_loop())

if __name__ == "__main__":
    print(f"--- Démarrage de l'Agent Bastet ({get_version()}) ---")
    
    # Démarrer le subprocess ROS 2
    start_ros2_listener()
    
    # Récupérer les offsets de calibration depuis la Gateway au démarrage
    threading.Thread(target=fetch_offsets_from_gateway, daemon=True).start()
    
    # Récupérer les calibrations caméra depuis la Gateway au démarrage
    threading.Thread(target=fetch_camera_cals_from_gateway, daemon=True).start()
    
    # Thread 1: Envoi périodique de l'état (REST)
    t_status = threading.Thread(target=update_status_loop, daemon=True)
    t_status.start()
    
    # Thread 2: Boucle horaire de mise à jour en mode hibernation
    t_update = threading.Thread(target=hourly_update_loop, daemon=True)
    t_update.start()
    
    # Thread principal: WebSocket client
    start_websocket_client()
