#!/bin/bash
# SpotBot — Auto-start (systemd) v2
# ============================================
# Auto-détection caméras (mono / stéréo / none)
# Topics adaptatifs : mono → /camera/image_raw, stereo → /camera/left/ + /camera/right/
# SLAM auto : ORB-SLAM3 mono ou stéréo selon le nombre de caméras détectées
# ============================================

LOG=/var/log/spotbot
mkdir -p $LOG

# Auto-updater
echo "[SpotBot] Checking for updates... (disabled for development)" | tee -a $LOG/startup.log
# python3 /opt/spotbot/updater.py >> $LOG/updater.log 2>&1

# Fix HOME for ROS logging
export HOME=/root
mkdir -p /root/.ros/log

source /opt/ros2_jazzy/install/setup.bash
source /opt/spotbot/ros2_ws/install/setup.bash

echo "[SpotBot] $(date) — Starting..." | tee $LOG/startup.log

# ============================================
# 1. AUTO-DETECT CAMERAS
# ============================================

# Read mapping from /opt/spotbot/config/camera_mapping.json
CAM_LEFT="/dev/video0"
CAM_RIGHT="/dev/video2"

MAPPING_FILE="/opt/spotbot/config/camera_mapping.json"
if [ -f "$MAPPING_FILE" ]; then
    LEFT_PARSE=$(grep -o '"left"[[:space:]]*:[[:space:]]*"[^"]*"' "$MAPPING_FILE" | cut -d'"' -f4)
    RIGHT_PARSE=$(grep -o '"right"[[:space:]]*:[[:space:]]*"[^"]*"' "$MAPPING_FILE" | cut -d'"' -f4)
    [ -n "$LEFT_PARSE" ] && CAM_LEFT="$LEFT_PARSE"
    [ -n "$RIGHT_PARSE" ] && CAM_RIGHT="$RIGHT_PARSE"
fi

# Auto-detect which cameras are actually connected
HAS_LEFT=false
HAS_RIGHT=false
if [ -e "$CAM_LEFT" ]; then
    HAS_LEFT=true
fi
if [ -e "$CAM_RIGHT" ]; then
    HAS_RIGHT=true
fi

# Determine camera mode
if $HAS_LEFT && $HAS_RIGHT; then
    CAM_MODE="stereo"
    CAM_COUNT=2
elif $HAS_LEFT; then
    CAM_MODE="mono"
    CAM_COUNT=1
else
    CAM_MODE="none"
    CAM_COUNT=0
fi

echo "[SpotBot] Camera mode: $CAM_MODE ($CAM_COUNT detected)" | tee -a $LOG/startup.log
echo "[SpotBot]   Left=$CAM_LEFT (present=$HAS_LEFT)  Right=$CAM_RIGHT (present=$HAS_RIGHT)" | tee -a $LOG/startup.log

# ---- Launch camera(s) with mode-appropriate topics ----

if $HAS_LEFT; then
    fuser -k "$CAM_LEFT" 2>/dev/null || true
    sleep 1

    if [ "$CAM_MODE" = "mono" ]; then
        # Mono → publish to /camera/left/image_raw (matches streaming_engine.py
        # which hardcodes the cam0 topic to /camera/left/image_raw even in mono
        # mode, since it only sees one camera and assigns it to cam index 0).
        # Using /camera/image_raw here would leave the engine subscribed to a
        # topic that no node publishes to, so ffmpeg never spawns.
        ros2 run usb_cam usb_cam_node_exe --ros-args \
          -p use_intra_process_comms:=true \
          -r __node:=usb_cam1 \
          -p video_device:="$CAM_LEFT" -p pixel_format:=yuyv2rgb \
          -p image_width:=640 -p image_height:=480 -p framerate:=10.0 \
          -p camera_info_url:=file:///opt/spotbot/config/camera_stereo_left.yaml \
          -p camera_name:=usb_cam_left -p frame_id:=camera_link \
          -r image_raw:=/camera/left/image_raw -r camera_info:=/camera/left/camera_info \
          >> $LOG/camera1.log 2>&1 &
        echo "[SpotBot] Camera OK ($CAM_LEFT → /camera/left/image_raw [mono])" | tee -a $LOG/startup.log
    else
        # Stereo → left publishes to /camera/left/image_raw
        ros2 run usb_cam usb_cam_node_exe --ros-args \
          -p use_intra_process_comms:=true \
          -r __node:=usb_cam1 \
          -p video_device:="$CAM_LEFT" -p pixel_format:=yuyv2rgb \
          -p image_width:=640 -p image_height:=480 -p framerate:=10.0 \
          -p camera_info_url:=file:///opt/spotbot/config/camera_stereo_left.yaml \
          -p camera_name:=usb_cam_left -p frame_id:=camera_link \
          -r image_raw:=/camera/left/image_raw -r camera_info:=/camera/left/camera_info \
          >> $LOG/camera1.log 2>&1 &
        echo "[SpotBot] Camera Left OK ($CAM_LEFT → /camera/left/image_raw [stereo])" | tee -a $LOG/startup.log
    fi
else
    echo "[SpotBot] Camera Left NOT detected ($CAM_LEFT)" | tee -a $LOG/startup.log
fi

if $HAS_RIGHT; then
    fuser -k "$CAM_RIGHT" 2>/dev/null || true
    sleep 1

    # Right camera → /camera/right/image_raw (with stereo calibration)
    ros2 run usb_cam usb_cam_node_exe --ros-args \
          -p use_intra_process_comms:=true \
      -r __node:=usb_cam2 \
      -p video_device:="$CAM_RIGHT" -p pixel_format:=yuyv2rgb \
      -p image_width:=640 -p image_height:=480 -p framerate:=10.0 \
      -p camera_info_url:=file:///opt/spotbot/config/camera_stereo_right.yaml \
      -p camera_name:=usb_cam_right -p frame_id:=camera2_link \
      -r image_raw:=/camera/right/image_raw -r camera_info:=/camera/right/camera_info \
      >> $LOG/camera2.log 2>&1 &
    echo "[SpotBot] Camera Right OK ($CAM_RIGHT → /camera/right/image_raw [stereo])" | tee -a $LOG/startup.log
