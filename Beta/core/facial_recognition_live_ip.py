'''
Script de reconnaissance faciale en temps réel optimisé avec Multiprocessing et Suivi (Tracking).

Architecture :
- Processus Principal : Gère la capture et l'affichage de la webcam pour une fluidité maximale.
- Processus Travailleur (Worker) : 
    - Effectue une détection/reconnaissance faciale complète périodiquement (tous les N frames).
    - Entre les détections complètes, il utilise un algorithme de suivi (CSRT) très rapide pour suivre les visages, 
      éliminant ainsi le décalage entre la vidéo et les boîtes de détection.
'''
import cv2
import numpy as np
import os
import face_recognition
from multiprocessing import Process, Queue


def face_recognition_worker(input_queue, output_queue):
    '''
    Processus travailleur qui effectue la reconnaissance et le suivi.
    '''
    # --- 1. Préparation des données ---
    KNOWN_FACES_DIR = "known_faces"
    print("[Worker] Chargement des visages connus...")
    known_face_encodings = []
    known_face_names = []
    for person_name in os.listdir(KNOWN_FACES_DIR):
        person_dir = os.path.join(KNOWN_FACES_DIR, person_name)
        if os.path.isdir(person_dir):
            for image_name in os.listdir(person_dir):
                image_path = os.path.join(person_dir, image_name)
                if os.path.isfile(image_path) and image_name.lower().endswith((".png", ".jpg", ".jpeg")):
                    image = face_recognition.load_image_file(image_path)
                    encodings = face_recognition.face_encodings(image)
                    if encodings:
                        known_face_encodings.append(encodings[0])
                        known_face_names.append(person_name)
    print(f"[Worker] {len(known_face_names)} visages chargés.")

    # --- 2. Boucle de traitement avec suivi ---
    frame_counter = 0
    multi_tracker = None
    tracked_names = []
    DETECTION_INTERVAL = 6 # Lancer la détection complète tous les 6 frames

    while True:
        frame = input_queue.get()
        if frame is None: # Signal de terminaison
            break

        # Lancer la détection lourde périodiquement ou si le suivi est perdu
        if frame_counter % DETECTION_INTERVAL == 0 or multi_tracker is None:
            # Trouver tous les visages et leurs encodages
            face_locations = face_recognition.face_locations(frame)
            face_encodings = face_recognition.face_encodings(frame, face_locations)

            # Recréer les trackers
            multi_tracker = cv2.legacy.MultiTracker_create()
            tracked_names = []
            new_boxes = []

            for face_encoding, face_location in zip(face_encodings, face_locations):
                matches = face_recognition.compare_faces(known_face_encodings, face_encoding)
                name = "Inconnu"
                face_distances = face_recognition.face_distance(known_face_encodings, face_encoding)
                if len(face_distances) > 0:
                    best_match_index = np.argmin(face_distances)
                    if matches[best_match_index]:
                        name = known_face_names[best_match_index]
                
                tracked_names.append(name)
                
                # Convertir la location en format (x, y, w, h) pour le tracker
                top, right, bottom, left = face_location
                box = (left, top, right - left, bottom - top)
                new_boxes.append(box)
                
                # Initialiser et ajouter un nouveau tracker
                tracker = cv2.legacy.TrackerCSRT_create()
                multi_tracker.add(tracker, frame, box)
            
            output_queue.put((new_boxes, tracked_names))

        else: # Sinon, utiliser le suivi rapide
            success, boxes = multi_tracker.update(frame)
            if success:
                # Envoyer les boîtes mises à jour avec les noms déjà connus
                output_queue.put((boxes, tracked_names))
            else:
                # Le suivi a échoué, forcer une nouvelle détection au prochain frame
                multi_tracker = None 

        frame_counter += 1


if __name__ == '__main__':
    input_queue = Queue(maxsize=1)
    output_queue = Queue(maxsize=1)

    recognition_process = Process(target=face_recognition_worker, args=(input_queue, output_queue))
    recognition_process.daemon = True
    recognition_process.start()
    url = "http://100.69.195.33:8080/video"
    video_capture = cv2.VideoCapture(url)
    if not video_capture.isOpened():
        print("Erreur: Impossible d'accéder à la webcam.")
        exit()

    boxes = []
    names = []

    while True:
        ret, frame = video_capture.read()
        if not ret:
            break

        # Préparer l'image pour le worker (plus petite pour la détection)
        small_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
        rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

        if input_queue.empty():
            input_queue.put(rgb_small_frame)

        try:
            boxes, names = output_queue.get_nowait()
        except Exception:
            pass

        # Afficher les boîtes de suivi
        for i, box in enumerate(boxes):
            p1 = (int(box[0]), int(box[1]))
            p2 = (int(box[0] + box[2]), int(box[1] + box[3]))
            
            # Remettre à l'échelle pour l'affichage sur l'image originale
            p1_display = (p1[0] * 2, p1[1] * 2)
            p2_display = (p2[0] * 2, p2[1] * 2)
            
            cv2.rectangle(frame, p1_display, p2_display, (0, 255, 0), 2)
            
            name = names[i] if i < len(names) else ""
            cv2.rectangle(frame, (p1_display[0], p2_display[1] - 35), (p2_display[0], p2_display[1]), (0, 255, 0), cv2.FILLED)
            font = cv2.FONT_HERSHEY_DUPLEX
            cv2.putText(frame, name, (p1_display[0] + 6, p2_display[1] - 6), font, 1.0, (255, 255, 255), 1)

        cv2.imshow('Reconnaissance et Suivi en Direct', frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    print("Arrêt du script...")
    input_queue.put(None)
    recognition_process.join()
    video_capture.release()
    cv2.destroyAllWindows()
    print("Script terminé.")