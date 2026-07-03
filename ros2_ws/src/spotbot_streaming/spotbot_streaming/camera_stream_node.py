#!/usr/bin/env python3
"""
SpotBot — Camera Stream Node
=============================
Streaming vidéo optimisé depuis la/les caméra(s) USB du Pi 5.

Stratégie (par priorité) :
  1. PC local détecté sur LAN   → GStreamer H.264 UDP (latence ~50ms)
  2. Aucun PC local             → FFmpeg H.264 RTSP vers serveur distant
  3. Module Alfa présent        → Streaming forcé via interface Alfa (meilleur signal)

Support :
  - 1 caméra (mono) : /dev/video0
  - 2 caméras (stéréo) : /dev/video0 + /dev/video1 (deux streams indépendants)
  - Sans module Alfa : routing standard
  - Avec module Alfa : routing forcé via interface Alfa si signal > seuil

Publications ROS 2:
  /streaming/status  (std_msgs/String) — état du streaming en cours
  /streaming/active  (std_msgs/Bool)   — True si un stream tourne

Topics écoutés:
  /streaming/start   (std_msgs/String) — forcer démarrage avec paramètre
  /streaming/stop    (std_msgs/Empty)  — arrêter tous les streams
"""

import os
import re
import socket
import subprocess
import threading
import time
import glob

import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Bool
from std_srvs.srv import Empty as EmptySrv

from .wifi_manager import (
    detect_alfa_interface,
    get_best_interface_ip,
    _get_interface_ip,
    _get_builtin_wifi_interface,
)


# ============================================================
# Constantes
# ============================================================

BROADCAST_PORT    = 5005
LISTEN_PORT       = BROADCAST_PORT + 1
DISCOVERY_MSG     = b"ROBOT_DISCOVERY_ALLP"
DISCOVERY_TIMEOUT = 10.0      # secondes à attendre la réponse du PC
VIDEO_PORT_BASE   = 5000      # cam0 = 5000, cam1 = 5002

# Serveur RTSP distant (fallback) — modifier selon votre infra
REMOTE_RTSP_HOST  = "ha.arthonetwork.fr"
REMOTE_RTSP_PORT  = 48554

# Résolution et FPS par caméra
STREAM_WIDTH   = 640
STREAM_HEIGHT  = 480
STREAM_FPS     = 30
STREAM_BITRATE = "2M"


# ============================================================
# Helpers — Détection des caméras
# ============================================================

def list_video_devices() -> list[str]:
    """Retourne la liste des /dev/videoX accessibles."""
    devs = sorted(glob.glob('/dev/video*'))
    # Filtrer : garder seulement les vraies caméras (pas les métadonnées)
    real = []
    for d in devs:
        try:
            result = subprocess.run(
                ['v4l2-ctl', '--device', d, '--info'],
                capture_output=True, text=True, timeout=2
            )
            if 'Video Capture' in result.stdout:
                real.append(d)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            # v4l2-ctl absent → garder les video0/video1 pairs par convention
            if re.search(r'/dev/video[02468]$', d):
                real.append(d)
    return real[:2]   # Max 2 caméras


# ============================================================
# Découverte LAN — chercher un PC qui écoute
# ============================================================

def discover_pc_on_lan(bind_iface_ip: str = '') -> str | None:
    """
    Envoie un broadcast UDP et attend la réponse d'un PC sur le LAN.

    Returns:
        IP du PC si trouvé, None sinon
    """
    try:
        bcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        bcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        bcast_sock.settimeout(2)
        if bind_iface_ip:
            bcast_sock.bind((bind_iface_ip, 0))

        listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_sock.bind(('', LISTEN_PORT))
        listen_sock.settimeout(DISCOVERY_TIMEOUT)

        bcast_sock.sendto(DISCOVERY_MSG, ('255.255.255.255', BROADCAST_PORT))
        bcast_sock.close()

        data, addr = listen_sock.recvfrom(1024)
        msg = data.decode('utf-8', errors='ignore')
        if msg.startswith('PC_IP:'):
            pc_ip = msg.split(':', 1)[1].strip()
            return pc_ip
        return addr[0]   # Fallback: adresse source de la réponse

    except socket.timeout:
        return None
    except OSError:
        return None
    finally:
        try:
            bcast_sock.close()
        except Exception:
            pass
        try:
            listen_sock.close()
        except Exception:
            pass


# ============================================================
# Constructeurs de commandes de streaming
# ============================================================

