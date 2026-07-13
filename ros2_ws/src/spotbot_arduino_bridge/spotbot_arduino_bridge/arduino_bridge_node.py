#!/usr/bin/env python3
"""
SpotBot – Arduino Bridge Node
=============================
BNO085 uniquement. Pas de MPU6050.

Publie:
  /imu/data             (sensor_msgs/Imu)   – quaternion fused BNO085 (50 Hz)
  /imu/data_raw         (sensor_msgs/Imu)   – alias /imu/data (compatibilité rtabmap)
  /sensors/ultrasonic   (sensor_msgs/Range) – distance HC-SR04 en mètres
  /sensors/obstacle     (std_msgs/Bool)     – True si obstacle < 30 cm
  /arduino/status       (std_msgs/String)   – état connexion

Souscrit:
  /cmd_joint_angles (std_msgs/Float32MultiArray) – 12 angles servos [deg]
  /cmd_motion       (std_msgs/String)             – stand | sit | reset_imu
"""

import json
import time
import glob
import struct
import threading
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy

from std_msgs.msg import String, Float32MultiArray, Bool
from sensor_msgs.msg import Imu, Range
from geometry_msgs.msg import Vector3

try:
    import serial
    import serial.tools.list_ports
    SERIAL_OK = True
except ImportError:
    SERIAL_OK = False


