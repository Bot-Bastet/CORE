# CORE (Central Operating Recognition Engine)

## Vue d'ensemble
CORE est le "cerveau" logiciel du robot **Bastet**. Il s'agit d'un système modulaire conçu pour être embarqué directement sur le robot ou exécuté sur un serveur distant (système déporté).

L'objectif principal est de fournir une intelligence artificielle capable de :
*   **Compréhension orale** : Recevoir et exécuter des instructions vocales.
*   **Vision par ordinateur** : Reconnaissance d'objets, représentation spatiale et analyse de contexte visuel.
*   **Interaction Contextuelle** : Répondre à des questions sur l'environnement (ex: "Qu'est-ce que je tiens dans ma main ?").
*   **Reconnaissance Faciale** : Identifier les utilisateurs pour personnaliser les interactions (ex: récupérer l'emploi du temps via l'intranet de l'école).

## Architecture Modulaire
Le système est conçu pour la flexibilité :
*   **Mode Embarqué** : Tout le traitement se fait sur le robot (nécessite une puissance de calcul suffisante).
*   **Mode Déporté** : Le robot agit comme un terminal (Micro + Caméra + Haut-parleur) et transmet les données un serveur puissant qui héberge CORE.

## Fonctionnalités Clés
1.  **Reconnaissance d'Objets & Analyse Spatiale** : Utilisation de modèles IA avancés (YOLO, etc.) pour "voir" et comprendre l'environnement.
2.  **Traitement du Langage Naturel (NLP)** : Interprétation des commandes vocales et dialogue avec l'utilisateur.
3.  **Reconnaissance Faciale & Profils** :
    *   Les utilisateurs peuvent uploader leur faciès via une application mobile dédiée.
    *   Une fois identifié, le système peut accéder à des données personnelles sécurisées (agenda, notes, etc.) grâce aux identifiants intranet pré-enregistrés.

## Installation et Lancement

### Prérequis
*   Python 3.8+
*   Webcam (pour la vision)
*   Microphone (pour les commandes vocales)
*   Connexion Internet (pour le mode déporté ou le téléchargement des modèles)

### Installation
1.  Cloner le dépôt :
    ```bash
    git clone https://github.com/Bot-Bastet/CORE.git
    cd CORE
    ```
2.  Installer les dépendances :
    ```bash
    pip install -r requirements.txt
    ```
    *(Note : Assurez-vous d'avoir un fichier `requirements.txt` ou installez manuellement `ultralytics`, `opencv-python`, etc.)*

### Lancement
Pour lancer le module de vision principal :
```bash
python core/yolov8_live.py
```
*(Adaptez la commande selon le point d'entrée principal de votre application)*

## Contribution
Les contributions sont les bienvenues pour améliorer la précision des modèles, ajouter de nouvelles commandes vocales ou optimiser l'architecture déportée.
