import sys
import json
import time
import math
import os
import queue
import threading
import subprocess
import rclpy

# Debug log to a file so we can see startup progress even when stdout/stderr are captured.
_DEBUG_LOG_PATH = "/tmp/ros2_listener_debug.log"
try:
    with open(_DEBUG_LOG_PATH, "a") as _df:
        _df.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ros2_listener module loaded\n")
except Exception:
    pass
from rclpy.node import Node
from sensor_msgs.msg import JointState, Imu
from nav_msgs.msg import Odometry
from std_msgs.msg import Float32MultiArray, String
from geometry_msgs.msg import PoseStamped, Twist

from rclpy.qos import QoSProfile, ReliabilityPolicy

class ROS2TelemetryListener(Node):
    def __init__(self):
        super().__init__('ros2_telemetry_listener')
        
        self.joints = [90.0] * 12
        # FIX NATIF v2 : le firmware Arduino gère 100% la calibration (EEPROM +
        # q_offset^-1 dans readBNO085). ros2_listener ne fait que calculer RPY
        # pour le dashboard, sans aucun état de calibration local.
        self.imu = {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}
        self.pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        self.path = []
        self.topics_list = []
        self.cam_subscribers = {1: None, 2: None}
        self.cam_processes = {1: None, 2: None}

        
        # Subscriptions
        qos_best_effort = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.create_subscription(JointState, '/joint_states', self.joint_callback, 10)
        self.create_subscription(Imu, '/imu/data', self.imu_callback, qos_best_effort)
        # Real Arduino servo positions (from firmware, not motion_node)
        self.create_subscription(Float32MultiArray, '/arduino/servo_positions', self.servo_pos_callback, 10)
        self.create_subscription(Odometry, '/odom', self.odom_callback, 10)
        self.create_subscription(PoseStamped, '/orb_slam3/camera_pose', self.slam_pose_callback, 10)
        
        # Publisher for calibration offsets
        self.calib_pub = self.create_publisher(Float32MultiArray, '/cmd_joint_calibration', 10)
        # Publisher for streaming commands (delegated to streaming_engine)
        self.stream_cmd_pub = self.create_publisher(String, '/streaming/command', 10)
        self.angles_pub = self.create_publisher(Float32MultiArray, '/cmd_joint_angles', 10)
        self.manual_angles_pub = self.create_publisher(Float32MultiArray, '/cmd_manual_joint_angles', 10)
        self.motion_pub = self.create_publisher(String, '/cmd_motion', 10)
        self.pose_pub = self.create_publisher(String, '/cmd_pose', 10)
        self.vel_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.goal_pub = self.create_publisher(PoseStamped, '/goal_pose', 10)
        self.posture_pub = self.create_publisher(String, '/cmd_posture', 10)
        self.telemetry_counter = 0
        
        # Timer to print state to stdout as JSON (5 Hz)
        self.create_timer(0.2, self.publish_telemetry)
        
        # Timer to update topics list (every 5s)
        self.create_timer(5.0, self.check_topics)
        
        # Stdin listener thread
        t = threading.Thread(target=self.stdin_loop, daemon=True)
        t.start()
        
    def joint_callback(self, msg):
        if msg.position:
            # Map joints
            for i, pos in enumerate(msg.position):
                if i < 12:
                    # Convert rad to deg, centered around 90 deg
                    self.joints[i] = round(math.degrees(pos) + 90.0, 1)
                    
    def imu_callback(self, msg):
        q = msg.orientation
        sinr_cosp = 2 * (q.w * q.x + q.y * q.z)
        cosr_cosp = 1 - 2 * (q.x * q.x + q.y * q.y)
        roll = math.atan2(sinr_cosp, cosr_cosp)

        sinp = 2 * (q.w * q.y - q.z * q.x)
        if abs(sinp) >= 1:
            pitch = math.copysign(math.pi / 2, sinp)
        else:
            pitch = math.asin(sinp)

        siny_cosp = 2 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1 - 2 * (q.y * q.y + q.z * q.z)
        yaw = math.atan2(siny_cosp, cosy_cosp)
        
        # FIX NATIF : ne PAS soustraire de offset ici. Le quaternion recu sur
        # /imu/data est deja calibre par arduino_bridge_node.py. Conversion RPY directe.
        roll_deg = round(math.degrees(roll), 1)
        pitch_deg = round(math.degrees(pitch), 1)
        yaw_deg = round(math.degrees(yaw), 1)
        # Normalize yaw to [-180, 180]
        while yaw_deg > 180: yaw_deg -= 360
        while yaw_deg < -180: yaw_deg += 360
        self.imu = {
            "roll": roll_deg,
            "pitch": pitch_deg,
            "yaw": yaw_deg
        }
        
    def _update_pose_from_msg(self, pos, ori):
        """Met a jour self.pose et self.path depuis une position + orientation."""
        siny_cosp = 2 * (ori.w * ori.z + ori.x * ori.y)
        cosy_cosp = 1 - 2 * (ori.y * ori.y + ori.z * ori.z)
        yaw = math.atan2(siny_cosp, cosy_cosp)
        
        self.pose = {
            "x": round(pos.x, 3),
            "y": round(pos.y, 3),
            "theta": round(yaw, 3)
        }
        
        # Append to path if moved significantly
        if not self.path or math.hypot(pos.x - self.path[-1]["x"], pos.y - self.path[-1]["y"]) > 0.05:
            self.path.append({"x": self.pose["x"], "y": self.pose["y"], "theta": self.pose["theta"]})
            if len(self.path) > 150:
                self.path.pop(0)

    def servo_pos_callback(self, msg):
        """Real Arduino servo positions (from firmware, not motion_node).
        Uses a separate field to avoid race conditions with /joint_states."""
        if msg.data and len(msg.data) >= 12:
            self.servo_angles = [round(a, 1) for a in msg.data[:12]]

    def odom_callback(self, msg):
        self._update_pose_from_msg(msg.pose.pose.position, msg.pose.pose.orientation)

    def slam_pose_callback(self, msg):
        self._update_pose_from_msg(msg.pose.position, msg.pose.orientation)

    def check_topics(self):
        try:
            topic_info = self.get_topic_names_and_types()
            # Calculate mock frequency or check if active
            self.topics_list = [{"name": name, "type": types[0], "hz": 10.0} for name, types in topic_info]
        except Exception:
            pass
            
    def get_camera_devices(self):
        import os, json
        from pathlib import Path
        default_mapping = {
            1: "/dev/video0",
            2: "/dev/video2"
        }
        mapping_file = Path("/opt/spotbot/config/camera_mapping.json")
        if mapping_file.exists():
            try:
                data = json.loads(mapping_file.read_text())
                left = data.get("left")
                right = data.get("right")
                if left:
                    default_mapping[1] = left
                if right:
                    default_mapping[2] = right
            except Exception:
                pass
        return default_mapping

    def publish_telemetry(self):
        import os
        self.telemetry_counter += 1
        
        # Fast telemetry (every tick: 0.2s / 5 Hz)
        data = {
            "type": "telemetry_diagnostics",
            "joints": self.joints,
            "imu": self.imu,
            "pose": self.pose
        }
        # Include real Arduino servo positions if available (separate from motion_node joints)
        if hasattr(self, 'servo_angles') and self.servo_angles:
            data["servo_angles"] = self.servo_angles
        
        # Slow telemetry (every 15 ticks = 3.0s)
        if self.telemetry_counter % 15 == 0:
            mapping = self.get_camera_devices()
            has_cam1 = os.path.exists(mapping[1])
            has_cam2 = os.path.exists(mapping[2])
            data.update({
                "path": self.path,
                "topics": self.topics_list,
                "cameras": {"cam1": has_cam1, "cam2": has_cam2}
            })
            
        print(json.dumps(data))
        sys.stdout.flush()

    def stdin_loop(self):
        for line in sys.stdin:
            try:
                msg_json = json.loads(line.strip())
                if msg_json.get("type") == "motor_calibration":
                    offsets = msg_json.get("offsets", [])
                    if len(offsets) == 12:
                        cal_msg = Float32MultiArray()
                        cal_msg.data = [float(x) for x in offsets]
                        self.calib_pub.publish(cal_msg)
                elif msg_json.get("type") == "manual_joint_control":
                    angles = msg_json.get("angles", [])
                    if len(angles) == 12:
                        self.get_logger().info(f"Publication de manual_joint_control sur /cmd_manual_joint_angles: {angles}")
                        ang_msg = Float32MultiArray()
                        ang_msg.data = [float(x) for x in angles]
                        self.manual_angles_pub.publish(ang_msg)
                elif msg_json.get("type") == "arduino_cmd":
                    cmd = msg_json.get("cmd", "")
                    if cmd:
                        self.get_logger().info(f"[ros2_listener] arduino_cmd reçu : {cmd} payload={msg_json}")
                        motion_msg = String()
                        if cmd in ["attach", "detach", "write", "set_offset", "set_limit", "set_invert"]:
                            compact = {}
                            for k in ["cmd", "index", "angle", "chk", "offset", "min", "max", "inverted", "manual", "raw"]:
                                if k in msg_json:
                                    compact[k] = msg_json[k]
                            if cmd in ["attach", "write"] and "manual" not in compact:
                                compact["manual"] = True
                                self.get_logger().info(f"[ros2_listener] Forced manual=true for arduino_cmd {cmd}")
                            if cmd == "write" and "chk" not in compact:
                                idx = compact.get("index")
                                ang = compact.get("angle")
                                if idx is not None and ang is not None:
                                    try:
                                        compact["chk"] = (int(idx) + int(float(ang))) % 100
                                    except Exception:
                                        pass
                            motion_msg.data = json.dumps(compact)
                        else:
                            motion_msg.data = cmd
                            # FIX NATIF v2 : le mot reset_imu est juste transmis tel quel
                            # a l'Arduino via /cmd_motion (-> arduino_bridge -> serial ->
                            # resetBNO085() qui arme flag_capture_initial_pose). Le firmware
                            # Arduino capture la prochaine pose, l'offset EEPROM, et
                            # applique q_offset^-1 sur toutes les trames suivants.
                            if cmd == "reset_imu":
                                self.get_logger().info("IMU reset -> transmis a l'Arduino (capture asynchrone + EEPROM persist)")
                            
                            # Si c'est une commande de pose pour stopper ou réactiver le robot,
                            # on la transmet aussi à motion_node.py via /cmd_pose.
                            if cmd in ["stop", "stand", "sit"]:
                                pose_msg = String()
                                pose_msg.data = cmd
                                self.pose_pub.publish(pose_msg)
                                self.get_logger().info(f"Pose cmd '{cmd}' transmise aussi a /cmd_pose pour motion_node")
                        self.motion_pub.publish(motion_msg)
                elif msg_json.get("type") == "robot_posture":
                    key = msg_json.get("key")
                    value = msg_json.get("value")
                    if key and value is not None:
                        posture_msg = String()
                        posture_msg.data = json.dumps({key: value})
                        self.posture_pub.publish(posture_msg)
                elif msg_json.get("type") == "cmd_vel":
                    twist = Twist()
                    twist.linear.x = float(msg_json.get("linear", 0.0))
                    twist.linear.y = float(msg_json.get("lateral", 0.0))
                    twist.angular.z = float(msg_json.get("angular", 0.0))
                    self.vel_pub.publish(twist)
                elif msg_json.get("type") == "nav_goal":
                    goal = PoseStamped()
                    goal.header.stamp = self.get_clock().now().to_msg()
                    goal.header.frame_id = "map"
                    goal.pose.position.x = float(msg_json.get("x", 0.0))
                    goal.pose.position.y = float(msg_json.get("y", 0.0))
                    goal.pose.orientation.w = 1.0
                    self.goal_pub.publish(goal)
                elif msg_json.get("type") == "start_camera":
                    cam_id = msg_json.get("camera", 1)
                    self.stream_cmd_pub.publish(String(data=json.dumps({"command": "start", "camera": cam_id})))
                elif msg_json.get("type") == "stop_camera":
                    cam_id = msg_json.get("camera", 1)
                    self.stream_cmd_pub.publish(String(data=json.dumps({"command": "stop", "camera": cam_id})))
            except Exception:
                pass
            # NOTE: dead `q.put_nowait(frame_data)` block removed — `q` and
            # `frame_data` were undefined, which killed this daemon thread
            # on the first `for` iteration after the inner try/except, so
            # `start_camera`/`stop_camera` messages from the agent never
            # reached the /streaming/command publisher.

        # EOF reached: the parent agent.py died (or closed its write end of
        # the stdin pipe). Log the condition then exit the process immediately
        # to avoid an orphaned ROS 2 node that would block the next restart.
        print("[ros2_listener] EOF sur stdin — arrêt du processus", file=sys.stderr, flush=True)
        os._exit(0)


def _debug_log(msg):
    try:
        with open(_DEBUG_LOG_PATH, "a") as _df:
            _df.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def main():
    print("[ros2_listener] main() démarré", flush=True)
    _debug_log("main() started")
    _debug_log(f"ROS_DOMAIN_ID={os.environ.get('ROS_DOMAIN_ID', 'unset')}")
    _debug_log(f"RMW_IMPLEMENTATION={os.environ.get('RMW_IMPLEMENTATION', 'unset')}")
    rclpy.init()
    print("[ros2_listener] rclpy.init() terminé", flush=True)
    _debug_log("rclpy.init() done")
    node = None
    try:
        node = ROS2TelemetryListener()
        print("[ros2_listener] Node ROS2 démarré avec succès.", flush=True)
        _debug_log("ROS2TelemetryListener created")
        rclpy.spin(node)
    except Exception as e:
        print(f"[ros2_listener] Erreur fatale: {type(e).__name__}: {e}", flush=True)
        _debug_log(f"fatal error: {type(e).__name__}: {e} | args={getattr(e, 'args', None)}")
        import traceback
        traceback.print_exc()
        _debug_log(traceback.format_exc())
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
