import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from geometry_msgs.msg import Twist
import asyncio
import websockets
import json
import threading
import time
import os

# Configuration Hub (peut être trouvée via DiscoveryManager, ici simplifié pour l'exemple)
HUB_URI = os.environ.get("HUB_URI", "ws://192.168.1.100:8000/ws/node/robot")
ROBOT_ID = "bastet_bot_v1"

class RobotBridge(Node):
    def __init__(self):
        super().__init__('core_bridge')
        self.publisher = self.create_publisher(String, 'robot_status', 10)
        self.subscription = self.create_subscription(Twist, 'cmd_vel', self.cmd_vel_callback, 10)
        
        # État interne
        self.battery_level = 100
        
        # Démarrer la boucle WebSocket dans un thread séparé
        self.ws_thread = threading.Thread(target=self.start_ws_loop, daemon=True)
        self.ws_thread.start()

    def cmd_vel_callback(self, msg):
        # Mouvement reçu de ROS (ex: navigation autonome) -> Envoyer notif au Core ?
        pass

    def start_ws_loop(self):
        asyncio.run(self.connect_to_hub())

    async def connect_to_hub(self):
        while True:
            try:
                async with websockets.connect(HUB_URI) as websocket:
                    self.get_logger().info(f"Connecté au HUB Core: {HUB_URI}")
                    
                    while True:
                        # Écouter les commandes venant du Core (ex: "Avance", "Stop")
                        message = await websocket.recv()
                        data = json.loads(message)
                        
                        if data.get("type") == "command":
                            cmd = data.get("payload", {})
                            self.execute_command(cmd)
                            
            except Exception as e:
                self.get_logger().warn(f"Connexion Hub perdue: {e}")
                await asyncio.sleep(5)

    def execute_command(self, cmd):
        action = cmd.get("action")
        self.get_logger().info(f"Commande reçue du Core: {action}")
        
        # Traduction Core -> ROS Twist
        twist = Twist()
        if action == "move_forward":
            twist.linear.x = 0.5
        elif action == "turn_left":
            twist.angular.z = 0.5
        # ... publier sur cmd_vel (si on avait un publisher cmd_vel, ici on a juste un sub pour l'exemple inverse)
        # En réalité, le bridge doit PUBLISH sur cmd_vel pour bouger le robot

def main(args=None):
    rclpy.init(args=args)
    bridge = RobotBridge()
    rclpy.spin(bridge)
    bridge.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
