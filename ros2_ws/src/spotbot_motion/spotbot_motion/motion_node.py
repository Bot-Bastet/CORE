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

import rclpy
from rclpy.node import Node

from std_msgs.msg import String, Float32MultiArray
from geometry_msgs.msg import Twist
from sensor_msgs.msg import JointState

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
        self._mode  = 'stop'  # 'stand', 'walk', 'sit', 'stop'

        # Publishers
        self._joint_angles_pub = self.create_publisher(
            Float32MultiArray, '/cmd_joint_angles', 10
        )
        self._joint_state_pub = self.create_publisher(
            JointState, '/joint_states', 10
        )

        # Subscribers
        self.create_subscription(Twist,  '/cmd_vel',  self._cmd_vel_cb,  10)
        self.create_subscription(String, '/cmd_gait', self._cmd_gait_cb, 10)
        self.create_subscription(String, '/cmd_pose', self._cmd_pose_cb, 10)

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
        if cmd in ('stand', 'sit', 'stop'):
            self._mode = cmd
            self._vx = self._vy = self._omega = 0.0
            self.get_logger().info(f'Pose: {cmd}')

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------

    def _update(self):
        # Timeout cmd_vel (securite)
        if self._mode == 'walk' and (time.time() - self._last_cmd_time) > 0.5:
            self._mode = 'stand'
            self._vx = self._vy = self._omega = 0.0

        # Calculer les angles
        if self._mode == 'walk':
            angles_deg = self._gait.step(self._dt, self._vx, self._vy, self._omega)
        elif self._mode == 'sit':
            angles_deg = self._gait.sit()
        elif self._mode == 'stop':
            return  # Ne rien envoyer
        else:  # stand
            angles_deg = self._gait.stand()

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
