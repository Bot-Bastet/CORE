"""
Bastet AI V2 - AI Agent Module
Supporte LM Studio (distant) ou modèle local via llama-cpp.
"""

import threading
import time
import os
import requests
import json
import re
import socket
from datetime import datetime


class AIAgent(threading.Thread):
    def __init__(self, config: dict, shared_data: dict = None):
        super().__init__(daemon=True)
        self.config = config
        self.shared_data = shared_data if shared_data is not None else {}
        self.running = True
        self.ready = False
        
        # Config
        self.provider = config.get("ai_provider", "lmstudio")
        self.lm_studio_url = config.get("lm_studio_url", "http://192.168.0.57:1234/v1")
        self.nvidia_nim_api_key = config.get("nvidia_nim_api_key", "")
        self.nim_model = config.get("nim_model", "meta/llama3-8b-instruct")
        self.local_model_path = config.get("model_path", "")
        
        # Local model (si utilisé)
        self.llm = None
        
        # Historique conversation
        self.conversation_history = []
        self.max_history = 20
        
        # Timestamp pour éviter doublons
        self.last_processed_timestamp = 0

    def load_model(self):
        """Charge le modèle local ou configure l'API distante."""
        if self.provider == "lmstudio":
            print(f"✓ AI Agent configuré pour LM Studio: {self.lm_studio_url}")
            self.ready = True
        elif self.provider == "nvidia_nim":
            print(f"✓ AI Agent configuré pour NVIDIA NIM (Modèle: {self.nim_model})")
            if not self.nvidia_nim_api_key or self.nvidia_nim_api_key == "YOUR_NVIDIA_API_KEY":
                print("⚠ ATTENTION: nvidia_nim_api_key n'est pas configurée dans config.json !")
            self.ready = True
        else:
            print(f"Chargement du modèle local: {self.local_model_path}")
            try:
                from llama_cpp import Llama
                self.llm = Llama(
                    model_path=self.local_model_path,
                    n_ctx=4096,
                    n_gpu_layers=-1,
                    verbose=False
                )
                print("✓ Modèle local chargé")
                self.ready = True
            except Exception as e:
                print(f"✗ Erreur chargement modèle: {e}")

    def get_system_prompt(self, schedule_context: str = ""):
        """Retourne le prompt système de Bastet."""
        now = datetime.now()
        date_str = now.strftime("%A %d %B %Y")
        time_str = now.strftime("%H:%M")
        
        agenda_block = ""
        if schedule_context:
            agenda_block = f"""

AGENDA DE L'UTILISATEUR (SOURCE DE VÉRITÉ ABSOLUE - NE JAMAIS INVENTER):
{schedule_context}
RESPECTE CES HORAIRES À LA LETTRE. Le premier cours du jour est la première entrée AUJOURD'HUI dans cet agenda.
"""
        
        return f"""Tu es Bastet, une IA d'assistance embarquée dans un robot communiquant avec des humains.

DATE/HEURE: {date_str}, {time_str}
{agenda_block}
RÈGLES STRICTES:
- Tu es le "cerveau" d'un robot quadrupède nommé Bastet.
- L'utilisateur peut te demander de te déplacer, d'avancer ou de bouger. C'est TOI qui dois exécuter ce mouvement physiquement.
- Pour commander ton propre corps physique, tu DOIS ABSOLUMENT ajouter le tag exact `[CMD: action]` à la toute fin de ta réponse.
- Les SEULES actions autorisées sont : `[CMD: avancer]`, `[CMD: stop]`
- N'invente jamais d'autres tags (ex: pas de `[Bouge vers...]`). Reste strictement sur `[CMD: avancer]`.
- Utilise UNIQUEMENT l'agenda ci-dessus pour le planning. Ne devin JAMAIS.
- Ne dis JAMAIS "Selon l'agenda" ou "D'après l'API". Parle naturellement comme si tu connaissais l'emploi du temps de la personne de tête. (Ex: "Demain tu as cours de Unity à 9h45").
- Sois TRÈS CONCISE (1-2 phrases max)
- Pas de formules de politesse excessives."""


    def generate_reaction(self, vision_data: dict, schedule_context: str, user_input: str = None):
        """Génère une réponse basée sur le contexte."""
        
        # Construire le contexte visuel
        context_parts = []
        
        if vision_data:
            faces = vision_data.get("faces_names", [])
            objects = vision_data.get("objects", [])
            if faces:
                context_parts.append(f"Personnes visibles: {', '.join(faces)}")
            if objects:
                context_parts.append(f"Objets détectés: {', '.join(objects[:5])}")
        
        vision_context = " | ".join(context_parts) if context_parts else ""
        
        # Messages - l'agenda est déjà inclus dans le system prompt
        messages = [
            {"role": "system", "content": self.get_system_prompt(schedule_context)},
        ]
        if vision_context:
            messages.append({"role": "system", "content": f"Contexte vision: {vision_context}"})
        
        # Ajouter historique
        messages.extend(self.conversation_history[-self.max_history:])
        
        # Ajouter input utilisateur
        if user_input:
            messages.append({"role": "user", "content": user_input})
            self.conversation_history.append({"role": "user", "content": user_input})
        
        # Générer réponse
        if self.provider == "lmstudio":
            response = self._query_lm_studio(messages)
        elif self.provider == "nvidia_nim":
            response = self._query_nvidia_nim(messages)
        else:
            response = self._query_local(messages)
        
        if response:
            # --- Check and send UDP Commands ---
            cmd_match = re.search(r'\[CMD:\s*(.*?)\]', response, re.IGNORECASE)
            if cmd_match:
                cmd = cmd_match.group(1).strip().upper()
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    # Envoyer plusieurs fois pour garantir la reception dans la boucle 240Hz
                    for _ in range(5):
                        sock.sendto(cmd.encode('utf-8'), ('127.0.0.1', 5005))
                        import time as _t; _t.sleep(0.01)
                    sock.close()
                    print(f"[ROBOT] Commande envoyee : {cmd}")
                except Exception as e:
                    print(f"Erreur envoi commande UDP: {e}")
                
                # Remove command from user-facing text
                response = re.sub(r'\[CMD:\s*.*?\]', '', response, flags=re.IGNORECASE).strip()
            # -----------------------------------

            self.conversation_history.append({"role": "assistant", "content": response})
        
        return response

    def _query_lm_studio(self, messages: list) -> str:
        """Requête vers LM Studio API."""
        try:
            response = requests.post(
                f"{self.lm_studio_url}/chat/completions",
                json={
                    "messages": messages,
                    "max_tokens": 150,
                    "temperature": 0.3,
                    "stream": False
                },
                timeout=60
            )
            
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                print(f"LM Studio error: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"LM Studio connection error: {e}")
            return None

    def _query_nvidia_nim(self, messages: list) -> str:
        """Requête vers NVIDIA NIM API."""
        if not self.nvidia_nim_api_key or self.nvidia_nim_api_key == "YOUR_NVIDIA_API_KEY":
            print("Erreur: Clé API NVIDIA NIM manquante.")
            return "Désolé, ma clé API NVIDIA n'est pas configurée pour l'instant."

        try:
            headers = {
                "Authorization": f"Bearer {self.nvidia_nim_api_key}",
                "Content-Type": "application/json"
            }
            response = requests.post(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers=headers,
                json={
                    "model": self.nim_model,
                    "messages": messages,
                    "max_tokens": 150,
                    "temperature": 0.3,
                    "stream": False
                },
                timeout=60
            )
            
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                print(f"NVIDIA NIM error: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            print(f"NVIDIA NIM connection error: {e}")
            return None

    def _query_local(self, messages: list) -> str:
        """Requête vers modèle local llama-cpp."""
        if not self.llm:
            return None
        
        try:
            # Formater en prompt
            prompt = ""
            for msg in messages:
                role = msg["role"]
                content = msg["content"]
                if role == "system":
                    prompt += f"[INST] <<SYS>>\n{content}\n<</SYS>>\n\n"
                elif role == "user":
                    prompt += f"{content} [/INST]\n"
                elif role == "assistant":
                    prompt += f"{content}\n[INST] "
            
            output = self.llm(prompt, max_tokens=500, stop=["[INST]", "</s>"])
            return output["choices"][0]["text"].strip()
            
        except Exception as e:
            print(f"Local model error: {e}")
            return None

    def run(self):
        """Boucle principale de l'agent."""
        self.load_model()
        if not self.ready:
            print("AI Agent non prêt.")
            return
        
        print("✓ AI Agent démarré")
        
        while self.running:
            try:
                # Vérifier s'il y a un nouveau input utilisateur
                latest_input = self.shared_data.get('latest_user_input')
                
                if latest_input:
                    timestamp = latest_input.get('timestamp', 0)
                    
                    if timestamp > self.last_processed_timestamp:
                        self.last_processed_timestamp = timestamp
                        content = latest_input.get('content', '')
                        
                        if content:
                            # Générer réponse
                            vision = self.shared_data.get('vision_context', {})
                            schedule = self.shared_data.get('schedule_context', '')
                            
                            response = self.generate_reaction(vision, schedule, content)
                            
                            if response:
                                self.shared_data['latest_ai_response'] = {
                                    'content': response,
                                    'timestamp': time.time()
                                }
            
            except Exception as e:
                print(f"AI Agent error: {e}")
            
            time.sleep(0.1)

    def stop(self):
        self.running = False
