# Projet BASTET - Robot IA Vision Contextuelle

## 1. Présentation Rapide du Projet

### Objectif Principal
Développement d'un **robot autonome intelligent** équipé de capacités de vision par ordinateur et d'interaction naturelle avec les utilisateurs. Le robot sera déployé sur un campus universitaire pour reconnaître les étudiants, fournir des informations contextuelles (emploi du temps, localisation) et communiquer de manière naturelle.

### Design du Robot

Le robot **BASTET** s'inspire du design quadrupède de **Boston Dynamics Spot**, mais en version **miniature et économique** adaptée à un projet étudiant :

| Caractéristique | Spot (Boston Dynamics) | BASTET (Notre projet) |
|-----------------|------------------------|----------------------|
| **Taille** | ~84 cm de long | ~30-40 cm de long |
| **Poids** | ~32 kg | ~5-8 kg (estimé) |
| **Locomotion** | 4 pattes articulées | 4 pattes (servomoteurs) |
| **Caméras** | 5 caméras stéréo | 1 caméra HD + streaming |
| **Puissance** | Moteurs haute performance | Servos hobby/éducation |
| **Budget** | ~75 000 € | Budget étudiant limité |

Ce choix de design quadrupède permet une **meilleure stabilité** et **adaptation au terrain** du campus (marches, pentes, surfaces irrégulières) par rapport à un robot à roues classique.

### Architecture Globale

```
┌─────────────────┐    RTSP Stream    ┌──────────────────┐    API/WS    ┌──────────────────┐
│     ROBOT       │ ─────────────────►│ Serveur Mandataire│◄───────────►│  PC Distant      │
│  (ROS2 + Caméra)│                   │     RTSP          │             │  (Logiciel IA)   │
└─────────────────┘                   └──────────────────┘             └──────────────────┘
        │                                                                       │
        │                              ┌──────────────────┐                     │
        └─────────────────────────────►│   Frontend Web   │◄────────────────────┘
          Contrôle ROS2                │   (React.js)     │
                                       └──────────────────┘
```

### Pourquoi RTSP ?

**RTSP (Real-Time Streaming Protocol)** a été choisi pour le streaming vidéo du robot vers le serveur mandataire pour plusieurs raisons :

| Avantage | Description |
|----------|-------------|
| **Faible latence** | Protocole optimisé pour le temps réel, essentiel pour la vision robotique |
| **Standard industriel** | Compatible avec la majorité des caméras IP et logiciels de vision |
| **Contrôle bidirectionnel** | Permet pause/play/seek contrairement au streaming HTTP simple |
| **Économie de bande passante** | Utilise RTP/UDP pour le transport, évitant l'overhead TCP |
| **Qualité adaptative** | Supporte différents codecs (H.264, H.265) selon les besoins |
| **Intégration ROS2** | Packages natifs disponibles (ros2_camera, v4l2_camera) |

Le serveur mandataire RTSP agit comme un **relais centralisé**, permettant à plusieurs clients (PC de traitement, interface de monitoring) d'accéder au flux sans surcharger le robot.

---

## 2. Composants Logiciels Actuels

### 2.1 Système de Vision (`vision.py`)

Le module de vision utilise deux technologies complémentaires :

- **YOLOv8** : Détection d'objets en temps réel (personnes, objets du quotidien)
- **Face Recognition** : Reconnaissance faciale des utilisateurs enregistrés

**Fonctionnalités clés :**
- Chargement automatique des visages depuis le dossier `known_faces/`
- Persistance de détection (2 secondes) pour éviter le scintillement
- Optimisation par réduction de résolution (1/4) pour le traitement facial

### 2.2 Agent IA (`ai_agent.py`)

Un agent conversationnel nommé **"Bastet"** capable de :

- Répondre aux questions de manière concise et directe
- Intégrer le contexte visuel (qui est présent, quels objets)
- Accéder à l'emploi du temps de l'utilisateur identifié
- Fonctionner en mode local (llama-cpp) ou via API externe (LM Studio)

### 2.3 Intégration MyGES (`myges_integration.py`)

Connexion à l'API **Kordis/MyGES** pour récupérer :
- L'emploi du temps du jour et de la semaine
- Les informations de cours (salle, professeur, horaires)
- Le statut actuel de l'utilisateur (en cours, libre)

### 2.4 Serveur Backend (`server.py`)

Architecture **FastAPI** avec :
- WebSocket pour la communication temps réel avec le frontend
- endpoints REST pour le chat et le contrôle
- Gestion multi-thread (Vision, IA, Audio, TTS)

