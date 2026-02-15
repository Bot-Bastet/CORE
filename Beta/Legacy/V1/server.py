
import sys
import threading
import time
import asyncio
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import os

# Ensure we can import from local directory
sys.path.append(os.getcwd())

try:
    from vision import VisionSystem
    from ai_agent import AIAgent
    from myges_integration import MyGesIntegration
    from audio_listener import AudioListener
    from tts_module import TTSHandler
except ImportError as e:
    print(f"CRITICAL IMPORT ERROR: {e}")
    # We proceed but functionality will be broken, user needs to see this
    
app = FastAPI(title="Bastet AI Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- GLOBAL STATE MANAGER ---
class SystemState:
    def __init__(self):
        self.vision_thread = None
        self.ai_thread = None
        self.audio_thread = None
        self.tts = None
        self.myges = None
        self.shared_data = {}

state = SystemState()

# --- WEBSOCKET MANAGER ---
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
        # Filter dead connections
        to_remove = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                to_remove.append(connection)
        
        for c in to_remove:
            self.disconnect(c)

manager = ConnectionManager()

# --- LIFECYCLE ---
@app.on_event("startup")
async def startup_event():
    print("--- STARTING BASTET SERVER ---", flush=True)
    
    # 1. Initialize Shared Data
    import queue
    state.shared_data = {
        'vision_context': None,    
        'schedule_context': "En attente de connexion...", 
        'schedule_week': None,
        'ai_context': None,        
        'latest_user_input': None,  
        'is_speaking': False,
        'stream_queue': queue.Queue()  # Queue for token streaming
    }

    # 2. Init MyGes Object (No Login yet)
    try:
        state.myges = MyGesIntegration()
    except Exception as e:
        print(f"MyGes Init Error: {e}")

    # 3. Init TTS [MOVED TO BOOT SEQUENCE to prevent hanging]
    # state.tts = TTSHandler(state.shared_data)

    # 4. Boot System in Thread (Non-blocking)
    threading.Thread(target=boot_system_sequence, daemon=True).start()

    # Start Background Monitor Loop
    asyncio.create_task(monitor_loop())
    
    print("DEBUG: Startup Event Finished (Released to Uvicorn)", flush=True)

def boot_system_sequence():
    """Start subsystems sequentially to avoid startup freeze"""
    print("DEBUG: Boot sequence started...", flush=True)
    
    # Init TTS here (Pygame can block)
    try:
        print("DEBUG: Initializing TTS/Audio Mixer...", flush=True)
        state.tts = TTSHandler(state.shared_data)
        print("DEBUG: TTS Initialized.", flush=True)
    except Exception as e:
        print(f"DEBUG: TTS Init Failed: {e}", flush=True)

    # Load System Config from system_config.json
    ai_provider = "local"
    lm_studio_url = "http://localhost:1234/v1"
    model_filename = "NemoMix-Unleashed-12B-Q5_K_M.gguf" # Default fallback
    
    import json
    import os
    if os.path.exists("system_config.json"):
        try:
            with open("system_config.json", "r") as f:
                config_data = json.load(f)
                ai_provider = config_data.get("ai_provider", "local")
                lm_studio_url = config_data.get("lm_studio_url", "http://localhost:1234/v1")
                # Optional: allow overriding model filename driven by config
                # model_filename = config_data.get("model_filename", ...) 
        except:
             pass

    # Model Config (Mistral 7B)
    model_config = {
        "name": "Mistral 7B v0.3 Instruct (Classic)",
        "repo_id": "MaziyarPanahi/Mistral-7B-Instruct-v0.3-GGUF", 
        "filename": "Mistral-7B-Instruct-v0.3.Q5_K_M.gguf", # Hardcoded preference for now or from config?
        "provider": ai_provider,
        "api_url": lm_studio_url
    }

    try:
        time.sleep(1) # Let server settle
        
        try:
            print("DEBUG: Initializing Vision System (Object)...", flush=True)
            state.vision_thread = VisionSystem(shared_data=state.shared_data)
        except Exception as ve:
            print(f"DEBUG: Vision Init Failed (Camera Issue?): {ve}", flush=True)
            # Continue anyway so AI can work

        
        print(f"DEBUG: Initializing AI Agent ({ai_provider.upper()})...", flush=True)
        state.ai_thread = AIAgent(model_config=model_config, shared_data=state.shared_data)
        
        # Explicitly load model HERE to ensure it works and we see output
        print("DEBUG: Loading AI Model Synchronously...", flush=True)
        state.ai_thread.load_model()
        print("DEBUG: AI Model Loaded.", flush=True)
        
        print("DEBUG: Initializing Audio Listener (Object)...", flush=True)
        state.audio_thread = AudioListener(shared_data=state.shared_data)
        
        state.vision_thread.daemon = True
        state.ai_thread.daemon = True
        state.audio_thread.daemon = True
        
        # Sequenced Startup
        print("DEBUG: Starting AI Thread...", flush=True)
        state.ai_thread.start()
        
        # No need to sleep for AI anymore, it is loaded.
        
        print("DEBUG: Starting Vision & Audio...", flush=True)
        if state.vision_thread:
            state.vision_thread.start()
        else:
            print("DEBUG: Skipping Vision Start (Not Initialized)", flush=True)

        if state.audio_thread:
            state.audio_thread.start()
        else:
            print("DEBUG: Skipping Audio Start (Not Initialized)", flush=True)
        
        state.audio_thread.stt_enabled = False
        print("DEBUG: All Systems GO.", flush=True)
        
    except Exception as e:
        print(f"Error starting threads: {e}")

# Global lock for login
login_lock = threading.Lock()

def connect_myges():
    """Connect to MyGes for Teano if not already connected."""
    if state.myges and state.myges.access_token:
        return # Already connected
        
    # Non-blocking check to avoid spamming threads
    if login_lock.locked():
        return

    with login_lock:
        # Double check inside lock
        if state.myges and state.myges.access_token:
            return

        print("Attempting connection to MyGes (Teano detected)...", flush=True)
        try:
            # We assume stored credentials are for Teano as per user request
            if state.myges.login():
                state.shared_data['schedule_context'] = state.myges.get_context_string()
                state.shared_data['schedule_week'] = state.myges.get_week_summary()
                print("MyGes Connected successfully.", flush=True)
            else:
                 state.shared_data['schedule_context'] = "Erreur connexion MyGes (Teano)."
        except Exception as e:
            print(f"MyGes Error: {e}", flush=True)
            state.shared_data['schedule_context'] = "Service indisponible."

# --- BACKGROUND MONITOR ---
last_ai_context = ""
last_user_input_timestamp = 0
last_audio_broadcast_timestamp = 0

async def monitor_loop():
    global last_ai_context, last_user_input_timestamp, last_audio_broadcast_timestamp
    while True:
        try:
            # INTERRUPTION LOGIC: Stop TTS if new user input
            current_user = state.shared_data.get('latest_user_input')
            if current_user and current_user['timestamp'] > last_user_input_timestamp:
                last_user_input_timestamp = current_user['timestamp']
                
                if state.tts:
                    state.tts.stop()
                
                # Clear pending stream tokens so they don't lag behind
                stream_q = state.shared_data.get('stream_queue')
                if stream_q:
                    with stream_q.mutex:
                        stream_q.queue.clear()

            # 0. Check for Stream Tokens
            stream_q = state.shared_data.get('stream_queue')
            if stream_q:
                tokens = []
                # Drain queue up to a limit to avoid blocking event loop too long
                while not stream_q.empty() and len(tokens) < 50:
                    tokens.append(stream_q.get_nowait())
                
                if tokens:
                    await manager.broadcast({
                        "type": "stream_token",
                        "payload": "".join(tokens)
                    })

            # 1. Check for AI Output (Final Completion)
            current_ai = state.shared_data.get('ai_context')
            if current_ai and current_ai != last_ai_context:
                last_ai_context = current_ai
                # Trigger TTS (Full sentence/response)
                # IMPORTANT: In a perfect world we stream TTS. 
                # For now we use the existing non-blocking speak() which takes the whole text.
                # This might feel "late" compared to the visual stream.
                state.tts.speak(current_ai)
                
                # Note: We do NOT send "chat_message" here if we are streaming, 
                # or we send it as a "confirmation" / "finalize" event?
                # Let's send it as "chat_message_final" or transparently handle it in UI
                # Actually, sending "chat_message" refreshes the full thing, which is fine/safe.
                await manager.broadcast({
                    "type": "chat_message",
                    "payload": { "role": "assistant", "content": current_ai }
                })
            
            # 2. Check for User Input (from Audio) to mirror in UI
            user_inp = state.shared_data.get('latest_user_input')
            if user_inp and user_inp.get('type') == 'text_from_audio':
                inp_ts = user_inp.get('timestamp', 0)
                if inp_ts > last_audio_broadcast_timestamp:
                    last_audio_broadcast_timestamp = inp_ts
                    await manager.broadcast({
                        "type": "chat_message",
                        "payload": { "role": "user", "content": user_inp['content'] }
                    })
                
            # 3. Broadcast Status
            status_payload = {
                "vision": state.shared_data.get('vision_context', {}),
                "schedule": state.shared_data.get('schedule_context', ""),
                "is_speaking": state.shared_data.get('is_speaking', False),
                "stt_enabled": getattr(state.audio_thread, 'stt_enabled', False)
            }
            await manager.broadcast({ "type": "status_update", "payload": status_payload })

            # 4. Check for Identity (Teano) for Agenda Loading
            vc = state.shared_data.get('vision_context')
            if vc:
                names = vc.get('faces_names', [])
                # Check for Teano (case insensitive just in case, though vision usually returns capitalized)
                if any(n.lower() == "teano" for n in names):
                     # Trigger login if not connected. Run in thread to not block loop.
                     threading.Thread(target=connect_myges, daemon=True).start()

            # 5. Check for Logs (Simple Proxy)
            # You might want to implement a real log handler queue
            
            await asyncio.sleep(0.05)
            
        except Exception as e:
            print(f"Monitor Loop Error: {e}")
            await asyncio.sleep(1)

# --- API ENDPOINTS ---
class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
async def send_chat(req: ChatRequest):
    # 1. Update UI immediately
    await manager.broadcast({ "type": "chat_message", "payload": { "role": "user", "content": req.message } })
    
    # 2. Halt current speech if any
    if state.tts: state.tts.stop()
    
    # 3. Send to AI Agent
    state.shared_data['latest_user_input'] = {
        'type': 'text_from_api', # distinct from audio
        'content': req.message,
        'timestamp': time.time()
    }
    return {"status": "sent"}

@app.post("/api/stt")
async def toggle_stt(enable: str):
    is_on = (enable.lower() == 'true')
    print(f"Toggling STT: {is_on}")
    if state.audio_thread:
        state.audio_thread.stt_enabled = is_on
    return {"status": "ok", "enabled": is_on}

@app.post("/api/stop")
async def stop_system():
    print("STOP COMMAND RECEIVED")
    if state.tts:
        state.tts.stop()
    # Optional: Interrupt AI generation if possible
    # state.ai_thread.interrupt() 
    return {"status": "stopped"}

@app.websocket("/ws/status")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send existing history immediately
        if state.ai_thread and hasattr(state.ai_thread, 'history'):
            # Filter history to only include user/assistant messages suitable for UI
            history_export = [
                m for m in state.ai_thread.history 
                if m.get('role') in ['user', 'assistant'] 
                and not m.get('content', '').startswith("SYSTEM:")
            ]
            await websocket.send_json({
                "type": "history_sync",
                "payload": history_export
            })

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
