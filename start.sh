#!/bin/bash
# SpotBot — Auto-start (systemd)
# Camera + TF + SLAM + Arduino Bridge (comm only) + ROSboard

LOG=/var/log/spotbot
mkdir -p $LOG

# Auto-updater
echo "[SpotBot] Checking for updates..." | tee -a $LOG/startup.log
python3 /opt/spotbot/updater.py >> $LOG/updater.log 2>&1


# Fix HOME for ROS logging
export HOME=/root
mkdir -p /root/.ros/log

source /opt/ros2_jazzy/install/setup.bash
source /opt/spotbot/ros2_ws/install/setup.bash

echo "[SpotBot] $(date) — Starting..." | tee $LOG/startup.log

# 1. Cameras
# Camera 1 (/dev/video0)
fuser -k /dev/video0 2>/dev/null || true
sleep 1
ros2 run usb_cam usb_cam_node_exe --ros-args \
  -r __node:=usb_cam1 \
  -p video_device:=/dev/video0 -p pixel_format:=yuyv2rgb \
  -p image_width:=640 -p image_height:=480 -p framerate:=15.0 \
  -p camera_info_url:=file:///opt/spotbot/config/camera_calibration.yaml \
  -p camera_name:=usb_cam1 -p frame_id:=camera_link \
  -r image_raw:=/camera/left/image_raw -r camera_info:=/camera/left/camera_info \
  >> $LOG/camera1.log 2>&1 &

# Camera 2 (/dev/video2)
fuser -k /dev/video2 2>/dev/null || true
sleep 1
ros2 run usb_cam usb_cam_node_exe --ros-args \
  -r __node:=usb_cam2 \
  -p video_device:=/dev/video2 -p pixel_format:=yuyv2rgb \
  -p image_width:=640 -p image_height:=480 -p framerate:=15.0 \
  -p camera_name:=usb_cam2 -p frame_id:=camera2_link \
  -r image_raw:=/camera/right/image_raw -r camera_info:=/camera/right/camera_info \
  >> $LOG/camera2.log 2>&1 &

echo "[SpotBot] Cameras OK" | tee -a $LOG/startup.log

# 2. TF
ros2 run tf2_ros static_transform_publisher \
  --x 0.1 --y 0 --z 0.05 --roll 0 --pitch 0 --yaw 0 \
  --frame-id base_link --child-frame-id camera_link \
  >> $LOG/tf.log 2>&1 &
ros2 run tf2_ros static_transform_publisher \
  --x 0 --y 0 --z 0 --roll 0 --pitch 0 --yaw 0 \
  --frame-id odom --child-frame-id base_link \
  >> $LOG/tf.log 2>&1 &
echo "[SpotBot] TF OK" | tee -a $LOG/startup.log
sleep 3

# 3. SLAM (ORB-SLAM3)
/home/tealo/ros2_ws/run_slam.sh >> $LOG/orbslam3.log 2>&1 &
echo "[SpotBot] SLAM OK" | tee -a $LOG/startup.log
sleep 2

# 4. Arduino (auto-detect port, don't pass empty string)
if [ -e /dev/arduino ]; then
    ARDUINO_PORT=/dev/arduino
elif [ -e /dev/ttyACM0 ]; then
    ARDUINO_PORT=/dev/ttyACM0
else
    ARDUINO_PORT=""
fi

if [ -n "$ARDUINO_PORT" ]; then
    ros2 run spotbot_arduino_bridge arduino_bridge_node --ros-args \
      -p baudrate:=115200 -p auto_flash:=false \
      >> $LOG/arduino.log 2>&1 &
    echo "[SpotBot] Arduino OK ($ARDUINO_PORT)" | tee -a $LOG/startup.log
else
    echo "[SpotBot] No Arduino detected" | tee -a $LOG/startup.log
fi
sleep 2

# 5. ROSboard
ros2 run rosboard rosboard_node >> $LOG/rosboard.log 2>&1 &
echo "[SpotBot] ROSboard OK (:8888)" | tee -a $LOG/startup.log

echo "[SpotBot] $(date) — Ready!" | tee -a $LOG/startup.log

# Keep alive
while true; do sleep 3600; done
