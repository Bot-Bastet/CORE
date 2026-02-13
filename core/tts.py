"""
Bastet AI V2 - Text-to-Speech Module
Utilise edge_tts avec voix naturelle française.
DÉSACTIVÉ par défaut.
"""

import edge_tts
import pygame
import threading
import asyncio
import os
import time


class TTSHandler:
    def __init__(self, config: dict, shared_data: dict = None):
        self.config = config
        self.shared_data = shared_data if shared_data is not None else {}
        self.lock = threading.Lock()
        self.output_file = "temp_tts_output.mp3"
        self.stop_event = threading.Event()
        
        # DÉSACTIVÉ par défaut
        self.enabled = config.get("tts_enabled", False)
        self.voice = config.get("tts_voice", "fr-FR-DeniseNeural")
        
        # Expose l'état dans shared_data
        self.shared_data['tts_enabled'] = self.enabled
        
        # Init pygame mixer
        try:
            if pygame.mixer.get_init():
                pygame.mixer.quit()
            pygame.mixer.init(frequency=24000, buffer=4096)
            print(f"✓ TTS Mixer initialisé. Voix: {self.voice}")
        except Exception as e:
            print(f"⚠ TTS Mixer init failed: {e}")
        
        # Fallback pyttsx3
        self.fallback_engine = None
        try:
            import pyttsx3
            self.fallback_engine = pyttsx3.init()
            voices = self.fallback_engine.getProperty('voices')
            for voice in voices:
                if 'french' in voice.name.lower() or 'fr' in voice.id.lower():
                    self.fallback_engine.setProperty('voice', voice.id)
                    break
            self.fallback_engine.setProperty('rate', 180)
        except Exception as e:
            print(f"⚠ pyttsx3 fallback non disponible: {e}")

    def toggle(self, state: bool):
        """Active ou désactive le TTS."""
        self.enabled = state
        self.shared_data['tts_enabled'] = state
        print(f"TTS {'Activé' if state else 'Désactivé'}")
        
        # Si désactivé, stopper la lecture en cours
        if not state:
            self.stop()
    
    def is_enabled(self) -> bool:
        return self.enabled

    def speak(self, text: str):
        """Parle le texte (non-bloquant). Ne fait rien si TTS désactivé."""
        if not self.enabled:
            return
        
        if not text or len(text.strip()) < 2:
            return
        
        self.stop()
        self.stop_event.clear()
        
        thread = threading.Thread(target=self._speak_thread, args=(text,), daemon=True)
        thread.start()

    def _speak_thread(self, text: str):
        with self.lock:
            if self.stop_event.is_set() or not self.enabled:
                return
            
            try:
                self.shared_data['is_speaking'] = True
                
                # Essayer edge_tts d'abord
                if self._try_edge_tts(text):
                    if not self.stop_event.is_set() and self.enabled:
                        self._play_audio()
                elif self.fallback_engine:
                    self._use_pyttsx3(text)
                    
            except Exception as e:
                print(f"TTS Error: {e}")
            finally:
                self.shared_data['is_speaking'] = False

    def _try_edge_tts(self, text: str) -> bool:
        """Génère l'audio avec edge_tts. Retourne True si succès."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._generate_audio(text))
            loop.close()
            
            if os.path.exists(self.output_file) and os.path.getsize(self.output_file) > 1000:
                return True
            return False
        except Exception as e:
            print(f"Edge TTS error: {e}")
            return False

    async def _generate_audio(self, text: str):
        communicate = edge_tts.Communicate(text, self.voice)
        await communicate.save(self.output_file)

    def _use_pyttsx3(self, text: str):
        """Fallback local TTS."""
        if not self.fallback_engine or not self.enabled:
            return
        try:
            self.fallback_engine.say(text)
            self.fallback_engine.runAndWait()
            time.sleep(0.3)
        except Exception as e:
            print(f"pyttsx3 error: {e}")

    def stop(self):
        """Stoppe la lecture en cours."""
        self.stop_event.set()
        try:
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.stop()
        except:
            pass
        if self.fallback_engine:
            try:
                self.fallback_engine.stop()
            except:
                pass
        self.shared_data['is_speaking'] = False

    def _play_audio(self):
        try:
            if self.stop_event.is_set() or not self.enabled:
                return

            if os.path.exists(self.output_file) and os.path.getsize(self.output_file) > 100:
                pygame.mixer.music.load(self.output_file)
                pygame.mixer.music.play()
                
                while pygame.mixer.music.get_busy():
                    if self.stop_event.is_set() or not self.enabled:
                        pygame.mixer.music.stop()
                        break
                    pygame.time.Clock().tick(10)
                
                try:
                    pygame.mixer.music.unload()
                except:
                    pass
            
            time.sleep(0.3)
            
        except Exception as e:
            print(f"Playback Error: {e}")
