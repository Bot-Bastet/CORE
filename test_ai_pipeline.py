import socket
import json
import threading
import time
import os
import sys

# Ajouter le repertoire courant au path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.ai_agent import AIAgent

def udp_dummy_listener():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1', 5005))
        sock.settimeout(10.0)
        print("Dummy UDP Listener démarre sur le port 5005...")
        data, _ = sock.recvfrom(1024)
        msg = data.decode('utf-8').strip()
        print(f"!!! MESSAGE UDP REÇU : {msg} !!!")
        with open("test_udp_result.txt", "w") as f:
            f.write(msg)
    except Exception as e:
        print(f"Dummy Listener Erreur: {e}")
    finally:
        sock.close()

def main():
    listener_thread = threading.Thread(target=udp_dummy_listener)
    listener_thread.daemon = True
    listener_thread.start()
    
    time.sleep(1) # Laisse le temps au listener
    
    with open('config.json', 'r') as f:
        config = json.load(f)
        
    agent = AIAgent(config, {})
    agent.load_model()
    
    if not agent.ready:
        print("FAIL: Agent.ready = False")
        sys.exit(1)
        
    print("Generation de la reaction via NVIDIA NIM...")
    prompt = "C'est un test systeme. Reponds tres exactement par la chaine 'Bonjour les amis ! [CMD: avancer]' sans guillemets."
    response = agent.generate_reaction({}, "", prompt)
    
    print(f"Reponse de l'IA apres nettoyage: {response}")
    
    time.sleep(2)
    
    if os.path.exists("test_udp_result.txt"):
        with open("test_udp_result.txt", "r") as f:
            res = f.read()
            if "avancer" in res.lower():
                print("TEST PIPELINE IA -> UDP : SUCCES")
            else:
                print(f"TEST PIPELINE IA -> UDP : ECHEC (Recu: {res})")
    else:
        print("TEST PIPELINE IA -> UDP : ECHEC (Rien recu sur le port 5005)")

if __name__ == "__main__":
    main()
