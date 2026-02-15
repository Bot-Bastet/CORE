import socket
import threading
import time
import json
import requests
import uuid

class DiscoveryManager:
    def __init__(self, role, port=8000, remote_server_url=None):
        self.role = role
        self.port = port
        self.node_id = f"{role}-{str(uuid.uuid4())[:8]}"
        self.remote_server_url = remote_server_url
        
        self.running = True
        self.found_peers = {} # {id: {ip, port, role, last_seen}}
        
        # UDP Config
        self.broadcast_port = 37020
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.settimeout(0.2)
        self.sock.bind(("", self.broadcast_port))

    def start(self):
        """Lance les threads de découverte (Broadcast + Remote)."""
        threading.Thread(target=self._broadcast_loop, daemon=True).start()
        threading.Thread(target=self._listen_loop, daemon=True).start()
        if self.remote_server_url:
            threading.Thread(target=self._remote_registration_loop, daemon=True).start()
            
    def get_hub_ip(self):
        """Cherche un Hub parmi les pairs découverts."""
        # 1. Local Broadcast
        for pid, peer in self.found_peers.items():
            if peer.get("role") == "hub":
                return peer.get("ip")
        
        # 2. Remote Registry (Fallback)
        if self.remote_server_url:
            try:
                resp = requests.get(f"{self.remote_server_url}/peers", timeout=2)
                if resp.status_code == 200:
                    peers = resp.json()
                    for pid, peer in peers.items():
                        if peer.get("role") == "hub":
                            # TODO: Gérer NAT/Public IP si différent
                            return peer.get("local_ip") 
            except:
                pass
                
        return None

    def _broadcast_loop(self):
        """Envoie périodiquement 'Je suis là'."""
        while self.running:
            msg = json.dumps({
                "id": self.node_id,
                "role": self.role,
                "port": self.port,
                "type": "hello"
            })
            try:
                self.sock.sendto(msg.encode(), ('<broadcast>', self.broadcast_port))
            except:
                pass
            time.sleep(2)

    def _listen_loop(self):
        """Écoute les 'Je suis là' des autres."""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                msg = json.loads(data)
                
                if msg.get("id") == self.node_id:
                    continue
                    
                self.found_peers[msg["id"]] = {
                    "ip": addr[0],
                    "port": msg.get("port", 8000),
                    "role": msg.get("role", "unknown"),
                    "last_seen": time.time()
                }
                # print(f"🔍 Peer Discovered (UDP): {msg['role']} at {addr[0]}")
            except socket.timeout:
                pass
            except Exception as e:
                print(f"Discovery Error: {e}")

    def _remote_registration_loop(self):
        """S'enregistre sur le serveur distant."""
        while self.running:
            try:
                # Trouver IP locale
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                
                requests.post(f"{self.remote_server_url}/register", json={
                    "node_id": self.node_id,
                    "role": self.role,
                    "local_ip": local_ip,
                    "port": self.port
                }, timeout=5)
            except Exception as e:
                # print(f"Remote Registry Error: {e}")
                pass
            time.sleep(10)
