#!/usr/bin/env python3
"""
Bastet AI - Configuration Wizard
Interface CLI interactive pour configurer le système.
"""

import json
import os
import sys

# Pour la navigation clavier cross-platform
try:
    import msvcrt  # Windows
    WINDOWS = True
except ImportError:
    import tty
    import termios
    WINDOWS = False


def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def get_key():
    """Capture une touche clavier."""
    if WINDOWS:
        key = msvcrt.getch()
        if key == b'\xe0':  # Flèches Windows
            key = msvcrt.getch()
            return {b'H': 'UP', b'P': 'DOWN', b'K': 'LEFT', b'M': 'RIGHT'}.get(key, '')
        elif key == b'\r':
            return 'ENTER'
        elif key == b'\x1b':
            return 'ESC'
        return key.decode('utf-8', errors='ignore')
    else:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == '\x1b':
                ch2 = sys.stdin.read(2)
                return {'[A': 'UP', '[B': 'DOWN', '[C': 'RIGHT', '[D': 'LEFT'}.get(ch2, '')
            elif ch == '\r' or ch == '\n':
                return 'ENTER'
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ═══════════════════════════════════════════════════════════════
# YOLO MODELS GRID
# ═══════════════════════════════════════════════════════════════
YOLO_MODELS = {
    # (row, col) -> model_name
    # Rows: v5=0, v8=1, v9=2, v10=3, v11=4
    # Cols: nano=0, small=1, medium=2, large=3, xlarge=4
    (0, 0): "yolov5n", (0, 1): "yolov5s", (0, 2): "yolov5m", (0, 3): "yolov5l", (0, 4): "yolov5x",
    (1, 0): "yolov8n", (1, 1): "yolov8s", (1, 2): "yolov8m", (1, 3): "yolov8l", (1, 4): "yolov8x",
    (2, 0): "yolov9t", (2, 1): "yolov9s", (2, 2): "yolov9m", (2, 3): "yolov9c", (2, 4): "yolov9e",
    (3, 0): "yolov10n", (3, 1): "yolov10s", (3, 2): "yolov10m", (3, 3): "yolov10l", (3, 4): "yolov10x",
    (4, 0): "yolo11n", (4, 1): "yolo11s", (4, 2): "yolo11m", (4, 3): "yolo11l", (4, 4): "yolo11x",
}

ROW_LABELS = ["v5", "v8", "v9", "v10", "v11"]
COL_LABELS = ["nano", "small", "medium", "large", "xlarge"]
COL_RESOURCES = ["~2GB", "~4GB", "~6GB", "~10GB", "~16GB"]


def draw_yolo_grid(sel_row, sel_col):
    """Affiche la grille YOLO avec la sélection en surbrillance."""
    clear_screen()
    print("\n╔═══════════════════════════════════════════════════════════════════╗")
    print("║          YOLO Model Selection (↑↓←→ pour naviguer, ENTER valider) ║")
    print("╠═══════════════════════════════════════════════════════════════════╣")
    print("║ VRAM   │   ~2GB  │   ~4GB  │   ~6GB  │  ~10GB  │  ~16GB  │")
    print("║────────┼─────────┼─────────┼─────────┼─────────┼─────────┤")
    
    for row_idx, row_label in enumerate(ROW_LABELS):
        line = f"║  {row_label:4}  │"
        for col_idx in range(5):
            model = YOLO_MODELS.get((row_idx, col_idx), "???")
            # Simplify name for display
            short = model.replace("yolo", "").replace("v", "")
            if row_idx == sel_row and col_idx == sel_col:
                cell = f" [{short:^5}] "
            else:
                cell = f"  {short:^5}  "
            line += cell + "│"
        print(line)
    
    print("╚═══════════════════════════════════════════════════════════════════╝")
    selected = YOLO_MODELS.get((sel_row, sel_col), "yolov8n")
    print(f"\n  ► Sélectionné: {selected}.pt")
    print("\n  [ENTER] Valider   [ESC] Annuler")


def select_yolo_model(default="yolov8n"):
    """Interface de sélection YOLO avec navigation clavier."""
    # Trouver position par défaut
    sel_row, sel_col = 1, 0  # v8n par défaut
    for (r, c), name in YOLO_MODELS.items():
        if name == default.replace(".pt", ""):
            sel_row, sel_col = r, c
            break
    
    while True:
        draw_yolo_grid(sel_row, sel_col)
        key = get_key()
        
        if key == 'UP' and sel_row > 0:
            sel_row -= 1
        elif key == 'DOWN' and sel_row < 4:
            sel_row += 1
        elif key == 'LEFT' and sel_col > 0:
            sel_col -= 1
        elif key == 'RIGHT' and sel_col < 4:
            sel_col += 1
        elif key == 'ENTER':
            return YOLO_MODELS.get((sel_row, sel_col), "yolov8n") + ".pt"
        elif key == 'ESC':
            return default + ".pt" if not default.endswith(".pt") else default


