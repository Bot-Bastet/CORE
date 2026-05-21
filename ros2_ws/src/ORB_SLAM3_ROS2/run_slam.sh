#!/bin/bash
source /opt/ros2_jazzy/install/setup.bash
source /home/tealo/ros2_ws/install/setup.bash

# Count number of video devices (ignoring media devices)
CAM_COUNT=$(ls -1 /dev/video* 2>/dev/null | grep -v media | wc -l)

echo "Detected $CAM_COUNT camera(s)."

if [ "$CAM_COUNT" -ge 2 ]; then
    echo "Launching STEREO SLAM..."
    ros2 run orbslam3 stereo /home/tealo/ORB_SLAM3/Vocabulary/ORBvoc.txt /home/tealo/ORB_SLAM3/Examples/Stereo/EuRoC.yaml true
else
    echo "Launching MONOCULAR SLAM..."
    ros2 run orbslam3 mono /home/tealo/ORB_SLAM3/Vocabulary/ORBvoc.txt /home/tealo/ORB_SLAM3/Examples/Monocular/EuRoC.yaml
fi
