"""
WiFi management for Bastet robot (Pi 5).
"""
import os, re, time, subprocess

def get_wifi_list() -> list:
    import re
    try:
        subprocess.run(["sudo", "iwlist", "wlan0", "scan"], capture_output=True, text=True, timeout=10)
        res = subprocess.run(["sudo", "iwlist", "wlan0", "scan"], capture_output=True, text=True, timeout=10)
        networks = []; current_network = {}
        for line in res.stdout.split('\n'):
            line = line.strip()
            if not line: continue
            cell_match = re.search(r'Cell \d+ - Address: ([0-9A-Fa-f:]+)', line)
            if cell_match:
                if current_network.get("ssid"): networks.append(current_network)
                current_network = {"bssid": cell_match.group(1), "ssid": "", "signal": 0, "security": "Open"}; continue
            if not current_network: continue
            essid_match = re.search(r'ESSID:"([^"]*)"', line)
            if essid_match: current_network["ssid"] = essid_match.group(1); continue
            signal_match = re.search(r'Quality=(\d+)/(\d+)', line)
            if signal_match: current_network["signal"] = int((int(signal_match.group(1)) / int(signal_match.group(2))) * 100); continue
            enc_match = re.search(r'Encryption key:(on|off)', line)
            if enc_match: current_network["security"] = "Open" if enc_match.group(1) == "off" else "Secured"; continue
            if "WPA2" in line: current_network["security"] = "WPA2"
            elif "WPA" in line and current_network["security"] != "WPA2": current_network["security"] = "WPA"
        if current_network.get("ssid"): networks.append(current_network)
        unique = {}
        for net in networks:
            ssid = net["ssid"]
            if ssid and (ssid not in unique or net["signal"] > unique[ssid]["signal"]): unique[ssid] = net
        return sorted(unique.values(), key=lambda x: x["signal"], reverse=True)
    except Exception as e: print(f"[Agent] Erreur scan wifi: {e}"); return []

def connect_to_wifi(ssid: str, password: str) -> dict:
    try:
        if password and len(password) < 8: return {"status": "error", "message": "Mot de passe trop court."}
        conf_path = "/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"; content = ""
        if os.path.exists(conf_path):
            with open(conf_path, "r") as f: content = f.read()
        ssids = []
        for line in content.splitlines():
            if "=" in line:
                parts = line.split("=", 1)
                if parts[0].strip() == "ssid": ssids.append(parts[1].strip().strip("\"'"))
        if not password and ssid in ssids:
            subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "reconfigure"], check=True)
            res_list = subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "list_networks"], capture_output=True, text=True)
            net_id = None
            for line in res_list.stdout.split("\n"):
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1].strip("\"'") == ssid: net_id = parts[0]; break
            if net_id is not None: subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "select_network", net_id], check=True)
            else: return {"status": "error", "message": f"Reseau '{ssid}' introuvable."}
        else:
            blocks = content.split("network={"); new_blocks = [blocks[0]]
            for block in blocks[1:]:
                brace_idx = block.find("}")
                if brace_idx != -1 and (f'ssid="{ssid}"' in block[:brace_idx] or f"ssid='{ssid}'" in block[:brace_idx]):
                    new_blocks[0] += block[brace_idx:].lstrip("}").lstrip("\n"); continue
                new_blocks.append("network={" + block)
            new_content = "".join(new_blocks).strip() + "\n\n"
            if password: new_network = f'network={{\n\tssid="{ssid}"\n\tpsk="{password}"\n}}\n'
            else: new_network = f'network={{\n\tssid="{ssid}"\n\tkey_mgmt=NONE\n}}\n'
            new_content += new_network
            for p in ["/etc/wpa_supplicant/wpa_supplicant-wlan0.conf", "/etc/wpa_supplicant/wpa_supplicant.conf"]:
                try:
                    with open(p, "w") as f: f.write(new_content)
                    os.chmod(p, 0o600)
                except Exception: pass
            subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "reconfigure"], check=True)
            res_list = subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "list_networks"], capture_output=True, text=True)
            net_id = None
            for line in res_list.stdout.split("\n"):
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1].strip("\"'") == ssid: net_id = parts[0]; break
            if net_id is not None: subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "select_network", net_id], check=True)
        for _ in range(12):
            res = subprocess.run(["ip", "addr", "show", "wlan0"], capture_output=True, text=True)
            if "inet " in res.stdout:
                subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "enable_network", "all"], capture_output=True)
                return {"status": "success", "message": f"Connecte a {ssid}."}
            time.sleep(1)
        subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "enable_network", "all"], capture_output=True)
        return {"status": "error", "message": f"Delai IP depasse pour {ssid}."}
    except Exception as e:
        subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "enable_network", "all"], capture_output=True)
        return {"status": "error", "message": str(e)}

def forget_wifi_network(ssid: str) -> dict:
    try:
        conf_path = "/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"; content = ""
        if os.path.exists(conf_path):
            with open(conf_path, "r") as f: content = f.read()
        blocks = content.split("network={"); new_blocks = [blocks[0]]; removed = False
        for block in blocks[1:]:
            brace_idx = block.find("}")
            if brace_idx != -1 and (f'ssid="{ssid}"' in block[:brace_idx] or f"ssid='{ssid}'" in block[:brace_idx]):
                new_blocks[0] += block[brace_idx:].lstrip("}").lstrip("\n"); removed = True; continue
            new_blocks.append("network={" + block)
        if not removed: return {"status": "error", "message": f"Reseau '{ssid}' non trouve."}
        new_content = "".join(new_blocks).strip() + "\n"
        for p in ["/etc/wpa_supplicant/wpa_supplicant-wlan0.conf", "/etc/wpa_supplicant/wpa_supplicant.conf"]:
            try:
                with open(p, "w") as f: f.write(new_content)
                os.chmod(p, 0o600)
            except Exception: pass
        subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "reconfigure"], check=True)
        subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "enable_network", "all"], capture_output=True)
        return {"status": "success", "message": f"Reseau '{ssid}' oublie."}
    except Exception as e: return {"status": "error", "message": str(e)}
