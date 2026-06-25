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
import socket
from pathlib import Path

# Set global socket timeout to prevent hangs
socket.setdefaulttimeout(30.0)

# Config
GATEWAY_URL = "https://ha.arthonetwork.fr:44888"
WS_URL = "wss://ha.arthonetwork.fr:44888/ws/robot"
API_TOKEN = "bst_c9f28d3a1e4b85c7f0d4b9a2e6f1c3d5"
VERSION_FILE = Path("/opt/spotbot/version.txt")

# SSL context for self-signed certificates if any
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

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
    try:
        res = subprocess.run(
            ["systemctl", "is-active", "spotbot.service"],
            capture_output=True,
            text=True
        )
        return res.stdout.strip() == "active"
    except Exception:
        return False

def trigger_updater():
    print("[Agent] Lancement de la mise à jour...")
    try:
        subprocess.Popen(["sudo", "python3", "/opt/spotbot/updater.py"])
    except Exception as e:
        print(f"[Agent] Erreur lancement updater : {e}")

CAMERA_MAPPING_FILE = Path("/opt/spotbot/config/camera_mapping.json")

def get_camera_devices() -> dict:
    default_mapping = {
        1: "/dev/video0",
        2: "/dev/video2"
    }
    if CAMERA_MAPPING_FILE.exists():
        try:
            data = json.loads(CAMERA_MAPPING_FILE.read_text())
            left = data.get("left")
            right = data.get("right")
            if left:
                default_mapping[1] = left
            if right:
                default_mapping[2] = right
        except Exception:
            pass
    return default_mapping

def check_camera_connected(cam_id: int) -> bool:
    """Vérifie si la caméra physique est connectée au système."""
    mapping = get_camera_devices()
    dev = mapping.get(cam_id)
    if dev:
        return os.path.exists(dev)
    return False

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

camera_processes = {
    1: None,
    2: None
}

def is_device_free(device: str) -> bool:
    """Vérifie qu'un périphérique vidéo existe et n'est pas verrouillé."""
    if not os.path.exists(device):
        return False
    try:
        result = subprocess.run(["fuser", device], capture_output=True, text=True)
        return result.returncode != 0
    except Exception:
        return True

def start_camera_stream(cam_id: int):
    proc = camera_processes.get(cam_id)
    if proc is not None:
        if proc.poll() is None:
            return
        else:
            try:
                proc.wait()
            except Exception:
                pass
            camera_processes[cam_id] = None

    mapping = get_camera_devices()
    device = mapping.get(cam_id)
    if not device:
        print(f"[Agent] Aucun device mappé pour Cam {cam_id}.")
        return

    if not os.path.exists(device):
        print(f"[Agent] Cam {cam_id}: {device} inexistant.")
        return

    if not is_device_free(device):
        print(f"[Agent] Cam {cam_id}: {device} indisponible (verrouillé par ROS).")
        return

    rtsp_url = f"rtsp://ha.arthonetwork.fr:48554/robot/cam{cam_id}"

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "v4l2",
        "-input_format", "mjpeg",
        "-video_size", "640x480",
        "-framerate", "10",
        "-i", device,
        "-vcodec", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-profile:v", "baseline",
        "-level", "3.0",
        "-crf", "32",
        "-threads", "2",
        "-pix_fmt", "yuv420p",
        "-g", "10",
        "-bf", "0",
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        rtsp_url
    ]

    try:
        print(f"[Agent] Démarrage du flux RTSP Cam {cam_id} sur {device}...")
        camera_processes[cam_id] = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[Agent] Erreur démarrage Cam {cam_id} : {e}")

def stop_camera_stream(cam_id: int):
    proc = camera_processes.get(cam_id)
    if proc is not None:
        print(f"[Agent] Arrêt du flux RTSP pour Cam {cam_id}...")
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        camera_processes[cam_id] = None

# ─── ARDUINO FIRMWARE ACTIONS ─────────────────────────────────────────────────

ARDUINO_VERSION_FILE = Path("/opt/spotbot/arduino_version.txt")

