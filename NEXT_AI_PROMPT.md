# 🐕 CONTEXTE & INSTRUCTIONS POUR LA PROCHAINE IA

Ce document sert de prompt d'initialisation et de guide de travail pour résoudre les problèmes restants sur le robot Bastet (SpotBot).

---

## 1. 🏗️ ARCHITECTURE & CONFIGURATION DU PROJET

Le projet est composé de trois sous-systèmes principaux :
1. **La Gateway (CORE-Gateway)** :
   * Serveur FastAPI (`main.py`) s'exécutant sur une machine distante (VM Docker sur l'hôte `ha.arthonetwork.fr` / `82.67.220.37`).
   * Gère le dashboard utilisateur et coordonne les commandes du robot.
2. **Le CPU du Robot (Pi 5)** :
   * Exécute un agent en arrière-plan (`agent.py`) via le service systemd `spotbot-agent.service` (lancé en tant que `root`).
   * L'agent communique avec la Gateway via WebSocket (`wss://ha.arthonetwork.fr:44888/ws/robot?token=...`).
3. **Le Microcontrôleur (Arduino Mega 2560)** :
   * Connecté en USB au Pi, il reçoit les commandes de cinématique des moteurs et renvoie la télémétrie capteurs (imu, tension...).

### 🔑 Identifiants & Connexions
* **IP Locale du Robot** : `192.168.1.156` (connecté actuellement en Ethernet).
* **Utilisateur SSH** : `bastet`
* **Mot de passe** : `bastet`
* **Port SSH** : `22` (local)
* **Tunnel SSH Inverse (autossh)** :
  * Le service `bastet-tunnel.service` tourne sur le robot et se connecte à la Gateway `82.67.220.37` (nouvelle IP publique).
  * Il mappe le port distant **`49022`** de la Gateway vers le port local `22` du Pi.
  * Commande de connexion depuis le serveur Gateway : `ssh -p 49022 bastet@localhost`.
* **Token d'authentification Gateway** :
  ```text
  bst_c9f28d3a1e4b85c7f0d4b9a2e6f1c3d5
  ```
  (Doit être fourni dans l'en-tête `X-API-Token` de toutes les requêtes REST de l'updater ou de l'agent).

---

## 2. 📶 ÉTAT ACTUEL DU SYSTÈME WIFI (A CORRIGER)

Le système d'auto-connexion WiFi et de scan présente des instabilités majeures :
1. **Échec d'auto-connexion** : Après que le robot a démarré ou tourné pendant un certain temps, il ne se connecte pas automatiquement au WiFi ciblé (`Freebox-5E6B8A`).
2. **Chargement infini du Scan** : Dans l'interface Gateway, lorsqu'on clique sur "Rafraîchir" pour scanner les WiFi, le chargement tourne à l'infini.

### Pistes d'investigation pour l'IA :
* **Service WiFi actif** : Nous avons configuré `wpa_supplicant@wlan0.service` pour utiliser `/etc/wpa_supplicant/wpa_supplicant-wlan0.conf`.
* **Conflits système** : Le Pi tourne sous Debian 13 (Trixie). Il est très probable que **NetworkManager** soit installé et actif, ce qui entre en conflit avec l'instance manuelle de `wpa_supplicant@wlan0.service` et bloque l'interface `wlan0`.
* **Blocage du scan** : Dans `agent.py`, la fonction `get_wifi_list()` exécute `sudo iwlist wlan0 scan`. Si `wlan0` est verrouillé par NetworkManager ou un autre processus, ou si l'interface est désactivée, cette commande peut expirer, lever une exception ou bloquer le thread de réception WebSocket, causant le chargement infini sur l'interface Gateway.

---

## 3. 🎯 MISSION DE L'IA

Votre tâche est de résoudre définitivement ces problèmes de WiFi :
1. **Établir un diagnostic propre de la configuration réseau sur le Pi 5** :
   * Vérifier si NetworkManager est actif (`systemctl is-active NetworkManager`).
   * Configurer le système pour utiliser soit uniquement NetworkManager (via des commandes `nmcli` dans l'agent), soit désactiver NetworkManager pour laisser le contrôle à `wpa_supplicant@wlan0`.
2. **Corriger l'auto-connexion** :
   * Veiller à ce que l'interface `wlan0` s'associe automatiquement au SSID configuré (ex. `Freebox-5E6B8A`) au boot et après déconnexion.
3. **Résoudre le chargement infini du scan** :
   * Sécuriser la fonction `get_wifi_list()` dans `agent.py` pour éviter les blocages.
   * Si la commande de scan échoue ou expire, s'assurer qu'un message d'erreur ou une liste vide soit renvoyée proprement à la Gateway via WebSocket, afin d'interrompre l'animation de chargement sur le dashboard.
