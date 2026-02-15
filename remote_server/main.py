from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from pydantic import BaseModel
import time
import json
import asyncio
import os

app = FastAPI(title="Bastet Remote Relay")

# Registre des nœuds : { "node_id": { "ip": "...", "role": "...", "last_seen": 123 } }
registry = {}

# Sockets actifs pour le tunne : { "node_id": WebSocket }
active_tunnels = {}

class NodeRegistration(BaseModel):
    node_id: str
    role: str
    local_ip: str
    public_ip: str | None = None
    port: int = 8000

@app.get("/")
def read_root():
    return {"status": "Bastet Relay Online"}

@app.post("/register")
def register_node(node: NodeRegistration):
    """Enregistre un nœud pour la découverte."""
    registry[node.node_id] = {
        "role": node.role,
        "local_ip": node.local_ip,
        "public_ip": node.public_ip, # Sera rempli par l'appelant ou le middleware
        "port": node.port,
        "last_seen": time.time()
    }
    print(f"✅ Node Registered: {node.node_id} ({node.role}) at {node.local_ip}")
    return {"status": "registered", "peers": registry}

@app.get("/peers")
def get_peers():
    """Retourne la liste des nœuds actifs (timeout 60s)."""
    now = time.time()
    # Nettoyage des vieux nœuds
    active_peers = {k: v for k, v in registry.items() if now - v["last_seen"] < 60}
    return active_peers

# --- VAULT & SECURITY ---
from .database import save_user_creds, get_user_creds, add_face_image, get_faces

class Credentials(BaseModel):
    username: str
    password: str
    intranet_url: str = "https://myges.fr"

@app.post("/vault/credentials")
def store_credentials(creds: Credentials):
    """Stocke les identifiants de manière sécurisée (chiffrés)."""
    save_user_creds(creds.username, creds.password, creds.intranet_url)
    return {"status": "stored_securely"}

@app.get("/vault/faces")
def list_faces():
    """Renvoie la liste des visages pour la synchro Vision Node."""
    return get_faces()

@app.post("/vault/upload_face")
async def upload_face(username: str, file: UploadFile = File(...)):
    """Upload une photo de visage pour un utilisateur."""
    os.makedirs("uploads/faces", exist_ok=True)
    file_path = f"uploads/faces/{username}_{file.filename}"
    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())
    
    add_face_image(username, file_path)
    return {"status": "uploaded", "path": file_path}

# --- SCRAPING SERVICE ---

@app.get("/scrape/myges/{username}")
def scrape_myges(username: str):
    """
    Récupère les infos Intranet (Notes, Agenda) en utilisant les creds chiffrés.
    Le LLM ne voit JAMAIS le mot de passe.
    """
    creds = get_user_creds(username)
    if not creds:
        return {"error": "No credentials found for this user"}
    
    # Mock Scraping (Simulation de connexion MyGES)
    # Dans la réalité, ici on utiliserait Selenium ou Requests avec creds['password']
    print(f"🔐 Decrypted password for {username}: {creds['password'][:3]}***")
    
    import random
    cours = ["Mathématiques", "IA & Data", "Anglais", "Management", "Projet Annuel"]
    salles = ["B204", "C102", "Labo IA", "Amphi A"]
    notes = ["18/20 en Python", "15/20 en Anglais", "12/20 en Maths"]
    
    is_exam_period = random.choice([True, False])
    
    data = {
        "status": "success",
        "username": username,
        "next_class": {
            "subject": random.choice(cours),
            "room": random.choice(salles),
            "time": "14:00"
        },
        "recent_grades": random.sample(notes, 2),
        "messages": ["Rappel: Rendu projet dimanche !"] if is_exam_period else []
    }
    
    return data

@app.websocket("/tunnel/{node_id}")
async def tunnel_endpoint(websocket: WebSocket, node_id: str):
    """
    WebSocket Relay.
    Tout ce qui est envoyé ici est redirigé vers le destinataire cible si précisé,
    ou broadcasté si c'est un Hub.
    """
    await websocket.accept()
    active_tunnels[node_id] = websocket
    print(f"🔌 Tunnel Connected: {node_id}")
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            target = message.get("target_id")
            
            if target and target in active_tunnels:
                # Routage direct (Relay)
                await active_tunnels[target].send_text(data)
            else:
                # Broadcast aux autres (sauf expéditeur)
                for nid, ws in active_tunnels.items():
                    if nid != node_id:
                        try:
                            await ws.send_text(data)
                        except:
                            pass
                            
    except WebSocketDisconnect:
        print(f"🔌 Tunnel Disconnected: {node_id}")
        del active_tunnels[node_id]
