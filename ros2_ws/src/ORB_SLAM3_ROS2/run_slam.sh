#!/bin/bash
source /opt/ros2_jazzy/install/setup.bash
source /home/tealo/ros2_ws/install/setup.bash

# Ensure ORB-SLAM3 libraries are found
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/home/tealo/ORB_SLAM3/lib:/home/tealo/ORB_SLAM3/Thirdparty/DBoW2/lib:/home/tealo/ORB_SLAM3/Thirdparty/g2o/lib

# Count number of real USB video devices
CAM_COUNT=$(v4l2-ctl --list-devices | grep -i "usb" | wc -l)

echo "Detected $CAM_COUNT USB camera(s)."

if [ "$CAM_COUNT" -ge 2 ]; then
    echo "Launching STEREO SLAM..."
    ros2 run orbslam3 stereo /home/tealo/ORB_SLAM3/Vocabulary/ORBvoc.txt /home/tealo/ORB_SLAM3/Examples/Stereo/EuRoC.yaml true
else
    echo "Launching MONOCULAR SLAM..."
    ros2 run orbslam3 mono /home/tealo/ORB_SLAM3/Vocabulary/ORBvoc.txt /home/tealo/ORB_SLAM3/Examples/Monocular/EuRoC.yaml
fi
