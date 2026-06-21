import os, subprocess

def is_device_free(device):
    if not os.path.exists(device):
        return False
    try:
        r = subprocess.run(["fuser", device], capture_output=True, text=True)
        return r.returncode != 0
    except:
        return True

with open("/opt/spotbot/agent.py", "r") as f:
    content = f.read()

old = '    device = "/dev/video0" if cam_id == 1 else "/dev/video2"'
new = '''    if cam_id == 1:
        if is_device_free("/dev/video0"):
            device = "/dev/video0"
        elif is_device_free("/dev/video1"):
            device = "/dev/video1"
        else:
            print(f"[Agent] Cam {cam_id}: video0 et video1 indisponibles.")
            return
    else:
        device = None
        for d in ["/dev/video0", "/dev/video1"]:
            if is_device_free(d):
                device = d
                break
        if device is None:
            print(f"[Agent] Cam {cam_id}: Aucun device disponible.")
            return'''

fn_def = "def is_device_free(device):\n"
if fn_def not in content:
    insert_before = "def start_camera_stream"
    func_code = '''def is_device_free(device: str) -> bool:
    """Verifie qu un peripherique video existe et n est pas verrouille."""
    if not os.path.exists(device):
        return False
    try:
        result = subprocess.run(["fuser", device], capture_output=True, text=True)
        return result.returncode != 0
    except Exception:
        return True

'''
    content = content.replace(insert_before, func_code + insert_before)
    print("is_device_free ajoutee")

if old in content:
    content = content.replace(old, new)
    with open("/opt/spotbot/agent.py", "w") as f:
        f.write(content)
    print("PATCHED OK")
else:
    print("Deja patche ou pattern introuvable")
    print("Pattern cherche:", repr(old[:60]))
