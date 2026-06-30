#!/bin/bash
# SpotBot — ORB-SLAM3 launcher (standalone or from start.sh)
# Usage:
#   ./run_slam.sh           → auto-detect camera count
#   ./run_slam.sh mono      → force mono
#   ./run_slam.sh stereo    → force stereo

source /opt/ros2_jazzy/install/setup.bash
source /home/tealo/ros2_ws/install/setup.bash

# Ensure ORB-SLAM3 libraries are found
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/home/tealo/ORB_SLAM3/lib:/home/tealo/ORB_SLAM3/Thirdparty/DBoW2/lib:/home/tealo/ORB_SLAM3/Thirdparty/g2o/lib

# Determine camera mode
if [ -n "$1" ]; then
    CAM_MODE="$1"
    echo "Mode forcé : $CAM_MODE"
else
    # Auto-detect via v4l2-ctl
    if ! command -v v4l2-ctl >/dev/null 2>&1; then
        echo "ERREUR: v4l2-ctl non trouvé. Installez v4l-utils."
        exit 1
    fi
    CAM_COUNT=$(v4l2-ctl --list-devices 2>/dev/null | grep -i "usb" | wc -l)
    if [ "$CAM_COUNT" -ge 2 ]; then
        CAM_MODE="stereo"
    elif [ "$CAM_COUNT" -ge 1 ]; then
        CAM_MODE="mono"
    else
        echo "Aucune caméra USB détectée — SLAM annulé."
        exit 1
    fi
    echo "Auto-détection : $CAM_MODE ($CAM_COUNT caméra(s) USB)"
fi

if [ "$CAM_MODE" = "stereo" ]; then
    echo "Lancement ORB-SLAM3 STEREO..."
    ros2 run orbslam3 stereo \
      /home/tealo/ORB_SLAM3/Vocabulary/ORBvoc.txt \
      /home/tealo/ORB_SLAM3/Examples/Stereo/EuRoC.yaml true
else
    echo "Lancement ORB-SLAM3 MONO..."
    ros2 run orbslam3 mono \
      /home/tealo/ORB_SLAM3/Vocabulary/ORBvoc.txt \
      /home/tealo/ORB_SLAM3/Examples/Monocular/EuRoC.yaml \
      --ros-args -r camera:=/camera/image_raw
fi
