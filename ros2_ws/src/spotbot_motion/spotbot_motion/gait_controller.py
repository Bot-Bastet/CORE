#!/usr/bin/env python3
"""
SpotBot — Gait Controller
Generateur de sequences de deplacement via courbes de Bezier
Adapte depuis spot_mini_mini (SpotMicroAI)

Gaits supportes:
  - 'trot'    : diagonal (FL+BR, FR+BL) — le plus rapide
  - 'crawl'   : une patte a la fois — le plus stable
  - 'stand'   : position statique
  - 'sit'     : position assise
"""

import math
import numpy as np
from .ik_solver import SpotIK


class BezierGait:
    """Generateur de trajectoire de pied via courbe de Bezier cubique."""

    def __init__(self, step_height: float = 0.05, step_length: float = 0.04):
        self.step_height = step_height
        self.step_length = step_length

    def _bezier_cubic(self, t: float, p0, p1, p2, p3) -> np.ndarray:
        """Courbe de Bezier cubique."""
        p0, p1, p2, p3 = np.array(p0), np.array(p1), np.array(p2), np.array(p3)
        return ((1-t)**3 * p0 +
                3*(1-t)**2 * t * p1 +
                3*(1-t) * t**2 * p2 +
                t**3 * p3)

    def foot_trajectory(self, phase: float, direction_x: float = 1.0,
                        direction_y: float = 0.0) -> np.ndarray:
        """
        Calcule la position du pied pour une phase donnee.

        Args:
            phase: [0, 1] — 0=debut, 1=fin du cycle
            direction_x: composante avant/arriere du deplacement
            direction_y: composante laterale du deplacement

        Returns:
            np.array([x, y, z]) position du pied (dedans epaule)
        """
        sl = self.step_length
        sh = self.step_height

        dx = direction_x * sl
        dy = direction_y * sl

        # Phase aerienne (0 -> 0.5): le pied se leve
        if phase < 0.5:
            t = phase / 0.5
            # Bezier de levee: depart -> milieu haut -> arrivee
            pos = self._bezier_cubic(
                t,
                [-dx/2,  dy/2,  0.0],
                [-dx/4,  dy/4,  sh],
                [ dx/4, -dy/4,  sh],
                [ dx/2, -dy/2,  0.0],
            )
        # Phase d'appui (0.5 -> 1.0): glissement sol
        else:
            t = (phase - 0.5) / 0.5
            pos = self._bezier_cubic(
                t,
                [ dx/2, -dy/2, 0.0],
                [ dx/4, -dy/4, 0.0],
                [-dx/4,  dy/4, 0.0],
                [-dx/2,  dy/2, 0.0],
            )

        return pos + np.array([0.0, 0.0, SpotIK.STAND_HEIGHT])


