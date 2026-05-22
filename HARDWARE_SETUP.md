# SpotBot — Guide de Setup Hardware v2.0

> **Version 2.0** — Ajout capteur ultrason HC-SR04 + module WiFi Alfa USB

## Matériel requis

| Composant | Quantité | Optionnel | Notes |
|-----------|----------|:---------:|-------|
| Raspberry Pi 5 (8 Go min) | 1 | | + carte SD 64 Go min (classe A2) |
| Arduino Mega 2560 | 1 | | Clone compatible OK |
| Servos MG90S & SG90 | 12 | | **10 MG90S** (pignons métal) + **2 SG90** (plastique) alimentés EN EXTERNE |
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

### 1. Configuration des Servos (10x MG90S & 2x SG90) → Arduino Mega

> **⚠️ CRITIQUE** : Les 12 servos consomment de forts pics de courant en pointe. Utilisez une alimentation externe dédiée 5-6V / 10A minimum. Ne jamais alimenter les servos via le 5V de l'Arduino.
> **Répartition des servos** :
> - **10 MG90S (pignons métal)** : Recommandés pour les articulations supportant le poids et la force (Knee/Elbow, Lower/Tibia, Upper/Cuisse, et Hip/Abad des pattes avant).
> - **2 SG90 (pignons plastique)** : Réservés aux hanches arrières (`BL Abad` et `BR Abad` - PIN D8 et D11), qui subissent le moins de charge dynamique.

```
Alimentation Externe 5-6V/10A
  ├─ (+) ──────────────────────────── Fil rouge (VCC) de tous les servos
  └─ (-)  ──── GND commun ─────────── Fil marron/noir de tous les servos
                    │
                    └──────────────── GND Arduino Mega (PIN GND)
```

| Servo | Articulation / Patte | PIN Arduino | Modèle Recommandé |
|-------|----------------------|-------------|-------------------|
| 0     | FR Abad (Hanche)     | **D2**      | **MG90S** (Métal) |
| 1     | FR Upper (Cuisse)    | **D3**      | **MG90S** (Métal) |
| 2     | FR Lower (Tibia)     | **D4**      | **MG90S** (Métal) |
| 3     | FL Abad (Hanche)     | **D5**      | **MG90S** (Métal) |
| 4     | FL Upper (Cuisse)    | **D6**      | **MG90S** (Métal) |
| 5     | FL Lower (Tibia)     | **D7**      | **MG90S** (Métal) |
| 6     | BR Abad (Hanche)     | **D8**      | **SG90** (Plastique) |
| 7     | BR Upper (Cuisse)    | **D9**      | **MG90S** (Métal) |
| 8     | BR Lower (Tibia)     | **D10**     | **MG90S** (Métal) |
| 9     | BL Abad (Hanche)     | **D11**     | **SG90** (Plastique) |
| 10    | BL Upper (Cuisse)    | **D12**     | **MG90S** (Métal) |
| 11    | BL Lower (Tibia)     | **D13**     | **MG90S** (Métal) |

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
│  D2 ──► Servo 0  (FR Abad MG90S) │ I2C SDA(20) ◄─ BNO08x │
│  D3 ──► Servo 1  (FR Upper MG90S)│ I2C SCL(21) ◄─ BNO08x │
│  D4 ──► Servo 2  (FR Lower MG90S)│                       │
│  D5 ──► Servo 3  (FL Abad MG90S) │ D18 (INT)  ◄── BNO08x │
│  D6 ──► Servo 4  (FL Upper MG90S)│ D19 (RST)  ──► BNO08x │
│  D7 ──► Servo 5  (FL Lower MG90S)│                       │
│  D8 ──► Servo 6  (BR Abad SG90)  │                       │
│  D9 ──► Servo 7  (BR Upper MG90S)│                       │
│  D10──► Servo 8  (BR Lower MG90S)│                       │
│  D11──► Servo 9  (BL Abad SG90)  │                       │
│  D12──► Servo 10 (BL Upper MG90S)│                       │
│  D13──► Servo 11 (BL Lower MG90S)│                       │
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

