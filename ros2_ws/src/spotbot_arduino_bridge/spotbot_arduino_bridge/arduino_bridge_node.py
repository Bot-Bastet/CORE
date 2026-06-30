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

    BAUDRATE       = 115200
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
            String, '/cmd_motion',
            self._motion_callback, 10
        )
        self.create_subscription(
            Float32MultiArray, '/cmd_joint_calibration',
            self._calib_callback, 10
        )

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

        # Timer principal de lecture
        rate = self.get_parameter('publish_rate').value
        self._timer = self.create_timer(1.0 / rate, self._spin_serial)
        
        # Timer de heartbeat (1 Hz) pour le watchdog
        self._heartbeat_timer = self.create_timer(1.0, self._send_heartbeat)

        self.get_logger().info('Arduino Bridge Node demarré. Auto-detection du port...')
        self._try_connect()

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
            # Reset hardware de l'Arduino Mega
            self._serial.dtr = False
            self._serial.rts = False
            time.sleep(0.5)
            self._serial.dtr = True
            self._serial.rts = True
            time.sleep(2.5)  # Attendre reset Arduino et fin du bootloader
            self._serial.reset_input_buffer()
            self._connected = True
            self.get_logger().info(f'Arduino connecte sur {port} @ {self._baudrate} baud')
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
                    # self.get_logger().info(f'RAW LINE: {line}')  # Supprimé pour éviter la surcharge de logging
                    self._parse_line(line)
        except (serial.SerialException, OSError, Exception) as e:
            self.get_logger().error(f'Perte connexion Arduino: {e}')
            self._connected = False
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
        except json.JSONDecodeError:
            if line.strip():
                self.get_logger().warn(f"Ligne non-JSON reçue de l'Arduino: {line}")
            return

        if 'imu' not in data and 'sonar' not in data and 'version' not in data:
            self.get_logger().info(f"Message reçu de l'Arduino: {line}")

        if 'imu' in data:
            self._publish_imu_bno085(data['imu'])

        if 'status' in data and data.get('bno085') is False:
            self.get_logger().error('BNO085 non detecte sur l\'Arduino! Verifiez I2C (0x4A) et les cables.')

        if 'sonar' in data and isinstance(data['sonar'], dict):
            self._publish_sonar(data['sonar'])

        if 'version' in data:
            self._save_arduino_version(data['version'])

    def _publish_imu_bno085(self, imu_raw: dict):
        """BNO085 : quaternion fused + accéleration linéaire + gyro -> /imu/data et /imu/data_raw."""
        msg = Imu()
        msg.header.stamp    = self.get_clock().now().to_msg()
        msg.header.frame_id = self.IMU_FRAME

        # Quaternion (encodé * 10000 côté Arduino)
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

        # Covariance selon niveau de calibration BNO085 (0-3)
        calib = imu_raw.get('calib', 0)
        oc = {3: 0.0001, 2: 0.001, 1: 0.01, 0: 0.1}.get(calib, 0.01)
        msg.orientation_covariance = [oc, 0, 0, 0, oc, 0, 0, 0, oc]

        # Accélération linéaire (gravité soustraite par BNO085, encodée cm/s² * 100)
        msg.linear_acceleration.x = imu_raw.get('lax', 0) / 100.0
        msg.linear_acceleration.y = imu_raw.get('lay', 0) / 100.0
        msg.linear_acceleration.z = imu_raw.get('laz', 0) / 100.0
        cov_a = [0.005, 0, 0, 0, 0.005, 0, 0, 0, 0.005]
        msg.linear_acceleration_covariance = cov_a

        # Gyroscope (encodé mrad/s * 1000)
        msg.angular_velocity.x = imu_raw.get('gx', 0) / 1000.0
        msg.angular_velocity.y = imu_raw.get('gy', 0) / 1000.0
        msg.angular_velocity.z = imu_raw.get('gz', 0) / 1000.0
        cov_g = [0.0003, 0, 0, 0, 0.0003, 0, 0, 0, 0.0003]
        msg.angular_velocity_covariance = cov_g

        self._imu_pub.publish(msg)      # /imu/data     (orientation valide)
        self._imu_raw_pub.publish(msg)  # /imu/data_raw (compatibilité rtabmap)

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
        """Envoie les angles des 12 servos a l'Arduino avec application des offsets."""
        if not self._connected:
            return
        angles = list(msg.data)[:12]
        angles += [90.0] * (12 - len(angles))  # completer si besoin
        
        # Appliquer les offsets de calibration
        for i in range(12):
            angles[i] += self._offsets[i]
            
        payload = json.dumps({'servos': [round(a, 1) for a in angles]}) + '\n'
        if not hasattr(self, '_last_servos_payload') or self._last_servos_payload != payload:
            self._last_servos_payload = payload
            self.get_logger().info(f"Envoi au port série de l'Arduino (servos) : {payload.strip()}")
        self._send(payload)

    def _calib_callback(self, msg: Float32MultiArray):
        """Enregistre les nouveaux offsets de calibration et les sauvegarde."""
        self._offsets = list(msg.data)[:12]
        self._offsets += [0.0] * (12 - len(self._offsets))
        self.get_logger().info(f"Nouveaux offsets recus et appliques : {self._offsets}")
        try:
            p = Path("/opt/spotbot/config/calibration.json")
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"offsets": self._offsets}, f)
        except Exception as e:
            self.get_logger().error(f"Erreur sauvegarde offsets : {e}")

    def _motion_callback(self, msg: String):
        """Envoie des commandes macro (stand, sit, walk, stop...)."""
        if not self._connected:
            return
        data = msg.data.strip()
        if data.startswith('{') and data.endswith('}'):
            payload = data + '\n'
        else:
            payload = json.dumps({'cmd': data}) + '\n'
        self.get_logger().info(f"Envoi au port série de l'Arduino: {payload.strip()}")
        self._send(payload)

    def _send(self, data: str):
        try:
            self._serial.write(data.encode('utf-8'))
            self._serial.flush()
        except (serial.SerialException, OSError, Exception) as e:
            self.get_logger().error(f'Erreur envoi: {e}')
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
