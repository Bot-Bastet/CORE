from ultralytics import YOLO

model = YOLO("yolov12x.pt")  # modèle de base
model.train(data="open-images-v7.yaml", epochs=100, imgsz=640)