### 2.5 Frontend Web (`frontend/`)

Interface React.js pour :
- Visualisation du contexte (personnes/objets détectés)
- Chat avec l'IA Bastet
- Contrôle du système (STT on/off, stop)

---

## 3. Fichiers URDF - Qu'est-ce que c'est ?

**URDF (Unified Robot Description Format)** est un format XML standard utilisé par ROS/ROS2 pour décrire un robot :

```xml
<robot name="mon_robot">
  <link name="base_link">
    <visual>
      <geometry>
        <mesh filename="package://mon_robot/meshes/base.stl"/>
      </geometry>
    </visual>
  </link>
  <joint name="camera_joint" type="fixed">
    <parent link="base_link"/>
    <child link="camera_link"/>
  </joint>
</robot>
```

**Utilité :**
- **Simulation** : Permet de tester le robot dans Gazebo/RViz avant construction
- **Cinématique** : Calcul des positions relatives des composants
- **Visualisation** : Affichage 3D du robot en temps réel
- **TF (Transform)** : Gestion des repères de coordonnées entre les pièces

Les fichiers URDF de notre projet décrivent la structure mécanique du robot (châssis, roues, support caméra, etc.) et seront utilisés avec ROS2 pour la navigation et le contrôle.

---

## 4. Avancement et Problèmes Rencontrés

### État Actuel (Janvier 2026)

| Composant | Statut | Progression |
|-----------|--------|-------------|
| Modèles 3D robot | ✅ Terminé | 100% |
| Fichiers URDF | ✅ Terminé | 100% |
| Matériel (pièces) | 🔄 En cours | ~80% |
| Assemblage robot | ❌ Non commencé | 0% |
| Logiciel Vision IA | ✅ Fonctionnel | 90% |
| Intégration MyGES | ✅ Fonctionnel | 100% |
| Frontend Web | ✅ Fonctionnel | 85% |
| Intégration ROS2 | ❌ En attente | 0% |

### Problèmes Rencontrés

1. **Latence IA** : Le modèle 12B initial était trop lent. Migration vers LM Studio avec streaming résolue.

2. **Reconnaissance faciale instable** : Problème de scintillement corrigé par persistance temporelle.

3. **Authentification MyGES** : API non documentée, reverse-engineering nécessaire pour l'OAuth2 implicite.

4. **Audio/TTS** : Conflits pygame avec les threads, résolu par initialisation séquencée.

### Fonctionnalités Futures (Non implémentées)

- [ ] Positionnement par rapport au campus (SLAM/GPS)
- [ ] Reconnaissance d'objets spécifiques au contexte scolaire
- [ ] Communication vocale bidirectionnelle améliorée
- [ ] Intégration ROS2 complète (navigation, contrôle moteur)

---

## 5. Planning Projet Actualisé

### Phase 1 : Logiciel (Octobre 2025 - Janvier 2026) ✅
- [x] Développement du système de vision
- [x] Intégration de l'IA conversationnelle
- [x] Connexion à MyGES
- [x] Interface web fonctionnelle

### Phase 2 : Hardware (Janvier - Mars 2026) 🔄
- [ ] Réception des dernières pièces
- [ ] Assemblage mécanique du robot
- [ ] Installation de l'électronique (Raspberry Pi / PC embarqué)
- [ ] Tests caméra + streaming RTSP

### Phase 3 : Intégration ROS2 (Mars - Mai 2026)
- [ ] Configuration de l'environnement ROS2
- [ ] Import des fichiers URDF
- [ ] Développement des nœuds de navigation
- [ ] Intégration du module vision dans ROS2

### Phase 4 : Tests Campus (Mai - Juin 2026)
- [ ] Déploiement sur le campus
- [ ] Tests utilisateurs
- [ ] Optimisation et corrections

---

## 6. Stack Technique

| Catégorie | Technologies |
|-----------|--------------|
| **Vision** | YOLOv8, OpenCV, face_recognition, MediaPipe |
| **IA** | LLaMA-cpp, LM Studio, Modèles GGUF (Mistral 7B) |
| **Backend** | Python 3.10+, FastAPI, Uvicorn, WebSockets |
| **Frontend** | React.js, Vite, WebSocket API |
| **Robotique** | ROS2 (Humble/Iron), URDF, Gazebo |
| **Streaming** | RTSP, FFmpeg, GStreamer |
| **Auth** | OAuth2 (MyGES/Kordis), Keyring |

---

*Document généré le 12 janvier 2026*
