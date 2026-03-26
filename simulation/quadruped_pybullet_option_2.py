"""
Quadruped PyBullet simulation - Option 2
- Robot overall dimensions (user): length=0.80 m, width=0.50 m, leg fully extended height=0.65 m
- Thigh and tibia (shin) are the same length
- Each leg has two actuated revolute joints: hip and knee (like Spot)
- Motor torque limit: 65 N·cm = 0.65 N·m

How to use:
  1) Install dependencies: pip install pybullet numpy
  2) Run: python quadruped_pybullet_option2.py
  3) By default the script starts in GUI mode (set USE_GUI=False to run headless).

Notes:
  - This is a simplified rigid-body model for testing controllers and gait patterns.
  - Inertia, masses and collision shapes are approximate and should be tuned for a realistic model.
  - This version generates a URDF file in Python to correctly model the leg geometry.
"""

import math
import time
import socket
import numpy as np
import pybullet as p
import pybullet_data

# ----------------------------- USER PARAMETERS -----------------------------
USE_GUI = True  # set False for headless / server
SIM_TIME = 3600.0  # total simulation seconds (increased for live testing)
TIMESTEP = 1.0 / 240.0
TORQUE_LIMIT_NM = 12.0  # increased for stability (original: 6.5 Nm real, but sim needs headroom)

# Robot geometry provided by user
# --- Dimensions (updated)
BASE_LENGTH = 0.80  # meters (front to back)
BASE_WIDTH = 0.50   # meters (left to right)
LEG_EXTENDED = 0.65 # meters (ground to top of robot when legs fully extended)

# Base thickness (half-depth in z). This value influences leg length computation.
BASE_HALF_Z = 0.035  # 0.035 m -> base thickness ~= 0.07 m
BASE_MID_HEIGHT = 0.35  # raised to give room for legs at spawn
HIP_OFFSET_Z = -0.05 # From project file

# We want thigh and tibia equal length.
# From project file: Cuisse = 32.5cm, Tibia = 32.5cm
THIGH_LENGTH = 0.325
SHIN_LENGTH = 0.325

# Visual / collision sizes (approx)
HIP_RADIUS = 0.03
LEG_RADIUS = 0.028

# Masses (approx)
BASE_MASS = 4.0
HIP_MASS = 0.4
SHIN_MASS = 0.3

# Controller gains
KP = 80  # increased for stiffer hold
KD = 10.0 # increased for stability

# ----------------------------- URDF GENERATION -----------------------------

