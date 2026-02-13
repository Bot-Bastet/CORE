import cv2
from ultralytics import YOLO

# "INSTALLATION" DU MODÈLE :
# En appelant ce nom de modèle, la bibliothèque va le
# télécharger automatiquement si il n'est pas déjà sur votre PC.
#
model = YOLO('yolov8l-oiv7.pt')

print("Modèle 'yolov8l-oiv7.pt' chargé (ou téléchargé) avec succès.")
print("Ce modèle connaît 600 classes du dataset Open Images V7.") #

# Ouvrir la webcam
cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("Erreur: Impossible d'ouvrir la webcam.")
    exit()

print("Appuyez sur 'q' pour quitter...")

while True:
    success, frame = cap.read()
    if not success:
        break

    # On utilise 20% de confiance
    results = model.predict(frame, conf=0.2, verbose=False)

    annotated_frame = results[0].plot()
    cv2.imshow("YOLOv8l OIDv7 (600 Classes) - Appuyez sur 'q'", annotated_frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()