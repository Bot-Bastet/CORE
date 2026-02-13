import edge_tts
import pygame
import threading
import asyncio
import os
import time

class TTSHandler:
    def __init__(self, shared_data=None):
        self.lock = threading.Lock()
        self.shared_data = shared_data if shared_data is not None else {}
        self.output_file = "temp_tts_output.mp3"
        self.stop_event = threading.Event()
        self.use_fallback = False
        
        # Initialize pygame mixer for audio playback
        try:
            if pygame.mixer.get_init():
                pygame.mixer.quit()
            pygame.mixer.init(frequency=24000, buffer=4096)
            print(f"Audio Mixer Config: {pygame.mixer.get_init()}")
        except Exception as e:
            print(f"Warning: Audio mixer init failed: {e}")
        
        # Try to load pyttsx3 as fallback
        try:
            import pyttsx3
            self.fallback_engine = pyttsx3.init()
            # Configure French voice if available
            voices = self.fallback_engine.getProperty('voices')
            for voice in voices:
                if 'french' in voice.name.lower() or 'fr' in voice.id.lower():
                    self.fallback_engine.setProperty('voice', voice.id)
                    break
            self.fallback_engine.setProperty('rate', 180)
            print("TTS Fallback (pyttsx3) ready.")
        except Exception as e:
            print(f"pyttsx3 fallback not available: {e}")
            self.fallback_engine = None

    def speak(self, text):
        """Non-blocking speak"""
        if not text or len(text.strip()) < 2:
            return
        # Cancel any previous speech
        self.stop() 
        self.stop_event.clear()
        
        thread = threading.Thread(target=self._speak_thread, args=(text,))
        thread.start()

    def _speak_thread(self, text):
        with self.lock:
            if self.stop_event.is_set():
                return
            try:
                # Try edge_tts first (better quality)
                if not self.use_fallback:
                    success = self._try_edge_tts(text)
                    if success:
                        if not self.stop_event.is_set():
                            self._play_audio()
                        return
                    else:
                        print("Edge TTS failed, switching to fallback...")
                        self.use_fallback = True
                
                # Fallback to pyttsx3
                if self.fallback_engine:
                    self._use_pyttsx3(text)
                    
            except Exception as e:
                print(f"TTS Error: {e}")
                self.shared_data['is_speaking'] = False

    def _try_edge_tts(self, text):
        """Try to generate audio with edge_tts. Returns True on success."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._generate_audio(text))
            loop.close()
            
            # Check if file was created and has content
            if os.path.exists(self.output_file) and os.path.getsize(self.output_file) > 1000:
                return True
            return False
        except Exception as e:
            print(f"Edge TTS error: {e}")
            return False

    async def _generate_audio(self, text):
        voice = "fr-FR-VivienneNeural"
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(self.output_file)

    def _use_pyttsx3(self, text):
        """Use local TTS as fallback"""
        if not self.fallback_engine:
            return
        try:
            self.shared_data['is_speaking'] = True
            self.fallback_engine.say(text)
            self.fallback_engine.runAndWait()
            time.sleep(0.3)
            self.shared_data['is_speaking'] = False
        except Exception as e:
            print(f"pyttsx3 error: {e}")
            self.shared_data['is_speaking'] = False

    def stop(self):
        """Stop current playback immediately"""
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
            if self.stop_event.is_set():
                return

            if os.path.exists(self.output_file) and os.path.getsize(self.output_file) > 100:
                self.shared_data['is_speaking'] = True
                
                pygame.mixer.music.load(self.output_file)
                pygame.mixer.music.play()
                
                while pygame.mixer.music.get_busy():
                    if self.stop_event.is_set():
                        pygame.mixer.music.stop()
                        break
                    pygame.time.Clock().tick(10)
                
                try:
                    pygame.mixer.music.unload()
                except:
                    pass
            
            time.sleep(0.5) 
            self.shared_data['is_speaking'] = False
            
        except Exception as e:
            print(f"Playback Error: {e}")
            self.shared_data['is_speaking'] = False