def generate_urdf():
    base_half_x = BASE_LENGTH / 2.0
    base_half_y = BASE_WIDTH / 2.0

    urdf = f"""
<robot name="quadruped">
    <link name="base_link">
        <visual>
            <geometry>
                <box size="{BASE_LENGTH} {BASE_WIDTH} {BASE_HALF_Z*2}"/>
            </geometry>
            <material name="grey">
                <color rgba="0.5 0.5 0.5 1"/>
            </material>
        </visual>
        <collision>
            <geometry>
                <box size="{BASE_LENGTH} {BASE_WIDTH} {BASE_HALF_Z*2}"/>
            </geometry>
        </collision>
        <inertial>
            <mass value="{BASE_MASS}"/>
            <inertia ixx="0.1" ixy="0" ixz="0" iyy="0.1" iyz="0" izz="0.1"/>
        </inertial>
    </link>
"""

    leg_prefix = ["FL", "FR", "RL", "RR"]
    leg_offsets = [
        [ base_half_x,  base_half_y, -BASE_HALF_Z + HIP_OFFSET_Z],  # FL
        [ base_half_x, -base_half_y, -BASE_HALF_Z + HIP_OFFSET_Z],  # FR
        [-base_half_x,  base_half_y, -BASE_HALF_Z + HIP_OFFSET_Z],  # RL
        [-base_half_x, -base_half_y, -BASE_HALF_Z + HIP_OFFSET_Z],  # RR
    ]

    for i in range(4):
        prefix = leg_prefix[i]
        offset = leg_offsets[i]
        
        urdf += f"""
    <!-- {prefix} Leg -->
    <joint name="{prefix}_hip_joint" type="revolute">
        <parent link="base_link"/>
        <child link="{prefix}_thigh"/>
        <origin xyz="{offset[0]} {offset[1]} {offset[2]}" rpy="0 0 0"/>
        <axis xyz="0 1 0"/>
        <limit lower="-1.57" upper="1.57" effort="{TORQUE_LIMIT_NM}" velocity="100"/>
    </joint>

    <link name="{prefix}_thigh">
        <visual>
            <origin xyz="0 0 {-THIGH_LENGTH/2}" rpy="0 0 0"/>
            <geometry>
                <capsule radius="{HIP_RADIUS}" length="{THIGH_LENGTH}"/>
            </geometry>
            <material name="orange">
                <color rgba="0.8 0.5 0.2 1"/>
            </material>
        </visual>
        <collision>
            <origin xyz="0 0 {-THIGH_LENGTH/2}" rpy="0 0 0"/>
            <geometry>
                <capsule radius="{HIP_RADIUS}" length="{THIGH_LENGTH}"/>
            </geometry>
        </collision>
        <inertial>
            <mass value="{HIP_MASS}"/>
            <origin xyz="0 0 {-THIGH_LENGTH/2}" rpy="0 0 0"/>
            <inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/>
        </inertial>
    </link>

    <joint name="{prefix}_knee_joint" type="revolute">
        <parent link="{prefix}_thigh"/>
        <child link="{prefix}_shin"/>
        <origin xyz="0 0 {-THIGH_LENGTH}" rpy="0 0 0"/>
        <axis xyz="0 1 0"/>
        <limit lower="0" upper="2.6" effort="{TORQUE_LIMIT_NM}" velocity="100"/>
    </joint>

    <link name="{prefix}_shin">
        <visual>
            <origin xyz="0 0 {-SHIN_LENGTH/2}" rpy="0 0 0"/>
            <geometry>
                <capsule radius="{LEG_RADIUS}" length="{SHIN_LENGTH}"/>
            </geometry>
            <material name="orange"/>
        </visual>
        <collision>
            <origin xyz="0 0 {-SHIN_LENGTH/2}" rpy="0 0 0"/>
            <geometry>
                <capsule radius="{LEG_RADIUS}" length="{SHIN_LENGTH}"/>
            </geometry>
        </collision>
        <inertial>
            <mass value="{SHIN_MASS}"/>
            <origin xyz="0 0 {-SHIN_LENGTH/2}" rpy="0 0 0"/>
            <inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/>
        </inertial>
    </link>
    <joint name="{prefix}_foot_joint" type="fixed">
        <parent link="{prefix}_shin"/>
        <child link="{prefix}_foot"/>
        <origin xyz="0 0 {-SHIN_LENGTH}" rpy="0 0 0"/>
    </joint>

    <link name="{prefix}_foot">
        <visual>
            <geometry><sphere radius="0.03"/></geometry>
            <material name="grey"/>
        </visual>
        <collision>
            <geometry><sphere radius="0.03"/></geometry>
        </collision>
        <inertial>
            <mass value="0.05"/>
            <inertia ixx="0.001" ixy="0" ixz="0" iyy="0.001" iyz="0" izz="0.001"/>
        </inertial>
    </link>
"""
    urdf += "</robot>"
    return urdf

# ----------------------------- SETUP PHYSICS -----------------------------
client = p.connect(p.GUI if USE_GUI else p.DIRECT)
p.setAdditionalSearchPath(pybullet_data.getDataPath())
p.setGravity(0, 0, -9.81)
p.setTimeStep(TIMESTEP)
plane = p.loadURDF("plane.urdf")

# Generate and load the robot
urdf_string = generate_urdf()
urdf_path = "quadruped.urdf"
with open(urdf_path, "w") as f:
    f.write(urdf_string)

robot = p.loadURDF(urdf_path, basePosition=[0, 0, BASE_MID_HEIGHT])

# --- Get joint indices from URDF ---
num_joints = p.getNumJoints(robot)
joint_name_to_id = {}
for i in range(num_joints):
    joint_info = p.getJointInfo(robot, i)
    joint_name_to_id[joint_info[1].decode('UTF-8')] = joint_info[0]

# Order joints consistently: FL, FR, RL, RR (hip, knee)
ordered_joint_ids = []
for prefix in ["FL", "FR", "RL", "RR"]:
    ordered_joint_ids.append(joint_name_to_id[f"{prefix}_hip_joint"])
    ordered_joint_ids.append(joint_name_to_id[f"{prefix}_knee_joint"])

print(f"Robot créé avec {num_joints} joints")

# Disable default motors so we can apply torques
for ji in ordered_joint_ids:
    p.setJointMotorControl2(robot, ji, controlMode=p.VELOCITY_CONTROL, force=0)
    p.changeDynamics(robot, ji, linearDamping=0.04, angularDamping=0.04)

