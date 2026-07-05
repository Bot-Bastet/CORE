"""
Camera device management for Bastet robot (Pi 5).
"""
import os, json, subprocess, time, urllib.request
from pathlib import Path
import state
from state import (
    GATEWAY_URL, API_TOKEN, ssl_ctx,
    CAMERA_MAPPING_FILE, CALIB_STATUS_FILE,
    CAMERA_CALIB_LEFT_FILE, CAMERA_CALIB_RIGHT_FILE, CAMERA_CALIB_MONO_FILE,
)

def get_camera_devices() -> dict:
    default_mapping = {1: {"device": "/dev/video0", "fingerprint": None}, 2: {"device": "/dev/video2", "fingerprint": None}}
    if CAMERA_MAPPING_FILE.exists():
        try:
            data = json.loads(CAMERA_MAPPING_FILE.read_text())
            left = data.get("left"); right = data.get("right")
            if left: default_mapping[1] = left if isinstance(left, dict) else {"device": left, "fingerprint": None}
            if right: default_mapping[2] = right if isinstance(right, dict) else {"device": right, "fingerprint": None}
        except Exception: pass
    for cam_id in [1, 2]:
        dev_info = default_mapping[cam_id]
        if isinstance(dev_info, dict):
            if not dev_info.get("fingerprint"): dev_info["fingerprint"] = get_camera_fingerprint(dev_info["device"])
        else: default_mapping[cam_id] = {"device": dev_info, "fingerprint": get_camera_fingerprint(dev_info)}
    return default_mapping

