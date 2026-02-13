# AI Vision Context

This project integrates Facial Detection, Object Recognition, and a Large Language Model (NemoMix-Unleashed-12B) to create a context-aware AI.

## Setup

1. **Install Dependencies**:
   Ensure you have Python installed (3.10+ recommended).
   ```bash
   pip install -r requirements.txt
   ```
   *Note: For GPU acceleration (highly recommended for the 12B model), ensure you install the CUDA version of PyTorch.*

2. **Models**:
   - The system uses `yolov8n.pt` for object detection (assumed to be in the parent directory or downloaded automatically).
   - The AI model `MarinaraSpaghetti/NemoMix-Unleashed-12B` will be downloaded from HuggingFace on the first run. **This is a ~24GB download.**

## Running the System

Run the main script:
```bash
python main.py
```

## How it Works

- **Vision Thread (`vision.py`)**: Captures video from the webcam, detects faces (MediaPipe) and objects (YOLO), and updates a shared context.
- **AI Thread (`ai_agent.py`)**: Monitors the shared context. When it detects changes (e.g., new objects appearing), it generates a reaction using the NemoMix LLM.
- **Main Thread (`main.py`)**: Orchestrates the startup and shutdown of threads.

## Requirements

- Webcam
- 24GB+ RAM (or decent GPU with VRAM) to run 12B model effectively.
- Internet connection for initial model download.
