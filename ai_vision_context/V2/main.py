#!/usr/bin/env python3
"""
Bastet AI V2 - Main Entry Point
Lance le serveur FastAPI et tous les sous-systèmes.
"""

import os
import sys
import json

# S'assurer qu'on est dans le bon répertoire
os.chdir(os.path.dirname(os.path.abspath(__file__)))

def check_config():
    """Vérifie si config.json existe, sinon lance le wizard."""
    if not os.path.exists("config.json"):
        print("Configuration non trouvée. Lancement du wizard...")
        import config_wizard
        config_wizard.main()
        
        if not os.path.exists("config.json"):
            print("Configuration annulée. Arrêt.")
            sys.exit(1)
    
    with open("config.json", 'r') as f:
        return json.load(f)


def main():
    print("""
    ██████╗  █████╗ ███████╗████████╗███████╗████████╗
    ██╔══██╗██╔══██╗██╔════╝╚══██╔══╝██╔════╝╚══██╔══╝
    ██████╔╝███████║███████╗   ██║   █████╗     ██║   
    ██╔══██╗██╔══██║╚════██║   ██║   ██╔══╝     ██║   
    ██████╔╝██║  ██║███████║   ██║   ███████╗   ██║   
    ╚═════╝ ╚═╝  ╚═╝╚══════╝   ╚═╝   ╚══════╝   ╚═╝   
                      AI V2.0
    """)
    
    # Vérifier/créer config
    config = check_config()
    print(f"\n✓ Configuration chargée")
    print(f"  • Provider: {config.get('ai_provider')}")
    print(f"  • YOLO: {config.get('yolo_model')}")
    print(f"  • TTS: {'Désactivé' if not config.get('tts_enabled') else 'Activé'}")
    print()
    
    # Lancer le serveur
    print("Démarrage du serveur sur http://localhost:8000")
    print("Interface web sur http://localhost:5173 (après npm run dev)")
    print("\nAppuyez sur Ctrl+C pour arrêter.\n")
    
    from core.server import run_server
    run_server()


if __name__ == "__main__":
    main()