def get_camera_fingerprint(device: str) -> str:
    try:
        r = subprocess.run(["udevadm", "info", "--query=property", "--name="+device], capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            props = {}
            for line in r.stdout.splitlines():
                if '=' in line: k, v = line.split('=', 1); props[k] = v
            serial = props.get('ID_SERIAL_SHORT', '')
            if serial: return serial
            vid = props.get('ID_VENDOR_ID', ''); pid = props.get('ID_MODEL_ID', '')
            if vid and pid: return f'{vid}:{pid}'
            devpath = props.get('DEVPATH', '')
            if devpath: return devpath
    except Exception: pass
    return device

def get_calibration_status() -> dict:
    default = {1: {"calibrated": False, "fingerprint": None}, 2: {"calibrated": False, "fingerprint": None}}
    if CALIB_STATUS_FILE.exists():
        try:
            data = json.loads(CALIB_STATUS_FILE.read_text())
            for cam_id in [1, 2]:
                if str(cam_id) in data: default[cam_id] = data[str(cam_id)]
                elif cam_id in data: default[cam_id] = data[cam_id]
        except Exception: pass
    return default

def save_calibration_status(cam_id: int, calibrated: bool, fingerprint: str = None):
    status = get_calibration_status()
    status[cam_id] = {"calibrated": calibrated, "fingerprint": fingerprint}
    try:
        CALIB_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CALIB_STATUS_FILE.write_text(json.dumps({str(k): v for k, v in status.items()}))
    except Exception as e: print(f"[Agent] Erreur sauvegarde calib_status: {e}")

def detect_camera_change() -> dict:
    status = get_calibration_status(); mapping = get_camera_devices(); result = {}
    for cam_id in [1, 2]:
        dev_info = mapping.get(cam_id, {})
        current_fp = dev_info.get("fingerprint") if isinstance(dev_info, dict) else None
        saved_fp = status.get(cam_id, {}).get("fingerprint")
        changed = False
        if current_fp and saved_fp and current_fp != saved_fp:
            changed = True; save_calibration_status(cam_id, False, current_fp)
            print(f"[Agent] Camera {cam_id} changed. Calibration invalidated.")
        elif current_fp and not saved_fp:
            save_calibration_status(cam_id, status.get(cam_id, {}).get("calibrated", False), current_fp)
        result[cam_id] = changed
    return result

def get_active_video_devices() -> list:
    fallback = ['/dev/video0', '/dev/video2']; out = set()
    try:
        if os.path.isdir('/dev/v4l/by-id'):
            for entry in os.listdir('/dev/v4l/by-id'):
                if not entry.startswith('usb-'): continue
                if 'index1' in entry or 'metadata' in entry: continue
                link = os.path.join('/dev/v4l/by-id', entry)
                try:
                    real = os.path.realpath(link)
                    if real.startswith('/dev/video'): out.add(real)
                except OSError: continue
    except Exception: pass
    return sorted(out) if out else sorted(set(fallback))

def _json_calib_to_yaml(calib: dict) -> str:
    def fmt_num(x):
        if isinstance(x, float) and x == int(x): return f"{int(x)}.0"
        return str(float(x)) if isinstance(x, (int, float)) else str(x)
    def fmt_matrix(rows, cols, data, indent=4):
        lines = []
        for r in range(rows):
            row_data = [fmt_num(data[r * cols + c]) for c in range(cols)]
            lines.append(f"{' ' * indent}{', '.join(row_data)}")
        return ",\n".join(lines)
    lines = ["%YAML:1.0"]
    lines.append(f"image_width: {calib.get('image_width', 640)}")
    lines.append(f"image_height: {calib.get('image_height', 480)}")
    lines.append(f"camera_name: {calib.get('camera_name', 'usb_cam')}")
    lines.append("")
    cm = calib.get("camera_matrix", {})
    lines.append("camera_matrix:")
    lines.append(f"  rows: {cm.get('rows', 3)}"); lines.append(f"  cols: {cm.get('cols', 3)}")
    lines.append(f"  data: [{fmt_matrix(3, 3, cm.get('data', [600.0,0,320.0,0,600.0,240.0,0,0,1.0]))}]")
    lines.append(""); lines.append(f"distortion_model: {calib.get('distortion_model', 'plumb_bob')}")
    lines.append("")
    dc = calib.get("distortion_coefficients", {})
    lines.append("distortion_coefficients:"); lines.append(f"  rows: {dc.get('rows', 1)}")
    lines.append(f"  cols: {dc.get('cols', 5)}"); lines.append(f"  data: [{fmt_matrix(1, 5, dc.get('data', [0.0]*5))}]")
    lines.append("")
    rm = calib.get("rectification_matrix", {})
    lines.append("rectification_matrix:"); lines.append(f"  rows: {rm.get('rows', 3)}")
    lines.append(f"  cols: {rm.get('cols', 3)}")
    lines.append(f"  data: [{fmt_matrix(3, 3, rm.get('data', [1.0,0,0,0,1.0,0,0,0,1.0]))}]")
    lines.append("")
    pm = calib.get("projection_matrix", {})
    lines.append("projection_matrix:"); lines.append(f"  rows: {pm.get('rows', 3)}")
    lines.append(f"  cols: {pm.get('cols', 4)}")
    lines.append(f"  data: [{fmt_matrix(3, 4, pm.get('data', [600.0,0,320.0,0,0,600.0,240.0,0,0,0,1.0,0]))}]")
    lines.append("")
    return "\n".join(lines)

def fetch_camera_cals_from_gateway():
    time.sleep(7)
    for cam_id in [1, 2]:
        try:
            url = f"{GATEWAY_URL}/core/camera/calibration/{cam_id}"
            req = urllib.request.Request(url, headers={"X-API-Token": API_TOKEN}, method="GET")
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
                calib = json.loads(resp.read().decode("utf-8"))
            yaml_data = _json_calib_to_yaml(calib)
            paths = [CAMERA_CALIB_MONO_FILE, CAMERA_CALIB_LEFT_FILE] if cam_id == 1 else [CAMERA_CALIB_RIGHT_FILE]
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f: f.write(yaml_data)
            mapping = get_camera_devices()
            fp = mapping.get(cam_id, {}).get("fingerprint") if isinstance(mapping.get(cam_id), dict) else None
            save_calibration_status(cam_id, True, fp)
        except Exception as e: print(f"[Agent] Impossible de recuperer calibration camera {cam_id}: {e}")
