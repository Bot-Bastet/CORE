"""
Bastet AI V2 - STT Handler avec Vosk
Écoute continue légère pour le français.
"""

import threading
import time
import os
import json
import queue
import sounddevice as sd
import numpy as np

try:
    from vosk import Model, KaldiRecognizer
    VOSK_AVAILABLE = True
except ImportError:
    VOSK_AVAILABLE = False
    print("⚠ Vosk non disponible - pip install vosk")


class STTHandler(threading.Thread):
    """Écoute continue avec Vosk (léger et rapide)."""
    
    def __init__(self, config: dict, shared_data: dict = None):
        super().__init__(daemon=True)
        self.config = config
        self.shared_data = shared_data if shared_data is not None else {}
        self.running = True
        self.enabled = False
        self.ready = False
        
        self.sample_rate = 16000
        self.model = None
        self.recognizer = None
        self.audio_queue = queue.Queue()
        
        # Expose state
        self.shared_data['stt_enabled'] = False
    
    def _load_model(self):
        if not VOSK_AVAILABLE:
            print("⚠ Vosk non installé")
            return False
        
        # Chercher le modèle français
        possible_paths = [
            os.path.join(os.path.dirname(__file__), "..", "models", "vosk-model-small-fr-0.22"),
            os.path.join(os.path.dirname(__file__), "..", "models", "vosk-model-small-fr"),
            os.path.join(os.path.dirname(__file__), "..", "models", "vosk-model-fr"),
        ]
        
        for path in possible_paths:
            if path and os.path.exists(path):
                try:
                    print(f"Chargement modèle Vosk STT: {os.path.basename(path)}")
                    self.model = Model(path)
                    self.recognizer = KaldiRecognizer(self.model, self.sample_rate)
                    self.recognizer.SetWords(False)
                    print("✓ Vosk STT chargé")
                    return True
                except Exception as e:
                    print(f"⚠ Erreur chargement Vosk: {e}")
        
        print("⚠ Modèle Vosk FR non trouvé dans V2/models/")
        return False
    
    def toggle(self, enabled: bool):
        self.enabled = enabled
        self.shared_data['stt_enabled'] = enabled
        
        if enabled:
            print("STT Continu Activé")
        else:
            print("STT Continu Désactivé")
    
    def _audio_callback(self, indata, frames, time_info, status):
        """Callback pour l'enregistrement audio."""
        if self.enabled:
            self.audio_queue.put(bytes(indata))
    
    def run(self):
        # Charger le modèle
        if not self._load_model():
            print("⚠ STT Vosk non disponible")
            return
        
        self.ready = True
        print("✓ STT Continu prêt (Vosk)")
        
        # Démarrer le stream audio
        try:
            with sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=4000,
                dtype=np.int16,
                channels=1,
                callback=self._audio_callback
            ):
                while self.running:
                    if not self.enabled:
                        time.sleep(0.1)
                        continue
                    
                    # Traiter l'audio en queue
                    try:
                        data = self.audio_queue.get(timeout=0.5)
                        
                        if self.recognizer.AcceptWaveform(data):
                            result = json.loads(self.recognizer.Result())
                            text = result.get("text", "").strip()
                            
                            if text and len(text) > 2:
                                print(f"[Entendu]: {text}")
                                
                                self.shared_data['latest_user_input'] = {
                                    'type': 'text_from_audio',
                                    'content': text,
                                    'timestamp': time.time()
                                }
                    except queue.Empty:
                        pass
                    except Exception as e:
                        print(f"STT erreur: {e}")
                        time.sleep(0.5)
                        
        except Exception as e:
            print(f"⚠ Erreur stream audio STT: {e}")
    
    def stop(self):
        self.running = False
        self.enabled = False
