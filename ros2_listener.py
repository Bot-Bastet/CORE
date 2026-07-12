import sys
import json
import time
import math
import os
import queue
import threading
import subprocess
import rclpy
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
        self.create_subscription(Odometry, '/odom', self.odom_callback, 10)
        self.create_subscription(PoseStamped, '/orb_slam3/camera_pose', self.slam_pose_callback, 10)
        
        # Publisher for calibration offsets
        self.calib_pub = self.create_publisher(Float32MultiArray, '/cmd_joint_calibration', 10)
        # Publisher for streaming commands (delegated to streaming_engine)
        self.stream_cmd_pub = self.create_publisher(String, '/streaming/command', 10)
        self.angles_pub = self.create_publisher(Float32MultiArray, '/cmd_joint_angles', 10)
        self.motion_pub = self.create_publisher(String, '/cmd_motion', 10)
        self.pose_pub = self.create_publisher(String, '/cmd_pose', 10)
        self.vel_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.goal_pub = self.create_publisher(PoseStamped, '/goal_pose', 10)
        self.posture_pub = self.create_publisher(String, '/cmd_posture', 10)
        
        # Timer to print state to stdout as JSON (2 Hz)
        self.create_timer(0.5, self.publish_telemetry)
        
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
        mapping = self.get_camera_devices()
        has_cam1 = os.path.exists(mapping[1])
        has_cam2 = os.path.exists(mapping[2])
        data = {
            "type": "telemetry_diagnostics",
            "joints": self.joints,
            "imu": self.imu,
            "pose": self.pose,
            "path": self.path,
            "topics": self.topics_list,
            "cameras": {"cam1": has_cam1, "cam2": has_cam2}
        }
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
                        self.get_logger().info(f"Publication de manual_joint_control sur /cmd_joint_angles: {angles}")
                        ang_msg = Float32MultiArray()
                        ang_msg.data = [float(x) for x in angles]
                        self.angles_pub.publish(ang_msg)
                elif msg_json.get("type") == "arduino_cmd":
                    cmd = msg_json.get("cmd", "")
                    if cmd:
                        motion_msg = String()
                        if cmd in ["attach", "detach", "write"]:
                            compact = {}
                            for k in ["cmd", "index", "angle", "chk"]:
                                if k in msg_json:
                                    compact[k] = msg_json[k]
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
        # the stdin pipe). Without this os._exit, the main thread keeps
        # rclpy.spin() alive forever, leaving an orphaned ROS 2 node with
        # the same name as the next agent restart's node → duplicate-node
        # name conflicts that silently break DDS discovery (the
        # streaming_engine stops seeing the new /streaming/command
        # publisher, so ffmpeg never spawns).
        os._exit(0)

def main():
    rclpy.init()
    node = ROS2TelemetryListener()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
