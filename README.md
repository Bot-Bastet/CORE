# 🐕 SpotBot-ROS2

SpotBot est un robot quadrupède (refork) basé sur un **Raspberry Pi 5** et un **Arduino Mega 2560**, propulsé par **ROS 2 Jazzy Jalisco** sur Debian 13 (Trixie).

## 🚀 Caractéristiques
- **Cerveau** : Raspberry Pi 5 (8GB RAM)
- **Contrôleur Moteurs** : Arduino Mega 2560 R3
- **Système** : Debian 13 (Trixie) avec ROS 2 Jazzy compilé depuis les sources.
- **Vision** : V-SLAM via ORB-SLAM3 et caméra USB (Mono ou Stéréo auto-détecté).
- **Interface** : Dashboard Web via ROSBoard.

## 🛠️ Installation Rapide
Le projet utilise une installation personnalisée de ROS 2 pour supporter Debian 13.
```bash
# Sourcing de l'environnement
source /opt/ros2_jazzy/install/setup.bash
source ~/ros2_ws/install/setup.bash
```

## 🎮 Gestion du Robot
Une commande unifiée a été créée pour simplifier l'usage quotidien :
- `spotbot start`   : Lance les nodes moteurs, caméra, SLAM et interface web.
- `spotbot stop`    : Arrête tous les processus ROS 2.
- `spotbot status`  : Affiche la température, RAM et les nodes actifs.
- `spotbot logs`    : Visualise le flux de données en temps réel.
- `~/ros2_ws/run_slam.sh` : Lance ORB-SLAM3 manuellement (Mono ou Stéréo auto-détecté).

## 📺 Interface de Debug (ROSBoard)
Accédez au dashboard en direct depuis n'importe quel appareil sur le même réseau :
👉 **http://[IP_DU_PI]:8888**

Topics disponibles en temps réel :
| Topic | Type | Description |
|---|---|---|
| `/orb_slam3/tracking_image` | Image | Caméra avec features trackées en vert |
| `/orb_slam3/map_points` | PointCloud2 | Nuage de points 3D |
| `/orb_slam3/camera_path` | Path | Tracé complet de la trajectoire |
| `/orb_slam3/camera_pose` | PoseStamped | Position instantanée de la caméra |

---

## 📷 Calibration de la Caméra

> [!CAUTION]
> **La calibration est OBLIGATOIRE pour que le SLAM fonctionne correctement.**
> Sans elle, les distances et la carte 3D seront fausses. Un profil générique par défaut est fourni dans `config/camera_calibration.yaml` mais il ne correspond pas à votre caméra physique.

### Étape 1 — Imprimer la mire de calibration
Imprimez ce damier sur une feuille A4 **sans mise à l'échelle** (100%) :
👉 https://github.com/opencv/opencv/blob/master/doc/pattern.png

> La mire fait **9×6** cases, chaque case mesure **~25 mm** (vérifiez avec une règle après impression).

### Étape 2 — Installer l'outil de calibration
```bash
sudo apt install ros-jazzy-camera-calibration
```

### Étape 3 — Lancer la calibration
Branchez votre caméra, sourcez ROS2, puis exécutez :
```bash
source /opt/ros2_jazzy/install/setup.bash
source ~/ros2_ws/install/setup.bash

# Lancer le nœud caméra (si pas déjà lancé)
ros2 run usb_cam usb_cam_node_exe --ros-args -p video_device:=/dev/video0 &

# Lancer l'outil de calibration
ros2 run camera_calibration cameracalibrator \
  --size 8x6 \
  --square 0.025 \
  --ros-args -r image:=/camera/image_raw -r camera_info:=/camera/camera_info
```

> **`--size 8x6`** = nombre de *coins intérieurs* (colonnes x lignes)
> **`--square 0.025`** = taille d'une case en mètres (25 mm = 0.025)

### Étape 4 — Effectuer la calibration
Une fenêtre s'ouvre avec votre flux vidéo. Présentez la mire devant la caméra depuis **plusieurs angles, distances et orientations** jusqu'à ce que les barres de progression passent au vert. Cliquez ensuite sur **"Calibrate"** puis **"Save"**.

### Étape 5 — Copier le fichier généré
```bash
# Le fichier est sauvegardé ici par défaut :
cp /tmp/calibrationdata.tar.gz ~/
cd ~ && tar -xzf calibrationdata.tar.gz

# Copier le résultat dans la config du projet
cp ost.yaml ~/ros2_ws/src/ORB_SLAM3_ROS2/config/camera_calibration.yaml
```

---

## 📂 Structure du Workspace
- `spotbot_arduino_bridge` : Pont USB Serial entre Pi 5 et Arduino.
- `spotbot_motion`         : Algorithmes de cinématique et mouvements.
- `spotbot_streaming`      : Gestion du flux vidéo et WiFi.
- `spotbot_description`    : Modèle 3D (URDF) du robot.
- `spotbot_bringup`        : Launchers principaux du système.
- `ORB_SLAM3_ROS2`         : Nœud personnalisé de V-SLAM (Mono/Stéréo) diffusant TF, Pose et Nuage de Points.
- `config/`                : Fichiers de configuration (calibration caméra, paramètres SLAM).

---

## 🤖 ROADMAP : CORE (Système Embarqué - Robot / ROS 2)

Ici, la priorité reste la mécanique, la navigation et la survie. Les fonctionnalités de traitement lourd, y compris l'audio, viennent en dernier.

### Étape 1 : Fondations ROS 2 et Connectivité (Client uniquement)
- [ ] Créer le nœud de communication principal se connectant au Gateway (WebSocket/MQTT).
- [ ] Implémenter l'envoi continu de la télémétrie (vitesse, position vSLAM, gyro).

### Étape 2 : Gestion du Flux Vidéo et Vision Locale
- [ ] Développer le nœud de capture caméra et diffusion du flux RTSP.
- [ ] Implémenter les nœuds de vision locale (YOLO et Reconnaissance Faciale).
- [ ] Mettre en place le téléchargement de la base de données des visages au boot.

### Étape 3 : Système d'Offloading Dynamique (Vision)
- [ ] Créer le Listener pour écouter les ordres du Gateway.
- [ ] Logique de bascule : Couper YOLO local si YOLO_REMOTE_ACTIVE, couper la reco faciale si FACE_REMOTE_ACTIVE. Reprise automatique en cas de perte de connexion.

### Étape 4 : Système d'Offloading Dynamique (Audio) & Capture Brute
- [ ] Mettre en place un nœud de capture micro (streaming audio sortant) et un nœud de lecture haut-parleur (streaming audio entrant).
- [ ] Intégrer la logique de bascule audio : Si le PC prend le relais (AUDIO_REMOTE_ACTIVE), le Pi se contente de faire transiter l'audio brut sans rien calculer.

### Étape 5 (À FAIRE TOUT À LA FIN) : Traitement Audio Local (STT/TTS)
- [ ] Implémenter des petits modèles STT (Speech-to-Text) et TTS (Text-to-Speech) locaux sur le Raspberry Pi.
- [ ] Configurer le robot pour qu'il n'utilise ces modèles locaux que s'il est totalement déconnecté du CORE-Node ou si la case STT/TTS n'est pas cochée sur le PC.

---
Projet développé avec ❤️ pour la robotique quadrupède.
