from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
import aiohttp
import asyncio
import os
import json
import logging

# Configuration Hub
HUB_URL = os.environ.get("HUB_HTTP_URL", "http://localhost:8000")
HUB_WS_URI = os.environ.get("HUB_WS_URI", "ws://localhost:8000/ws/node/vision")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vision_webrtc")

pcs = set()

async def consume_webrtc_stream():
    """
    Se connecte au Hub, écoute les offres WebRTC (du Robot),
    et y répond pour démarrer la réception du flux vidéo.
    """
    import websockets
    
    async with websockets.connect(HUB_WS_URI) as ws:
        logger.info(f"Connecté au Hub Vision: {HUB_WS_URI}")
        
        async for message in ws:
            data = json.loads(message)
            msg_type = data.get("type")
            
            if msg_type == "webrtc_offer":
                logger.info("Offre WebRTC reçue !")
                offer_payload = data.get("payload")
                
                # Créer une PeerConnection
                pc = RTCPeerConnection()
                pcs.add(pc)
                
                @pc.on("track")
                def on_track(track):
                    logger.info(f"Track reçu: {track.kind}")
                    if track.kind == "video":
                        # Ici, on passerait le track à YOLO
                        # Pour l'instant, on consomme juste les frames pour pas bloquer
                        asyncio.ensure_future(consume_track(track))
                    
                    @track.on("ended")
                    async def on_ended():
                        logger.info("Track ended")
                        
                # Définir l'offre distante
                offer = RTCSessionDescription(sdp=offer_payload["sdp"], type=offer_payload["type"])
                await pc.setRemoteDescription(offer)
                
                # Créer la réponse
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                
                # Envoyer la réponse au Hub
                payload = {
                    "sdp": pc.localDescription.sdp,
                    "type": pc.localDescription.type
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(f"{HUB_URL}/answer", json=payload) as resp:
                        logger.info(f"Réponse envoyée: {resp.status}")

async def consume_track(track):
    while True:
        try:
            frame = await track.recv()
            # Convertir frame (av.VideoFrame) -> numpy (OpenCV) -> YOLO
            # img = frame.to_ndarray(format="bgr24")
            # results = yolo.predict(img)
            # ...
        except Exception:
            break

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(consume_webrtc_stream())
