import cv2
import threading
import time
from ultralytics import YOLO
import mediapipe as mp
import os
import face_recognition
import numpy as np

class VisionSystem(threading.Thread):
    def __init__(self, model_path="../yolov8n.pt", shared_data=None):
        super().__init__()
        self.shared_data = shared_data if shared_data is not None else {}
        self.running = True
        self.ready = False
        self.known_face_encodings = []
        self.known_face_names = []
        
        self.model_path = model_path
        self.yolo = None
        self.cap = None

        # Face Recognition Initialization
        self.load_known_faces()

    def load_known_faces(self):
        print("Loading known faces...")
        faces_dir = "known_faces"
        if not os.path.exists(faces_dir):
            os.makedirs(faces_dir)
            print(f"Created {faces_dir} directory. Add images here.")
            return

        # Scan subdirectories recursively
        for root, dirs, files in os.walk(faces_dir):
            for filename in files:
                if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    try:
                        path = os.path.join(root, filename)
                        image = face_recognition.load_image_file(path)
                        encodings = face_recognition.face_encodings(image)
                        if encodings:
                            self.known_face_encodings.append(encodings[0])
                            
                            # Use folder name as person name if available
                            if root != faces_dir:
                                name = os.path.basename(root)
                            else:
                                name = os.path.splitext(filename)[0]
                                
                            self.known_face_names.append(name)
                            print(f"Loaded face: {name}")
                    except Exception as e:
                        print(f"Error loading face {filename}: {e}")
        
        print(f"Total known faces loaded: {len(self.known_face_names)}")

    def run(self):
        print("Vision System Started (Initializing Camera/Model)...", flush=True)
        
        # Lazy Load YOLO
        if not self.yolo:
             if os.path.exists(self.model_path):
                self.yolo = YOLO(self.model_path)
             else:
                print(f"Model {self.model_path} not found, downloading yolov8n.pt...")
                self.yolo = YOLO("yolov8n.pt")

        # Lazy Load Camera
        if not self.cap:
             self.cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
             
        self.ready = True # Signals main thread that we are up
        
        last_valid_faces = []
        last_valid_time = 0
        
        while self.running:
            if not self.cap.isOpened():
                print("Vision: Camera not accessible! Retrying...")
                self.cap.open(0, cv2.CAP_DSHOW)
                time.sleep(2)
                continue

            ret, frame = self.cap.read()
            if not ret:
                print("Vision: Failed to grab frame. Camera disconnected?")
                time.sleep(1)
                continue

            # Resize frame for faster processing (1/4 size)
            small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
            # Convert BGR (OpenCV) to RGB (face_recognition)
            rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
            
            # Find face locations and encodings
            face_locations = face_recognition.face_locations(rgb_small_frame)
            face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
            
            face_names = []
            for face_encoding in face_encodings:
                matches = face_recognition.compare_faces(self.known_face_encodings, face_encoding, tolerance=0.6)
                name = "Inconnu"
                
                # Or use face distance for best match
                face_distances = face_recognition.face_distance(self.known_face_encodings, face_encoding)
                if len(face_distances) > 0:
                    best_match_index = np.argmin(face_distances)
                    if matches[best_match_index]:
                        name = self.known_face_names[best_match_index]
                
                face_names.append(name)
            
            # Persistence Logic: Prevent flickering
            if face_names:
                last_valid_faces = face_names
                last_valid_time = time.time()
            elif time.time() - last_valid_time < 2.0:
                # Keep last known faces for 2 seconds if detection drops
                face_names = last_valid_faces

            # Object Recognition (YOLO) - Optional: reduce frequency?
            yolo_results = self.yolo(frame, verbose=False)
            detected_objects = []
            for result in yolo_results:
                for box in result.boxes:
                    if float(box.conf[0]) > 0.5:
                        detected_objects.append(self.yolo.names[int(box.cls[0])])

            # Update Shared Context
            self.shared_data['vision_context'] = {
                "faces_count": len(face_names),
                "faces_names": face_names,
                "objects": list(set(detected_objects)),
                "timestamp": time.time()
            }

            # Check if speaking to prevent CPU starvation (Audio Stutter fix)
            if self.shared_data.get('is_speaking', False):
                time.sleep(0.5)
                continue

            time.sleep(0.1)

        self.cap.release()

    def stop(self):
        self.running = False

    def draw_debug(self, frame, face_results, detected_objects):
        # Implementation of visualization if needed
        pass
