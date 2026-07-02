# SpotBot — Guide de Setup Hardware v2.0

> **Version 2.0** — Ajout capteur ultrason HC-SR04 + module WiFi Alfa USB

## Matériel requis

| Composant | Quantité | Optionnel | Notes |
|-----------|----------|:---------:|-------|
| Raspberry Pi 5 (8 Go min) | 1 | | + carte SD 64 Go min (classe A2) |
| Arduino Mega 2560 | 1 | | Clone compatible OK |
| Servos MG996R | 12 | | **12 MG996R** (pignons métal, couple élevé) alimentés EN EXTERNE |
| **BNO08x** (IMU principale) | 1 | | Capteur absolu 9-DOF I2C (quaternions fusionnés à bord) |
| **HC-SR04** (capteur ultrason) | 1 | ✅ Optionnel | Détection d'obstacles < 400 cm |
| Caméra USB (mono) | 1 ou 2 | | 1 = mono SLAM, 2 = stéréo SLAM |
| **Module WiFi Alfa USB** | 1 | ✅ Optionnel | AWUS036ACH ou similaire — basculement WiFi |
| Alimentation 6V/10A | 1 | | Pour les servos (non pour le Pi) |
| Alimentation Pi 5V/5A USB-C | 1 | | Officielle Raspberry Pi 5 |
| Câble USB-A vers USB-B | 1 | | Pi 5 → Arduino Mega (flash + Serial) |
| Condensateurs 1000µF 10V | 2 | | Sur alim servos (filtrage pics courant) |

---

## Schéma de câblage

### 1. Configuration des Servos (12x MG996R) → Arduino Mega

