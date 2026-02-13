"""
Bastet AI V2 - Push-to-Talk avec Vosk
STT léger et rapide pour le français.
"""

import os
import json
import time
import queue
import threading
import sounddevice as sd
import numpy as np

# Essayer d'importer Vosk (plus léger) puis fallback sur Whisper
try:
    from vosk import Model, KaldiRecognizer
    VOSK_AVAILABLE = True
except ImportError:
    VOSK_AVAILABLE = False
    print("⚠ Vosk non disponible - pip install vosk")


class PushToTalkRecorder:
    """Enregistreur Push-to-Talk avec Vosk."""
    
    def __init__(self, model_path: str = None):
        self.is_recording = False
        self.audio_queue = queue.Queue()
        self.sample_rate = 16000
        self.model = None
        self.recognizer = None
        
        # Charger le modèle Vosk
        self._load_model(model_path)
    
    def _load_model(self, model_path: str = None):
        if not VOSK_AVAILABLE:
            print("⚠ Vosk non installé")
            return
        
        # Chercher le modèle français
        possible_paths = [
            model_path,
            os.path.join(os.path.dirname(__file__), "..", "models", "vosk-model-small-fr-0.22"),
            os.path.join(os.path.dirname(__file__), "..", "models", "vosk-model-small-fr"),
            os.path.join(os.path.dirname(__file__), "..", "models", "vosk-model-fr"),
            "vosk-model-small-fr-0.22"
        ]
        
        for path in possible_paths:
            if path and os.path.exists(path):
                print(f"Chargement modèle Vosk: {path}")
                try:
                    self.model = Model(path)
                    self.recognizer = KaldiRecognizer(self.model, self.sample_rate)
                    self.recognizer.SetWords(True)
                    print("✓ Vosk chargé (modèle français)")
                    return
                except Exception as e:
                    print(f"⚠ Erreur chargement Vosk: {e}")
        
        print("⚠ Modèle Vosk FR non trouvé. Téléchargez-le depuis:")
        print("   https://alphacephei.com/vosk/models")
        print("   Placez 'vosk-model-small-fr-0.22' dans V2/models/")
    
    def _audio_callback(self, indata, frames, time_info, status):
        """Callback pour l'enregistrement audio."""
        if self.is_recording:
            self.audio_queue.put(bytes(indata))
    
    def start_recording(self):
        """Démarre l'enregistrement."""
        if not self.model:
            return False
        
        self.is_recording = True
        self.audio_queue = queue.Queue()
        
        # Reset le recognizer
        self.recognizer = KaldiRecognizer(self.model, self.sample_rate)
        
        print("🎤 Push-to-Talk: Enregistrement...")
        return True
    
    def stop_recording(self) -> str:
        """Arrête l'enregistrement et retourne le texte transcrit."""
        if not self.is_recording:
            return ""
        
        self.is_recording = False
        print("⏹ Fin enregistrement, transcription...")
        
        if not self.recognizer:
            return ""
        
        # Récupérer tout l'audio de la queue
        audio_data = b''
        while not self.audio_queue.empty():
            audio_data += self.audio_queue.get()
        
        if not audio_data:
            return ""
        
        # Transcrire
        if self.recognizer.AcceptWaveform(audio_data):
            result = json.loads(self.recognizer.Result())
            text = result.get("text", "")
        else:
            result = json.loads(self.recognizer.FinalResult())
            text = result.get("text", "")
        
        if text:
            print(f"[Vosk]: {text}")
        
        return text.strip()
    
    def record_for_duration(self, duration: float = 5.0) -> str:
        """Enregistre pendant une durée fixe."""
        if not self.model:
            return ""
        
        print(f"🎤 Enregistrement ({duration}s)...")
        
        # Reset recognizer
        self.recognizer = KaldiRecognizer(self.model, self.sample_rate)
        
        # Enregistrer
        try:
            recording = sd.rec(
                int(duration * self.sample_rate),
                samplerate=self.sample_rate,
                channels=1,
                dtype=np.int16
            )
            sd.wait()
            
            audio_bytes = recording.tobytes()
            
            # Transcrire
            if self.recognizer.AcceptWaveform(audio_bytes):
                result = json.loads(self.recognizer.Result())
            else:
                result = json.loads(self.recognizer.FinalResult())
            
            text = result.get("text", "").strip()
            
            if text:
                print(f"[Vosk]: {text}")
            
            return text
            
        except Exception as e:
            print(f"Erreur enregistrement: {e}")
            return ""


# Instance globale pour le PTT
ptt_recorder = None


def get_ptt_recorder() -> PushToTalkRecorder:
    global ptt_recorder
    if ptt_recorder is None:
        ptt_recorder = PushToTalkRecorder()
    return ptt_recorder