class GaitController:
    """
    Controleur de demarche pour SpotBot.
    Gere les phases de chaque patte et envoie les angles via IK.
    """

    # Decalages de phase par patte pour chaque demarche
    PHASE_OFFSETS = {
        'trot':  {'fr': 0.0, 'fl': 0.5, 'br': 0.5, 'bl': 0.0},
        'crawl': {'fr': 0.0, 'fl': 0.5, 'br': 0.75, 'bl': 0.25},
        'bound': {'fr': 0.0, 'fl': 0.0, 'br': 0.5, 'bl': 0.5},
    }

    def __init__(self, gait: str = 'trot', freq: float = 1.0):
        """
        Args:
            gait: 'trot' | 'crawl' | 'bound'
            freq: frequence du cycle de marche [Hz]
        """
        self.ik      = SpotIK()
        self.bezier  = BezierGait()
        self.gait    = gait
        self.freq    = freq
        self.t       = 0.0  # temps courant [s]
        self.offsets = self.PHASE_OFFSETS.get(gait, self.PHASE_OFFSETS['trot'])
        self.roll    = 0.0
        self.pitch   = 0.0
        self.body_height_multiplier = 1.0
        self.manual_roll = 0.0
        self.manual_pitch = 0.0
        self.manual_yaw = 0.0

    def set_gait(self, gait: str):
        """Change la demarche."""
        if gait in self.PHASE_OFFSETS:
            self.gait    = gait
            self.offsets = self.PHASE_OFFSETS[gait]

    def set_imu_feedback(self, roll: float, pitch: float):
        """Met a jour l'orientation IMU pour la stabilisation d'assiette active."""
        self.roll  = roll
        self.pitch = pitch

    def step(self, dt: float, vx: float = 0.0, vy: float = 0.0,
             omega: float = 0.0) -> list[float]:
        self.t += dt
        cycle_phase = (self.t * self.freq) % 1.0

        h_nominal = SpotIK.STAND_HEIGHT * self.body_height_multiplier
        total_roll = self.roll + self.manual_roll
        total_pitch = self.pitch + self.manual_pitch
        total_yaw = self.manual_yaw

        cR, sR = math.cos(total_roll), math.sin(total_roll)
        cP, sP = math.cos(total_pitch), math.sin(total_pitch)
        cY, sY = math.cos(total_yaw), math.sin(total_yaw)

        foot_positions = {}
        for leg in ['fr', 'fl', 'br', 'bl']:
            phase = (cycle_phase + self.offsets[leg]) % 1.0
            hip = SpotIK.HIP_POSITIONS[leg]
            rot_x = -omega * hip[1]
            rot_y =  omega * hip[0]
            
            # Bezier trajectory
            traj = self.bezier.foot_trajectory(phase, vx + rot_x, vy + rot_y)
            gait_z = traj[2] - SpotIK.STAND_HEIGHT
            gait_x = traj[0]
            gait_y = traj[1]

            # Foot in world space relative to body center:
            foot_x = hip[0] + gait_x
            foot_y = hip[1] + gait_y
            foot_z = h_nominal + gait_z

            # Hip rotated position (ZXY sequence):
            hx, hy = hip[0], hip[1]
            hx_rot = cY*cP*hx + (-sY*cR + cY*sP*sR)*hy
            hy_rot = sY*cP*hx + (cY*cR + sY*sP*sR)*hy
            hz_rot = -sP*hx + cP*sR*hy

            # Vector from hip_world to foot_world:
            w_dx = foot_x - hx_rot
            w_dy = foot_y - hy_rot
            w_dz = foot_z - hz_rot

            # Unrotate this vector back to local body frame:
            ux = cY*w_dx + sY*w_dy
            uy = -sY*w_dx + cY*w_dy
            uz = w_dz

            px = cP*ux - sP*uz
            py = uy
            pz = sP*ux + cP*uz

            lx = px
            ly = cR*py + sR*pz
            lz = -sR*py + cR*pz

            foot_positions[leg] = np.array([lx, ly, lz])

        return self.ik.solve_for_feet(foot_positions)

    def stand(self, body_height: float = None) -> list[float]:
        """Retourne les angles position debout avec stabilisation active."""
        h_nominal = (body_height if body_height is not None else SpotIK.STAND_HEIGHT) * self.body_height_multiplier
        total_roll = self.roll + self.manual_roll
        total_pitch = self.pitch + self.manual_pitch
        total_yaw = self.manual_yaw

        cR, sR = math.cos(total_roll), math.sin(total_roll)
        cP, sP = math.cos(total_pitch), math.sin(total_pitch)
        cY, sY = math.cos(total_yaw), math.sin(total_yaw)

        foot_positions = {}
        for leg in ['fr', 'fl', 'br', 'bl']:
            hip = SpotIK.HIP_POSITIONS[leg]

            hx, hy = hip[0], hip[1]
            hx_rot = cY*cP*hx + (-sY*cR + cY*sP*sR)*hy
            hy_rot = sY*cP*hx + (cY*cR + sY*sP*sR)*hy
            hz_rot = -sP*hx + cP*sR*hy

            foot_x = hx
            foot_y = hy
            foot_z = h_nominal

            w_dx = foot_x - hx_rot
            w_dy = foot_y - hy_rot
            w_dz = foot_z - hz_rot

            ux = cY*w_dx + sY*w_dy
            uy = -sY*w_dx + cY*w_dy
            uz = w_dz

            px = cP*ux - sP*uz
            py = uy
            pz = sP*ux + cP*uz

            lx = px
            ly = cR*py + sR*pz
            lz = -sR*py + cR*pz

            foot_positions[leg] = np.array([lx, ly, lz])

        return self.ik.solve_for_feet(foot_positions)

    def sit(self) -> list[float]:
        """Retourne les angles position assise avec stabilisation active."""
        return self.stand(body_height=-0.08)
