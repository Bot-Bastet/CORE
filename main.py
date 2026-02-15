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
    import argparse

    parser = argparse.ArgumentParser(description="Bastet AI V2 - CORE")
    parser.add_argument("--role", type=str, default="all", choices=["all", "hub", "vision", "llm", "stt", "tts"], help="Rôle de ce nœud (all, hub, vision, llm, stt, tts)")
    parser.add_argument("--hub-ip", type=str, default="localhost", help="IP du Hub (si role != hub)")
    args = parser.parse_args()

    print("""
    ██████╗  █████╗ ███████╗████████╗███████╗████████╗
    ██╔══██╗██╔══██╗██╔════╝╚══██╔══╝██╔════╝╚══██╔══╝
    ██████╔╝███████║███████╗   ██║   █████╗     ██║   
    ██╔══██╗██╔══██║╚════██║   ██║   ██╔══╝     ██║   
    ██████╔╝██║  ██║███████║   ██║   ███████╗   ██║   
    ╚═════╝ ╚═╝  ╚═╝╚══════╝   ╚═╝   ╚══════╝   ╚═╝   
                      AI V2.0 - Distributed Grid
    """)
    
    # Vérifier/créer config
    config = check_config()
    print(f"\n✓ Configuration chargée")
    print(f"  • Rôle: {args.role.upper()}")
    
    if args.role == "hub" or args.role == "all":
        # Mode Serveur (Hub)
        print("Démarrage du Serveur HUB...")
        from core.server import run_server
        # On passe les arguments au serveur via une variable globale ou modifiée
        # Pour simplifier, on injecte dans os.environ ou on modifie run_server
        os.environ["BASTET_ROLE"] = args.role
        run_server()
        
    else:
        # Mode Nœud (Vision ou LLM)
        print(f"Démarrage du Nœud {args.role.upper()}...")
        print(f"Connexion au Hub: ws://{args.hub_ip}:8000")
        
        from core.node_client import run_node
        run_node(args.role, args.hub_ip, config)


if __name__ == "__main__":
    main()
