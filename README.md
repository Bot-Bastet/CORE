# CORE (Central Operating Recognition Engine)

**CORE** est le "cerveau" logiciel du robot **Bastet**, désormais basé sur la **Version 2 (V2)** de notre architecture de vision et d'IA contextuelle.

## Structure du Projet

*   **/** (Racine) : Contient la version actuelle et principale du projet (**V2**). C'est ici que réside le code actif pour le robot Bastet.
    *   `core/` : Moteur de reconnaissance et logique principale.
    *   `web/` : Interface web de gestion.
    *   `main.py`, `config_wizard.py` : Scripts de lancement et configuration.
*   **`V1/`** : Contient l'ancienne version (**V1**) du contexte de vision IA, conservée pour référence.
*   **`Beta/`** : Archives du développement initial (anciennement "detection crane" / "perception-engine").

## Installation et Lancement (V2)

### Prérequis
*   Python 3.8+
*   Dépendances listées dans `requirements.txt`

### Démarrage
Pour lancer l'application principale (V2) :
```bash
python main.py
```
Ou utilisez les scripts de démarrage rapide :
*   `start_bastet.bat` (Windows)
*   `start.ps1` (PowerShell)

## Fonctionnalités (V2)
Cette version intègre :
*   Reconnaissance d'objets et faciale avancée.
*   Contexte visuel pour les interactions IA (LLM).
*   Interface Web pour le monitoring et la configuration.
*   Système modulaire pour le déploiement sur le robot Bastet.
