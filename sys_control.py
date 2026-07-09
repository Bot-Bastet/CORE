import subprocess
import os
import time
import json
import threading
import config

def is_spotbot_service_active() -> bool:
    """Vérifie si le service spotbot est actif (l'agent est actif par définition)."""
    return True

def stop_spotbot_service():
    """Arrête le service ROS 2 spotbot pour libérer les caméras."""
    try:
        print("[Agent] Arret de spotbot.service pour liberer la camera...")
        subprocess.run(["systemctl", "stop", "spotbot.service"], check=True)
    except Exception as e:
        print(f"[Agent] Erreur lors de l'arret de spotbot.service : {e}")

def start_spotbot_service():
    """Redémarre le service ROS 2 spotbot."""
    try:
        print("[Agent] Redemarrage de spotbot.service...")
        subprocess.run(["systemctl", "start", "spotbot.service"], check=True)
    except Exception as e:
        print(f"[Agent] Erreur lors du demarrage de spotbot.service : {e}")

def get_cap_device(device_path):
    """Convertit un chemin /dev/videoN en index entier pour OpenCV."""
    if isinstance(device_path, str) and device_path.startswith("/dev/video"):
        try:
            return int(device_path.replace("/dev/video", ""))
        except ValueError:
            pass
    return device_path

def start_ros2_listener():
    """Démarre le subprocess ros2_listener.py avec l'environnement ROS 2 configuré."""
    cmd = [
        "bash", "-c",
        "source /opt/ros2_jazzy/install/setup.bash && source /opt/spotbot/ros2_ws/install/setup.bash && python3 -u /opt/spotbot/ros2_listener.py"
    ]
    try:
        print("[Agent] Démarrage du subprocess ros2_listener...")
        config.ros2_process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        def read_stdout():
            for line in config.ros2_process.stdout:
                try:
                    data = json.loads(line.strip())
                    if int(time.time()) % 10 == 0:
                        print(f"[Agent] Télémétrie reçue avec succès du listener: {list(data.keys())}")
                    data["ai_state"] = {
                        "tts": config.tts_target,
                        "stt": config.stt_target,
                        "chat": config.chat_target,
                        "yolo": config.yolo_state,
                        "face_rec": config.face_rec_state
                    }
                    config.latest_telemetry = data
                except Exception as e:
                    print(f"[Agent] Erreur décodage ligne de télémétrie: {e}. Ligne brute: {line.strip()[:100]}")
                    
        def read_stderr():
            for line in config.ros2_process.stderr:
                print(f"[Agent - Listener Error] {line.strip()}")
                
        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()
    except Exception as e:
        print(f"[Agent] Erreur fatale au lancement de ros2_listener: {e}")
