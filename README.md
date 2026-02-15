# CORE (Central Operating Recognition Engine) - Bastet V2

**CORE** est le système nerveux central et distribué du robot **Bastet**.
Il permet de déporter l'intelligence (LLM, Vision) sur des machines puissantes tout en contrôlant le robot à distance, avec une connectivité résiliente (Local/4G).

## 🌍 Architecture Hybride "Grid"

Le système est agnostique au réseau. Il fonctionne aussi bien en **LAN (WiFi sans internet)** qu'en **4G (via Relais)**.

### 1. Les Nœuds du Système
- **Hub (Cerveau Central)** : Orchestre tout. Héberge l'état du monde et l'interface Web.
- **Vision Node (Yeux)** : Gère la caméra et l'analyse d'image (YOLO/FaceID). Déporté sur PC avec GPU.
- **LLM Node (Conscience)** : Gère le modèle de langage (Llama 3, Mistral). Déporté sur un autre PC puissant.
- **Robot Node (Corps)** : Le robot physique (ROS 2). Gère les moteurs, le micro et les haut-parleurs.

### 2. Connectivité Intelligente
Les nœuds se trouvent automatiquement grâce au **DiscoveryManager** :
1.  **UDP Broadcast (Priorité 1)** : Si les appareils sont sur le même WiFi, ils se parlent directement (Latence < 5ms).
2.  **Registre Distant (Priorité 2)** : Si le broadcast échoue, ils demandent au `Remote Server` l'IP locale de chacun.
3.  **Relais WebSocket (Secours 4G)** : Si aucune connexion directe n'est possible (NAT, 4G vs WiFi), tout transite par le serveur distant (Latence ~100ms).

---

## 🚀 Installation et Lancement

### 1. Lancer le Hub (Chef d'Orchestre)
Sur la machine principale (ex: ton PC ou le Robot si puissant) :
```bash
python main.py --role hub
```

### 2. Lancer un Nœud de Calcul
Vous pouvez désormais lancer chaque service sur une machine dédiée :

**Vision (Yeux)** :
```bash
python main.py --role vision --hub-ip auto
```
**Intelligence (Cerveau)** :
```bash
python main.py --role llm --hub-ip auto
```
**Parole (STT)** :
```bash
python main.py --role stt --hub-ip auto
```
**Synthèse (TTS)** :
```bash
python main.py --role tts --hub-ip auto
```

### 3. Lancer le Robot (ROS 2)
Sur la Raspberry Pi / Jetson du robot :
```bash
cd robot
docker-compose up --build
```
*Le robot va automatiquement chercher le Hub et s'y connecter.*

### 4. Serveur Relais (Cloud)
Adresse officielle : **bastet.arthonetwork.fr**
Le code du dossier `remote_server` est déployé sur ce domaine pour gérer les connexions 4G.
(Configuré dans `config.json` via `remote_server_url`).

---

## 📂 Structure du Projet

- **`core/`** : Logique métier (Vision, IA, Serveur, Client WebSocket).
- **`robot/`** : Environnement complet pour le robot (Docker ROS 2 + Bridge Python).
- **`remote_server/`** : Code du serveur relais (à héberger sur le Cloud).
- **`web/`** : Interface de contrôle React.

## ✨ Fonctionnalités Avancées
- **Auto-Switch Réseau** : Le robot peut passer de WiFi à 4G sans redémarrer le Core, grâce à la reconnexion automatique du `NodeClient`.
- **Déport de Calcul Dynamique** : Vous pouvez lancer autant de nœuds "Vision" que vous voulez (ex: plusieurs caméras) ou changer de machine pour le LLM à la volée.

## 🛡️ Sécurité & Cloud (Bastet Vault)

Le système intègre un coffre-fort numérique hébergé sur le **Serveur Relais**.

### 1. Synchronisation des Visages
Au démarrage, le **Nœud Vision** télécharge les visages connus depuis le serveur distant.
*   API : `GET /vault/faces`

### 2. Scraping Sécurisé (MyGES)
Les identifiants Intranet ne transitent **jamais** vers le LLM.
1.  Vous stockez vos identifiants (chiffrés) sur le Serveur Relais.
2.  Quand le Robot vous reconnaît, le Hub demande au Serveur de récupérer votre agenda/notes.
3.  Le Serveur renvoie uniquement les **données** (pas le mot de passe).

#### Enregistrer ses identifiants
```bash
curl -X POST "http://bastet.arthonetwork.fr:8000/vault/credentials" \
     -H "Content-Type: application/json" \
     -d '{"username": "Teano", "password": "MonMotDePasse"}'
```

---

## 🛡️ Configuration Réseau & Ports

Pour que les machines communiquent librement, ouvrez les ports suivants dans vos pare-feu (Windows Defender / UFW) :

### 1. Sur le PC "Hub" (Serveur Central)
- **TCP 8000** : API HTTP & WebSocket (Communication avec tous les nœuds).
- **UDP 37020** : Discovery (Pour que les nœuds trouvent le Hub automatiquement).

### 2. Sur le VPS "Serveur Relais" (bastet.arthonetwork.fr)
- **TCP 8000** : Relais HTTP & WebSocket.

### 3. Sur TOUTES les machines (Robot, Vision, LLM)
- **Sortant** : Autoriser tout le trafic sortant.
- **WebRTC** : Autoriser le trafic **UDP** entrant/sortant sur les ports dynamiques (ou désactiver le pare-feu sur le réseau local de confiance) pour la vidéo en temps réel.

> **Note** : En mode 4G (Relais), seul le port 8000 sortant vers le VPS est nécessaire.

---
*Projet Bastet V2 - Développé par Teano & Gemini*
