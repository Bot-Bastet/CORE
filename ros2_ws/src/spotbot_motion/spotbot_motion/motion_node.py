#!/usr/bin/env python3
"""
SpotBot — Motion Node ROS 2
============================
Noeud principal de controle de mouvement.

Souscrit:
  /cmd_vel          (geometry_msgs/Twist) — commandes de deplacement
  /cmd_gait         (std_msgs/String)     — changement de demarche (trot/crawl/bound)
  /cmd_pose         (std_msgs/String)     — (stand, sit, stop)

Publie:
  /cmd_joint_angles (std_msgs/Float32MultiArray) — 12 angles vers l'Arduino
  /joint_states     (sensor_msgs/JointState)     — etat des joints pour RViz
"""

import time
import math

import rclpy
from rclpy.node import Node

from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String, Float32MultiArray
from geometry_msgs.msg import Twist, Quaternion
from sensor_msgs.msg import JointState, Imu
from nav_msgs.msg import Odometry

from .gait_controller import GaitController


class MotionNode(Node):
    """Noeud ROS 2 de controle de mouvement SpotBot."""

    JOINT_NAMES = [
        'fr_abad_joint', 'fr_upper_joint', 'fr_lower_joint',
        'fl_abad_joint', 'fl_upper_joint', 'fl_lower_joint',
        'br_abad_joint', 'br_upper_joint', 'br_lower_joint',
        'bl_abad_joint', 'bl_upper_joint', 'bl_lower_joint',
    ]

    def __init__(self):
        super().__init__('spotbot_motion')

        self.declare_parameter('gait',       'trot')
        self.declare_parameter('gait_freq',   1.0)
        self.declare_parameter('update_rate', 50.0)
        self.declare_parameter('max_speed',   0.3)

        gait     = self.get_parameter('gait').value
        freq     = self.get_parameter('gait_freq').value
        rate     = self.get_parameter('update_rate').value
        self._max_speed = self.get_parameter('max_speed').value

        self._gait = GaitController(gait=gait, freq=freq)
        self._dt   = 1.0 / rate

        self._vx    = 0.0
        self._vy    = 0.0
        self._omega = 0.0
        self._mode  = 'idle'  # 'stand', 'walk', 'sit', 'stop', 'idle' — idle = no publishing

        # Variables d'odometrie
        self._odom_x = 0.0
        self._odom_y = 0.0
        self._odom_yaw = 0.0

        # Publishers
        self._joint_angles_pub = self.create_publisher(
            Float32MultiArray, '/cmd_joint_angles', 10
        )
        self._joint_state_pub = self.create_publisher(
            JointState, '/joint_states', 10
        )
        self._odom_pub = self.create_publisher(
            Odometry, '/odom/kinematic', 10
        )

        # Subscribers
        self.create_subscription(Twist,  '/cmd_vel',  self._cmd_vel_cb,  10)
        self.create_subscription(String, '/cmd_gait', self._cmd_gait_cb, 10)
        self.create_subscription(String, '/cmd_pose', self._cmd_pose_cb, 10)
        self.create_subscription(String, '/cmd_posture', self._cmd_posture_cb, 10)
        
        imu_qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, depth=10)
        self.create_subscription(Imu,    '/imu/data', self._imu_cb,      imu_qos)

        # Timer principal
        self._last_cmd_time = time.time()
        self._timer = self.create_timer(self._dt, self._update)

        self.get_logger().info(f'Motion Node demarre | gait={gait} freq={freq}Hz rate={rate}Hz')

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _cmd_vel_cb(self, msg: Twist):
        max_s = self._max_speed
        self._vx    = max(-max_s, min(max_s, msg.linear.x))
        self._vy    = max(-max_s, min(max_s, msg.linear.y))
        self._omega = max(-1.0, min(1.0, msg.angular.z))
        self._last_cmd_time = time.time()

        if abs(self._vx) > 0.01 or abs(self._vy) > 0.01 or abs(self._omega) > 0.01:
            self._mode = 'walk'
        else:
            self._mode = 'stand'

    def _cmd_gait_cb(self, msg: String):
        self._gait.set_gait(msg.data)
        self.get_logger().info(f'Demarche: {msg.data}')

    def _cmd_pose_cb(self, msg: String):
        cmd = msg.data.lower().strip()
        if cmd in ('stand', 'sit', 'stop', 'idle'):
            self._mode = cmd
            self._vx = self._vy = self._omega = 0.0
            self.get_logger().info(f'Pose: {cmd}')

    def _cmd_posture_cb(self, msg: String):
        import json
        try:
            data = json.loads(msg.data)
            for k, v in data.items():
                if k == "height":
                    self._gait.body_height_multiplier = float(v) / 100.0
                    self.get_logger().info(f"Manual Height set to {v}%")
                elif k == "roll":
                    self._gait.manual_roll = math.radians(float(v))
                    self.get_logger().info(f"Manual Roll set to {v} deg")
                elif k == "pitch":
                    self._gait.manual_pitch = math.radians(float(v))
                    self.get_logger().info(f"Manual Pitch set to {v} deg")
                elif k == "yaw":
                    self._gait.manual_yaw = math.radians(float(v))
                    self.get_logger().info(f"Manual Yaw set to {v} deg")
        except Exception as e:
            self.get_logger().error(f"Error parsing posture: {e}")

    def _imu_cb(self, msg: Imu):
        import math
        q = msg.orientation
        # Calcul du roulis (roll, rotation axe X)
        sinr_cosp = 2 * (q.w * q.x + q.y * q.z)
        cosr_cosp = 1 - 2 * (q.x * q.x + q.y * q.y)
        roll = math.atan2(sinr_cosp, cosr_cosp)

        # Calcul du tangage (pitch, rotation axe Y)
        sinp = 2 * (q.w * q.y - q.z * q.x)
        if abs(sinp) >= 1:
            pitch = math.copysign(math.pi / 2, sinp)
        else:
            pitch = math.asin(sinp)

        self._gait.set_imu_feedback(roll, pitch)

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------

    def _update(self):
        # Timeout cmd_vel (securite)
        if self._mode == 'walk' and (time.time() - self._last_cmd_time) > 0.5:
            self._mode = 'stand'
            self._vx = self._vy = self._omega = 0.0

        # IDLE mode: do NOT publish any joint angles (robot off, servos detached)
        if self._mode in ('idle', 'stop'):
            return

        # Calculer les angles
        if self._mode == 'walk':
            angles_deg = self._gait.step(self._dt, self._vx, self._vy, self._omega)
            # Estimation de la vitesse reelle (efficacite moyenne de 90%)
            gait_efficiency = 0.90
            vx_est = self._vx * gait_efficiency
            vy_est = self._vy * gait_efficiency
            omega_est = self._omega * gait_efficiency
        elif self._mode == 'sit':
            angles_deg = self._gait.sit()
            vx_est = vy_est = omega_est = 0.0
        elif self._mode == 'stop':
            return  # Ne rien envoyer
        else:  # stand
            angles_deg = self._gait.stand()
            vx_est = vy_est = omega_est = 0.0

        # Integration de la pose odometrique (repere global odom)
        dx = vx_est * self._dt
        dy = vy_est * self._dt
        dyaw = omega_est * self._dt

        self._odom_yaw += dyaw
        # Rotation de l'increment de deplacement dans le repere global
        self._odom_x += dx * math.cos(self._odom_yaw) - dy * math.sin(self._odom_yaw)
        self._odom_y += dx * math.sin(self._odom_yaw) + dy * math.cos(self._odom_yaw)

        # Publier l'odometrie cinematique
        self._publish_odometry(vx_est, vy_est, omega_est)

        # Publier les angles
        self._publish_joints(angles_deg)

    def _publish_joints(self, angles_deg: list):
        import math

        # Float32MultiArray pour l'Arduino
        msg = Float32MultiArray()
        msg.data = [float(a) for a in angles_deg[:12]]
        self._joint_angles_pub.publish(msg)

        # JointState pour RViz
        js = JointState()
        js.header.stamp = self.get_clock().now().to_msg()
        js.name         = self.JOINT_NAMES
        js.position     = [math.radians(a - 90.0) for a in angles_deg[:12]]
        self._joint_state_pub.publish(js)

    def _publish_odometry(self, vx: float, vy: float, omega: float):
        import math

        odom = Odometry()
        odom.header.stamp = self.get_clock().now().to_msg()
        odom.header.frame_id = 'odom'
        odom.child_frame_id = 'base_link'

        # Position
        odom.pose.pose.position.x = self._odom_x
        odom.pose.pose.position.y = self._odom_y
        odom.pose.pose.position.z = 0.0

        # Orientation yaw -> Quaternion
        cy = math.cos(self._odom_yaw * 0.5)
        sy = math.sin(self._odom_yaw * 0.5)
        q = Quaternion()
        q.w = cy
        q.x = 0.0
        q.y = 0.0
        q.z = sy
        odom.pose.pose.orientation = q

        # Twist (vitesses dans base_link)
        odom.twist.twist.linear.x = vx
        odom.twist.twist.linear.y = vy
        odom.twist.twist.angular.z = omega

        # Covariances standard pour odom_kinematic
        odom.pose.covariance = [
            0.01, 0.0,  0.0,  0.0,  0.0,  0.0,
            0.0,  0.01, 0.0,  0.0,  0.0,  0.0,
            0.0,  0.0,  999.0, 0.0,  0.0,  0.0,
            0.0,  0.0,  0.0,  999.0, 0.0,  0.0,
            0.0,  0.0,  0.0,  0.0,  999.0, 0.0,
            0.0,  0.0,  0.0,  0.0,  0.0,  0.05
        ]

        odom.twist.covariance = [
            0.02, 0.0,  0.0,  0.0,  0.0,  0.0,
            0.0,  0.02, 0.0,  0.0,  0.0,  0.0,
            0.0,  0.0,  999.0, 0.0,  0.0,  0.0,
            0.0,  0.0,  0.0,  999.0, 0.0,  0.0,
            0.0,  0.0,  0.0,  0.0,  999.0, 0.0,
            0.0,  0.0,  0.0,  0.0,  0.0,  0.1
        ]

        self._odom_pub.publish(odom)


def main(args=None):
    rclpy.init(args=args)
    node = MotionNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
