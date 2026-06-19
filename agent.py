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
from pathlib import Path

# Config
GATEWAY_URL = "https://ha.arthonetwork.fr:44888"
WS_URL = "wss://ha.arthonetwork.fr:44888/ws/robot"
API_TOKEN = "bst_c9f28d3a1e4b85c7f0d4b9a2e6f1c3d5"
VERSION_FILE = Path("/opt/spotbot/version.txt")

# SSL context for self-signed certificates if any
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

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
        # Exécuter l'updater en tâche de fond pour ne pas bloquer l'agent
        subprocess.Popen(["sudo", "python3", "/opt/spotbot/updater.py"])
    except Exception as e:
        print(f"[Agent] Erreur lancement updater : {e}")

camera_processes = {
    1: None,
    2: None
}

def start_camera_stream(cam_id: int):
    proc = camera_processes.get(cam_id)
    if proc is not None:
        if proc.poll() is None:
            return  # Déjà en cours et actif
        else:
            try:
                proc.wait()
            except Exception:
                pass
            camera_processes[cam_id] = None
        
    device = "/dev/video0" if cam_id == 1 else "/dev/video2"
    rtsp_url = f"rtsp://ha.arthonetwork.fr:48554/robot/cam{cam_id}"
    
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "v4l2",
        "-input_format", "mjpeg",
        "-video_size", "640x480",
        "-framerate", "15",
        "-i", device,
        "-vcodec", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-g", "30",
        "-bf", "0",
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        rtsp_url
    ]
    
    try:
        print(f"[Agent] Démarrage du flux RTSP pour Cam {cam_id}...")
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

def update_status_loop():
    """Rapporte régulièrement l'état à la Gateway via REST."""
    print("[Agent] Démarrage du rapport d'état périodique...")
    while True:
        try:
            active = is_spotbot_service_active()
            status = "online" if active else "hibernating"
            
            metrics = get_system_metrics()
            # Calculate CPU percentage based on 1m load average (Pi 5 has 4 cores)
            cpu_percent = min(int(metrics.get("cpu_load_1m", 0.0) * 25), 100)

            payload = {
                "seen_person": None,
                "seen_objects": [],
                "last_chat": [],
                "robot_status": status,
                "robot_version": get_version(),
                "sensors": {
                    "cpu_percent": cpu_percent,
                    "ram_percent": metrics.get("ram_percent", 0.0),
                    "temp_c": metrics.get("cpu_temp", 0.0),
                    "spotbot_service_active": active,
                    "system": metrics,
                    "spotbot_service": "active" if active else "inactive"
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
    """Déclenche la vérification de mise à jour toutes les heures en mode hibernation."""
    print("[Agent] Démarrage de la surveillance des mises à jour (toutes les heures)...")
    while True:
        # Attendre 1 heure
        time.sleep(3600)
        try:
            # Ne pas mettre à jour si le robot est actif
            if not is_spotbot_service_active():
                print("[Agent] Mode hibernation détecté. Vérification horaire de mise à jour...")
                trigger_updater()
        except Exception as e:
            print(f"[Agent] Erreur boucle mise à jour : {e}")

def start_websocket_client():
    """Écoute en WebSocket pour les commandes instantanées."""
    # Note : On utilise 'websockets' s'il est installé, sinon on boucle/rebranche
    # Pour s'assurer de ne pas crash si websockets n'est pas dispo, on tente un import.
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
        uri = f"{WS_URL}?token={API_TOKEN}"
        while True:
            try:
                print(f"[Agent] Connexion WebSocket vers {WS_URL}...")
                async with websockets.connect(uri, ssl=ssl_ctx) as ws:
                    print("[Agent] Connecté au WebSocket de la Gateway.")
                    # S'identifier
                    await ws.send(json.dumps({"type": "chat", "text": f"Bastet Agent {get_version()} connecté."}))
                    
                    while True:
                        msg = await ws.recv()
                        try:
                            data = json.loads(msg)
                            msg_type = data.get("type")
                            
                            if msg_type == "trigger_update":
                                print("[Agent] Commande de mise à jour reçue par WebSocket !")
                                trigger_updater()
                                
                            elif msg_type == "start_robot":
                                print("[Agent] Commande de démarrage du robot reçue !")
                                subprocess.run(["sudo", "systemctl", "start", "spotbot.service"])
                                
                            elif msg_type == "stop_robot":
                                print("[Agent] Commande d'arrêt du robot reçue !")
                                subprocess.run(["sudo", "systemctl", "stop", "spotbot.service"])
                                
                            elif msg_type == "start_camera":
                                cam = data.get("camera", 1)
                                start_camera_stream(cam)
                                
                            elif msg_type == "stop_camera":
                                cam = data.get("camera", 1)
                                stop_camera_stream(cam)
                                
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                print(f"[Agent] Déconnexion WebSocket ({e}). Reconnexion dans 5s...")
                await asyncio.sleep(5)

    asyncio.run(ws_loop())

if __name__ == "__main__":
    print(f"--- Démarrage de l'Agent Bastet ({get_version()}) ---")
    
    # Thread 1: Envoi périodique de l'état (REST)
    t_status = threading.Thread(target=update_status_loop, daemon=True)
    t_status.start()
    
    # Thread 2: Boucle horaire de mise à jour en mode hibernation
    t_update = threading.Thread(target=hourly_update_loop, daemon=True)
    t_update.start()
    
    # Thread principal: WebSocket client
    start_websocket_client()