class ArduinoBridgeNode(Node):
    """ROS 2 node bridging Pi 5 <-> Arduino Mega via USB Serial (JSON protocol)."""

    BAUDRATE       = 250000  # must match SERIAL_BAUD in Arduino firmware (250000)
    READ_TIMEOUT   = 0.05
    RETRY_INTERVAL = 3.0
    IMU_FRAME      = 'imu_link'

    def __init__(self):
        super().__init__('arduino_bridge')

        # Parametres
        self.declare_parameter('port', '')         # vide = auto-detection
        self.declare_parameter('baudrate', self.BAUDRATE)
        self.declare_parameter('auto_flash', True)
        self.declare_parameter('firmware_path', '')
        self.declare_parameter('publish_rate', 50.0)

        self._port_param    = self.get_parameter('port').value
        self._baudrate      = self.get_parameter('baudrate').value
        self._auto_flash    = self.get_parameter('auto_flash').value
        self._firmware_path = self.get_parameter('firmware_path').value

        # Publishers
        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        # /imu/data_raw : données brutes MPU6050 (nécessite Madgwick)
        self._imu_raw_pub  = self.create_publisher(Imu,    '/imu/data_raw',       qos)
        # /imu/data     : données fusionnées BNO085 (orientation valide)
        self._imu_pub      = self.create_publisher(Imu,    '/imu/data',           qos)
        self._sonar_pub    = self.create_publisher(Range,  '/sensors/ultrasonic', qos)
        self._obstacle_pub = self.create_publisher(Bool,   '/sensors/obstacle',    10)
        self._status_pub   = self.create_publisher(String, '/arduino/status',      10)
        # Publisher for real Arduino servo positions (20 Hz from firmware)
        self._servo_pos_pub = self.create_publisher(Float32MultiArray, '/arduino/servo_positions', 10)

        # Frame du capteur ultrason (front du robot)
        self.SONAR_FRAME    = 'sonar_link'
        self.SONAR_MIN_M    = 0.02    # 2 cm min
        self.SONAR_MAX_M    = 4.00    # 400 cm max
        self.SONAR_FOV      = 0.2618  # ~15 degres en radians (HC-SR04 spec)

        # Subscribers
        self.create_subscription(
            Float32MultiArray, '/cmd_joint_angles',
            self._joint_callback, 10
        )
        self.create_subscription(
            Float32MultiArray, '/cmd_manual_joint_angles',
            self._manual_joint_callback, 10
        )
        self.create_subscription(
            String, '/cmd_motion',
            self._motion_callback, 10
        )
        self.create_subscription(
            Float32MultiArray, '/cmd_joint_calibration',
            self._calib_callback, 10
        )

        # FIX NATIF (v2) : la calibration IMU est désormais gérée 100% côté Arduino
        # (EEPROM + q_offset^-1 dans readBNO085), avec persistance. Le bridge ROS
        # ne maintient plus d'état de calibration et se contente de relayer les
        # quaternions déjà calibrés que l'Arduino publie sur le port série.

        # Charger les offsets existants
        self._offsets = [0.0] * 12
        try:
            p = Path("/opt/spotbot/config/calibration.json")
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._offsets = data.get("offsets", [0.0] * 12)
                self.get_logger().info(f"Offsets charges depuis le fichier : {self._offsets}")
        except Exception as e:
            self.get_logger().error(f"Erreur chargement offsets : {e}")

        self._serial: serial.Serial | None = None
        self._connected = False
        self._last_retry = 0.0
        self._calib_synced = False
        self._consecutive_json_errors = 0
        self._max_json_errors_before_flush = 50
        self._consecutive_read_errors = 0
        self._max_read_errors_before_reconnect = 20

        # (FIX NATIF v2 : gestionnaire de calibration IMU retiré — l'Arduino gère
        # désormais la persistance et le calcul d'offset dans son firmware.)

        # Timer principal de lecture
        rate = self.get_parameter('publish_rate').value
        # (Suppression des hooks de calibration IMU locale : tout est dans le firmware.)
        self._timer = self.create_timer(1.0 / rate, self._spin_serial)
        
        # Timer de heartbeat (1 Hz) pour le watchdog
        self._heartbeat_timer = self.create_timer(1.0, self._send_heartbeat)

        self.get_logger().info('Arduino Bridge Node demarré. Auto-detection du port...')
        self._try_connect()

    # ------------------------------------------------------------------
    # FIX NATIF v2 : suppression des hooks de calibration IMU locale. Le
    # firmware Arduino (EEPROM + q_offset^-1) fournit un quaternion deja
    # calibre sur le port serie ; le bridge se contente de le publier tel quel
    # sur /imu/data et /imu/data_raw (les deux portent maintenant la meme
    # valeur calibree — la distinction _raw est documentee comme « avant
    # swap X-180 firmware + calibration offset »).
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Connexion / auto-detection
    # ------------------------------------------------------------------

    def _find_arduino_port(self) -> str | None:
        """Detecte automatiquement l'Arduino Mega sur les ports serie."""
        # Methode 0: priorité absolue au lien symbolique stable
        if glob.glob('/dev/arduino'):
            self.get_logger().info('Lien stable /dev/arduino trouvé.')
            return '/dev/arduino'

        # Methode 1: via pyserial list_ports (cherche VID/PID Arduino)
        if SERIAL_OK:
            for p in serial.tools.list_ports.comports():
                desc = (p.description or '').lower()
                mfr  = (p.manufacturer or '').lower()
                if 'arduino' in desc or 'arduino' in mfr or \
                   (p.vid == 0x2341 and p.pid in (0x0010, 0x0042)):  # Arduino Mega PIDs
                    self.get_logger().info(f'Arduino detecte: {p.device} ({p.description})')
                    return p.device

        # Methode 2: glob sur les devices TTY habituels
        candidates = (
            glob.glob('/dev/ttyUSB*') +
            glob.glob('/dev/ttyACM*')
        )
        candidates.sort()
        if candidates:
            self.get_logger().warn(
                f'Arduino non identifie par VID/PID. Tentative sur: {candidates[0]}'
            )
            return candidates[0]

        return None

    def _try_connect(self):
        """Tente de se connecter a l'Arduino."""
        port = self._port_param or self._find_arduino_port()
        if port is None:
            self.get_logger().warn('Arduino non trouve. Nouvelle tentative dans 3s...')
            return

        try:
            self._serial = serial.Serial(port, self._baudrate, timeout=self.READ_TIMEOUT)
            # Ne PAS faire de reset hardware (DTR) — l'Arduino tourne deja.
            # Le reset provoque 2s de bootloader → garbage → crash → boucle infinie.
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
            self._connected = True
            self._calib_synced = False
            self._consecutive_json_errors = 0
            self._consecutive_read_errors = 0
            self.get_logger().info(f'Arduino connecte sur {port} @ {self._baudrate} baud (sans reset)')
            self._publish_status(f'connected:{port}')

            # Flash si demande
            if self._auto_flash and self._firmware_path:
                self._flash_firmware(port)

        except serial.SerialException as e:
            self.get_logger().error(f'Erreur connexion {port}: {e}')
            self._connected = False

    # ------------------------------------------------------------------
    # Flash automatique
    # ------------------------------------------------------------------

    def _flash_firmware(self, port: str):
        """Flash le firmware Arduino via avrdude."""
        import subprocess
        hex_file = self._firmware_path
        if not hex_file.endswith('.hex'):
            self.get_logger().warn('firmware_path doit pointer vers un .hex (compile par Arduino IDE)')
            return

        self.get_logger().info(f'Flash firmware: {hex_file} -> {port}')
        cmd = [
            'avrdude', '-p', 'atmega2560', '-c', 'wiring',
            '-P', port, '-b', '115200', '-D',
            '-U', f'flash:w:{hex_file}:i'
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                self.get_logger().info('Flash Arduino reussi!')
            else:
                self.get_logger().error(f'Erreur flash: {result.stderr}')
        except FileNotFoundError:
            self.get_logger().warn('avrdude non trouve. Installez: sudo apt install avrdude')
        except subprocess.TimeoutExpired:
            self.get_logger().error('Timeout flash Arduino')

        time.sleep(2.0)  # Attendre reboot post-flash
        self._serial.reset_input_buffer()

    # ------------------------------------------------------------------
    # Boucle serie principale
    # ------------------------------------------------------------------

    def _spin_serial(self):
        if not self._connected:
            now = time.time()
            if now - self._last_retry > self.RETRY_INTERVAL:
                self._last_retry = now
                self._try_connect()
            return

        try:
            if self._serial.in_waiting:
                line = self._serial.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    self._consecutive_read_errors = 0  # reset on successful read
                    if not self._calib_synced:
                        self._calib_synced = True
                        threading.Thread(target=self._sync_calibration_to_arduino, daemon=True).start()
                    self._parse_line(line)
        except (serial.SerialException, OSError) as e:
            self._consecutive_read_errors += 1
            if self._consecutive_read_errors >= self._max_read_errors_before_reconnect:
                self.get_logger().error(
                    f'{self._consecutive_read_errors} erreurs lecture consecutives — '
                    'tentative reconnexion Arduino'
                )
                self._connected = False
                self._consecutive_read_errors = 0
                try:
                    if self._serial:
                        self._serial.close()
                except Exception:
                    pass
                self._serial = None
                self._publish_status('disconnected')
            else:
                try:
                    if self._serial and self._serial.is_open:
                        self._serial.reset_input_buffer()
                except Exception:
                    pass
        except Exception as e:
            self.get_logger().error(f'Perte connexion Arduino: {e}')
            self._connected = False
            self._consecutive_read_errors = 0
            try:
                if self._serial:
                    self._serial.close()
            except Exception:
                pass
            self._serial = None
            self._publish_status('disconnected')

    def _parse_line(self, line: str):
        """Decode une ligne JSON venant de l'Arduino v3.0 (BNO085 ou MPU6050)."""
        try:
            data = json.loads(line)
            self._consecutive_json_errors = 0  # reset counter on success
        except json.JSONDecodeError:
            self._consecutive_json_errors += 1
            if self._consecutive_json_errors >= self._max_json_errors_before_flush:
                self.get_logger().error(
                    f"{self._consecutive_json_errors} erreurs JSON consecutives — "
                    "flush du buffer serie (corruption detectee)"
                )
                try:
                    if self._serial and self._serial.is_open:
                        self._serial.reset_input_buffer()
                except Exception:
                    pass
                self._consecutive_json_errors = 0
            elif self._consecutive_json_errors == 1:
                if line.strip():
                    self.get_logger().warn(f"Ligne non-JSON reçue de l'Arduino: {line[:80]}")
            return

        if 'imu' not in data and 'sonar' not in data and 'version' not in data:
            self.get_logger().info(f"Message reçu de l'Arduino: {line}")

        if 'imu' in data:
            self._publish_imu_bno085(data['imu'])

        if 'servos' in data and isinstance(data['servos'], list):
            # Forward real Arduino servo positions to ROS2 for telemetry
            try:
                angles = [float(a) for a in data['servos'][:12]]
                msg = Float32MultiArray()
                msg.data = angles
                self._servo_pos_pub.publish(msg)
            except Exception:
                pass

        if 'status' in data and data.get('bno085') is False:
            self.get_logger().error('BNO085 non detecte sur l\'Arduino! Verifiez I2C (0x4A) et les cables.')

        if 'sonar' in data and isinstance(data['sonar'], dict):
            self._publish_sonar(data['sonar'])

        if 'version' in data:
            self._save_arduino_version(data['version'])

    def _publish_imu_bno085(self, imu_raw: dict):
        """BNO085 : quaternion fused + accéleration linéaire + gyro.

        FIX NATIF v2 : la calibration est désormais appliquée dans le firmware
        Arduino (q_offset EEPROM) AVANT la sérialisation JSON. Le bridge ne fait
        plus que publier tels quels les quaternions reçus sur /imu/data et
        /imu/data_raw (les deux topics reçoivent la meme valeur calibree).
        Pour les consommateurs qui voulaient du « vraiment brut » (avant swap
        X-180 firmware + offset utilisateur), ils doivent lire directement le
        port série de l'Arduino.
        """
        msg = Imu()
        msg.header.stamp    = self.get_clock().now().to_msg()
        msg.header.frame_id = self.IMU_FRAME

        qw = imu_raw.get('qw', 10000) / 10000.0
        qx = imu_raw.get('qx', 0)    / 10000.0
        qy = imu_raw.get('qy', 0)    / 10000.0
        qz = imu_raw.get('qz', 0)    / 10000.0
        norm = (qw**2 + qx**2 + qy**2 + qz**2) ** 0.5
        if norm > 1e-6:
            qw, qx, qy, qz = qw/norm, qx/norm, qy/norm, qz/norm
            
        msg.orientation.w = qw
        msg.orientation.x = qx
        msg.orientation.y = qy
        msg.orientation.z = qz

        calib = imu_raw.get('calib', 0)
        oc = {3: 0.0001, 2: 0.001, 1: 0.01, 0: 0.1}.get(calib, 0.01)
        msg.orientation_covariance = [oc, 0, 0, 0, oc, 0, 0, 0, oc]

        msg.linear_acceleration.x = imu_raw.get('lax', 0) / 100.0
        msg.linear_acceleration.y = imu_raw.get('lay', 0) / 100.0
        msg.linear_acceleration.z = imu_raw.get('laz', 0) / 100.0
        msg.linear_acceleration_covariance = [0.005, 0, 0, 0, 0.005, 0, 0, 0, 0.005]

        msg.angular_velocity.x = imu_raw.get('gx', 0) / 1000.0
        msg.angular_velocity.y = imu_raw.get('gy', 0) / 1000.0
        msg.angular_velocity.z = imu_raw.get('gz', 0) / 1000.0
        msg.angular_velocity_covariance = [0.0003, 0, 0, 0, 0.0003, 0, 0, 0, 0.0003]

        self._imu_pub.publish(msg)      # /imu/data     (orientation deja calibree firmware-side)
        self._imu_raw_pub.publish(msg)  # /imu/data_raw (meme valeur, conservée pour compat SLAM)

    def _publish_sonar(self, sonar_raw: dict):
        """HC-SR04 : distance en mètres + alerte obstacle."""
        dist_cm = sonar_raw.get('dist_cm', -1.0)
        valid   = sonar_raw.get('valid', False)
        alert   = sonar_raw.get('alert', False)

        msg = Range()
        msg.header.stamp    = self.get_clock().now().to_msg()
        msg.header.frame_id = self.SONAR_FRAME
        msg.radiation_type  = Range.ULTRASOUND
        msg.field_of_view   = self.SONAR_FOV
        msg.min_range       = self.SONAR_MIN_M
        msg.max_range       = self.SONAR_MAX_M
        msg.range = (dist_cm / 100.0) if (valid and dist_cm > 0) else float('inf')
        self._sonar_pub.publish(msg)

        obs_msg = Bool()
        obs_msg.data = alert
        self._obstacle_pub.publish(obs_msg)


    # ------------------------------------------------------------------
    # Subscribers callbacks
    # ------------------------------------------------------------------

    def _joint_callback(self, msg: Float32MultiArray):
        """Envoie les angles des 12 servos a l'Arduino avec application des offsets.
        Deduplication: ne re-envoie pas le meme payload que le precedent."""
        if not self._connected:
            return
        angles = list(msg.data)[:12]
        angles += [90.0] * (12 - len(angles))  # completer si besoin
        rounded = [round(a, 1) for a in angles]

        # Dedup: skip if same as last sent payload
        if hasattr(self, '_last_servo_angles') and self._last_servo_angles == rounded:
            return
        self._last_servo_angles = rounded
        
        # Calcul du checksum de sécurité (somme des angles modulo 1000)
        chk = sum(int(a) for a in rounded) % 1000
        payload = json.dumps({'servos': rounded, 'chk': chk}) + '\n'
        self._send(payload)

    def _manual_joint_callback(self, msg: Float32MultiArray):
        """Envoie les angles des 12 servos pour le test manuel (autorisé sans calibration)."""
        if not self._connected:
            return
        angles = list(msg.data)[:12]
        angles += [90.0] * (12 - len(angles))  # completer si besoin
        rounded = [round(a, 1) for a in angles]

        # Dedup: skip if same as last sent payload
        if hasattr(self, '_last_manual_servo_angles') and self._last_manual_servo_angles == rounded:
            return
        self._last_manual_servo_angles = rounded
        
        # Calcul du checksum de sécurité (somme des angles modulo 1000)
        chk = sum(int(a) for a in rounded) % 1000
        payload = json.dumps({'servos': rounded, 'chk': chk, 'manual': True}) + '\n'
        self._send(payload)

    def _calib_callback(self, msg: Float32MultiArray):
        """Enregistre les nouveaux offsets de calibration et les sauvegarde."""
        self._offsets = list(msg.data)[:12]
        self._offsets += [0.0] * (12 - len(self._offsets))
        self.get_logger().info(f"Nouveaux offsets recus et appliques : {self._offsets}")
        try:
            p = Path("/opt/spotbot/config/calibration.json")
            existing = {}
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    pass
            existing["offsets"] = self._offsets
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(existing, f)
            
            # Envoi direct à l'Arduino (dans un thread d'arrière-plan pour éviter de bloquer l'exécuteur ROS)
            threading.Thread(target=self._sync_calibration_to_arduino, daemon=True).start()
        except Exception as e:
            self.get_logger().error(f"Erreur sauvegarde offsets : {e}")

    def _sync_calibration_to_arduino(self):
        """Lit calibration.json et envoie tous les offsets, limites et inverts à l'Arduino Mega.
        Si tous les offsets sont zéro ET les limites sont [0,180] ET les inverts sont false,
        envoie clear_servo_calib pour effacer les magic numbers EEPROM (sécurité)."""
        if not self._connected:
            return
        p = Path("/opt/spotbot/config/calibration.json")
        if not p.exists():
            self.get_logger().info("Aucun fichier de calibration local trouvé pour synchro Arduino.")
            return
            
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            offsets = data.get("offsets", [])
            limits = data.get("limits", [])
            inverts = data.get("inverts", [])
            
            # 🔴 CRITICAL: if all offsets are zero and all limits are [0,180] and all inverts false,
            # this means calibration is NOT configured. Send clear_servo_calib to erase EEPROM
            # magic numbers so the Arduino safety gate (offsets_calibrated) stays active.
            all_offsets_zero = all(abs(o) < 0.01 for o in (offsets if len(offsets) >= 12 else [0]*12))
            all_limits_default = all(
                len(lim) >= 2 and abs(lim[0]) < 0.01 and abs(lim[1] - 180.0) < 0.01
                for lim in (limits[:12] if len(limits) >= 12 else [[0,180]]*12)
            ) if len(limits) >= 12 else True
            all_inverts_false = not any(inverts) if len(inverts) >= 12 else True
            
            if all_offsets_zero and all_limits_default and all_inverts_false:
                self.get_logger().warn(
                    "Tous les offsets sont à zéro — envoi de stop + clear_servo_calib pour désactiver "
                    "la calibration EEPROM (sécurité interlock actif)."
                )
                # 🔴 CRITICAL: Send stop FIRST to detach all servos before
                # clearing calibration. This prevents any motor movement.
                self._send(json.dumps({"cmd": "stop"}) + '\n')
                time.sleep(0.1)
                self._send(json.dumps({"cmd": "clear_servo_calib"}) + '\n')
                return
            
            self.get_logger().info(f"Synchronisation de la calibration vers l'Arduino ({len(offsets)} articulations)...")
            
            for i in range(12):
                # 1. Offset
                off = offsets[i] if i < len(offsets) else 0.0
                self._send(json.dumps({"cmd": "set_offset", "index": i, "offset": float(off)}) + '\n')
                time.sleep(0.05)
                
                # 2. Limits
                lim = limits[i] if i < len(limits) else [0.0, 180.0]
                self._send(json.dumps({"cmd": "set_limit", "index": i, "min": float(lim[0]), "max": float(lim[1])}) + '\n')
                time.sleep(0.05)
                
                # 3. Invert
                inv = inverts[i] if i < len(inverts) else False
                self._send(json.dumps({"cmd": "set_invert", "index": i, "inverted": bool(inv)}) + '\n')
                time.sleep(0.05)
                
            self.get_logger().info("Synchronisation de la calibration Arduino terminée.")
        except Exception as e:
            self.get_logger().error(f"Erreur lors de la synchronisation de la calibration : {e}")

    def _motion_callback(self, msg: String):
        """Envoie des commandes macro (stand, sit, walk, stop...)."""
        if not self._connected:
            return
        data = msg.data.strip()
        if data.startswith('{') and data.endswith('}'):
            # C'est un message JSON (ex: écriture servo individuelle)
            try:
                js = json.loads(data)
                # Si c'est une commande 'write' et qu'il n'y a pas de checksum, on le calcule et l'ajoute
                if js.get("cmd") == "write" and "chk" not in js:
                    idx = js.get("index")
                    angle = js.get("angle")
                    if idx is not None and angle is not None:
                        js["chk"] = (int(idx) + int(angle)) % 100
                        data = json.dumps(js)
            except Exception:
                pass
            payload = data + '\n'
        else:
            payload = json.dumps({'cmd': data}) + '\n'
        self.get_logger().info(f"Envoi au port série de l'Arduino: {payload.strip()}")
        self._send(payload)

    def _send(self, data: str):
        if not self._serial or not self._serial.is_open:
            self.get_logger().error('Tentative envoi serie mais port ferme')
            self._connected = False
            return
        try:
            self._serial.write(data.encode('utf-8'))
            self._serial.flush()
        except (serial.SerialException, OSError) as e:
            self.get_logger().error(f'Erreur envoi serie: {e} — tentative reset buffer')
            try:
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
                self._serial.write(data.encode('utf-8'))
                self._serial.flush()
            except Exception:
                self.get_logger().error('Echec retry envoi, deconnexion')
                self._connected = False
        except Exception as e:
            self.get_logger().error(f'Erreur inattendue envoi: {e}')
            self._connected = False

    def _publish_status(self, status: str):
        msg = String()
        msg.data = status
        self._status_pub.publish(msg)

    def _save_arduino_version(self, version: str):
        try:
            version_file = Path("/opt/spotbot/arduino_version.txt")
            if not version_file.exists() or version_file.read_text().strip() != version:
                version_file.parent.mkdir(parents=True, exist_ok=True)
                version_file.write_text(version)
                self.get_logger().info(f"Version de l'Arduino detectee et mise a jour : {version}")
        except Exception:
            pass


    def _send_heartbeat(self):
        if self._connected:
            self._send(json.dumps({'cmd': 'heartbeat'}) + '\n')


def main(args=None):
    rclpy.init(args=args)
    node = ArduinoBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
