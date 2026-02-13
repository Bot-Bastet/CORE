"""
Bastet AI V2 - AI Agent Module
Supporte LM Studio (distant) ou modèle local via llama-cpp.
"""

import threading
import time
import os
import requests
import json
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
        self.local_model_path = config.get("model_path", "")
        
        # Local model (si utilisé)
        self.llm = None
        
        # Historique conversation
        self.conversation_history = []
        self.max_history = 20
        
        # Timestamp pour éviter doublons
        self.last_processed_timestamp = 0

    def load_model(self):
        """Charge le modèle local si nécessaire."""
        if self.provider == "lmstudio":
            print(f"✓ AI Agent configuré pour LM Studio: {self.lm_studio_url}")
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

    def get_system_prompt(self):
        """Retourne le prompt système de Bastet."""
        now = datetime.now()
        date_str = now.strftime("%A %d %B %Y")
        time_str = now.strftime("%H:%M")
        
        return f"""Tu es Bastet, une IA d'assistance.

DATE/HEURE: {date_str}, {time_str}

RÈGLES STRICTES:
- Réponds UNIQUEMENT à ce qui est demandé
- Sois TRÈS CONCISE (1-2 phrases max)
- Pas de blabla, pas de formules de politesse excessives
- Si on demande "demain", ne parle QUE de demain
- Si on demande un cours spécifique, ne cite QUE celui-là
- Utilise un langage naturel et décontracté

Tu peux voir qui est devant toi et accéder à leur agenda."""

    def generate_reaction(self, vision_data: dict, schedule_context: str, user_input: str = None):
        """Génère une réponse basée sur le contexte."""
        
        # Construire le contexte
        context_parts = []
        
        if vision_data:
            faces = vision_data.get("faces_names", [])
            objects = vision_data.get("objects", [])
            if faces:
                context_parts.append(f"Personnes visibles: {', '.join(faces)}")
            if objects:
                context_parts.append(f"Objets détectés: {', '.join(objects[:5])}")
        
        if schedule_context:
            context_parts.append(f"Agenda: {schedule_context}")
        
        context = " | ".join(context_parts) if context_parts else "Aucun contexte spécifique"
        
        # Messages
        messages = [
            {"role": "system", "content": self.get_system_prompt()},
            {"role": "system", "content": f"Contexte actuel: {context}"}
        ]
        
        # Ajouter historique
        messages.extend(self.conversation_history[-self.max_history:])
        
        # Ajouter input utilisateur
        if user_input:
            messages.append({"role": "user", "content": user_input})
            self.conversation_history.append({"role": "user", "content": user_input})
        
        # Générer réponse
        if self.provider == "lmstudio":
            response = self._query_lm_studio(messages)
        else:
            response = self._query_local(messages)
        
        if response:
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
