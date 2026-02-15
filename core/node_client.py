import asyncio
import json
import time
import threading
import websockets
import sys

# Import des modules core pour l'exécution locale sur le nœud
try:
    from .vision import VisionSystem
    from .ai_agent import AIAgent
except ImportError:
    # Si lancé depuis la racine
    from core.vision import VisionSystem
    from core.ai_agent import AIAgent

class NodeClient:
    def __init__(self, role, hub_ip, config):
        self.role = role
        self.hub_uri = f"ws://{hub_ip}:8000/ws/node/{role}"
        self.config = config
        self.running = True
        self.websocket = None
        
        # État local synchronisé avec le Hub
        self.shared_data = {
            'is_speaking': False,
            'vision_context': {},
            'schedule_context': '',
            'latest_user_input': None,
            'latest_ai_response': None
        }
        
        # Modules locaux
        self.vision = None
        self.ai_agent = None

    async def connect(self):
        # Si aucune IP de Hub n'est fournie, on cherche via DiscoveryManager
        if self.hub_uri == "ws://auto:8000/ws/node/{self.role}":
            print("Recherche du Hub (UDP Broadcast + Remote)...")
            from core.discovery import DiscoveryManager
            discovery = DiscoveryManager(self.role, remote_server_url=self.config.get("remote_server_url"))
            discovery.start()
            
            # Essayer de trouver l'IP pendant 30s
            found_ip = None
            for _ in range(15):
                found_ip = discovery.get_hub_ip()
                if found_ip:
                    break
                print("... Hub non trouvé, nouvelle tentative dans 2s")
                await asyncio.sleep(2)
            
            if found_ip:
                print(f"✅ Hub trouvé à l'adresse: {found_ip}")
                self.hub_uri = f"ws://{found_ip}:8000/ws/node/{self.role}"
            else:
                print("❌ Hub introuvable. Vérifiez que le Hub est lancé (--role hub).")
                return

        while self.running:
            try:
                print(f"Tentative de connexion au Hub ({self.hub_uri})...")
                async with websockets.connect(self.hub_uri) as websocket:
                    self.websocket = websocket
                    print(f"✓ Connecté au Hub en tant que {self.role}")
                    
                    # Démarrer les tâches d'écoute et d'envoi
                    await asyncio.gather(
                        self.listen_for_updates(),
                        self.push_updates()
                    )
            except Exception as e:
                print(f"x Connexion perdue ou échouée: {e}")
                print("Nouvelle tentative dans 3s...")
                await asyncio.sleep(3)

    async def listen_for_updates(self):
        """Écoute les mises à jour venant du Hub (ex: contexte agenda pour l'IA)"""
        try:
            async for message in self.websocket:
                data = json.loads(message)
                # Mettre à jour l'état local avec les données du Hub
                if data.get("type") == "state_sync":
                    payload = data.get("payload", {})
                    # On ne met à jour que ce qui nous intéresse pour éviter les écrasements
                    # Par exemple l'IA a besoin de savoir si le TTS parle, et l'agenda
                    if "schedule_context" in payload:
                        self.shared_data["schedule_context"] = payload["schedule_context"]
                    if "is_speaking" in payload:
                        self.shared_data["is_speaking"] = payload["is_speaking"]
                    if "latest_user_input" in payload and self.role == "llm":
                        # L'IA doit réagir au input utilisateur
                        new_input = payload["latest_user_input"]
                        # Vérifier si c'est nouveau par timestamp
                         # (Logique déjà dans AIAgent, on met juste à jour)
                        self.shared_data["latest_user_input"] = new_input
                        
        except Exception as e:
            print(f"Erreur écoute: {e}")
            raise e

    async def push_updates(self):
        """Envoie les données locales vers le Hub"""
        while self.running:
            if self.role == "vision" and self.vision:
                # Récupérer les données de vision locale
                vision_data = self.shared_data.get("vision_context", {})
                if vision_data:
                    await self.websocket.send(json.dumps({
                        "type": "vision_update",
                        "payload": vision_data
                    }))
            
            elif self.role == "llm" and self.ai_agent:
                # Récupérer la réponse IA
                ai_resp = self.shared_data.get("latest_ai_response")
                if ai_resp:
                    # On ne renvoie que si c'est nouveau (timestamp checké côté Hub?)
                    # Pour simplifier, on envoie, le Hub filtrera
                    await self.websocket.send(json.dumps({
                        "type": "ai_response",
                        "payload": ai_resp
                    }))
            
            await asyncio.sleep(0.1)

    def start_local_modules(self):
        """Lance les modules spécifiques au rôle"""
        if self.role == "vision":
            print("Lancement du module Vision...")
            self.vision = VisionSystem(self.config, self.shared_data)
            self.vision.start()
            
        elif self.role == "llm":
            print("Lancement du module IA...")
            self.ai_agent = AIAgent(self.config, self.shared_data)
            self.ai_agent.start()
            
        elif self.role == "stt":
            print("Lancement du module STT...")
            from .stt import STTHandler
            self.stt = STTHandler(self.config, self.shared_data)
            self.stt.start()

        elif self.role == "tts":
            print("Lancement du module TTS...")
            from .tts import TTSHandler
            self.tts = TTSHandler(self.config, self.shared_data) 
            # Note: TTS might need an audio Output loop or just be a service
            # For now, it initializes resources. The loop is usually event-driven.

def run_node(role, hub_ip, config):
    client = NodeClient(role, hub_ip, config)
    
    # Démarrer les modules locaux dans des threads
    client.start_local_modules()
    
    # Démarrer la boucle async pour le WebSocket
    try:
        asyncio.run(client.connect())
    except KeyboardInterrupt:
        print("Arrêt du nœud...")
