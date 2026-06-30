#!/usr/bin/env python3
"""
SpotBot — Inverse Kinematics Solver
Adapte pour ROS 2 depuis spotMicro-master et spot_mini_mini

Geometrie SpotMicro:
  - Abad: rotation laterale (X axis)
  - Upper leg: rotation avant/arriere (Y axis)
  - Lower leg: rotation avant/arriere (Y axis)

Les dimensions proviennent du URDF spotbot.urdf.xacro.
"""

import math
import numpy as np


class LegIK:
    """
    Solveur IK analytique pour une patte a 3 DOF.

    Conventions:
      x = avant du robot
      y = gauche du robot
      z = haut du robot
    """

    def __init__(self,
                 abad_length: float = 0.060,
                 upper_length: float = 0.111,
                 lower_length: float = 0.118,
                 side: str = 'right'):
        """
        Args:
            abad_length:  longueur membre abad [m]
            upper_length: longueur segment superieur [m]
            lower_length: longueur segment inferieur [m]
            side:         'right' ou 'left'
        """
        self.l1 = abad_length
        self.l2 = upper_length
        self.l3 = lower_length
        self.side_sign = -1.0 if side == 'right' else 1.0

    def solve(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """
        Calcule les angles (abad, upper, lower) en radians pour atteindre
        le point (x, y, z) en coordonnees de l'epaule.

        Returns:
            (theta_abad, theta_upper, theta_lower) en radians
            ou (None, None, None) si hors workspace

        Signe y: negatif = droite, positif = gauche
        """
        # Abad: rotation autour de l'axe X
        # Distance du pied dans le plan YZ depuis l'articulation abad
        r_yz = math.sqrt(y**2 + z**2)
        # Cas degenere
        if r_yz < 1e-6:
            return None, None, None

        # Angle abad pour aligner le plan de la patte
        alpha = math.atan2(z, -self.side_sign * y)
        gamma = math.acos(max(-1.0, min(1.0, self.l1 / r_yz)))
        theta_abad = alpha + gamma * self.side_sign

        # Distance effective dans le plan de la patte (apres abad)
        r_xz = math.sqrt(
            x**2 + (math.sqrt(max(0.0, r_yz**2 - self.l1**2)))**2
        )

        # Verifier atteignabilite
        if r_xz > (self.l2 + self.l3) or r_xz < abs(self.l2 - self.l3):
            return None, None, None

        # Angle lower (coude)
        cos_lower = (r_xz**2 - self.l2**2 - self.l3**2) / (2 * self.l2 * self.l3)
        cos_lower = max(-1.0, min(1.0, cos_lower))
        theta_lower = -math.acos(cos_lower)  # toujours negatif (coude)

        # Angle upper
        beta = math.atan2(x, math.sqrt(max(0.0, r_yz**2 - self.l1**2)))
        psi  = math.acos(max(-1.0, min(1.0,
            (r_xz**2 + self.l2**2 - self.l3**2) / (2 * r_xz * self.l2)
        )))
        theta_upper = beta - psi

        return theta_abad, theta_upper, theta_lower

    def angles_to_degrees(self, rad_tuple) -> list[float]:
        """Convertit (abad, upper, lower) de radians en degres avec prise en compte du miroir droit/gauche."""
        if None in rad_tuple:
            return [90.0, 90.0, 90.0]
        
        # Pour le côté droit (side_sign < 0), la rotation physique du servo est inversée
        if self.side_sign < 0:
            return [90.0 - math.degrees(a) for a in rad_tuple]
        else:
            return [90.0 + math.degrees(a) for a in rad_tuple]


class SpotIK:
    """
    Solveur IK complet pour les 4 pattes de SpotBot.
    Retourne 12 angles servos [deg] dans l'ordre:
      [FR_abad, FR_upper, FR_lower,
       FL_abad, FL_upper, FL_lower,
       BR_abad, BR_upper, BR_lower,
       BL_abad, BL_upper, BL_lower]
    """

    # Positions des hanches en coordonnees corps (m)
    # (x_forward, y_lateral, z_up)
    HIP_POSITIONS = {
        'fr': np.array([ 0.10, -0.05, 0.0]),
        'fl': np.array([ 0.10,  0.05, 0.0]),
        'br': np.array([-0.10, -0.05, 0.0]),
        'bl': np.array([-0.10,  0.05, 0.0]),
    }

    STAND_HEIGHT = -0.15  # Hauteur sol (z negatif = vers le bas)

    def __init__(self):
        self.legs = {
            'fr': LegIK(side='right'),
            'fl': LegIK(side='left'),
            'br': LegIK(side='right'),
            'bl': LegIK(side='left'),
        }

    def stand_pose(self, body_height: float = None) -> list[float]:
        """Calcule les 12 angles pour position debout."""
        h = body_height if body_height is not None else self.STAND_HEIGHT
        angles = []
        for leg_name in ['fr', 'fl', 'br', 'bl']:
            foot_target = np.array([0.0, 0.0, h])
            abad, upper, lower = self.legs[leg_name].solve(*foot_target)
            angles.extend(self.legs[leg_name].angles_to_degrees((abad, upper, lower)))
        return angles

    def sit_pose(self) -> list[float]:
        """Position assise."""
        return self.stand_pose(body_height=-0.08)

    def solve_for_feet(self, foot_positions: dict) -> list[float]:
        """
        Calcule les angles pour des positions de pieds arbitraires.

        Args:
            foot_positions: dict {'fr': [x,y,z], 'fl': ..., 'br': ..., 'bl': ...}
                            en coordonnees de l'epaule

        Returns:
            liste de 12 angles [deg]
        """
        angles = []
        for leg_name in ['fr', 'fl', 'br', 'bl']:
            pos = foot_positions.get(leg_name, [0, 0, self.STAND_HEIGHT])
            abad, upper, lower = self.legs[leg_name].solve(*pos)
            angles.extend(self.legs[leg_name].angles_to_degrees((abad, upper, lower)))
        return angles


if __name__ == '__main__':
    # Test rapide
    ik = SpotIK()
    print('Stand pose:', ik.stand_pose())
    print('Sit pose:',   ik.sit_pose())
