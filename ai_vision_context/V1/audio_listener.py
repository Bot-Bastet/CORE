import whisper
import speech_recognition as sr
import threading
import time
import torch
import os
import numpy as np

class AudioListener(threading.Thread):
    def __init__(self, shared_data):
        super().__init__()
        self.shared_data = shared_data
        self.running = True
        self.stt_enabled = False  # Disabled by default, user must click mic button
        self.ready = False
        self.model = None
        self.recognizer = sr.Recognizer()
        # Audio capture settings - optimized for full sentence capture
        self.recognizer.energy_threshold = 400  # Higher threshold to reduce noise
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 1.5  # Wait longer before cutting (was 0.8)
        self.recognizer.phrase_threshold = 0.3
        self.recognizer.non_speaking_duration = 1.0  # Minimum silence to end phrase
        
    def load_model(self):
        print("Loading Whisper STT Model (small) on GPU...")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            # 'small' model: good balance between speed and accuracy (~500MB VRAM)
            self.model = whisper.load_model("small", device=device)
            print(f"Whisper loaded on {device}.")
            self.ready = True
        except Exception as e:
            print(f"Error loading Whisper: {e}")
            self.model = None

    def run(self):
        self.load_model()
        if not self.model:
            print("STT Thread Aborted due to model load failure.")
            return

        print("Audio Listener Thread Started. (Use '!stt off/on' to toggle)")
        
        # Calibrate microphone for ambient noise
        print("Calibrating microphone for ambient noise (2 seconds)...")
        try:
            with sr.Microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=2)
            print(f"Microphone calibrated. Energy threshold: {self.recognizer.energy_threshold}")
        except Exception as e:
            print(f"Microphone calibration failed: {e}")
        
        while self.running:
            if self.shared_data.get('is_speaking', False):
                time.sleep(0.1)
                continue

            if not self.stt_enabled:
                time.sleep(1)
                continue

            try:
                with sr.Microphone() as source:
                    # Listen for audio
                    try:
                        audio = self.recognizer.listen(source, timeout=8, phrase_time_limit=15)
                    except sr.WaitTimeoutError:
                        continue # No speech detected, listen again
                    
                    # If we started speaking during listening, discard
                    if self.shared_data.get('is_speaking', False):
                        continue

                    # Convert to 16kHz numpy array for Whisper
                    try:
                        wav_data = audio.get_raw_data(convert_rate=16000, convert_width=2)
                        audio_np = np.frombuffer(wav_data, dtype=np.int16).astype(np.float32) / 32768.0
                        
                        # Transcribe with French language forced
                        result = self.model.transcribe(
                            audio_np, 
                            fp16=torch.cuda.is_available(), 
                            language="fr",
                            task="transcribe",
                        )
                        text = result["text"].strip()
                        
                        if text:
                            # Final check
                            if self.shared_data.get('is_speaking', False): 
                                continue
                                
                            print(f"\n[Heard (Whisper)]: {text}")
                            # Update shared data
                            self.shared_data['latest_user_input'] = {
                                'type': 'text_from_audio',
                                'content': text,
                                'timestamp': time.time()
                            }
                    except Exception as trans_e:
                        print(f"Transcription Error: {trans_e}")
            
            except Exception as e:
                # print(f"Audio Error: {e}") 
                time.sleep(1)

    def stop(self):
        self.running = False

    def toggle(self, state: bool):
        self.stt_enabled = state
        print(f"STT {'Enabled' if state else 'Disabled'}")
