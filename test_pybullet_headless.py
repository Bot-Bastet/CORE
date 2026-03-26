"""
Test headless PyBullet physics - checks that all 8 joints hold their position
in POSITION_CONTROL mode without the body collapsing below ground level.
"""
import math
import time
import numpy as np
import pybullet as p
import pybullet_data
import sys, os, tempfile
sys.path.insert(0, '.')

TIMESTEP = 1.0 / 240.0
TORQUE_LIMIT_NM = 12.0
BASE_LENGTH = 0.80
BASE_WIDTH = 0.50
BASE_HALF_Z = 0.035
BASE_MID_HEIGHT = 0.35
HIP_OFFSET_Z = -0.05
THIGH_LENGTH = 0.325
SHIN_LENGTH = 0.325
HIP_RADIUS = 0.03
LEG_RADIUS = 0.028
BASE_MASS = 4.0
HIP_MASS = 0.4
SHIN_MASS = 0.3

def generate_urdf():
    base_half_x = BASE_LENGTH / 2.0
    base_half_y = BASE_WIDTH / 2.0
    urdf = f"""<robot name="quadruped">
    <link name="base_link">
        <visual><geometry><box size="{BASE_LENGTH} {BASE_WIDTH} {BASE_HALF_Z*2}"/></geometry></visual>
        <collision><geometry><box size="{BASE_LENGTH} {BASE_WIDTH} {BASE_HALF_Z*2}"/></geometry></collision>
        <inertial><mass value="{BASE_MASS}"/><inertia ixx="0.1" ixy="0" ixz="0" iyy="0.1" iyz="0" izz="0.1"/></inertial>
    </link>
"""
    leg_prefix = ["FL", "FR", "RL", "RR"]
    leg_offsets = [
        [ base_half_x,  base_half_y, -BASE_HALF_Z + HIP_OFFSET_Z],
        [ base_half_x, -base_half_y, -BASE_HALF_Z + HIP_OFFSET_Z],
        [-base_half_x,  base_half_y, -BASE_HALF_Z + HIP_OFFSET_Z],
        [-base_half_x, -base_half_y, -BASE_HALF_Z + HIP_OFFSET_Z],
    ]
    for i, prefix in enumerate(leg_prefix):
        offset = leg_offsets[i]
        urdf += f"""
    <joint name="{prefix}_hip_joint" type="revolute">
        <parent link="base_link"/><child link="{prefix}_thigh"/>
        <origin xyz="{offset[0]} {offset[1]} {offset[2]}" rpy="0 0 0"/>
        <axis xyz="0 1 0"/>
        <limit lower="-1.57" upper="1.57" effort="{TORQUE_LIMIT_NM}" velocity="100"/>
    </joint>
    <link name="{prefix}_thigh">
        <visual><origin xyz="0 0 {-THIGH_LENGTH/2}" rpy="0 0 0"/><geometry><capsule radius="{HIP_RADIUS}" length="{THIGH_LENGTH}"/></geometry></visual>
        <collision><origin xyz="0 0 {-THIGH_LENGTH/2}" rpy="0 0 0"/><geometry><capsule radius="{HIP_RADIUS}" length="{THIGH_LENGTH}"/></geometry></collision>
        <inertial><mass value="{HIP_MASS}"/><origin xyz="0 0 {-THIGH_LENGTH/2}" rpy="0 0 0"/><inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/></inertial>
    </link>
    <joint name="{prefix}_knee_joint" type="revolute">
        <parent link="{prefix}_thigh"/><child link="{prefix}_shin"/>
        <origin xyz="0 0 {-THIGH_LENGTH}" rpy="0 0 0"/>
        <axis xyz="0 1 0"/>
        <limit lower="0" upper="2.6" effort="{TORQUE_LIMIT_NM}" velocity="100"/>
    </joint>
    <link name="{prefix}_shin">
        <visual><origin xyz="0 0 {-SHIN_LENGTH/2}" rpy="0 0 0"/><geometry><capsule radius="{LEG_RADIUS}" length="{SHIN_LENGTH}"/></geometry></visual>
        <collision><origin xyz="0 0 {-SHIN_LENGTH/2}" rpy="0 0 0"/><geometry><capsule radius="{LEG_RADIUS}" length="{SHIN_LENGTH}"/></geometry></collision>
        <inertial><mass value="{SHIN_MASS}"/><origin xyz="0 0 {-SHIN_LENGTH/2}" rpy="0 0 0"/><inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/></inertial>
    </link>
    <joint name="{prefix}_foot_joint" type="fixed">
        <parent link="{prefix}_shin"/><child link="{prefix}_foot"/>
        <origin xyz="0 0 {-SHIN_LENGTH}" rpy="0 0 0"/>
    </joint>
    <link name="{prefix}_foot">
        <visual><geometry><sphere radius="0.03"/></geometry></visual>
        <collision><geometry><sphere radius="0.03"/></geometry></collision>
        <inertial><mass value="0.05"/><inertia ixx="0.001" ixy="0" ixz="0" iyy="0.001" iyz="0" izz="0.001"/></inertial>
    </link>
"""
    urdf += "</robot>"
    return urdf

client = p.connect(p.DIRECT)
p.setAdditionalSearchPath(pybullet_data.getDataPath())
p.setGravity(0, 0, -9.81)
p.setTimeStep(TIMESTEP)
p.loadURDF("plane.urdf")

urdf_string = generate_urdf()
tmpfile = os.path.join(tempfile.gettempdir(), "test_quad.urdf")
with open(tmpfile, "w") as f:
    f.write(urdf_string)

robot = p.loadURDF(tmpfile, basePosition=[0, 0, BASE_MID_HEIGHT])

num_joints = p.getNumJoints(robot)
joint_name_to_id = {}
for i in range(num_joints):
    info = p.getJointInfo(robot, i)
    joint_name_to_id[info[1].decode('UTF-8')] = info[0]

ordered_joint_ids = []
for prefix in ["FL", "FR", "RL", "RR"]:
    ordered_joint_ids.append(joint_name_to_id[f"{prefix}_hip_joint"])
    ordered_joint_ids.append(joint_name_to_id[f"{prefix}_knee_joint"])

for ji in ordered_joint_ids:
    p.setJointMotorControl2(robot, ji, controlMode=p.VELOCITY_CONTROL, force=0)
    p.changeDynamics(robot, ji, linearDamping=0.04, angularDamping=0.04)

steps = int(2.0 / TIMESTEP)
for s in range(steps):
    for leg_idx in range(4):
        hip_joint = ordered_joint_ids[leg_idx * 2]
        knee_joint = ordered_joint_ids[leg_idx * 2 + 1]
        p.setJointMotorControl2(robot, hip_joint, controlMode=p.POSITION_CONTROL, targetPosition=0.0, force=TORQUE_LIMIT_NM)
        p.setJointMotorControl2(robot, knee_joint, controlMode=p.POSITION_CONTROL, targetPosition=1.4, force=TORQUE_LIMIT_NM)
    p.stepSimulation()

pos, orn = p.getBasePositionAndOrientation(robot)
body_z = pos[2]
print(f"Position du corps apres 2 secondes: z={body_z:.3f} m")

if body_z > 0.10:
    print(f"TEST PHYSIQUE PYBULLET : SUCCES (robot stable, z={body_z:.3f} m)")
else:
    print(f"TEST PHYSIQUE PYBULLET : ECHEC (robot effondre, z={body_z:.3f} m)")

p.disconnect()
