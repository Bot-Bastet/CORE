"""
Bastet AI V2 - FastAPI Server
Serveur WebSocket + REST API.
"""

import sys
import json
import threading
import time
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import os

# Import des modules core
from .tts import TTSHandler
from .stt import STTHandler
from .vision import VisionSystem
from .ai_agent import AIAgent
from .calendar import CalendarIntegration


app = FastAPI(title="Bastet AI V2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SystemState:
    def __init__(self):
        self.config = {}
        self.shared_data = {
            'is_speaking': False,
            'tts_enabled': False,
            'stt_enabled': False,
            'calendar_loaded': False,
            'vision_context': {},
            'schedule_context': '',
            'latest_user_input': None,
            'latest_ai_response': None,
        }
        self.tts = None
        self.stt = None
        self.vision = None
        self.ai_agent = None
        self.calendar = None
        self.chat_history = []


state = SystemState()


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass


manager = ConnectionManager()


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            return json.load(f)
    else:
        print("⚠ config.json non trouvé, utilisation des défauts")
        return {
            "ai_provider": "lmstudio",
            "lm_studio_url": "http://192.168.0.57:1234/v1",
            "yolo_model": "yolov8n.pt",
            "camera_id": 0,
            "yolo_resolution": [640, 480],
            "yolo_fps": 30,
            "face_resolution": [640, 480],
            "tts_enabled": False,
            "stt_enabled": False,
            "tts_voice": "fr-FR-DeniseNeural"
        }


@app.on_event("startup")
async def startup_event():
    print("\n" + "="*50)
    print("       BASTET AI V2 - Démarrage")
    print("="*50 + "\n")
    
    state.config = load_config()
    threading.Thread(target=boot_system, daemon=True).start()
    asyncio.create_task(monitor_loop())


def boot_system():
    time.sleep(1)
    
    role = os.environ.get("BASTET_ROLE", "all")
    print(f"CORE System booting in mode: {role}")

    # TTS
    if role in ["all", "tts"]:
        state.tts = TTSHandler(state.config, state.shared_data)
        print(f"✓ TTS initialisé (Local)")
    elif role == "hub":
        print(f"ℹ TTS: En attente de nœud distant")
    
    # Calendar (Toujours sur le Hub pour l'instant)
    if role in ["all", "hub"]:
        state.calendar = CalendarIntegration(state.config, state.shared_data)
        state.calendar.connect()
        print(f"✓ Calendar initialisé")
    
    # Vision
    if role in ["all", "vision"]:
        time.sleep(0.5)
        state.vision = VisionSystem(state.config, state.shared_data)
        state.vision.start()
        print(f"✓ Vision System initialisé (Local)")
    elif role == "hub":
        print(f"ℹ Vision System: En attente de nœud distant")
    
    # STT (Vosk)
    if role in ["all", "stt"]:
        time.sleep(0.3)
        state.stt = STTHandler(state.config, state.shared_data)
        state.stt.start()
        print(f"✓ STT initialisé (Local)")
    elif role == "hub":
        print(f"ℹ STT: En attente de nœud distant")
    
    # AI Agent
    if role in ["all", "llm"]:
        time.sleep(0.3)
        state.ai_agent = AIAgent(state.config, state.shared_data)
        state.ai_agent.start()
        print(f"✓ AI Agent initialisé (Local)")
    elif role == "hub":
        print(f"ℹ AI Agent: En attente de nœud distant")
    
    print("\n" + "="*50)
    print(f"       BASTET AI V2 - Prêt ({role})")
    print("="*50 + "\n")


last_ai_response_ts = 0
last_user_input_ts = 0


async def monitor_loop():
    global last_ai_response_ts, last_user_input_ts
    
    while True:
        await asyncio.sleep(0.2)
        
        # Update calendar based on recognized faces
        vision_ctx = state.shared_data.get('vision_context', {})
        faces = vision_ctx.get('faces_names', [])
        
        if state.calendar:
            if faces:
                # Mettre à jour pour la première personne reconnue
                for face in faces:
                    if face and face != "Inconnu":
                        state.calendar.update_for_person(face)
                        break
            
            # Vérifier le timeout (retire l'agenda si personne partie)
            state.calendar.check_presence_timeout()
        
        await manager.broadcast({
            "type": "status_update",
            "payload": {
                "vision": vision_ctx,
                "schedule_context": state.shared_data.get('schedule_context', ''),
                "is_speaking": state.shared_data.get('is_speaking', False),
                "stt_enabled": state.shared_data.get('stt_enabled', False),
                "tts_enabled": state.shared_data.get('tts_enabled', False),
                "vision_enabled": state.shared_data.get('vision_enabled', True),
                "calendar_loaded": state.shared_data.get('calendar_loaded', False),
            }
        })
        
        # Check for STT input (audio messages)
        user_input = state.shared_data.get('latest_user_input')
        if user_input:
            ts = user_input.get('timestamp', 0)
            if ts > last_user_input_ts:
                last_user_input_ts = ts
                content = user_input.get('content', '')
                input_type = user_input.get('type', '')
                
                # Only broadcast audio messages (text messages are already sent via API)
                if content and input_type == 'text_from_audio':
                    state.chat_history.append({"role": "user", "content": content})
                    await manager.broadcast({
                        "type": "chat_message",
                        "payload": {"role": "user", "content": content}
                    })
        
        # Check for AI response
        ai_response = state.shared_data.get('latest_ai_response')
        if ai_response:
            ts = ai_response.get('timestamp', 0)
            if ts > last_ai_response_ts:
                last_ai_response_ts = ts
                content = ai_response.get('content', '')
                
                state.chat_history.append({"role": "assistant", "content": content})
                
                await manager.broadcast({
                    "type": "chat_message",
                    "payload": {"role": "assistant", "content": content}
                })
                
                if state.tts and state.tts.is_enabled():
                    state.tts.speak(content)


class ChatRequest(BaseModel):
    message: str


@app.post("/api/chat")
async def send_chat(req: ChatRequest):
    message = req.message.strip()
    if not message:
        return {"error": "Message vide"}
    
    state.chat_history.append({"role": "user", "content": message})
    
    await manager.broadcast({
        "type": "chat_message",
        "payload": {"role": "user", "content": message}
    })
    
    state.shared_data['latest_user_input'] = {
        'type': 'text',
        'content': message,
        'timestamp': time.time()
    }
    
    return {"status": "ok"}


@app.post("/api/tts")
async def toggle_tts(enable: str):
    enabled = enable.lower() == "true"
    if state.tts:
        state.tts.toggle(enabled)
    return {"tts_enabled": enabled}


@app.post("/api/stt")
async def toggle_stt(enable: str):
    enabled = enable.lower() == "true"
    if state.stt:
        state.stt.toggle(enabled)
    return {"stt_enabled": enabled}


@app.post("/api/vision")
async def toggle_vision(enable: str):
    """Active/désactive le système de vision (YOLO + reconnaissance faciale)."""
    enabled = enable.lower() == "true"
    if state.vision:
        state.vision.toggle(enabled)
    return {"vision_enabled": enabled}


@app.get("/api/settings")
async def get_settings():
    """Retourne l'état actuel de toutes les fonctionnalités."""
    return {
        "tts_enabled": state.shared_data.get('tts_enabled', False),
        "stt_enabled": state.shared_data.get('stt_enabled', False),
        "vision_enabled": state.shared_data.get('vision_enabled', True),
        "calendar_loaded": state.shared_data.get('calendar_loaded', False),
    }


@app.post("/api/stop")
async def stop_audio():
    if state.tts:
        state.tts.stop()
    return {"status": "stopped"}


@app.post("/api/record")
async def manual_record(duration: int = 5):
    """Enregistre avec Vosk (léger) pendant X secondes."""
    try:
        from .ptt import get_ptt_recorder
        recorder = get_ptt_recorder()
        
        if not recorder.model:
            return {"error": "Modèle Vosk non chargé. Installez vosk et téléchargez vosk-model-small-fr"}
        
        text = recorder.record_for_duration(float(duration))
        
        if text:
            return {"text": text}
        else:
            return {"text": "", "warning": "Aucun texte détecté"}
            
    except Exception as e:
        print(f"Erreur enregistrement: {e}")
        return {"error": str(e)}


@app.websocket("/ws/status")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    
    await websocket.send_json({
        "type": "history_sync",
        "payload": state.chat_history
    })
    
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.websocket("/ws/node/{role}")
async def node_websocket_endpoint(websocket: WebSocket, role: str):
    """Endpoint pour les nœuds distants (Vision, LLM)."""
    await manager.connect(websocket)
    print(f"➕ Nœud connecté: {role}")
    
    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            msg_type = data.get("type")
            payload = data.get("payload")
            
            # Détection de personne (Vision)
            if msg_type == "vision_update":
                state.shared_data["vision_context"] = payload
                
                # Si une personne est reconnue, on enrichit le contexte via le Remote Server
                detected_names = payload.get("faces", [])
                for name in detected_names:
                    if name != "Inconnu":
                        # Appel au Remote Server (Scraping Sécurisé)
                        remote_url = state.config.get("remote_server_url")
                        if remote_url:
                            try:
                                import requests
                                print(f"🔍 Enriching data for {name} via Remote Server...")
                                resp = requests.get(f"{remote_url}/scrape/myges/{name}", timeout=5)
                                if resp.status_code == 200:
                                    user_data = resp.json()
                                    # On pousse ces infos dans le contexte partagé pour le LLM
                                    state.shared_data[f"user_context_{name}"] = user_data
                                    print(f"✅ Data retrieved for {name}: {user_data.get('next_class')}")
                            except Exception as e:
                                print(f"⚠️ Scraping Failed: {e}")

                await manager.broadcast({
                    "type": "status_update",
                    "payload": {"vision": payload}
                })
                
            elif msg_type == "ai_response":
                # Réponse de l'IA distante
                content = payload.get("content")
                timestamp = payload.get("timestamp")
                
                # Check doublon ? (Simplifié ici)
                state.shared_data["latest_ai_response"] = payload
                
                # Broadcast Chat + TTS
                state.chat_history.append({"role": "assistant", "content": content})
                await manager.broadcast({
                    "type": "chat_message",
                    "payload": {"role": "assistant", "content": content}
                })
                
                if state.tts and state.tts.is_enabled():
                    state.tts.speak(content)
                    
    except WebSocketDisconnect:
        print(f"➖ Nœud déconnecté: {role}")
        manager.disconnect(websocket)
    except Exception as e:
        print(f"Erreur WebSocket Node ({role}): {e}")
        manager.disconnect(websocket)


@app.post("/offer")
async def offer(params: dict):
    """
    WebRTC Signaling: Echange d'offres/réponses SDP.
    Le streamer (Robot) envoie une OFFRE.
    Le viewer (Vision/Web) envoie une RÉPONSE.
    """
    # Simplification: On broadcast l'offre à tous les nœuds Vision connectés
    # Dans une vraie implém, on ciblerait un pair spécifique via ID.
    # Ici, on stocke l'offre et on attend qu'un Viewer la consomme via polling ou WS.
    
    # Pour l'instant, on utilise le WS Broadcast pour avertir
    await manager.broadcast({
        "type": "webrtc_offer",
        "payload": params
    })
    return {"status": "ok"}

@app.post("/answer")
async def answer(params: dict):
    """
    WebRTC Signaling: Réponse du Viewer vers le Streamer.
    """
    await manager.broadcast({
        "type": "webrtc_answer",
        "payload": params
    })
    return {"status": "ok"}

def run_server():
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run_server()
