# 🐕 CONTEXTE DE L'ARCHITECTURE DOUBLE RASPBERRY PI & INSTRUCTIONS

Ce document détaille l'architecture double Raspberry Pi du projet Bastet et sert de prompt pour la prochaine IA.

---

## 1. 🏗️ ARCHITECTURE DOUBLE RASPBERRY PI

Le projet utilise **deux Raspberry Pis distincts** pour séparer les tâches embarquées physiques des tâches de serveur/passerelle :

### 🟢 RASPBERRY PI 1 : LE ROBOT (Raspberry Pi 5)
* **Rôle** : Contrôle physique du robot, cinématique, lecture des capteurs IMU/tension, envoi des flux vidéo, et communication série avec le microcontrôleur Arduino Mega 2560.
* **OS / Logiciels** : Debian 13 (Trixie), ROS 2 Jazzy Jalisco compilé depuis les sources.
* **Processus principal** : Un agent en arrière-plan (`agent.py`) s'exécutant via le service systemd `spotbot-agent.service` en tant que `root`.
* **Identifiants & Accès** :
  * **IP Locale** : `192.168.1.156` (Ethernet local).
  * **Accès SSH** : `bastet` / `bastet` (Port 22).
  * **WiFi (`wlan0`)** : Géré par le service `wpa_supplicant@wlan0.service` (ou via NetworkManager en cas de conflit). Actuellement configuré pour se connecter à la `Freebox-5E6B8A`.

### 🔵 RASPBERRY PI 2 : LA GATEWAY (Raspberry Pi faisant office de Serveur)
* **Rôle** : Héberge le serveur central de coordination, l'interface graphique (Dashboard), et le serveur de diffusion vidéo (MediaMTX).
* **OS / Logiciels** : Linux, Docker, Docker Compose, Caddy (reverse proxy).
* **Adresse Publique** : `ha.arthonetwork.fr` (IP publique : `82.67.220.37`).
* **Accès SSH** : `tealo` (via la clé SSH privée `id_ed25519_openclaw` sur Windows).
* **Services principaux (conteneurs Docker)** :
  * **`bastet-face-server`** : Le backend FastAPI (`main.py`) qui écoute sur le port interne `44888` (mappé sur `127.0.0.1:44887` sur l'hôte, et exposé publiquement via Caddy sur `44888`).
  * **`caddy-proxy`** : Gère les certificats SSL et redirige les requêtes.
  * **`bastet-rtsp-proxy` (MediaMTX)** : Reçoit les flux vidéo RTSP envoyés par le Robot Pi et les distribue au Dashboard.

---

## 2. 🔌 PROTOCOLES DE COMMUNICATION & SÉCURITÉ

1. **WebSocket Temps Réel** :
   * Le Robot Pi 1 (`agent.py`) se connecte en sortant vers la Gateway du Pi 2 :
     `wss://ha.arthonetwork.fr:44888/ws/robot?token=bst_c9f28d3a1e4b85c7f0d4b9a2e6f1c3d5`
   * Ce canal sert à transmettre la télémétrie en temps réel et à recevoir les commandes instantanées (démarrage, flash Arduino, mouvements, WiFi).
2. **Tunnel SSH Inverse (autossh)** :
   * Le Robot Pi 1 lance `bastet-tunnel.service` qui se connecte au Pi 2 (`tealo@82.67.220.37`) via SSH.
   * Il redirige le port distant **`49022`** du Pi 2 vers le port local `22` du Robot Pi 1.
   * Cela permet de se connecter au Robot Pi 1 depuis le Pi 2 avec : `ssh -p 49022 bastet@localhost`.
3. **Jetons de sécurité (X-API-Token)** :
   * Le token d'API est : `bst_c9f28d3a1e4b85c7f0d4b9a2e6f1c3d5`
   * Toutes les requêtes HTTP (ex. progrès d'update, requêtes de statut) doivent l'inclure.

---

## 3. 📶 LE PROBLÈME WIFI A RÉSOUDRE

Le WiFi (`wlan0`) du Robot Pi 1 présente deux bugs majeurs :
1. **Pas d'auto-connexion au démarrage** : Le robot est resté allumé pendant un moment mais ne s'est pas associé automatiquement au WiFi configuré (`Freebox-5E6B8A`), obligeant l'utilisateur à le brancher en Ethernet (`192.168.1.156`).
2. **Scan WiFi infini** : Cliquer sur "Rafraîchir" dans l'interface Gateway pour scanner les WiFi environnants tourne en boucle sans s'arrêter.

### Tâche pour l'IA :
* Résoudre le conflit potentiel sur le Robot Pi 1 entre **NetworkManager** (installé par défaut sur Debian 13) et la gestion manuelle via `wpa_supplicant@wlan0.service`.
* Sécuriser la fonction `get_wifi_list()` de `CORE/agent.py` (qui lance `sudo iwlist wlan0 scan`). Si l'interface `wlan0` est verrouillée ou occupée par un autre gestionnaire, la commande ne doit pas bloquer le thread de réception WebSocket de l'agent. Si elle échoue ou expire, elle doit renvoyer une réponse d'échec propre à la Gateway afin d'arrêter l'animation de chargement sur l'interface.
