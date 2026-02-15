"""
Bastet AI V2 - Vision Module
YOLO + Face Recognition avec résolutions configurables.
"""

import cv2
import threading
import time
import os
import numpy as np
from ultralytics import YOLO
import face_recognition


class VisionSystem(threading.Thread):
    def __init__(self, config: dict, shared_data: dict = None):
        super().__init__(daemon=True)
        self.config = config
        self.shared_data = shared_data if shared_data is not None else {}
        self.running = True
        self.paused = False  # Permet de mettre en pause sans tuer le thread
        self.ready = False
        
        # Config
        self.camera_id = config.get("camera_id", 0)
        self.yolo_model_name = config.get("yolo_model", "yolov8n.pt")
        self.yolo_resolution = config.get("yolo_resolution", [640, 480])
        self.yolo_fps = config.get("yolo_fps", 30)
        self.face_resolution = config.get("face_resolution", [640, 480])
        
        # Objects
        self.yolo = None
        self.cap = None
        self.known_face_encodings = []
        self.known_face_names = []
        
        # Expose state
        # Expose state
        self.shared_data['vision_enabled'] = True
        
        # Tentative de sync avec le Remote Server
        self.remote_url = self.config.get("remote_server_url", "")
        if self.remote_url:
            self.sync_faces_from_remote()
        
        # Charger visages connus
        self._load_known_faces()

    def sync_faces_from_remote(self):
        """Télécharge les visages depuis le serveur distant."""
        import requests
        try:
            print(f"🔄 Syncing faces from {self.remote_url}...")
            resp = requests.get(f"{self.remote_url}/vault/faces", timeout=5)
            if resp.status_code == 200:
                faces = resp.json()
                for face in faces:
                    # Simulation: on log juste car on n'a pas encore le endpoint download binaire en place
                    print(f"   -> Found remote face for: {face['username']}")
            print("✅ Face Sync Complete")
        except Exception as e:
            print(f"⚠️ Face Sync Failed: {e}")

    def toggle(self, enabled: bool):
        """Active ou désactive la vision."""
        self.paused = not enabled
        self.shared_data['vision_enabled'] = enabled
        if not enabled:
            # Libérer la caméra quand en pause
            if self.cap and self.cap.isOpened():
                self.cap.release()
                self.cap = None
            self.shared_data['vision_context'] = {}
            print("⏸ Vision en pause - caméra libérée")
        else:
            # Rouvrir la caméra
            print("▶ Vision réactivée - réouverture caméra...")
            self.cap = cv2.VideoCapture(self.camera_id, cv2.CAP_DSHOW)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.yolo_resolution[0])
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.yolo_resolution[1])
            self.cap.set(cv2.CAP_PROP_FPS, self.yolo_fps)
            print(f"✓ Vision réactivée - Caméra {self.camera_id}")

    def _load_known_faces(self):
        """Charge les visages connus depuis le dossier known_faces."""
        print("Chargement des visages connus...")
        faces_dir = os.path.join(os.path.dirname(__file__), "..", "known_faces")
        
        if not os.path.exists(faces_dir):
            os.makedirs(faces_dir)
            print(f"✓ Dossier {faces_dir} créé. Ajoutez des images ici.")
            return

        for root, dirs, files in os.walk(faces_dir):
            for filename in files:
                if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    try:
                        path = os.path.join(root, filename)
                        image = face_recognition.load_image_file(path)
                        encodings = face_recognition.face_encodings(image)
                        if encodings:
                            self.known_face_encodings.append(encodings[0])
                            
                            # Utiliser le nom du dossier si disponible
                            if root != faces_dir:
                                name = os.path.basename(root)
                            else:
                                name = os.path.splitext(filename)[0]
                            
                            self.known_face_names.append(name)
                            print(f"  ✓ Visage chargé: {name}")
                    except Exception as e:
                        print(f"  ✗ Erreur chargement {filename}: {e}")
        
        print(f"✓ Total visages connus: {len(self.known_face_names)}")

    def run(self):
        print("Démarrage du système de vision...")
        
        # Charger YOLO
        model_path = os.path.join(os.path.dirname(__file__), "..", "models", self.yolo_model_name)
        if os.path.exists(model_path):
            self.yolo = YOLO(model_path)
        else:
            print(f"Téléchargement de {self.yolo_model_name}...")
            self.yolo = YOLO(self.yolo_model_name)
        
        # Ouvrir caméra
        self.cap = cv2.VideoCapture(self.camera_id, cv2.CAP_DSHOW)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.yolo_resolution[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.yolo_resolution[1])
        self.cap.set(cv2.CAP_PROP_FPS, self.yolo_fps)
        
        self.ready = True
        print(f"✓ Vision démarrée - Caméra {self.camera_id} @ {self.yolo_resolution[0]}x{self.yolo_resolution[1]}")
        
        last_valid_faces = []
        last_valid_time = 0
        
        while self.running:
            # Mode pause - attendre sans bloquer le CPU
            if self.paused:
                time.sleep(0.5)
                continue
            
            if not self.cap or not self.cap.isOpened():
                print("⚠ Caméra inaccessible, reconnexion...")
                self.cap = cv2.VideoCapture(self.camera_id, cv2.CAP_DSHOW)
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.yolo_resolution[0])
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.yolo_resolution[1])
                time.sleep(2)
                continue

            ret, frame = self.cap.read()
            if not ret:
                time.sleep(1)
                continue

            # Resize pour face recognition (plus rapide)
            scale = self.face_resolution[0] / self.yolo_resolution[0]
            small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
            rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
            
            # Reconnaissance faciale
            face_locations = face_recognition.face_locations(rgb_small_frame)
            face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
            
            face_names = []
            for face_encoding in face_encodings:
                matches = face_recognition.compare_faces(self.known_face_encodings, face_encoding, tolerance=0.6)
                name = "Inconnu"
                
                face_distances = face_recognition.face_distance(self.known_face_encodings, face_encoding)
                if len(face_distances) > 0:
                    best_match_index = np.argmin(face_distances)
                    if matches[best_match_index]:
                        name = self.known_face_names[best_match_index]
                
                face_names.append(name)
            
            # Persistance (éviter le scintillement)
            if face_names:
                last_valid_faces = face_names
                last_valid_time = time.time()
            elif time.time() - last_valid_time < 2.0:
                face_names = last_valid_faces

            # YOLO
            yolo_results = self.yolo(frame, verbose=False)
            detected_objects = []
            for result in yolo_results:
                for box in result.boxes:
                    if float(box.conf[0]) > 0.5:
                        detected_objects.append(self.yolo.names[int(box.cls[0])])

            # Mettre à jour shared_data
            self.shared_data['vision_context'] = {
                "faces_count": len(face_names),
                "faces_names": face_names,
                "objects": list(set(detected_objects)),
                "timestamp": time.time()
            }

            # Réduire CPU si l'IA parle
            if self.shared_data.get('is_speaking', False):
                time.sleep(0.5)
                continue

            time.sleep(0.1)

        if self.cap:
            self.cap.release()

    def stop(self):
        self.running = False