> **💡 Note de conception importante :** Ce robot est basé sur le design **Spotmicro de KDY0523** (référence Thingiverse [3445283](https://www.thingiverse.com/thing:3445283)).
>
> ⚠️ **IMPORTANT :** Pour notre configuration spécifique (10 servos MG90S + 2 SG90 et Arduino Mega 2560) :
> 1. Vous **devez absolument** utiliser les fichiers comportant le suffixe **`_mg` (Micro Gear)**. Ces fichiers sont modifiés pour s'adapter à la taille des micro-servos (MG90S/SG90). N'imprimez pas les versions standard prévues pour les gros servos MG996R.
> 2. Vous **devez** imprimer les plaques et capots conçus pour l'**Arduino Mega** afin d'avoir l'espace nécessaire à l'intérieur du châssis. N'imprimez pas les pièces estampillées `non-mega`.

### 1. Châssis principal (Main Frame & Covers)
Ces pièces forment le corps central du SpotBot et abritent le Raspberry Pi 5, l'Arduino Mega, l'IMU BNO08x, le capteur ultrason HC-SR04 et l'alimentation.

| Fichier STL | Quantité | Rôle / Description | Recommandation Arduino Mega |
|-------------|:--------:|--------------------|-----------------------------|
| `plate.stl` | 1 | Plaque de base / Support central principal | Standard |
| `L_side_plate.stl` | 1 | Flanc gauche du robot (espace élargi pour Mega) | **Version Spécifique Mega** |
| `R_side_plate.stl` | 1 | Flanc droit du robot (espace élargi pour Mega) | **Version Spécifique Mega** |
| `F_cover.stl` | 1 | Capot avant (tête / support caméra standard) | Standard |
| `R_cover.stl` | 1 | Capot arrière | Standard |
| `T_cover_mg.stl` | 1 | Capot supérieur (adapté micro-servos & Mega) | **Version Spécifique Mega + `_mg`** |
| `B_cover_mg.stl` | 1 | Capot inférieur (adapté micro-servos & Mega) | **Version Spécifique Mega + `_mg`** |

---

### 2. Épaules (Shoulders)
Ces pièces réalisent l'articulation de la hanche (Abduction / Adduction) pour chaque patte.

| Fichier STL | Quantité | Rôle / Description | Notes / Assemblage |
|-------------|:--------:|--------------------|--------------------|
| `I_shoulder_mg.stl` | 4 | Épaule interne (Inner Shoulder) | **Version `_mg`** indispensable pour MG90S/SG90 |
| `O_shoulder.stl` | 4 | Épaule externe (Outer Shoulder) | Standard (identique pour toutes les hanches) |

---

### 3. Bras et Articulations (Limbs)
Ces pièces constituent les membres mobiles (cuisse, tibia, pied) pour les pattes gauches et droites.

| Fichier STL | Quantité | Rôle / Description | Notes / Assemblage |
|-------------|:--------:|--------------------|--------------------|
| `L_arm_joint_mg.stl` | 2 | Articulation supérieure gauche | **Version `_mg`** (Pattes FL et BL) |
| `R_arm_joint_mg.stl` | 2 | Articulation supérieure droite | **Version `_mg`** (Pattes FR and BR) |
| `L_arm_mg.stl` | 2 | Bras gauche (Upper arm / Cuisse) | **Version `_mg`** (Pattes FL et BL) |
| `R_arm_mg.stl` | 2 | Bras droit (Upper arm / Cuisse) | **Version `_mg`** (Pattes FR and BR) |
| `L_arm_cover.stl` | 2 | Cache / Capot de protection bras gauche | Standard (Pattes FL et BL) |
| `R_arm_cover.stl` | 2 | Cache / Capot de protection bras droit | Standard (Pattes FR and BR) |
| `L_wrist_mg.stl` | 2 | Poignet/Tibia gauche (Lower arm / Wrist) | **Version `_mg`** (Pattes FL et BL) |
| `R_wrist_mg.stl` | 2 | Poignet/Tibia droit (Lower arm / Wrist) | **Version `_mg`** (Pattes FR and BR) |
| `foot.stl` | 4 | Embout de pied | À imprimer en **TPU (Flexible)** si possible |

---

### 4. Supports de Capteurs (Sensors Mounts)
Pièces optionnelles mais fortement recommandées pour intégrer proprement le capteur de distance.

| Fichier STL | Quantité | Rôle / Description | Notes / Assemblage |
|-------------|:--------:|--------------------|--------------------|
| `L_ultra_sonic.stl` | 1 | Support gauche pour capteur HC-SR04 | Se monte sur le capot avant `F_cover.stl` |
| `R_ultra_sonic.stl` | 1 | Support droit pour capteur HC-SR04 | Se monte sur le capot avant `F_cover.stl` |

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