def get_arduino_version() -> str:
    if ARDUINO_VERSION_FILE.exists():
        try:
            return ARDUINO_VERSION_FILE.read_text().strip()
        except Exception:
            pass
    return "v0.0.0"

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
            shell=True, capture_output=True, text=True, timeout=120,
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
        _ensure_arduino_lib("SparkFun BNO08x Arduino Library")
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
            import shutil
            if sketch_dest.exists():
                shutil.rmtree(sketch_dest)
            shutil.copytree(sketch_src, sketch_dest)
            print(f"[Agent] Sketch copié vers {sketch_dest}")
        elif not sketch_dest.exists():
            print(f"[Agent] ✗ Sketch introuvable : {sketch_src} ni {sketch_dest}")
            report_arduino_progress("failed_no_sketch", 0)
            return
        else:
            print(f"[Agent] Utilisation du sketch existant sur {sketch_dest}")

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
        ARDUINO_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        ARDUINO_VERSION_FILE.write_text(version)
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

# ─── ROS 2 TELEMETRY SUBPROCESS ───────────────────────────────────────────────

def start_ros2_listener():
    global ros2_process, latest_telemetry
    cmd = [
        "bash", "-c",
        "source /opt/ros2_jazzy/install/setup.bash && source /opt/spotbot/ros2_ws/install/setup.bash && python3 /opt/spotbot/ros2_listener.py"
    ]
    try:
        print("[Agent] Démarrage du subprocess ros2_listener...")
        ros2_process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1
        )
        
        def read_stdout():
            global latest_telemetry
            for line in ros2_process.stdout:
                try:
                    data = json.loads(line.strip())
                    data["ai_state"] = {
                        "tts": tts_target,
                        "stt": stt_target,
                        "chat": chat_target,
                        "yolo": yolo_state,
                        "face_rec": face_rec_state
                    }
                    latest_telemetry = data
                except Exception:
                    pass
                    
        t = threading.Thread(target=read_stdout, daemon=True)
        t.start()
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
        conf_path = "/etc/wpa_supplicant/wpa_supplicant.conf"
        content = ""
        if os.path.exists(conf_path):
            with open(conf_path, "r") as f:
                content = f.read()
                
        # Check if SSID exists in wpa_supplicant.conf
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
            with open(conf_path, "w") as f:
                f.write(new_content)
                
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
            
        return {"status": "error", "message": f"Délai d'obtention IP dépassé pour {ssid}."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ─── MAIN LOOPS ───────────────────────────────────────────────────────────────

def update_status_loop():
    print("[Agent] Démarrage du rapport d'état périodique...")
    while True:
        try:
            active = is_spotbot_service_active()
            status = "online" if active else "hibernating"
            
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
                    "arduino_connected": is_arduino_connected()
                },
                "ai_state": {
                    "tts": tts_target,
                    "stt": stt_target,
                    "chat": chat_target,
                    "yolo": yolo_state,
                    "face_rec": face_rec_state
                }
            }
            
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
        global tts_target, stt_target, chat_target, yolo_state, face_rec_state, ros2_process
        uri = f"{WS_URL}?token={API_TOKEN}"
        while True:
            try:
                print(f"[Agent] Connexion WebSocket vers {WS_URL}...")
                async with websockets.connect(uri, ssl=ssl_ctx) as ws:
                    print("[Agent] Connecté au WebSocket de la Gateway.")
                    await ws.send(json.dumps({"type": "chat", "text": f"Bastet Agent {get_version()} connecté."}))
                    
                    # Concurrently broadcast telemetry data
                    async def send_telemetry_loop():
                        last_sent = None
                        while True:
                            global latest_telemetry
                            if latest_telemetry and latest_telemetry != last_sent:
                                try:
                                    await ws.send(json.dumps(latest_telemetry))
                                    last_sent = latest_telemetry
                                except Exception:
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
                                    print("[Agent] Commande de mise à jour reçue !")
                                    trigger_updater()
                                    
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
                                    if is_spotbot_service_active():
                                        if ros2_process and ros2_process.stdin:
                                            ros2_process.stdin.write(json.dumps(data) + "\n")
                                            ros2_process.stdin.flush()
                                            print(f"[Agent] Start camera {cam} déléguée au ros2_listener (ROS actif).")
                                    else:
                                        start_camera_stream(cam)
                                    
                                elif msg_type == "stop_camera":
                                    cam = data.get("camera", 1)
                                    if is_spotbot_service_active():
                                        if ros2_process and ros2_process.stdin:
                                            ros2_process.stdin.write(json.dumps(data) + "\n")
                                            ros2_process.stdin.flush()
                                            print(f"[Agent] Stop camera {cam} déléguée au ros2_listener (ROS actif).")
                                    else:
                                        stop_camera_stream(cam)
                                    
                                elif msg_type == "motor_calibration":
                                    print("[Agent] Commande de calibration reçue !")
                                    if ros2_process and ros2_process.stdin:
                                        ros2_process.stdin.write(json.dumps(data) + "\n")
                                        ros2_process.stdin.flush()

                                elif msg_type == "arduino_cmd":
                                    cmd = data.get("cmd", "")
                                    print(f"[Agent] Commande Arduino reçue : {cmd}")
                                    if ros2_process and ros2_process.stdin:
                                        ros2_process.stdin.write(json.dumps({"type": "arduino_cmd", "cmd": cmd}) + "\n")
                                        ros2_process.stdin.flush()
                                        
                                elif msg_type == "manual_joint_control":
                                    if ros2_process and ros2_process.stdin:
                                        ros2_process.stdin.write(json.dumps(data) + "\n")
                                        ros2_process.stdin.flush()
                                        
                                elif msg_type == "scan_wifi":
                                    print("[Agent] Commande de scan WiFi reçue !")
                                    networks = get_wifi_list()
                                    known_ssids = []
                                    conf_path = "/etc/wpa_supplicant/wpa_supplicant.conf"
                                    if os.path.exists(conf_path):
                                        try:
                                            with open(conf_path, "r") as f:
                                                content = f.read()
                                            ssids = []
                                            for line in content.splitlines():
                                                line = line.strip()
                                                if "=" in line:
                                                    parts = line.split("=", 1)
                                                    if parts[0].strip() == "ssid":
                                                        ssids.append(parts[1].strip().strip("\"'"))
                                            for s in ssids:
                                                if s not in known_ssids:
                                                    known_ssids.append(s)
                                        except Exception as e_wpa:
                                            print(f"[Agent] Erreur lecture wpa_supplicant.conf : {e_wpa}")
                                    await ws.send(json.dumps({
                                        "type": "wifi_list",
                                        "networks": networks,
                                        "known_ssids": known_ssids
                                    }))
                                    
                                elif msg_type == "connect_wifi":
                                    ssid = data.get("ssid")
                                    password = data.get("password")
                                    print(f"[Agent] Connexion WiFi vers {ssid}...")
                                    res = connect_to_wifi(ssid, password)
                                    await ws.send(json.dumps({"type": "wifi_connect_result", **res}))

                                elif msg_type == "save_camera_mapping":
                                    left = data.get("left")
                                    right = data.get("right")
                                    print(f"[Agent] Sauvegarde du mapping caméra: left={left}, right={right}")
                                    try:
                                        CAMERA_MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
                                        with open(str(CAMERA_MAPPING_FILE), "w") as f_map:
                                            json.dump({"left": left, "right": right}, f_map)
                                        subprocess.run(["sudo", "systemctl", "restart", "spotbot.service"])
                                        await ws.send(json.dumps({"type": "camera_mapping_saved", "status": "ok"}))
                                    except Exception as e_map:
                                        print(f"[Agent] Erreur sauvegarde mapping camera : {e_map}")
                                        await ws.send(json.dumps({"type": "camera_mapping_saved", "status": "error", "message": str(e_map)}))
                                    
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
    
    # Thread 1: Envoi périodique de l'état (REST)
    t_status = threading.Thread(target=update_status_loop, daemon=True)
    t_status.start()
    
    # Thread 2: Boucle horaire de mise à jour en mode hibernation
    t_update = threading.Thread(target=hourly_update_loop, daemon=True)
    t_update.start()
    
    # Thread principal: WebSocket client
    start_websocket_client()