# ----------------------------- GAIT (simple trot) -----------------------------

def trot_desired_angle(leg_index, t):
    # simple sinusoidal trot: FL & RR in phase, FR & RL opposite
    freq = 1.0  # Hz
    # leg_index: 0=FL, 1=FR, 2=RL, 3=RR
    if leg_index in (0, 3): # FL, RR
        phase = 0.0
    else: # FR, RL
        phase = math.pi
    hip_amp = 0.45
    knee_amp = 0.4  # Amplitude of knee motion

    hip_target = hip_amp * math.sin(2 * math.pi * freq * t + phase)

    # The robot must maintain a deep crouch. Knee angle oscillates around 2.2 rad.
    # Range is [2.0, 2.4] rad.
    knee_target = 2.0 + knee_amp * (0.5 * (1 - math.cos(2 * math.pi * freq * t + phase)))
    return hip_target, knee_target

# ----------------------------- SIMULATION LOOP -----------------------------

t = 0.0
startup_time = 1.0  # seconds to stabilize initially
steps = int(SIM_TIME / TIMESTEP)
log_every = int(0.2 / TIMESTEP)

# --- UDP COMMAND SERVER ---
udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp_sock.bind(('127.0.0.1', 5005))
udp_sock.setblocking(False)
robot_state = "STOP"
print("En écoute sur UDP 5005 pour les commandes (AVANCER, STOP)...")
# --------------------------

for s in range(steps):
    # Check for commands
    try:
        data, _ = udp_sock.recvfrom(1024)
        msg = data.decode('utf-8').strip().upper()
        if msg == "AVANCER":
            robot_state = "TROT"
            print("Commande reçue : AVANCER -> TROT")
        elif msg == "STOP":
            robot_state = "STOP"
            print("Commande reçue : STOP -> CROUCH")
    except BlockingIOError:
        pass
    except Exception as e:
        print(f"UDP Error: {e}")

    for leg_idx in range(4):
        hip_joint = ordered_joint_ids[leg_idx * 2]
        knee_joint = ordered_joint_ids[leg_idx * 2 + 1]

        if t < startup_time or robot_state == "STOP":
            # Use POSITION_CONTROL for stable standing - much more robust than torque
            p.setJointMotorControl2(
                robot, hip_joint,
                controlMode=p.POSITION_CONTROL,
                targetPosition=0.0,
                force=TORQUE_LIMIT_NM
            )
            p.setJointMotorControl2(
                robot, knee_joint,
                controlMode=p.POSITION_CONTROL,
                targetPosition=1.4,  # gentle crouch: ~80deg, stable without collapsing
                force=TORQUE_LIMIT_NM
            )
        else:
            # Normal trot gait - TORQUE_CONTROL
            hip_des, knee_des = trot_desired_angle(leg_idx, t - startup_time)

            # hip
            hip_state = p.getJointState(robot, hip_joint)
            hip_pos = hip_state[0]
            hip_vel = hip_state[1]
            hip_torque = KP * (hip_des - hip_pos) + KD * (0 - hip_vel)
            hip_torque = max(-TORQUE_LIMIT_NM, min(TORQUE_LIMIT_NM, hip_torque))
            p.setJointMotorControl2(robot, hip_joint, controlMode=p.TORQUE_CONTROL, force=hip_torque)

            # knee
            knee_state = p.getJointState(robot, knee_joint)
            knee_pos = knee_state[0]
            knee_vel = knee_state[1]
            knee_torque = KP * (knee_des - knee_pos) + KD * (0 - knee_vel)
            knee_torque = max(-TORQUE_LIMIT_NM, min(TORQUE_LIMIT_NM, knee_torque))
            p.setJointMotorControl2(robot, knee_joint, controlMode=p.TORQUE_CONTROL, force=knee_torque)

    p.stepSimulation()
    t += TIMESTEP

    if USE_GUI:
        time.sleep(TIMESTEP)

    if s % log_every == 0:
        pos = [p.getJointState(robot, ji)[0] for ji in ordered_joint_ids]
        print(f"t={t:.2f}s joints:", [f"{x:.2f}" for x in pos])

print("Simulation terminée")

# Keep GUI open until closed by user
if USE_GUI:
    print("Fermez la fenêtre pour terminer")
    while p.isConnected():
        time.sleep(0.2)

if p.isConnected():
    p.disconnect()
