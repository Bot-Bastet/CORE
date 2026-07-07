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
        """
        Avance d'un pas de temps et calcule les 12 angles de servo.

        Args:
            dt:    delta temps [s]
            vx:    vitesse avant [m/s] (normalise)
            vy:    vitesse laterale [m/s] (normalise)
            omega: vitesse de rotation [rad/s] (normalise)

        Returns:
            liste de 12 angles servos [deg]
        """
        self.t += dt
        cycle_phase = (self.t * self.freq) % 1.0

        foot_positions = {}
        for leg in ['fr', 'fl', 'br', 'bl']:
            phase = (cycle_phase + self.offsets[leg]) % 1.0
            # Ajout de la composante de rotation (yaw)
            hip = SpotIK.HIP_POSITIONS[leg]
            rot_x = -omega * hip[1]
            rot_y =  omega * hip[0]
            pos = self.bezier.foot_trajectory(phase,
                                               vx + rot_x,
                                               vy + rot_y)
            # Stabilisation d'assiette active via IMU feedback
            dz = -hip[0] * math.sin(self.pitch) - hip[1] * math.sin(self.roll)
            # Limiter la compensation a +/- 3.5 cm pour eviter les saturations d'IK
            dz = max(-0.035, min(0.035, dz))
            pos[2] += dz
            foot_positions[leg] = pos

        return self.ik.solve_for_feet(foot_positions)

    def stand(self, body_height: float = None) -> list[float]:
        """Retourne les angles position debout avec stabilisation active."""
        h = body_height if body_height is not None else SpotIK.STAND_HEIGHT
        foot_positions = {}
        for leg in ['fr', 'fl', 'br', 'bl']:
            hip = SpotIK.HIP_POSITIONS[leg]
            dz = -hip[0] * math.sin(self.pitch) - hip[1] * math.sin(self.roll)
            dz = max(-0.035, min(0.035, dz))
            foot_positions[leg] = np.array([0.0, 0.0, h + dz])
        return self.ik.solve_for_feet(foot_positions)

    def sit(self) -> list[float]:
        """Retourne les angles position assise avec stabilisation active."""
        return self.stand(body_height=-0.08)
