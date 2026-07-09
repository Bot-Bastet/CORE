#!/usr/bin/env python3
"""
SpotBot — Launch principal
Usage:
  ros2 launch spotbot_bringup spotbot.launch.py mode:=mono
  ros2 launch spotbot_bringup spotbot.launch.py mode:=stereo
  ros2 launch spotbot_bringup spotbot.launch.py mode:=mono slam:=false
"""

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument, IncludeLaunchDescription,
    OpaqueFunction, LogInfo
)
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare
from launch_ros.actions import Node


def generate_launch_description():
    # ---- Arguments ----
    mode_arg = DeclareLaunchArgument(
        'mode',
        default_value='mono',
        description='Mode camera: mono | stereo'
    )
    slam_arg = DeclareLaunchArgument(
        'slam',
        default_value='true',
        description='Activer le V-SLAM rtabmap: true | false'
    )
    nav_arg = DeclareLaunchArgument(
        'nav',
        default_value='false',
        description='Activer Nav2: true | false'
    )
    arduino_arg = DeclareLaunchArgument(
        'arduino',
        default_value='true',
        description='Activer le bridge Arduino: true | false'
    )
    rviz_arg = DeclareLaunchArgument(
        'rviz',
        default_value='false',
        description='Lancer RViz2: true | false'
    )
    streaming_arg = DeclareLaunchArgument(
        'streaming',
        default_value='true',
        description='Activer le streaming camera (LAN/RTSP): true | false'
    )
    wifi_watchdog_arg = DeclareLaunchArgument(
        'wifi_watchdog',
        default_value='true',
        description='Activer le watchdog WiFi Alfa: true | false'
    )

    mode    = LaunchConfiguration('mode')
    slam    = LaunchConfiguration('slam')
    nav     = LaunchConfiguration('nav')
    arduino = LaunchConfiguration('arduino')
    rviz    = LaunchConfiguration('rviz')

    # ---- Robot State Publisher ----
    description_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            PathJoinSubstitution([
                FindPackageShare('spotbot_description'), 'launch', 'description.launch.py'
            ])
        ])
    )

    # ---- Camera mono ----
    camera_mono_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            PathJoinSubstitution([
                FindPackageShare('spotbot_bringup'), 'launch', 'camera_mono.launch.py'
            ])
        ]),
        condition=UnlessCondition(  # si mode != stereo
            # SimpleCondition workaround: on utilise IfCondition sur 'mono'
            LaunchConfiguration('slam')  # placeholder, voir note
        )
    )

    # ---- SLAM ----
    slam_mono_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            PathJoinSubstitution([
                FindPackageShare('spotbot_slam'), 'launch', 'rtabmap_mono.launch.py'
            ])
        ]),
        condition=IfCondition(slam)
    )

    # ---- Arduino Bridge ----
    arduino_bridge_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            PathJoinSubstitution([
                FindPackageShare('spotbot_arduino_bridge'), 'launch', 'arduino_bridge.launch.py'
            ])
        ]),
        condition=IfCondition(arduino)
    )

    # ---- RViz2 ----
    rviz_node = Node(
        package='rviz2',
        executable='rviz2',
        name='rviz2',
        arguments=['-d', PathJoinSubstitution([
            FindPackageShare('spotbot_bringup'), 'config', 'spotbot.rviz'
        ])],
        condition=IfCondition(rviz),
        output='screen'
    )

    # ---- EKF Sensor Fusion (robot_localization) ----
    ekf_config = PathJoinSubstitution([
        FindPackageShare('spotbot_bringup'), 'config', 'ekf.yaml'
    ])
    ekf_node = Node(
        package='robot_localization',
        executable='ekf_node',
        name='ekf_filter_node',
        output='screen',
        parameters=[ekf_config],
        remappings=[('/odometry/filtered', '/odom')]
    )

    # ---- Navigation 2 (Nav2) ----
    nav2_params_file = PathJoinSubstitution([
        FindPackageShare('spotbot_bringup'), 'config', 'nav2_params.yaml'
    ])
    nav2_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            PathJoinSubstitution([
                FindPackageShare('nav2_bringup'), 'launch', 'bringup_launch.py'
            ])
        ]),
        launch_arguments={
            'use_sim_time': 'false',
            'params_file': nav2_params_file,
            'autostart': 'true',
            'map': '', # Laisse vide pour utiliser le SLAM en temps reel
        }.items(),
        condition=IfCondition(nav)
    )

    streaming_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            PathJoinSubstitution([
                FindPackageShare('spotbot_streaming'), 'launch', 'streaming.launch.py'
            ])
        ]),
        launch_arguments={
            'cameras':       LaunchConfiguration('mode'),
            'wifi_watchdog': LaunchConfiguration('wifi_watchdog'),
        }.items(),
        condition=IfCondition(LaunchConfiguration('streaming'))
    )

    return LaunchDescription([
        mode_arg, slam_arg, nav_arg, arduino_arg, rviz_arg,
        streaming_arg, wifi_watchdog_arg,
        LogInfo(msg=["SpotBot launch | mode=", LaunchConfiguration('mode'),
                     " slam=", LaunchConfiguration('slam'),
                     " streaming=", LaunchConfiguration('streaming')]),
        description_launch,
        ekf_node,
        nav2_launch,
        slam_mono_launch,
        arduino_bridge_launch,
        streaming_launch,
        rviz_node,
    ])
