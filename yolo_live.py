
import cv2
from ultralytics import YOLO

# Charger le modèle YOLOv8 pré-entraîné
# La première fois que vous exécutez ceci, le modèle sera téléchargé automatiquement.
model = YOLO('yolov8n.pt')

# Ouvrir la webcam (l'indice 0 est généralement la webcam par défaut)
cap = cv2.VideoCapture(0)

# Vérifier si la webcam s'est bien ouverte
if not cap.isOpened():
    print("Erreur: Impossible d'ouvrir la webcam.")
    exit()

# Boucle pour lire les images de la webcam
while True:
    # Lire une nouvelle image
    success, frame = cap.read()

    if success:
        # Effectuer la détection d'objets sur l'image
        results = model(frame)

        # Visualiser les résultats sur l'image
        # .plot() dessine les boîtes et les labels directement sur l'image
        annotated_frame = results[0].plot()

        # Afficher l'image annotée dans une fenêtre
        cv2.imshow("YOLOv8 Détection en direct", annotated_frame)

        # Attendre 1ms et vérifier si la touche 'q' est pressée pour quitter
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    else:
        # Arrêter la boucle si la lecture de l'image échoue
        break

# Libérer la webcam et fermer les fenêtres d'affichage
cap.release()
cv2.destroyAllWindows()

print("Script terminé.")