else
    echo "[SpotBot] Camera Right NOT detected ($CAM_RIGHT)" | tee -a $LOG/startup.log
fi

# ============================================
# 2. TF
# ============================================
ros2 run tf2_ros static_transform_publisher \
  --x 0.1 --y 0 --z 0.05 --roll 0 --pitch 0 --yaw 0 \
  --frame-id base_link --child-frame-id camera_link \
  >> $LOG/tf.log 2>&1 &
ros2 run tf2_ros static_transform_publisher \
  --x 0 --y -0.06 --z 0 --roll 0 --pitch 0 --yaw 0 \
  --frame-id camera_link --child-frame-id camera2_link \
  >> $LOG/tf.log 2>&1 &
echo "[SpotBot] TF OK" | tee -a $LOG/startup.log
sleep 3

# ============================================
# 3. SLAM — auto mono/stereo via run_slam.sh
# ============================================
if [ "$CAM_MODE" != "none" ]; then
    echo "[SpotBot] SLAM: ORB-SLAM3 $CAM_MODE (via run_slam.sh)" | tee -a $LOG/startup.log
    /opt/spotbot/ros2_ws/src/ORB_SLAM3_ROS2/run_slam.sh "$CAM_MODE" >> $LOG/orbslam3.log 2>&1 &
    echo "[SpotBot] SLAM launched" | tee -a $LOG/startup.log
else
    echo "[SpotBot] SLAM SKIPPED (no cameras detected)" | tee -a $LOG/startup.log
fi
sleep 2

# ============================================
# 4. Arduino (auto-detect port)
# ============================================
if [ -e /dev/arduino ]; then
    ARDUINO_PORT=/dev/arduino
elif [ -e /dev/ttyACM0 ]; then
    ARDUINO_PORT=/dev/ttyACM0
else
    ARDUINO_PORT=""
fi

if [ -n "$ARDUINO_PORT" ]; then
    # Kill any existing bridge to prevent serial port contention and fragmented reads
    pkill -f arduino_bridge_node 2>/dev/null || true
    fuser -k "$ARDUINO_PORT" 2>/dev/null || true
    sleep 2
    ros2 run spotbot_arduino_bridge arduino_bridge_node --ros-args \
      -p baudrate:=500000 -p auto_flash:=false \
      >> $LOG/arduino.log 2>&1 &
    echo "[SpotBot] Arduino OK ($ARDUINO_PORT)" | tee -a $LOG/startup.log
fi
sleep 2

# ============================================
# 5. Motion Node (Gait & IK Controller)
# ============================================
ros2 run spotbot_motion motion_node --ros-args \
  -p gait:=trot -p gait_freq:=1.0 -p update_rate:=50.0 \
  >> $LOG/motion.log 2>&1 &
echo "[SpotBot] Motion Control OK" | tee -a $LOG/startup.log
sleep 2

# ============================================
# 5b. EKF Sensor Fusion (robot_localization)
# ============================================
ros2 run robot_localization ekf_node --ros-args \
  --params-file /opt/spotbot/ros2_ws/install/spotbot_bringup/share/spotbot_bringup/config/ekf.yaml \
  -r /odometry/filtered:=/odom \
  >> $LOG/ekf.log 2>&1 &
echo "[SpotBot] EKF Sensor Fusion launched" | tee -a $LOG/startup.log

# Wait for the node to be discovered in ROS graph
echo "[SpotBot] Waiting for EKF node to appear in graph..." | tee -a $LOG/startup.log
for i in {1..20}; do
  if ros2 node list --no-daemon 2>/dev/null | grep -q "ekf_filter_node"; then
    echo "[SpotBot] EKF node detected!" | tee -a $LOG/startup.log
    break
  fi
  sleep 1
done

ros2 lifecycle set /ekf_filter_node configure --no-daemon >> $LOG/ekf.log 2>&1
ros2 lifecycle set /ekf_filter_node activate --no-daemon >> $LOG/ekf.log 2>&1
echo "[SpotBot] EKF Sensor Fusion Active" | tee -a $LOG/startup.log
sleep 2

# ============================================
# 5c. Navigation 2 (Nav2)
# ============================================
ros2 launch nav2_bringup bringup_launch.py \
  use_sim_time:=false \
  params_file:=/opt/spotbot/ros2_ws/install/spotbot_bringup/share/spotbot_bringup/config/nav2_params.yaml \
  autostart:=true \
  map:="" \
  >> $LOG/nav2.log 2>&1 &
echo "[SpotBot] Navigation 2 OK" | tee -a $LOG/startup.log
sleep 2

# ============================================
# 6. ROSboard
# ============================================
ros2 run rosboard rosboard_node >> $LOG/rosboard.log 2>&1 &
echo "[SpotBot] ROSboard OK (:8888)" | tee -a $LOG/startup.log

# ============================================
# 7. Streaming Engine (dual-pipeline: VSLAM IPC + Gateway WebRTC)
# ============================================
ros2 run spotbot_streaming streaming_engine >> $LOG/streaming.log 2>&1 &
echo "[SpotBot] Streaming Engine OK" | tee -a $LOG/startup.log

echo "[SpotBot] $(date) — Ready! (mode=$CAM_MODE, cameras=$CAM_COUNT)" | tee -a $LOG/startup.log

# Keep alive
while true; do sleep 3600; done
