#!/usr/bin/env python3
"""
SpotBot — Arduino Bridge Launch
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare
from launch_ros.actions import Node


def generate_launch_description():
    port_arg = DeclareLaunchArgument(
        'port', default_value='',
        description='Port serie Arduino (vide = auto-detection)'
    )
    auto_flash_arg = DeclareLaunchArgument(
        'auto_flash', default_value='false',
        description='Flash automatique du firmware au demarrage'
    )
    firmware_path_arg = DeclareLaunchArgument(
        'firmware_path', default_value='',
        description='Chemin vers le fichier .hex Arduino compile'
    )

    bridge_node = Node(
        package='spotbot_arduino_bridge',
        executable='arduino_bridge_node',
        name='arduino_bridge',
        parameters=[{
            'port':          LaunchConfiguration('port'),
            'auto_flash':    LaunchConfiguration('auto_flash'),
            'firmware_path': LaunchConfiguration('firmware_path'),
            'baudrate':      115200,
            'publish_rate':  50.0,
        }],
        output='screen',
    )

    return LaunchDescription([
        port_arg, auto_flash_arg, firmware_path_arg,
        bridge_node
    ])
