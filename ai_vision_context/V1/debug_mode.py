
import sys
import os
import time
import threading
import queue
import json
import logging

# Ensure we can import from local directory
sys.path.append(os.getcwd())

# Suppress other logs if needed
logging.basicConfig(level=logging.ERROR)

from ai_agent import AIAgent
from myges_integration import MyGesIntegration

def main():
    print("=========================================")
    print("   MODE DEBUG 'CHEAT' - BASTET AI")
    print("=========================================")
    print("STATUS: Salles STT/TTS DESACTIVÉES.")
    print("STATUS: Web UI DESACTIVÉE.")
    print("STATUS: Vision Physique DESACTIVÉE (Simulation active).")
    
    # 1. Initialize Shared Data
    shared_data = {
        'vision_context': None,    
        'schedule_context': "En attente de connexion...", 
        'schedule_week': None,
        'ai_context': None,        
        'latest_user_input': None,  
        'is_speaking': False, # Mocked
        'stream_queue': queue.Queue()
    }

    # 2. Configura AI Provider
    ai_provider = "local"
    # Default to LM Studio if user hinted so, but let's read config first
    lm_studio_url = "http://localhost:1234/v1"
    
    if os.path.exists("system_config.json"):
        try:
            with open("system_config.json", "r") as f:
                config_data = json.load(f)
                ai_provider = config_data.get("ai_provider", "local")
                lm_studio_url = config_data.get("lm_studio_url", "http://localhost:1234/v1")
                print(f"[CONFIG] Chargé: Provider={ai_provider}, URL={lm_studio_url}")
        except:
            print("[CONFIG] Erreur lecture configuration. Default local.")

    model_config = {
        "name": "Mistral Debug",
        "repo_id": "MaziyarPanahi/Mistral-7B-Instruct-v0.3-GGUF", 
        "filename": "Mistral-7B-Instruct-v0.3.Q5_K_M.gguf",
        "provider": ai_provider,
        "api_url": lm_studio_url
    }

    # 3. Start AI Agent
    print(f"\n[SYSTEM] Démarrage AI Agent ({ai_provider})...")
    ai_agent = AIAgent(model_config=model_config, shared_data=shared_data)
    ai_agent.daemon = True
    ai_agent.start()
    
    # Ensure model is verified/loaded
    print("[SYSTEM] Attente disponibilité modèle...")
    while not ai_agent.ready and ai_agent.running:
        time.sleep(1)
    
    if not ai_agent.running:
        print("[FATAL] L'agent AI n'a pas pu démarrer.")
        return

    print("\n[SYSTEM] Modèle PRÊT.")

    # 4. MyGes Integration Wrapper
    myges = MyGesIntegration()
    
    def check_and_connect_teano():
        # Logic to connect only if needed
        if not myges.access_token:
            print("\n[SYSTEM] Tentative connexion MyGes (Teano détecté)...")
            try:
                # Assuming credentials are saved or prompts will handle it
                # Note: getpass might be tricky in some envs but standard terminal should work
                if myges.login():
                    shared_data['schedule_context'] = myges.get_context_string()
                    shared_data['schedule_week'] = myges.get_week_summary()
                    print("[SYSTEM] MyGes Connecté avec succès.")
                    # print(f"[INFO AGENDA] {shared_data['schedule_context']}")
                else:
                    print("[SYSTEM] Échec connexion MyGes check indentifiants.")
                    shared_data['schedule_context'] = "Erreur de connexion (Identifiants invalides?)."
            except Exception as e:
                print(f"[SYSTEM] Erreur MyGes: {e}")

    # 5. Queue Drainer (to prevent memory leak from stream_queue)
    def drain_queue():
        while True:
            try:
                _ = shared_data['stream_queue'].get(timeout=1)
            except queue.Empty:
                pass
    
    threading.Thread(target=drain_queue, daemon=True).start()

    # Helper function defined INSIDE main to access shared_data
    def update_vision_data(count, names, objects):
        shared_data['vision_context'] = {
            "faces_count": count,
            "faces_names": names,
            "objects": objects
        }

    # 6. Command Loop
    print("\n-----------------------------------------")
    print("COMMANDES DE SIMULATION:")
    print("  /nob     -> Simuler: Personne (0 visages)")
    print("  /unk     -> Simuler: Inconnu (1 visage)")
    print("  /tea     -> Simuler: Teano (Déclenche Agenda)")
    print("  /refr    -> Rafraichir Agenda (Force)")
    print("  /quit    -> Quitter")
    print("-----------------------------------------")
    print("Tapez votre message pour parler au LLM.\n")

    # Set default vision to nobody
    update_vision_data(0, [], [])

    while True:
        try:
            print("\n> ", end="")
            u_input = input().strip()
            
            if not u_input:
                continue

            if u_input.lower() in ["/quit", "exit"]:
                break
                
            if u_input.lower() == "/nob":
                update_vision_data(0, [], [])
                print("[VISION SIMULÉE] Personne.")
                continue
                
            if u_input.lower() == "/unk":
                update_vision_data(1, ["Inconnu"], [])
                print("[VISION SIMULÉE] Inconnu présent.")
                continue
                
            if u_input.lower() == "/tea":
                update_vision_data(1, ["Teano"], [])
                print("[VISION SIMULÉE] Teano présent.")
                check_and_connect_teano()
                continue
            
            if u_input.lower() == "/refr":
                 if myges.login():
                     shared_data['schedule_context'] = myges.get_context_string()
                     shared_data['schedule_week'] = myges.get_week_summary()
                     print("[SYSTEM] Agenda mis à jour.")
                 continue

            # Send to AI
            shared_data['latest_user_input'] = {
                'type': 'text_console',
                'content': u_input,
                'timestamp': time.time()
            }
            
            # Allow AI to process and print
            time.sleep(0.5) 

        except KeyboardInterrupt:
            break

    ai_agent.stop()
    print("\n[SYSTEM] Arrêt du système.")

if __name__ == "__main__":
    main()
