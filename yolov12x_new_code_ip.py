import cv2
from ultralytics import YOLO
import threading
import time

# --- Classe pour gérer la lecture vidéo dans un thread séparé ---
class VideoStream:
    """Classe pour lire le flux vidéo dans un thread dédié"""
    def __init__(self, src=0):
        self.stream = cv2.VideoCapture(src)
        if not self.stream.isOpened():
            print(f"Erreur: Impossible d'ouvrir le flux vidéo : {src}")
            exit()
            
        (self.grabbed, self.frame) = self.stream.read()
        self.stopped = False
        print("Flux vidéo démarré...")

    def start(self):
        # Démarrer le thread pour lire les images
        threading.Thread(target=self.update, args=(), daemon=True).start()
        return self

    def update(self):
        # Boucle infinie pour lire les images
        while True:
            if self.stopped:
                return
            (self.grabbed, self.frame) = self.stream.read()

    def read(self):
        # Retourner l'image la plus récente
        return self.frame

    def stop(self):
        # Arrêter le thread
        self.stopped = True
        self.stream.release()

# -----------------------------------------------------------------

# "INSTALLATION" DU MODÈLE :
# Utilisez 'n' (nano) pour de la performance temps-réel, surtout sur CPU
model = YOLO('yolov8n-oiv7.pt') # CHANGEMENT ICI
print("Modèle 'yolov8n-oiv7.pt' chargé.")

# Ouvrir le flux vidéo avec notre classe "threadée"
url = "http://100.69.195.33:8080/video"
vs = VideoStream(src=url).start()

print("Appuyez sur 'q' pour quitter...")
print("La vidéo peut prendre quelques secondes pour se stabiliser...")

# Laissez le temps au buffer de se remplir un peu
time.sleep(2.0) 

while True:
    # Lire la DERNIÈRE image disponible (pas une vieille image du buffer)
    frame = vs.read()
    if frame is None:
        print("Erreur de lecture du frame, arrêt.")
        break
        
    # --- Optionnel : redimensionner pour encore plus de vitesse ---
    # frame_resized = cv2.resize(frame, (640, 480))
    # results = model.predict(frame_resized, conf=0.2, verbose=False)
    # -------------------------------------------------------------

    # --- Prédiction sur l'image pleine résolution ---
    results = model.predict(frame, conf=0.2, verbose=False)
    # -----------------------------------------------

    annotated_frame = results[0].plot()
# --- Redimensionner pour l'affichage ---
    scale_percent = 60  # Mettez 50 pour 50%, 75 pour 75%, etc.
    width = int(annotated_frame.shape[1] * scale_percent / 100)
    height = int(annotated_frame.shape[0] * scale_percent / 100)
    dim = (width, height)
    
    # Créer l'image redimensionnée
    display_frame = cv2.resize(annotated_frame, dim, interpolation=cv2.INTER_AREA)

    # Afficher la petite image
    cv2.imshow("YOLOv8 (Fenêtre réduite) - Appuyez sur 'q'", display_frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

vs.stop()
cv2.destroyAllWindows()