# ═══════════════════════════════════════════════════════════════
# CAMERA DETECTION
# ═══════════════════════════════════════════════════════════════
def list_cameras():
    """Détecte les caméras disponibles."""
    cameras = []
    try:
        import cv2
        for i in range(10):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                cameras.append(i)
                cap.release()
    except ImportError:
        print("⚠ OpenCV non installé, impossible de détecter les caméras.")
    return cameras


def get_camera_resolutions(cam_idx):
    """Liste les résolutions supportées par une caméra."""
    common_resolutions = [
        (320, 240, 30), (640, 480, 30), (640, 480, 60),
        (800, 600, 30), (1280, 720, 30), (1280, 720, 60),
        (1920, 1080, 30), (1920, 1080, 60), (2560, 1440, 30),
        (3840, 2160, 30)
    ]
    supported = []
    try:
        import cv2
        cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
        if cap.isOpened():
            for w, h, fps in common_resolutions:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                cap.set(cv2.CAP_PROP_FPS, fps)
                actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                actual_fps = int(cap.get(cv2.CAP_PROP_FPS))
                if (actual_w, actual_h, actual_fps) not in supported:
                    supported.append((actual_w, actual_h, actual_fps))
            cap.release()
    except ImportError:
        pass
    return supported if supported else [(640, 480, 30)]


def select_from_list(title, options, default_idx=0):
    """Menu de sélection avec flèches."""
    sel = default_idx
    while True:
        clear_screen()
        print(f"\n  {title}\n")
        for i, opt in enumerate(options):
            prefix = " ► " if i == sel else "   "
            print(f"{prefix}{opt}")
        print("\n  [↑↓] Naviguer  [ENTER] Valider")
        
        key = get_key()
        if key == 'UP' and sel > 0:
            sel -= 1
        elif key == 'DOWN' and sel < len(options) - 1:
            sel += 1
        elif key == 'ENTER':
            return sel


