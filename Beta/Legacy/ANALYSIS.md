# Analyse du Projet CORE (Bastet V2)

Ce document présente une analyse technique du projet CORE, une architecture distribuée pour le robot Bastet.

## 1. Architecture

### 1.1 Modèle Distribué Client-Serveur
Le projet a évolué vers une architecture modulaire où les capacités de calcul intensives sont déportées.
- **Serveur (CORE)** : Héberge l'intelligence, la vision (YOLO/Face Rec), et les intégrations (MyGes, Agenda). Il expose une API REST et WebSocket.
- **Client (Robot Bastet)** : (Futur/En cours) Le robot agit comme une interface physique (caméra, micro, haut-parleur) qui communique avec le CORE via réseau.
- **Interface Web** : Un dashboard React pour le monitoring et le contrôle (TTS, STT, Vision).

### 1.2 Modularité
Le système est conçu autour d'un `SystemState` partagé et de modules indépendants :
- **Vision** : Détection d'objets (YOLO) et reconnaissance faciale (`face_recognition`).
- **AI Agent** : Cerveau central, compatible avec LLM local ou distant (LM Studio).
- **Intégrations** :
    - *MyGes* : Récupération d'agenda et notes via API.
    - *Support* : (À implémenter) Base de connaissance pour les questions fréquentes.

### 1.3 Flux de Données
1. **Perception** : La caméra capture les images -> `VisionSystem`.
2. **Contexte** : `VisionSystem` met à jour `shared_data` (visages, objets). `MyGesIntegration` injecte l'agenda.
3. **Cognition** : `AIAgent` reçoit l'input utilisateur + contexte visuel/agenda -> Génère une réponse.
4. **Action** : Réponse envoyée via WebSocket (texte) et TTS (audio).

## 2. Optimisation

### 2.1 Points Forts
- **Asynchronisme** : Utilisation de `FastAPI` et `asyncio` pour le serveur web est un excellent choix pour gérer les E/S (WebSocket, appels API).
- **Gestion des Threads** : Les modules lourds (Vision, AI) tournent dans des threads séparés (`threading.Thread`), évitant de bloquer la boucle d'événement principale.
- **Déport de Calcul** : L'option d'utiliser LM Studio sur une machine distante permet d'alléger la charge du CORE.

### 2.2 Pistes d'Amélioration
- **Modèles Vision** : `face_recognition` est CPU-intensive. Sur un environnement contraint sans GPU, cela peut ralentir la boucle principale. L'utilisation d'un TPU (Coral) ou d'un modèle plus léger (MobileFaceNet) serait bénéfique.
- **Latence Réseau** : Le flux vidéo brut n'est pas encore transmis du robot au serveur dans le code actuel (la caméra est locale `cv2.VideoCapture(0)`). Pour une vraie déportation, implémenter un streaming RTP/WebRTC sera crucial.
- **Docker** : L'image Docker devra minimiser la taille. Utiliser `python:slim` et multi-stage builds.

## 3. Sécurité

### 3.1 Authentification et Données Sensibles
- **Actuel** : Les identifiants MyGes sont stockés via `keyring` (bon pour local) ou `user_config.txt` (moins sécurisé si en clair).
- **Risque** : L'API `GET /api/settings` expose l'état du système mais pas de secrets. Cependant, il n'y a pas d'authentification sur l'API WebSocket ou REST. N'importe qui sur le réseau local peut contrôler le robot.
- **Recommandation** :
    - Ajouter une clé API ou Basic Auth pour les endpoints critiques.
    - Ne jamais logger les mots de passe (le code semble déjà faire attention via `keyring`).

### 3.2 Flux de Données
- Le flux vidéo et les conversations sont potentiellement sensibles. Si le robot est sur un réseau public (école), il faut chiffrer les communications (HTTPS/WSS) et sécuriser l'accès.

## Conclusion
Le projet CORE V2 repose sur une base solide et moderne (FastAPI, React, Modularité). La transition vers une architecture totalement déportée nécessitera une attention particulière à la latence vidéo et à la sécurité des communications réseau.