> **⚠️ CRITIQUE** : Les 12 servos consomment de forts pics de courant en pointe (jusqu'à 15-20A en charge complète). Utilisez une alimentation externe dédiée 6V / 10-20A minimum. Ne jamais alimenter les servos via le 5V de l'Arduino.
> **Répartition des servos** :
> - **12 MG996R (pignons métal)** : Installés sur l'ensemble des 12 articulations pour supporter la structure et assurer des mouvements puissants et fluides.

```
Alimentation Externe 5-6V/10A
  ├─ (+) ──────────────────────────── Fil rouge (VCC) de tous les servos
  └─ (-)  ──── GND commun ─────────── Fil marron/noir de tous les servos
                    │
                    └──────────────── GND Arduino Mega (PIN GND)
```

| Servo | Articulation / Patte | PIN Arduino | Modèle Recommandé |
|-------|----------------------|-------------|-------------------|
| 0     | FR Abad (Hanche)     | **D2**      | **MG996R** (Métal) |
| 1     | FR Upper (Cuisse)    | **D3**      | **MG996R** (Métal) |
| 2     | FR Lower (Tibia)     | **D4**      | **MG996R** (Métal) |
| 3     | FL Abad (Hanche)     | **D5**      | **MG996R** (Métal) |
| 4     | FL Upper (Cuisse)    | **D6**      | **MG996R** (Métal) |
| 5     | FL Lower (Tibia)     | **D7**      | **MG996R** (Métal) |
| 6     | BR Abad (Hanche)     | **D8**      | **MG996R** (Métal) |
| 7     | BR Upper (Cuisse)    | **D9**      | **MG996R** (Métal) |
| 8     | BR Lower (Tibia)     | **D10**     | **MG996R** (Métal) |
| 9     | BL Abad (Hanche)     | **D11**     | **MG996R** (Métal) |
| 10    | BL Upper (Cuisse)    | **D12**     | **MG996R** (Métal) |
| 11    | BL Lower (Tibia)     | **D13**     | **MG996R** (Métal) |

> Chaque servo : 3 fils — **Marron/Noir=GND, Rouge=VCC (alim externe), Jaune/Orange/Blanc=Signal (Arduino)**

---

### 2. BNO08x (BNO080/BNO085) → Arduino Mega *(IMU UNIQUE & PRINCIPALE)*

> **Le BNO08x est la seule IMU à utiliser (oubliez le MPU6050).** Il intègre sa propre fusion gyro+accél+magnétomètre (processeur ARM Cortex-M0+ SH-2 intégré) et calcule directement des **quaternions calibrés**. Le filtre complémentaire n'est pas nécessaire côté ROS, et la stabilité du SLAM rtabmap est grandement accrue.

| Broche BNO08x | → | Broche Arduino Mega | Notes |
|:---:|:---:|:---:|---|
| VCC | → | **3.3V** | Alimentation stable en 3.3V |
| GND | → | **GND** | Masse commune |
| SDA | → | **PIN 20 (SDA)** | Ligne de données I2C (Mega) |
| SCL | → | **PIN 21 (SCL)** | Ligne d'horloge I2C (Mega) |
| INT | → | **D18** | Interruption de données (recommandé) |
| RST | → | **D19** | Reset matériel (permet le reset via firmware) |
| PS0 | → | **GND** | Configuration mode I2C (adresse 0x4A) |
| PS1 | → | **GND** | Configuration mode I2C (adresse 0x4A) |

> **Adresse I2C : 0x4A** (PS0=GND, PS1=GND). Le MPU6050 est totalement absent de ce câblage.

**Breakouts compatibles :**
| Référence | MCU | Lien |
|-----------|-----|------|
| SparkFun VR IMU Breakout — BNO080/BNO085 | SH-2 | [sparkfun.com](https://www.sparkfun.com/products/22857) |
| Adafruit BNO085 9-DOF IMU | SH-2 | [adafruit.com](https://www.adafruit.com/product/4754) |

**Librairie Arduino requise :**
```
Arduino IDE → Library Manager → "SparkFun BNO08x"
# ou avec arduino-cli:
arduino-cli lib install "SparkFun BNO08x"
```

---

### 3. Raspberry Pi 5 → Arduino Mega

```
Raspberry Pi 5                     Arduino Mega
  USB-A port    ═══════════════════  USB-B port
                (câble USB standard)

Communication : Serial @ 115200 baud
Utilisation  : Flash firmware + communication JSON bidirectionnelle
```

> L'Arduino se connecte automatiquement comme `/dev/ttyUSB0` ou `/dev/ttyACM0` sur le Pi 5.

---

### 4. Caméra(s) USB → Raspberry Pi 5

**Mode Monoculaire :**
```
Pi 5 USB port ──── Caméra USB unique
                   Apparait comme /dev/video0
```

**Mode Stéréo (2 cameras) :**
```
Pi 5 USB port 1 ──── Caméra GAUCHE → /dev/video0
Pi 5 USB port 2 ──── Caméra DROITE → /dev/video1
```
> Placer les deux caméras parallèles, séparées d'environ 6-12 cm (baseline stéréo).

---

### 5. HC-SR04 (capteur ultrason) → Arduino Mega *(OPTIONNEL)*

> Le HC-SR04 permet au robot de détecter les obstacles devant lui en temps réel.
> Le Pi 5 reçoit les données via le bridge JSON et publie sur `/sensors/ultrasonic`.

| Broche HC-SR04 | → | Broche Arduino Mega | Fil couleur courant |
|:--------------:|:---:|:-------------------:|:-------------------:|
| VCC | → | **5V** Arduino | Rouge |
| GND | → | **GND** commun | Noir/Marron |
| TRIG | → | **D22** | Jaune/Orange |
| ECHO | → | **D23** | Bleu/Vert |

> **Note :** Ne pas utiliser D2–D13 (réservés aux servos). D22 et D23 sont des GPIO libres du Mega.

**Placement recommandé sur le robot :**
```
      ┌────────┐
      │ HC-SR04│  ← Fixé à l'avant du chassis, centré, à ~5 cm du sol
      │ [o] [o]│     Angle d'émission ~15° — portée 2 cm à 400 cm
      └────────┘
          │
         ↓ détecte les obstacles dans un cône de ~15°
```

**Topic ROS 2 publié :**
```bash
ros2 topic echo /sensors/ultrasonic  # sensor_msgs/Range (distance en mètres)
ros2 topic echo /sensors/obstacle    # std_msgs/Bool (True si < 30 cm)
```

---

### 6. Module WiFi Alfa USB → Raspberry Pi 5 *(OPTIONNEL)*

> Le module Alfa fournit une **deuxième interface WiFi** pour le Pi 5.
> Le WiFi watchdog surveille les signaux et bascule automatiquement vers la meilleure connexion.
> **Aucune configuration manuelle nécessaire** — l'auto-détection se fait par VID/PID USB.

| Connexion | Description |
|-----------|-------------|
| Alfa USB → **Port USB Pi 5** | Plug & Play — détecté automatiquement |
| Antenne Alfa | Visser sur le connecteur SMA du module |

**Modules compatibles (testés) :**
| Modèle | Chipset | VID:PID USB |
|--------|---------|-------------|
| AWUS036ACH | RTL8812AU | 0bda:8812 |
| AWUS036AC | RTL8812AU | 0bda:8812 |
| AWUS036NH | RT3070 | 148f:3070 |
| AWUS036H | RT3070 | 148f:5370 |

**Comportement automatique :**
```
Signal wlan0 > -70 dBm  →  Streaming via wlan0 (Pi intégré)
Signal wlan0 < -70 dBm  →  Basculement vers Alfa (wlan1) sans coupure
Alfa non branché        →  Mono-WiFi standard (fonctionnement normal)
```

**Topics ROS 2 :**
```bash
ros2 topic echo /wifi/status      # std_msgs/String
ros2 topic echo /wifi/alfa_active # std_msgs/Bool  (True = Alfa utilisé)
```

---

```
┌─────────────────────────────────────────────────────────┐
│                    RASPBERRY PI 5                        │
│                                                          │
│  USB-C ◄── Alim 5V/5A officielle                        │
│                                                          │
│  USB-A ──► Arduino Mega (Serial + Flash)                 │
│  USB-A ──► Caméra gauche /dev/video0                     │
│  USB-A ──► Caméra droite /dev/video1 (stéréo optionnel) │
│                                                          │
│  GPIO  ──► (libre pour extensions futures)               │
└─────────────────────────────────────────────────────────┘
                         │ USB
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   ARDUINO MEGA 2560                      │
│                                                          │
│  D2 ──► Servo 0  (FR Abad MG996R) │ I2C SDA(20) ◄─ BNO08x │
│  D3 ──► Servo 1  (FR Upper MG996R)│ I2C SCL(21) ◄─ BNO08x │
│  D4 ──► Servo 2  (FR Lower MG996R)│                       │
│  D5 ──► Servo 3  (FL Abad MG996R) │ D18 (INT)  ◄── BNO08x │
│  D6 ──► Servo 4  (FL Upper MG996R)│ D19 (RST)  ──► BNO08x │
│  D7 ──► Servo 5  (FL Lower MG996R)│                       │
│  D8 ──► Servo 6  (BR Abad MG996R) │                       │
│  D9 ──► Servo 7  (BR Upper MG996R)│                       │
│  D10──► Servo 8  (BR Lower MG996R)│                       │
│  D11──► Servo 9  (BL Abad MG996R)  │                       │
│  D12──► Servo 10 (BL Upper MG996R)│                       │
│  D13──► Servo 11 (BL Lower MG996R)│                       │
│                                                          │
│  GND ◄────────────── GND commun servos + BNO08x          │
└─────────────────────────────────────────────────────────┘
                         │
          ┌──────────────┘
          │ VCC Signal uniquement
          ▼
┌─────────────────────────────────────────────────────────┐
│             ALIMENTATION EXTERNE 5-6V / 10A              │
│                                                          │
│  (+) ──────────────────────────────── VCC (rouge) servos │
│  (-) ──── GND commun Arduino ──────── GND (marron) servos│
│                                                          │
│  ⚠️ Condensateurs 100µF (1000µF recommandé) sur les      │
│     bornes + et - pour filtrer les pics de courant       │
└─────────────────────────────────────────────────────────┘
```

---

## Checklist avant mise sous tension

**Obligatoire :**
- [ ] GND Arduino connecté au GND de l'alimentation externe servos
- [ ] VCC servos branché sur **alimentation externe 6V** (JAMAIS Arduino 5V)
- [ ] Condensateurs 1000µF sur bornes alim servos (anti-pics courant)
- [ ] Tous les fils signal servos sur les bonnes pins (D2 à D13)
- [ ] BNO08x : SDA→PIN20, SCL→PIN21, VCC→3.3V, GND→GND, INT→D18, RST→D19
- [ ] Câble USB Pi5↔Arduino branché
- [ ] Alimentation Pi 5 branchée séparément (USB-C 5V/5A)

**HC-SR04 (si installé) :**
- [ ] TRIG → **D22**, ECHO → **D23**
- [ ] VCC → **5V Arduino** (pas l'alim externe)
- [ ] GND → GND commun
- [ ] Capteur fixé à l'avant du chassis, centré, dégagé

**Module Alfa (si installé) :**
- [ ] Branché sur port USB Pi 5
- [ ] Antenne vissée sur connecteur SMA
- [ ] Drivers installés (`bash install/setup_wifi.sh`)

---

## Installation logicielle séquentielle

```bash
# Sur le Raspberry Pi 5 (Ubuntu 24.04 AArch64)

# 1. Cloner le repo
git clone https://github.com/TON_USERNAME/spotbot-ros2.git
cd spotbot-ros2

# 2. Installer ROS 2 Jazzy
bash install/install_ros2.sh

# 3. Installer les dépendances
bash install/install_deps.sh

# 4. Builder le workspace
bash install/build_workspace.sh

# 5. Lancer (tout-en-un)
source ~/spotbot-ros2/ros2_ws/install/setup.bash
ros2 launch spotbot_bringup spotbot.launch.py mode:=mono
```

---

## Calibration des servos

Après installation, ajustez les valeurs dans `spotbot_controller.ino` :

```cpp
// Ajustez ces valeurs selon votre montage réel (0-180 deg)
const float SERVO_STAND[NUM_SERVOS] = {
    90, 90, 90,  // FR: abad, upper, lower
    90, 90, 90,  // FL: ...
    90, 90, 90,  // BR: ...
    90, 90, 90   // BL: ...
};
```

Commande de test servo individuel depuis le Pi :
```bash
ros2 topic pub /cmd_joint_angles std_msgs/Float32MultiArray \
  "data: [90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0]"
```

---

## Flash du firmware Arduino depuis le Pi 5

```bash
# Compiler avec Arduino CLI (recommandé sur le Pi)
arduino-cli compile -b arduino:avr:mega arduino/spotbot_controller/

# Ou utiliser l'IDE Arduino sur un PC puis copier le .hex sur le Pi

# Flash automatique (le node ROS le fait au démarrage si configuré)
python3 ros2_ws/src/spotbot_arduino_bridge/spotbot_arduino_bridge/arduino_flasher.py \
    arduino/spotbot_controller/spotbot_controller.ino.hex

# Flash manuel
avrdude -p atmega2560 -c wiring -P /dev/ttyUSB0 -b 115200 \
    -U flash:w:spotbot_controller.hex:i
```

---

## Liste complète des Modèles 3D à imprimer

> ### 📥 Liens de Téléchargement Direct :
> 
> * ⚡ **PACK D'IMPRESSION PRÊT À L'EMPLOI (RECOMMANDÉ) :**
>   * **[Télécharger le SPOTBOT READY-TO-PRINT PACK (ZIP)](https://github.com/Bot-Bastet/CORE/raw/main/references/spotbot_ready_to_print_pack.zip)** 🚀
>   * *Ce pack unique contient toutes les pièces requises aux bonnes quantités (ex: 4x pieds, 4x épaules, etc.) pré-triées pour votre setup (Mega + micro-servos). Vous avez juste à tout extraire et à le glisser directement dans votre slicer (Cura, PrusaSlicer...) pour lancer l'impression d'un coup !*
> 
> * 🌐 **Sur Thingiverse (Archives complètes d'origine) :**
>   * [Télécharger l'archive complète ZIP de Thingiverse](https://www.thingiverse.com/thing:3445283/zip)
>   * [Voir et télécharger les fichiers STL individuellement sur Thingiverse](https://www.thingiverse.com/thing:3445283/files)
>
> * 📦 **Sauvegardés dans votre propre dépôt GitHub (Archives brutes) :**
>   * [Télécharger l'Archive STL - Part 1 sur 2](https://github.com/Bot-Bastet/CORE/raw/main/references/Spotmicro%20-%20robot%20dog%20-%203445283%20-%20part%201%20of%202.zip)
>   * [Télécharger l'Archive STL - Part 2 sur 2](https://github.com/Bot-Bastet/CORE/raw/main/references/Spotmicro%20-%20robot%20dog%20-%203445283%20-%20part%202%20of%202.zip)

> **💡 Note de conception importante :** Ce robot est basé sur le design **Spotmicro de KDY0523** (référence Thingiverse [3445283](https://www.thingiverse.com/thing:3445283)).
>
> ⚠️ **IMPORTANT :** Pour notre configuration spécifique avec servos standards **MG996R** :
> 1. Vous **devez** utiliser les fichiers et dimensions **standards** (sans le suffixe `_mg`). Les pièces d'origine sont parfaitement dimensionnées pour accueillir les gros servos MG996R.
> 2. Vous **devez** imprimer les plaques et capots conçus pour l'**Arduino Mega** afin d'avoir l'espace nécessaire à l'intérieur du châssis. N'imprimez pas les pièces estampillées `non-mega`.

### 1. Châssis principal (Main Frame & Covers)
Ces pièces forment le corps central du SpotBot et abritent le Raspberry Pi 5, l'Arduino Mega, l'IMU BNO08x, le capteur ultrason HC-SR04 et l'alimentation.

| Fichier STL (Cliquer pour télécharger ⬇️) | Quantité | Rôle / Description | Recommandation Arduino Mega |
|--------------------------------------------|:--------:|--------------------|-----------------------------|
| [plate.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/plate.stl) | 1 | Plaque de base / Support central principal | Standard |
| [L_side_plate.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/L_side_plate.stl) | 1 | Flanc gauche du robot (espace élargi pour Mega) | **Version Spécifique Mega** |
| [R_side_plate.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/R_side_plate.stl) | 1 | Flanc droit du robot (espace élargi pour Mega) | **Version Spécifique Mega** |
| [F_cover.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/F_cover.stl) | 1 | Capot avant (tête / support caméra standard) | Standard |
| [R_cover.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/R_cover.stl) | 1 | Capot arrière | Standard |
| [T_cover_mg.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/T_cover_mg.stl) | 1 | Capot supérieur (adapté micro-servos & Mega) | **Version Spécifique Mega + `_mg`** |
| [B_cover_mg.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/B_cover_mg.stl) | 1 | Capot inférieur (adapté micro-servos & Mega) | **Version Spécifique Mega + `_mg`** |

---

### 2. Épaules (Shoulders)
Ces pièces réalisent l'articulation de la hanche (Abduction / Adduction) pour chaque patte.

| Fichier STL (Cliquer pour télécharger ⬇️) | Quantité | Rôle / Description | Notes / Assemblage |
|--------------------------------------------|:--------:|--------------------|--------------------|
| [I_shoulder_mg.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/I_shoulder_mg.stl) | 4 | Épaule interne (Inner Shoulder) | **Version `_mg`** indispensable pour MG90S/SG90 |
| [O_shoulder.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/O_shoulder.stl) | 4 | Épaule externe (Outer Shoulder) | Standard (identique pour toutes les hanches) |

---

### 3. Bras et Articulations (Limbs)
Ces pièces constituent les membres mobiles (cuisse, tibia, pied) pour les pattes gauches et droites.

| Fichier STL (Cliquer pour télécharger ⬇️) | Quantité | Rôle / Description | Notes / Assemblage |
|--------------------------------------------|:--------:|--------------------|--------------------|
| [L_arm_joint_mg.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/L_arm_joint_mg.stl) | 2 | Articulation supérieure gauche | **Version `_mg`** (Pattes FL et BL) |
| [R_arm_joint_mg.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/R_arm_joint_mg.stl) | 2 | Articulation supérieure droite | **Version `_mg`** (Pattes FR and BR) |
| [L_arm_mg.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/L_arm_mg.stl) | 2 | Bras gauche (Upper arm / Cuisse) | **Version `_mg`** (Pattes FL et BL) |
| [R_arm_mg.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/R_arm_mg.stl) | 2 | Bras droit (Upper arm / Cuisse) | **Version `_mg`** (Pattes FR and BR) |
| [L_arm_cover.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/L_arm_cover.stl) | 2 | Cache / Capot de protection bras gauche | Standard (Pattes FL et BL) |
| [R_arm_cover.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/R_arm_cover.stl) | 2 | Cache / Capot de protection bras droit | Standard (Pattes FR and BR) |
| [L_wrist_mg.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/L_wrist_mg.stl) | 2 | Poignet/Tibia gauche (Lower arm / Wrist) | **Version `_mg`** (Pattes FL et BL) |
| [R_wrist_mg.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/R_wrist_mg.stl) | 2 | Poignet/Tibia droit (Lower arm / Wrist) | **Version `_mg`** (Pattes FR and BR) |
| [foot.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/foot.stl) | 4 | Embout de pied | À imprimer en **TPU (Flexible)** si possible |

---

### 4. Supports de Capteurs (Sensors Mounts)
Pièces optionnelles mais fortement recommandées pour intégrer proprement le capteur de distance.

| Fichier STL (Cliquer pour télécharger ⬇️) | Quantité | Rôle / Description | Notes / Assemblage |
|--------------------------------------------|:--------:|--------------------|--------------------|
| [L_ultra_sonic.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/L_ultra_sonic.stl) | 1 | Support gauche pour capteur HC-SR04 | Se monte sur le capot avant `F_cover.stl` |
| [R_ultra_sonic.stl](https://github.com/Bot-Bastet/CORE/raw/main/references/stl_individual/R_ultra_sonic.stl) | 1 | Support droit pour capteur HC-SR04 | Se monte sur le capot avant `F_cover.stl` |

---

### ⚙️ Conseils d'Impression et Paramètres Recommandés

1. **Remplissage (Infill) :**
   * Pour le châssis central (`plate.stl`, `L_side_plate.stl`, `R_side_plate.stl`) : **20% à 30%** en motif Gyroïde ou Grille.
   * Pour les pièces mobiles et d'effort (`I_shoulder_mg.stl`, `L_arm_joint_mg.stl`, `R_arm_joint_mg.stl`, `L_arm_mg.stl`, `R_arm_mg.stl`, `L_wrist_mg.stl`, `R_wrist_mg.stl`) : **35% à 50%** de remplissage pour garantir la rigidité sous la force des servos MG90S.
2. **Nombre de parois (Walls/Perimeters) :**
   * Réglez sur au moins **3 à 4 lignes de paroi** (perimeters) pour augmenter la résistance mécanique sans alourdir le robot.
3. **Matériau :**
   * **PLA ou PETG** pour toutes les pièces structurelles. Le PETG offre une meilleure résistance aux chocs et une flexibilité salutaire lors des chutes, mais le PLA convient parfaitement s'il est imprimé avec assez de parois.
   * **TPU ou Filament Flexible** pour les 4 `foot.stl`. Si vous n'avez pas de TPU, vous pouvez les imprimer en PLA/PETG et coller des patins en caoutchouc ou de la gaine thermo-rétractable sous les pieds pour éviter que le robot ne glisse sur le carrelage ou le parquet.