def build_gstreamer_cmd(device: str, pc_ip: str, port: int,
                        bind_ip: str = '') -> str:
    """
    Commande GStreamer H.264 UDP vers PC local (très faible latence).

    Pipeline:
      v4l2src → videoconvert → x264enc (ultrafast/zerolatency) → rtph264pay → udpsink

    Args:
        device:   /dev/videoX
        pc_ip:    IP du PC sur le LAN
        port:     Port UDP de destination
        bind_ip:  IP locale à utiliser pour l'envoi (force l'interface, optionnel)
    """
    multicast_iface = f'multicast-iface={bind_ip}' if bind_ip else ''

    cmd = (
        f"gst-launch-1.0 -q "
        f"v4l2src device={device} ! "
        f"video/x-raw,width={STREAM_WIDTH},height={STREAM_HEIGHT},framerate={STREAM_FPS}/1 ! "
        f"videoconvert ! "
        f"x264enc tune=zerolatency speed-preset=ultrafast bitrate=1500 ! "
        f"rtph264pay config-interval=1 pt=96 ! "
        f"udpsink host={pc_ip} port={port} sync=false async=false"
    )
    return cmd


def build_ffmpeg_rtsp_cmd(device: str, cam_id: int = 0,
                           bind_ip: str = '') -> str:
    """
    Commande FFmpeg RTSP vers serveur distant (fallback internet).

    Args:
        device:   /dev/videoX
        cam_id:   0 ou 1 (pour distinguer les flux stéréo)
        bind_ip:  IP locale à forcer pour le routing (Alfa)
    """
    stream_name  = f"robot/cam{cam_id}"
    rtsp_url     = f"rtsp://{REMOTE_RTSP_HOST}:{REMOTE_RTSP_PORT}/{stream_name}"
    bind_opt     = f"-bind_address {bind_ip}" if bind_ip else ""

    cmd = (
        f"ffmpeg -f v4l2 "
        f"-video_size {STREAM_WIDTH}x{STREAM_HEIGHT} "
        f"-framerate {STREAM_FPS} "
        f"-i {device} "
        f"-c:v libx264 "
        f"-preset ultrafast "
        f"-tune zerolatency "
        f"-b:v {STREAM_BITRATE} "
        f"-f rtsp "
        f"-rtsp_transport tcp "
        f"{bind_opt} "
        f"{rtsp_url}"
    )
    return cmd


# ============================================================
# Gestionnaire de processus de streaming
# ============================================================

class StreamProcess:
    """Wraps un subprocess de streaming avec auto-restart."""

    RESTART_DELAY = 5.0

    def __init__(self, name: str, cmd: str, logger=None):
        self.name    = name
        self.cmd     = cmd
        self.logger  = logger
        self._proc:  subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop   = threading.Event()

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _run_loop(self):
        while not self._stop.is_set():
            self._log(f"[{self.name}] Démarrage: {self.cmd[:80]}...")
            try:
                self._proc = subprocess.Popen(
                    self.cmd, shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE
                )
                self._proc.wait()
                if self._stop.is_set():
                    break
                rc = self._proc.returncode
                self._log(f"[{self.name}] Processus terminé (rc={rc}), restart dans {self.RESTART_DELAY}s")
            except Exception as e:
                self._log(f"[{self.name}] Erreur: {e}")

            self._stop.wait(self.RESTART_DELAY)

    def _log(self, msg: str):
        if self.logger:
            self.logger.info(msg)
        else:
            print(msg)


# ============================================================
# Node ROS 2
# ============================================================

