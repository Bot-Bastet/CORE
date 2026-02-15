import asyncio
import json
import logging
import os
import platform
import socket

import aiohttp
import cv2
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaPlayer

# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("robot_streamer")

HUB_URL = os.environ.get("HUB_HTTP_URL", "http://localhost:8000")

class CameraStreamTrack(VideoStreamTrack):
    """
    Track vidéo WebRTC qui lit depuis OpenCV.
    """
    def __init__(self):
        super().__init__()
        self.cap = cv2.VideoCapture(0)  # /dev/video0
        # Optimisation Latence: Basse résolution, haut FPS
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        # Lecture bloquante mais rapide
        ret, frame = self.cap.read()
        if not ret:
            # Si erreur, frame noire ou retry
            return None

        # Convertir en Frame aiortc
        from av import VideoFrame
        new_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        new_frame.pts = pts
        new_frame.time_base = time_base
        return new_frame

async def run(pc, player):
    # Créer une offre
    pc.addTrack(CameraStreamTrack())
    # Si on voulait l'audio: pc.addTrack(player.audio)

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # Envoyer l'offre au Hub (Signaling)
    payload = {
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type,
        "node_id": "robot_camera"
    }
    
    logger.info(f"Envoi de l'offre WebRTC au Hub: {HUB_URL}")
    
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{HUB_URL}/offer", json=payload) as resp:
            res = await resp.json()
            logger.info(f"Réponse Hub: {res}")
            
    # Attendre la réponse (Answer) via WebSocket (géré par bridge.py ou ici)
    # Pour simplifier, ce script est "complet" mais dans la réalité, 
    # il faudrait qu'il écoute le WS pour recevoir l'Answer du Vision Node.
    # Ici, on va faire un polling simple ou attendre que le bridge.py nous la passe via IPC.
    
    # NOTE: Pour une intégration parfaite, ce code devrait être FUSIONNÉ dans `bridge.py` 
    # pour profiter de la connexion WebSocket existante.
    
    print("Streamer en attente de connexion...")
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    pc = RTCPeerConnection()
    # player = MediaPlayer('/dev/video0') # Si on utilisait MediaPlayer direct
    
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run(pc, None))
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(pc.close())
