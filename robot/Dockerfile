FROM ros:humble-ros-base

# Installation des dépendances Python et système pour le Bridge
RUN apt-get update && apt-get install -y \
    python3-pip \
    python3-venv \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /robot_app

# Copier le code du bridge et discovery
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copier le code source
COPY . .

# Source ROS 2 entrypoint automatically
RUN echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc

# Commande de lancement
CMD ["bash", "start_robot.sh"]