class CameraStreamNode(Node):
    """
    Node ROS 2 de gestion du streaming caméra.
    Gère automatiquement la découverte LAN, le mode Alfa, et le nombre de caméras.
    """

    REDISCOVERY_INTERVAL = 30.0   # Rédécouvrir le PC toutes les 30s

    def __init__(self):
        super().__init__('camera_stream')

        # ---- Paramètres configurables ----
        self.declare_parameter('cameras',           'auto')  # 'auto' | 'mono' | 'stereo'
        self.declare_parameter('remote_rtsp_host',  REMOTE_RTSP_HOST)
        self.declare_parameter('remote_rtsp_port',  REMOTE_RTSP_PORT)
        self.declare_parameter('width',             STREAM_WIDTH)
        self.declare_parameter('height',            STREAM_HEIGHT)
        self.declare_parameter('fps',               STREAM_FPS)
        self.declare_parameter('prefer_alfa',       True)   # Préférer Alfa si dispo
        self.declare_parameter('discovery_timeout', DISCOVERY_TIMEOUT)

        self._cam_mode     = self.get_parameter('cameras').value
        self._prefer_alfa  = self.get_parameter('prefer_alfa').value

        # ---- Publishers ----
        self._status_pub = self.create_publisher(String, '/streaming/status', 10)
        self._active_pub = self.create_publisher(Bool,   '/streaming/active',  10)

        # ---- Services ----
        self.create_service(EmptySrv, '/streaming/stop', self._stop_srv)

        # ---- État interne ----
        self._streams:   dict[str, StreamProcess] = {}
        self._pc_ip:     str | None = None
        self._alfa_iface: str | None = None
        self._alfa_ip:    str | None = None
        self._last_discovery = 0.0

        # ---- Démarrage ----
        self.get_logger().info('Camera Stream Node démarré.')
        self._alfa_iface = detect_alfa_interface()
        if self._alfa_iface:
            self._alfa_ip = _get_interface_ip(self._alfa_iface)
            self.get_logger().info(
                f'Module Alfa détecté: {self._alfa_iface} ({self._alfa_ip})'
            )
        else:
            self.get_logger().info('Pas de module Alfa. Routing standard.')

        # Timer principal
        self._timer = self.create_timer(5.0, self._tick)
        # Premier démarrage immédiat
        self.create_timer(1.0, self._initial_start)

    # ------------------------------------------------------------------
    # Démarrage initial
    # ------------------------------------------------------------------

    def _initial_start(self):
        """Découverte PC + lancement des streams au boot."""
        devices = self._get_camera_devices()
        if not devices:
            self.get_logger().warn('Aucune caméra USB détectée on /dev/video*')
            self._publish_status('no_camera')
            return

        self.get_logger().info(f'Caméras détectées: {devices}')

        # Découverte LAN
        bind_ip = self._alfa_ip if (self._prefer_alfa and self._alfa_ip) else ''
        self.get_logger().info('Recherche d\'un PC sur le LAN...')
        self._pc_ip = discover_pc_on_lan(bind_ip)
        self._last_discovery = time.time()

        if self._pc_ip:
            self.get_logger().info(f'PC trouvé: {self._pc_ip} → Stream LAN GStreamer')
        else:
            self.get_logger().info('Aucun PC local → Stream RTSP distant')

        self._start_streams(devices)

    def _tick(self):
        """Vérification périodique: re-découverte PC, état des streams."""
        # Re-découverte toutes les REDISCOVERY_INTERVAL secondes
        if time.time() - self._last_discovery > self.REDISCOVERY_INTERVAL:
            devices = self._get_camera_devices()
            bind_ip = self._alfa_ip if (self._prefer_alfa and self._alfa_ip) else ''
            new_pc  = discover_pc_on_lan(bind_ip)
            self._last_discovery = time.time()

            if new_pc != self._pc_ip:
                self.get_logger().info(
                    f'Changement de mode: PC {self._pc_ip} → {new_pc}. Restart streams.'
                )
                self._stop_all_streams()
                self._pc_ip = new_pc
                self._start_streams(devices)

        # Publier l'état
        active = any(s.is_running() for s in self._streams.values())
        msg = Bool()
        msg.data = active
        self._active_pub.publish(msg)

    # ------------------------------------------------------------------
    # Gestion des streams
    # ------------------------------------------------------------------

    def _get_camera_devices(self) -> list[str]:
        """Retourne la liste des caméras selon le mode configuré."""
        all_devs = list_video_devices()
        if self._cam_mode == 'mono':
            return all_devs[:1]
        elif self._cam_mode == 'stereo':
            return all_devs[:2]
        else:  # auto
            return all_devs

    def _start_streams(self, devices: list[str]):
        """Lance un processus de streaming pour chaque caméra."""
        bind_ip = self._alfa_ip if (self._prefer_alfa and self._alfa_ip) else ''

        for idx, device in enumerate(devices):
            key = f'cam{idx}'
            if key in self._streams:
                continue  # Déjà en cours

            if self._pc_ip:
                # Mode local GStreamer
                port = VIDEO_PORT_BASE + (idx * 2)
                cmd  = build_gstreamer_cmd(device, self._pc_ip, port, bind_ip)
                mode = f'LAN→{self._pc_ip}:{port} (GStreamer)'
            else:
                # Mode distant FFmpeg RTSP
                cmd  = build_ffmpeg_rtsp_cmd(device, idx, bind_ip)
                mode = f'RTSP→{REMOTE_RTSP_HOST}:{REMOTE_RTSP_PORT}/cam{idx}'

            self.get_logger().info(f'Stream {key} ({device}) via {mode}')
            proc = StreamProcess(key, cmd, self.get_logger())
            proc.start()
            self._streams[key] = proc

        self._publish_status('streaming' if self._streams else 'idle')

    def _stop_all_streams(self):
        for key, proc in self._streams.items():
            proc.stop()
        self._streams.clear()

    def _stop_srv(self, request, response):
        self.get_logger().info('Arrêt de tous les streams (service)')
        self._stop_all_streams()
        self._publish_status('stopped')
        return response

    # ------------------------------------------------------------------
    # Publication d'état
    # ------------------------------------------------------------------

    def _publish_status(self, status: str):
        msg = String()
        cameras = list(self._streams.keys())
        alfa    = f' [Alfa:{self._alfa_iface}]' if self._alfa_iface else ''
        pc      = f' PC:{self._pc_ip}' if self._pc_ip else ' RTSP:remote'
        msg.data = f'{status}{alfa}{pc} {cameras}'
        self._status_pub.publish(msg)
        self.get_logger().info(f'Status: {msg.data}')


def main(args=None):
    rclpy.init(args=args)
    node = CameraStreamNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node._stop_all_streams()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
