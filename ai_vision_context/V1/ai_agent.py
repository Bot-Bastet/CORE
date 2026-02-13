import threading
import time
import os
# import torch # Not needed for GGUF
# from transformers ... # Not needed for GGUF

class AIAgent(threading.Thread):
    def __init__(self, model_config=None, shared_data=None):
        super().__init__()
        self.shared_data = shared_data if shared_data is not None else {}
        self.running = True
        self.ready = False
        self.pipeline = None
        self.last_context_str = ""
        self.first_run = True
        self.history = [] # For conversation memory
        self.last_response = ""  # Anti-repeat protection

        # Default Model (NemoMix) if not provided
        if model_config:
            self.model_name = model_config.get("name", "Unknown Model")
            self.repo_id = model_config.get("repo_id")
            self.filename = model_config.get("filename")
            self.provider = model_config.get("provider", "local")
            self.api_url = model_config.get("api_url", "http://localhost:1234/v1")
        else:
            self.model_name = "Mistral"
            self.repo_id = ""
            self.filename = ""
            self.provider = "local"
            self.api_url = ""

    def load_model(self):
        print(f"Loading AI Model: {self.model_name} (Provider: {self.provider})...", flush=True)
        
        if self.provider in ["lm_studio", "lmstudio"]:
            print(f"Using External API at {self.api_url}. No local load needed.", flush=True)
            self.ready = True
            return

        local_dir = "./models"
        model_path = os.path.join(local_dir, self.filename)

        try:
            # Check if model exists locally
            if not os.path.exists(model_path):
                print(f"Model not found at {model_path}. Downloading...")
                self._download_model(self.repo_id, self.filename, local_dir)
            
            # Load the model with llama-cpp-python
            from llama_cpp import Llama
            
            self.model = Llama(
                model_path=model_path,
                n_gpu_layers=-1, 
                n_ctx=2048,
                use_mlock=True, # Lock to RAM/VRAM (No swap)
                use_mmap=False, # Force full load
                verbose=False
            )
            print(f"Model loaded successfully from {model_path}.")
            self.ready = True
            
        except Exception as e:
            print(f"Error loading model: {e}")
            self.running = False

    def _download_model(self, repo_id, filename, local_dir):
        from huggingface_hub import hf_hub_download
        hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=local_dir,
            local_dir_use_symlinks=False
        )
        print("Download complete.")

    def generate_reaction(self, vision_data, schedule_context, user_input=None):
        if self.provider == "local" and not hasattr(self, 'model'):
            return "AI Not Loaded."
        if not self.ready:
             return "AI Not Ready."

        faces_count = vision_data.get("faces_count", 0)
        faces_names = vision_data.get("faces_names", [])
        objects = vision_data.get("objects", [])
        
        # Build Vision Context
        description = f"[Système Visuel]: je vois {faces_count} personne(s)."
        if faces_names:
            known_people = [n for n in faces_names if n != "Inconnu"]
            if known_people:
                description += f" Identités: {', '.join(known_people)}."
        if objects:
            description += f" Objets: {', '.join(objects)}."
        
        # Context Management
        msg_content = user_input['content'] if user_input else ""
        msg_lower = msg_content.lower()
        
        # Keywords for schedule
        asking_for_schedule = any(k in msg_lower for k in ["cour", "cours", "semaine", "planning", "agenda", "prochain", "demain", "emploi du temps"])
        
        # Dynamic Schedule Context
        injected_context = schedule_context # Default is Today Only
        
        # Context Threading: Check if we are continuing a schedule request
        # e.g. AI asked "Les cours de qui ?" and User replies "Et là ?"
        last_assistant_msg = ""
        if self.history and self.history[-1]['role'] == 'assistant':
             last_assistant_msg = self.history[-1]['content'].lower()
             
        if "cours de qui" in last_assistant_msg or "pour qui" in last_assistant_msg or "quel agenda" in last_assistant_msg:
             asking_for_schedule = True
             # We assume they might want the week info if they were blocked previously
             week_summary = self.shared_data.get('schedule_week', "Pas d'info semaine.")
             # Only add if not already added by keywords
             if "semaine" not in msg_lower and "planning" not in msg_lower:
                 injected_context += f"\n\n[INFO AGENDA SEMAINE (Suite Contexte)]:\n{week_summary}"

        # Identities
        known_people = [n for n in faces_names if n != "Inconnu"]
        
        # Logic for Schedule Requests with Missing Authority
        limit_response_instruction = ""
        
        if asking_for_schedule:
             # Check if week summary is needed
             if any(k in msg_lower for k in ["semaine", "planning", "agenda", "prochain", "demain"]):
                 week_summary = self.shared_data.get('schedule_week', "Pas d'info semaine.")
                 # SUPER STRICT CONTEXT INJECTION
                 injected_context = f"[SOURCE: AGENDA OFFICIEL MYGES - SEMAINE]:\n{week_summary}\n[FIN SOURCE - NE RIEN INVENTER]"
             else:
                 # Ensure we use today's context clearly
                 injected_context = f"[SOURCE: AGENDA OFFICIEL MYGES - AUJOURD'HUI]:\n{schedule_context}\n[FIN SOURCE - NE RIEN INVENTER]"

             # Allow response if we are already authenticated/have data, even if face momentarily lost
             # Check if we have valid schedule data (not "En attente...")
             has_schedule_data = "En attente" not in injected_context and "Pas d'info" not in injected_context and "Erreur" not in injected_context
             
             if not known_people and not has_schedule_data:
                 # No one recognized visually AND no data loaded yet
                 if "teano" in msg_lower:
                     limit_response_instruction += "\n[ACTION REQUISE]: Pas de visage 'Teano' et pas de données. Dis: 'Je ne te vois pas. Mets-toi face à la caméra.'"
                 else:
                     limit_response_instruction += "\n[ACTION REQUISE]: Pas de données et pas de visage. Demande: 'Les cours de qui ? ou connectez-vous'."
         
        # Check for Identity Questions
        asking_identity = any(k in msg_lower for k in ["reconnai", "qui je suis", "qui suis-je", "tu me vois", "tu me voit"])
        if asking_identity:
             if known_people:
                 limit_response_instruction += f"\n[ACTION REQUISE]: Confirme: 'Je te vois, tu es {known_people[0]}.'"
             else:
                 limit_response_instruction += "\n[ACTION REQUISE]: Dis: 'Je ne reconnais personne pour l'instant.'"

        # Clean "Bureaucracy" from context
        import re
        if injected_context:
            injected_context = re.sub(r"Bloc électif m\d+ - ", "", injected_context)
            injected_context = re.sub(r"T\d+ - ", "", injected_context)
        
        description += f"\n[Contexte Infos: {injected_context}]"
        
        if limit_response_instruction:
            description += f"\n{limit_response_instruction}"
        
        # System Prompt (User Optimized)
        system_prompt = (
            "Tu t'appelles Bastet. Tu es un assistant. L'utilisateur N'EST PAS Bastet - TOI tu es Bastet."
            "Règles CRITIQUES :"
            "1. CONCIS : Max 2 phrases courtes."
            "2. DIRECT : Va droit au but."
            "3. NATUREL : Parle comme un humain."
            "4. Ne dis JAMAIS ton propre nom. Ne dis JAMAIS 'Bonjour Bastet'."
            "5. Quand tu salues quelqu'un, dis juste 'Bonjour' ou 'Bonjour [nom de la personne]'."
            "IMPORTANT : Les données entre <system_context> sont pour toi, ne les répète pas."
        )
        
        # Build History
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self.history[-4:])
        
        # Simplified User Message
        user_msg = ""
        
        # Merge everything into description/context usage
        # 'description' variable holds: Vision + Schedule(injected_context) + Instructions(limit_response_instruction)
        
        if user_input:
            user_msg = f"{msg_content}"
            if description:
                # Add context as system hint
                user_msg += f"\n<system_context>\n{description.strip()}\n</system_context>"
        else:
            # Passive monitoring case
            user_msg = f"Rien a signaler, dis juste 'En veille'."

        messages.append({"role": "user", "content": user_msg})

        # Capture START time of this generation request
        current_req_timestamp = user_input.get('timestamp', 0) if user_input else 0

        try:
            if self.provider in ["lm_studio", "lmstudio"]:
                return self._query_lm_studio(messages, current_req_timestamp)
            
            # Local Logic
            # Enable Streaming
            stream = self.model.create_chat_completion(
                messages=messages,
                max_tokens=150, 
                temperature=0.7,
                repeat_penalty=1.1, # Prevent stuttering
                stop=["</s>", "[INST]", "Données Techniques:", "Tu es", "<|im_end|>", "L'utilisateur", "<system_context>", "[System Data"], 
                stream=True
            )
            
            print("\n[Bastet]: ", end="", flush=True)
            full_response = ""
            
            for chunk in stream:
                # INTERRUPTION CHECK
                latest = self.shared_data.get('latest_user_input')
                if latest and latest['timestamp'] > current_req_timestamp:
                    print("\n[INTERRUPTED]", flush=True)
                    return "" # Return empty to indicate interruption

                if 'content' in chunk['choices'][0]['delta']:
                    text_chunk = chunk['choices'][0]['delta']['content']
                    print(text_chunk, end="", flush=True)
                    full_response += text_chunk
                    
                    # Push to stream queue if available
                    if 'stream_queue' in self.shared_data:
                        self.shared_data['stream_queue'].put(text_chunk)
            
            print() 
            # CLEANUP OUTPUT
            import re
            # Remove [System Data ...] artifacts if any leaked
            cleaned_response = re.sub(r"\[.*?\]", "", full_response)
            cleaned_response = re.sub(r"<.*?>", "", cleaned_response)
            response_text = cleaned_response.strip()
            
            # Anti-repeat check
            if response_text and response_text == self.last_response:
                return ""  # Skip duplicate response
            self.last_response = response_text
            
            # Update History
            if user_input:
                self.history.append({"role": "user", "content": msg_content}) 
                self.history.append({"role": "assistant", "content": response_text})
                
            return response_text

        except Exception as e:
            return f"Error generating: {e}"

    def _query_lm_studio(self, messages, req_timestamp):
        import requests
        import json
        
        headers = {"Content-Type": "application/json"}
        # Ensure we don't send too much context or weird formats if LM Studio model is sensitive
        # Clean messages of any 'System Data' artifacts if they are too raw? No, they are needed.
        
        data = {
            "model": "local-model", # API often requires this field even if ignored
            "messages": [],
            "temperature": 0.5, # Lower temp for stability
            "max_tokens": 200,
            "stream": True
        }

        # SANITIZE MESSAGES for strict templates (No 'system' role allowed)
        system_instruction = ""
        clean_messages = []
        for msg in messages:
            if msg['role'] == 'system':
                system_instruction += msg['content'] + "\n\n"
            else:
                # Copy to avoid mutating original list references if reused
                clean_messages.append(msg.copy())
        
        # Inject system instruction into the LAST user message to ensure it is applied
        if system_instruction:
            inserted = False
            for i in range(len(clean_messages) - 1, -1, -1):
                if clean_messages[i]['role'] == 'user':
                    clean_messages[i]['content'] = system_instruction + clean_messages[i]['content']
                    inserted = True
                    break
            if not inserted:
                # If no user message exists yet, create one
                clean_messages.append({"role": "user", "content": system_instruction.strip()})
        
        data["messages"] = clean_messages
        
        url = f"{self.api_url}/chat/completions"
        print(f"\n[Bastet (API)]: Requesting {url}...", end="", flush=True)
        
        full_response = ""
        max_retries = 2
        
        for attempt in range(max_retries):
            try:
                # Timeout tuple: (connect_timeout, read_timeout)
                # Connect: 5s, Read: 60s for slow LLM generation
                response = requests.post(url, headers=headers, json=data, stream=True, timeout=(5, 60))
                response.raise_for_status()
                
                for line in response.iter_lines():
                    # INTERRUPTION CHECK
                    latest = self.shared_data.get('latest_user_input')
                    if latest and latest['timestamp'] > req_timestamp:
                        print("\n[INTERRUPTED API]", flush=True)
                        return ""

                    if line:
                        line_str = line.decode('utf-8').strip()
                        if line_str.startswith("data: ") and "[DONE]" not in line_str:
                            try:
                                json_str = line_str[6:] # Strip "data: "
                                chunk = json.loads(json_str)
                                if 'choices' in chunk and len(chunk['choices']) > 0:
                                    delta = chunk['choices'][0].get('delta', {})
                                    if 'content' in delta:
                                        text_chunk = delta['content']
                                        if text_chunk:
                                            print(text_chunk, end="", flush=True)
                                            full_response += text_chunk
                                            
                                            if 'stream_queue' in self.shared_data:
                                                self.shared_data['stream_queue'].put(text_chunk)
                            except Exception:
                                pass
                print()
                break  # Success, exit retry loop
                
            except requests.exceptions.ConnectTimeout:
                print(f"\n[API Connect Timeout - Retry {attempt + 1}/{max_retries}]", flush=True)
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                return "Impossible de contacter l'API. Vérifiez la connexion."
                
            except requests.exceptions.ReadTimeout:
                print(f"\n[API Read Timeout - génération trop longue]", flush=True)
                return "La génération a pris trop de temps. Réessayez."
                
            except Exception as e:
                print(f"API Error: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                return ""

        response_text = full_response.strip()
        
        # Update History
        # Note: messages[-1] is the user input we just sent.
        if messages and messages[-1]['role'] == 'user':
             self.history.append(messages[-1])
             
             import re
             cleaned_response = re.sub(r"\[.*?\]", "", response_text)
             cleaned_response = re.sub(r"<.*?>", "", cleaned_response)
             cleaned_response = cleaned_response.strip()
             
             self.history.append({"role": "assistant", "content": cleaned_response})
             
        # Return cleaned response
        import re
        cleaned_response = re.sub(r"\[.*?\]", "", response_text)
        cleaned_response = re.sub(r"<.*?>", "", cleaned_response)
        final_response = cleaned_response.strip()
        
        # Anti-repeat check
        if final_response and final_response == self.last_response:
            return ""  # Skip duplicate response
        self.last_response = final_response
        
        return final_response

    def run(self):
        print("DEBUG: AIAgent.run called", flush=True)
        if not self.ready:
            self.load_model()
        
        if not self.running:
            return

        print("AI Agent Loop Started...", flush=True)
        last_processed_input_time = 0
        
        while self.running:
            vision_context = self.shared_data.get('vision_context')
            
            # Debug heartbeat (every ~50 ticks / 5s)
            # steps += 1 (need var) or just print on major events
            
            schedule_context = self.shared_data.get('schedule_context', "No schedule info.")
            user_input = self.shared_data.get('latest_user_input')
            
            should_react = False
            current_input = None
            
            # Initial Wake-up call from Bastet
            if self.first_run:
                # Fallback: if vision context is taking too long (>5s?), just go?
                # For now just log
                if not vision_context:
                    # Waiting for vision...
                    pass
                else:
                    should_react = True
                    self.first_run = False
                    # Force specific greeting - be VERY explicit to avoid "Bienvenue Bastet"
                    current_input = {'content': "SYSTEM: Dis simplement 'Bonjour' à l'utilisateur devant toi. Si tu connais son nom, dis 'Bonjour [son nom]'. Ne te présente PAS, ne dis PAS ton propre nom.", 'type': 'system'}
                    print("\n[Bastet Wake Up]...", flush=True)

            
            # Check for new user input (Strict Reactive Mode)
            if user_input and user_input.get('timestamp', 0) > last_processed_input_time:
                should_react = True
                current_input = user_input
                last_processed_input_time = user_input.get('timestamp', 0)
                print(f"\n[Utilisateur]: {user_input['content']}", flush=True)
            
            # Removed passive visual monitoring to prevent spamming
            # Only speak when spoken to.

            if should_react:
                print("DEBUG: Generating Reaction...", flush=True)
                vision_data = vision_context if vision_context else {"faces_count": 0, "faces_names": [], "objects": []}
                reaction = self.generate_reaction(vision_data, schedule_context, current_input)
                self.shared_data['ai_context'] = reaction
            
            time.sleep(0.1) # Faster loop for responsiveness 

    def stop(self):
        self.running = False