# ═══════════════════════════════════════════════════════════════
# MAIN CONFIG WIZARD
# ═══════════════════════════════════════════════════════════════
def main():
    clear_screen()
    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║                                                          ║
    ║   ██████╗  █████╗ ███████╗████████╗███████╗████████╗    ║
    ║   ██╔══██╗██╔══██╗██╔════╝╚══██╔══╝██╔════╝╚══██╔══╝    ║
    ║   ██████╔╝███████║███████╗   ██║   █████╗     ██║       ║
    ║   ██╔══██╗██╔══██║╚════██║   ██║   ██╔══╝     ██║       ║
    ║   ██████╔╝██║  ██║███████║   ██║   ███████╗   ██║       ║
    ║   ╚═════╝ ╚═╝  ╚═╝╚══════╝   ╚═╝   ╚══════╝   ╚═╝       ║
    ║                                                          ║
    ║              Configuration Wizard v2.0                   ║
    ╚══════════════════════════════════════════════════════════╝
    """)
    
    # Charger config existante ou défauts
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
        print("  ✓ Configuration existante chargée.")
    else:
        config = {
            "ai_provider": "lmstudio",
            "lm_studio_url": "http://192.168.0.57:1234/v1",
            "model_path": "./models/nemomix-unleashed-12b.gguf",
            "yolo_model": "yolov8n.pt",
            "camera_id": 0,
            "yolo_resolution": [640, 480],
            "yolo_fps": 30,
            "face_resolution": [640, 480],
            "tts_enabled": False,
            "stt_enabled": False,
            "tts_voice": "fr-FR-DeniseNeural"
        }
        print("  ⚙ Configuration par défaut créée.")
    
    input("\n  Appuyez sur ENTER pour commencer la configuration...")
    
    # ─────────────────────────────────────────────────
    # 1. AI PROVIDER
    # ─────────────────────────────────────────────────
    ai_options = [
        "LM Studio (serveur distant)",
        "Python local (llama-cpp)"
    ]
    default_ai = 0 if config.get("ai_provider") == "lmstudio" else 1
    ai_choice = select_from_list("Provider IA:", ai_options, default_ai)
    
    if ai_choice == 0:
        config["ai_provider"] = "lmstudio"
        clear_screen()
        print("\n  Configuration LM Studio\n")
        current_url = config.get("lm_studio_url", "http://192.168.0.57:1234/v1")
        print(f"  URL actuelle: {current_url}")
        new_url = input("  Nouvelle URL (ENTER pour garder): ").strip()
        if new_url:
            config["lm_studio_url"] = new_url
    else:
        config["ai_provider"] = "local"
        clear_screen()
        print("\n  Configuration modèle local\n")
        current_path = config.get("model_path", "./models/model.gguf")
        print(f"  Chemin actuel: {current_path}")
        new_path = input("  Nouveau chemin (ENTER pour garder): ").strip()
        if new_path:
            config["model_path"] = new_path
    
    # ─────────────────────────────────────────────────
    # 2. YOLO MODEL
    # ─────────────────────────────────────────────────
    current_yolo = config.get("yolo_model", "yolov8n.pt")
    config["yolo_model"] = select_yolo_model(current_yolo.replace(".pt", ""))
    
    # ─────────────────────────────────────────────────
    # 3. CAMERA
    # ─────────────────────────────────────────────────
    clear_screen()
    print("\n  Détection des caméras...")
    cameras = list_cameras()
    
    if cameras:
        cam_options = [f"Caméra {c}" for c in cameras]
        default_cam = cameras.index(config.get("camera_id", 0)) if config.get("camera_id", 0) in cameras else 0
        cam_choice = select_from_list("Sélection de la caméra:", cam_options, default_cam)
        config["camera_id"] = cameras[cam_choice]
        
        # Résolutions
        clear_screen()
        print(f"\n  Détection résolutions pour Caméra {cameras[cam_choice]}...")
        resolutions = get_camera_resolutions(cameras[cam_choice])
        
        if resolutions:
            res_options = [f"{w}x{h} @ {fps}fps" for w, h, fps in resolutions]
            res_choice = select_from_list("Résolution pour YOLO:", res_options, 0)
            w, h, fps = resolutions[res_choice]
            config["yolo_resolution"] = [w, h]
            config["yolo_fps"] = fps
            
            # Même résolution pour face recognition?
            same_res = select_from_list("Résolution pour reconnaissance faciale:", [
                f"Même résolution ({w}x{h})",
                "Différente..."
            ], 0)
            
            if same_res == 1:
                face_choice = select_from_list("Résolution reconnaissance faciale:", res_options, 0)
                fw, fh, _ = resolutions[face_choice]
                config["face_resolution"] = [fw, fh]
            else:
                config["face_resolution"] = [w, h]
    else:
        print("  ⚠ Aucune caméra détectée, utilisation de la caméra 0 par défaut.")
        config["camera_id"] = 0
    
    # ─────────────────────────────────────────────────
    # 4. TTS VOICE
    # ─────────────────────────────────────────────────
    voices = [
        ("fr-FR-DeniseNeural", "Denise (féminine, naturelle)"),
        ("fr-FR-VivienneNeural", "Vivienne (féminine, formelle)"),
        ("fr-FR-HenriNeural", "Henri (masculine)"),
        ("fr-FR-EloiseNeural", "Eloise (féminine, douce)"),
    ]
    voice_options = [v[1] for v in voices]
    current_voice = config.get("tts_voice", "fr-FR-DeniseNeural")
    default_voice = 0
    for i, (code, _) in enumerate(voices):
        if code == current_voice:
            default_voice = i
            break
    
    voice_choice = select_from_list("Voix TTS:", voice_options, default_voice)
    config["tts_voice"] = voices[voice_choice][0]
    
    # ─────────────────────────────────────────────────
    # SAVE CONFIG
    # ─────────────────────────────────────────────────
    config["tts_enabled"] = False  # Toujours désactivé par défaut
    config["stt_enabled"] = False
    
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    
    clear_screen()
    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║               Configuration Sauvegardée !                ║
    ╚══════════════════════════════════════════════════════════╝
    """)
    print("  Résumé:")
    print(f"    • Provider IA:    {config['ai_provider']}")
    if config['ai_provider'] == 'lmstudio':
        print(f"    • LM Studio URL:  {config['lm_studio_url']}")
    else:
        print(f"    • Modèle local:   {config['model_path']}")
    print(f"    • Modèle YOLO:    {config['yolo_model']}")
    print(f"    • Caméra:         {config['camera_id']}")
    print(f"    • Résolution:     {config['yolo_resolution'][0]}x{config['yolo_resolution'][1]} @ {config['yolo_fps']}fps")
    print(f"    • Voix TTS:       {config['tts_voice']}")
    print(f"    • TTS activé:     {'Oui' if config['tts_enabled'] else 'Non'}")
    print()
    print("  Lancez 'python main.py' pour démarrer Bastet AI.")
    print()


if __name__ == "__main__":
    main()
