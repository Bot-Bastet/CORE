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
  * **IP Locale** : `192.168.1.156` (connecté actuellement via Ethernet).
  * **Accès SSH** : `bastet` / `bastet` (Port 22).
  * **WiFi (`wlan0`)** : Géré par le service `wpa_supplicant@wlan0.service` (ou via NetworkManager en cas de conflit). Configuré pour se connecter à la `Freebox-5E6B8A`.

### 🔵 RASPBERRY PI 2 : LA GATEWAY (Raspberry Pi faisant office de Serveur)
* **Rôle** : Héberge le serveur de coordination, l'interface graphique (Dashboard), et le serveur de diffusion vidéo (MediaMTX).
* **OS / Logiciels** : Linux, Docker, Docker Compose, Caddy (reverse proxy).
* **Adresse Publique** : `ha.arthonetwork.fr` (IP publique : `82.67.220.37`).
* **Accès SSH** : `tealo` (via la clé SSH privée `id_ed25519_openclaw` sur Windows).
* **Services principaux (conteneurs Docker)** :
  * **`bastet-face-server`** : Le backend FastAPI (`main.py`) qui écoute sur le port interne `44888` (mappé sur `127.0.0.1:44887` sur l'hôte, et exposé publiquement via Caddy sur `44888`).
  * **`caddy-proxy`** : Gère les certificats SSL et redirige les requêtes.
  * **`Mediamtx`** : Reçoit les flux vidéo RTSP envoyés par le Robot Pi 1 et les distribue au Dashboard.

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

---

## 3. 📶 LES BUGS WIFI & MISE A JOUR A RÉSOUDRE

Le système d'auto-connexion WiFi, de scan et de mise à jour présente les symptômes suivants :
1. **Échec d'auto-connexion** : Le robot est resté allumé pendant un moment mais ne s'est pas associé automatiquement au WiFi configuré (`Freebox-5E6B8A`), ce qui a forcé l'utilisateur à le brancher en Ethernet (`192.168.1.156`).
2. **Scan WiFi infini** : Cliquer sur "Rafraîchir" dans l'interface Gateway pour scanner les WiFi environnants tourne en boucle sans s'arrêter.
3. **Mise à jour figée à 0%** : Cliquer sur "Lancer la mise à jour Robot" affiche `Progression 0%` et se fige immédiatement.

### Explications & Pistes techniques :
* **Le freeze à 0%** : Le bouton de mise à jour envoie le message `trigger_update` via le WebSocket. Cependant, le Robot Pi 1 n'étant pas connecté au WiFi et l'agent n'ayant pas réussi à ouvrir sa session WebSocket avec la Gateway (la session est close côté serveur), la commande de mise à jour n'est jamais reçue par le robot. Le Dashboard reste donc bloqué à `starting / 0%`.
* **Le scan infini** : Dans `agent.py`, la fonction `get_wifi_list()` exécute `sudo iwlist wlan0 scan`. Si `wlan0` est verrouillé par un conflit de services (par exemple, NetworkManager vs `wpa_supplicant`), la commande peut expirer ou bloquer. L'agent doit impérativement intercepter les exceptions/timeouts et renvoyer une réponse (même vide) à la Gateway pour débloquer l'interface.
* **Le conflit WiFi** : Sur Debian 13 (Trixie), NetworkManager gère par défaut les interfaces réseau. L'activation manuelle de `wpa_supplicant@wlan0.service` crée un conflit d'accès à l'interface `wlan0`. Il faut nettoyer cette configuration pour qu'un seul gestionnaire (soit NetworkManager via `nmcli` dans l'agent, soit `wpa_supplicant` pur) pilote la connexion WiFi de façon stable et persistance au démarrage.

---

## 4. 🎯 MISSION DE LA PROCHAINE IA

1. Connectez-vous en SSH directement au Robot Pi 1 depuis le PC utilisateur (`ssh bastet@192.168.1.156`, mot de passe `bastet`).
2. Diagnostiquez l'état du WiFi (`ip link`, `nmcli device`, `systemctl status NetworkManager`, etc.) pour identifier et corriger les conflits de services.
3. Rétablissez la connexion WiFi automatique et stable à `Freebox-5E6B8A`.
4. Assurez-vous que l'agent se connecte avec succès au WebSocket de la Gateway pour rétablir la communication temps réel.
5. Sécurisez la fonction de scan WiFi dans `agent.py` pour qu'elle renvoie toujours un résultat (même vide en cas d'erreur) afin d'éviter les chargements infinis dans le Dashboard.
