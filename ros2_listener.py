import sys
import json
import time
import math
import threading
import subprocess
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState, Imu, Image
from nav_msgs.msg import Odometry
from std_msgs.msg import Float32MultiArray

class ROS2TelemetryListener(Node):
    def __init__(self):
        super().__init__('ros2_telemetry_listener')
        
        self.joints = [90.0] * 12
        self.imu = {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}
        self.pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
        self.path = []
        self.topics_list = []
        self.cam_subscribers = {1: None, 2: None}
        self.cam_processes = {1: None, 2: None}
        
        # Subscriptions
        self.create_subscription(JointState, '/joint_states', self.joint_callback, 10)
        self.create_subscription(Imu, '/imu/data', self.imu_callback, 10)
        self.create_subscription(Odometry, '/odom', self.odom_callback, 10)
        
        # Publisher for calibration offsets
        self.calib_pub = self.create_publisher(Float32MultiArray, '/cmd_joint_calibration', 10)
        
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
        
        self.imu = {
            "roll": round(math.degrees(roll), 1),
            "pitch": round(math.degrees(pitch), 1),
            "yaw": round(math.degrees(yaw), 1)
        }
        
    def odom_callback(self, msg):
        pos = msg.pose.pose.position
        ori = msg.pose.pose.orientation
        
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

    def check_topics(self):
        try:
            topic_info = self.get_topic_names_and_types()
            # Calculate mock frequency or check if active
            self.topics_list = [{"name": name, "type": types[0], "hz": 10.0} for name, types in topic_info]
        except Exception:
            pass
            
    def publish_telemetry(self):
        data = {
            "type": "telemetry_diagnostics",
            "joints": self.joints,
            "imu": self.imu,
            "pose": self.pose,
            "path": self.path,
            "topics": self.topics_list
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
                elif msg_json.get("type") == "start_camera":
                    cam_id = msg_json.get("camera", 1)
                    self.start_cam_stream(cam_id)
                elif msg_json.get("type") == "stop_camera":
                    cam_id = msg_json.get("camera", 1)
                    self.stop_cam_stream(cam_id)
            except Exception:
                pass

    def start_cam_stream(self, cam_id):
        self.stop_cam_stream(cam_id)
        topics = ["/camera/image_raw", "/camera/left/image_raw"] if cam_id == 1 else ["/camera2/image_raw", "/camera/right/image_raw"]
        self.cam_subscribers[cam_id] = []
        for topic in topics:
            sub = self.create_subscription(
                Image,
                topic,
                lambda msg, cid=cam_id: self.image_callback(msg, cid),
                10
            )
            self.cam_subscribers[cam_id].append(sub)

    def stop_cam_stream(self, cam_id):
        if self.cam_processes[cam_id] is not None:
            try:
                self.cam_processes[cam_id].stdin.close()
                self.cam_processes[cam_id].terminate()
                self.cam_processes[cam_id].wait(timeout=2)
            except Exception:
                pass
            self.cam_processes[cam_id] = None
        if self.cam_subscribers[cam_id] is not None:
            if isinstance(self.cam_subscribers[cam_id], list):
                for sub in self.cam_subscribers[cam_id]:
                    if sub is not None:
                        self.destroy_subscription(sub)
            else:
                self.destroy_subscription(self.cam_subscribers[cam_id])
            self.cam_subscribers[cam_id] = None

    def image_callback(self, msg, cam_id):
        if self.cam_processes[cam_id] is None:
            enc = msg.encoding
            pix_fmt = "rgb24"
            if enc == "rgb8":
                pix_fmt = "rgb24"
            elif enc == "bgr8":
                pix_fmt = "bgr24"
            elif enc in ("yuyv", "yuyv422"):
                pix_fmt = "yuyv422"
            elif enc == "mono8":
                pix_fmt = "gray"
                
            rtsp_url = f"rtsp://ha.arthonetwork.fr:48554/robot/cam{cam_id}"
            cmd = [
                "ffmpeg",
                "-y",
                "-f", "rawvideo",
                "-pix_fmt", pix_fmt,
                "-s", f"{msg.width}x{msg.height}",
                "-r", "15",
                "-i", "-",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-f", "rtsp",
                "-rtsp_transport", "tcp",
                rtsp_url
            ]
            try:
                self.cam_processes[cam_id] = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                return
                
        if self.cam_processes[cam_id] and self.cam_processes[cam_id].stdin:
            try:
                self.cam_processes[cam_id].stdin.write(bytes(msg.data))
                self.cam_processes[cam_id].stdin.flush()
            except Exception:
                self.stop_cam_stream(cam_id)

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
