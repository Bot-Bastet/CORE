import cv2
from ultralytics import YOLO
import threading

class VideoStream:
    def __init__(self, src):
        self.stream = cv2.VideoCapture(src)
        self.stream.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.stream.isOpened():
            exit("Erreur: flux vidéo inaccessible")
        self.grabbed, self.frame = self.stream.read()
        self.stopped = False
        threading.Thread(target=self.update, daemon=True).start()
    
    def update(self):
        while not self.stopped:
            self.grabbed, self.frame = self.stream.read()
    
    def read(self):
        return self.frame
    
    def stop(self):
        self.stopped = True
        self.stream.release()

model = YOLO('yolov8n-oiv7.pt')
model.fuse()

vs = VideoStream('rtsp://82.66.150.66:8554/cam1')

while True:
    frame = vs.read()
    if frame is None:
        break
    
    results = model.predict(frame, conf=0.25, iou=0.5, imgsz=640, half=True, verbose=False, device=0)
    annotated = results[0].plot()
    
    display = cv2.resize(annotated, (960, 540), interpolation=cv2.INTER_LINEAR)
    cv2.imshow("YOLOv8", display)
    
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

vs.stop()
cv2.destroyAllWindows()