import time
import sys
import threading
# Imports moved inside main to allow for dependency checking
# from vision import VisionSystem
# from ai_agent import AIAgent

def main():
    # Check dependencies first
    try:
        import cv2
        import ultralytics
        import mediapipe
        import torch
        import transformers
    except ImportError as e:
        print(f"\nCRITICAL ERROR: Missing dependency: {e.name}")
        print("Please run: pip install -r requirements.txt")
        sys.exit(1)

    from vision import VisionSystem
    from ai_agent import AIAgent
    from myges_integration import MyGesIntegration
    from audio_listener import AudioListener
    from tts_module import TTSHandler

    print("Initializing modules...")
    
    # Shared Data Context
    shared_data = {
        'vision_context': None,    # { 'faces_count': 0, 'objects': [], ... }
        'schedule_context': None,  # String describing current class status
        'ai_context': None,        # Last AI response
        'latest_user_input': None,  # { 'type': 'audio'/'text', 'content': '...', 'timestamp': ... }
        'is_speaking': False        # Flag to prevent self-hearing
    }

    # Initialize TTS
    tts = TTSHandler(shared_data)
    
    myges = MyGesIntegration()
    shared_data['myges_instance'] = myges 
    
    # Login
    if not myges.login():
        print("Continuing without MyGes (Agenda will be empty).")

    # Initial Contexts
    shared_data['schedule_context'] = myges.get_context_string()
    shared_data['schedule_week'] = myges.get_week_summary()

    # --- Model Selection ---
    print("\n--- Choose AI Model ---")
    models = [
        {
            "name": "NemoMix-Unleashed-12B (Default - Balanced)",
            "repo_id": "bartowski/NemoMix-Unleashed-12B-GGUF",
            "filename": "NemoMix-Unleashed-12B-Q5_K_M.gguf"
        },
        {
            "name": "Dolphin 2.9.3 Mistral 7B (Uncensored/Coding)",
            "repo_id": "macadeliccc/dolphin-2.9.3-mistral-7B-32K-GGUF",
            "filename": "dolphin_2.9.3.q5_k_m.gguf"
        },
        {
            "name": "Qwen 2.5 7B Instruct (Strong Reasoning)",
            "repo_id": "Qwen/Qwen2.5-7B-Instruct-GGUF",
            "filename": "qwen2.5-7b-instruct-q5_k_m.gguf"
        },
        {
            "name": "Mistral 7B v0.3 Instruct (Classic)",
            "repo_id": "MaziyarPanahi/Mistral-7B-Instruct-v0.3-GGUF", 
            "filename": "Mistral-7B-Instruct-v0.3.Q5_K_M.gguf"
        }
    ]

    for idx, m in enumerate(models):
        print(f"{idx+1}. {m['name']}")

    choice = input("\nSelect Model (1-4) [Default 1]: ").strip()
    selected_model_config = models[0] # Default
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(models):
            selected_model_config = models[idx]

    print(f"Selected: {selected_model_config['name']}")

    # Initialize Threads
    vision_thread = VisionSystem(shared_data=shared_data)
    ai_thread = AIAgent(model_config=selected_model_config, shared_data=shared_data)
    audio_thread = AudioListener(shared_data=shared_data)


    # MyGes Login handled above

    try:
        # Set daemon to ensure they crash/exit when main thread dies
        vision_thread.daemon = True
        ai_thread.daemon = True
        audio_thread.daemon = True
        
        # Start Threads
        vision_thread.start()
        audio_thread.start()
        ai_thread.start()
        
        print("\nSystem running. Commands:")
        print("  - Type anything to chat with AI")
        print("  - !stt on / !stt off : Toggle Speech-to-Text")
        print("  - !context : Show current AI context")
        print("  - Ctrl+C to Stop")

        # To handle TTS without blocking on input(), we need a trick.
        # But 'input()' is fundamentally blocking. 
        # A simple workaround for this CLI tool:
        # We start a separate thread just for handling User Text Input so the Main Thread 
        # can stay free to check for TTS triggers.
        
        def input_loop():
            import msvcrt
            input_buffer = []
            while True:
                if msvcrt.kbhit():
                    try:
                        # getwch returns unicode char, getch returns bytes
                        char = msvcrt.getwch() 
                    except:
                        continue
                        
                    # ENTER key
                    if char == '\r' or char == '\n':
                        print() # Move to next line
                        
                        # 1. IMMEDIATE STOP if speaking
                        if shared_data.get('is_speaking', False):
                            tts.stop()
                            print(" [STOP AUDIO]")
                            # reset buffer to avoid sending half-typed commands
                            input_buffer = []
                            continue
                            
                        # 2. Process Command
                        user_text = "".join(input_buffer).strip()
                        input_buffer = []
                        
                        if user_text:
                            if user_text.lower() == "!context":
                                print(f"Vision: {shared_data.get('vision_context')}")
                                print(f"Schedule: {shared_data.get('schedule_context')}")
                            elif user_text.lower().startswith("!stt"):
                                if "on" in user_text.lower():
                                    audio_thread.stt_enabled = True
                                    print("STT ON")
                                elif "off" in user_text.lower():
                                    audio_thread.stt_enabled = False
                                    print("STT OFF")
                            else:
                                shared_data['latest_user_input'] = {
                                    'type': 'text',
                                    'content': user_text,
                                    'timestamp': time.time()
                                }
                    
                    # BACKSPACE
                    elif char == '\x08':
                        if input_buffer:
                            input_buffer.pop()
                            # Erase character from terminal
                            sys.stdout.write('\b \b')
                            sys.stdout.flush()
                            
                    # Normal Char
                    else:
                        input_buffer.append(char)
                        sys.stdout.write(char)
                        sys.stdout.flush()
                
                time.sleep(0.01)
        
        input_thread = threading.Thread(target=input_loop)
        input_thread.daemon = True
        input_thread.start()
        
        # Wait for all systems to be ready
        print("Waiting for models to load...")
        while not (vision_thread.ready and audio_thread.ready and ai_thread.ready):
            time.sleep(0.5)
        
        print("\n--- SYSTEM FULLY OPERATIONAL ---")
        try:
            import winsound
            winsound.Beep(1000, 200) # Simple beep
        except:
            pass
        print("Système prêt. Bastet va prendre la parole.")
        # tts.speak("Système entièrement initialisé. Je suis prête.") # Let Bastet speak instead

        
        print("> ", end="", flush=True)

        last_spoken = ""
        
        while True:
            # Main Loop: Monitor AI output for TTS
            current_ai = shared_data.get('ai_context')
            if current_ai and current_ai != last_spoken:
                last_spoken = current_ai
                # Speak it!
                tts.speak(current_ai)
            
            # Periodically update schedule
            # shared_data['schedule_context'] = myges.get_context_string() # Done inside loop if we wanted, or on demand
            
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nStopping system...")
        # Threads are daemons, will die on exit
        sys.exit(0)

if __name__ == "__main__":
    main()
