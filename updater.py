"""
Auto-updater pour CORE (Raspberry Pi / ROS 2).
Vérifie GitHub Releases et effectue git pull + colcon build si une nouvelle version existe.
Conçu pour tourner comme un nœud ROS 2 ou un service systemd.
"""
import os
import json
import time
import warnings  # stdlib — used locally inside report_progress only
import subprocess
import requests
import urllib3
import logging
import socket
from pathlib import Path

# Set global socket timeout to prevent hangs
socket.setdefaulttimeout(30.0)

# NOTE: we deliberately do NOT call ``urllib3.disable_warnings(...)`` at module
# scope, so that legitimate SSL errors raised by other HTTPS calls elsewhere in
# the process still surface in the logs. ``InsecureRequestWarning`` is silenced
# LOCALLY inside ``report_progress`` via the stdlib ``warnings`` context manager.

logger = logging.getLogger("core_auto_updater")

GITHUB_REPO = "Bot-Bastet/CORE"
WORKSPACE_ROOT = Path("/opt/spotbot/ros2_ws")
CORE_SRC = Path("/opt/spotbot")
VERSION_FILE = CORE_SRC / "version.txt"

API_TOKEN = "bst_c9f28d3a1e4b85c7f0d4b9a2e6f1c3d5"


def get_current_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return "v0.0.0"


def get_latest_release() -> dict | None:
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        resp = requests.get(url, timeout=5, headers={"Accept": "application/vnd.github+json"})
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning(f"[AutoUpdater] Impossible de joindre GitHub : {e}")
    return None


def _version_tuple(v: str) -> tuple:
    v = v.lstrip("v").split("-")[0]
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0, 0, 0)


def report_progress(status: str, percent: int):
    """Report progress to the Gateway with safe SSL + local-heartbeat fallback.

    Background: the previous implementation called ``requests.post(...)`` with
    default certificate verification AND swallowed every exception. When the
    Caddy reverse-proxy uses a self-signed certificate on
    ``ha.arthonetwork.fr:44888``, every progress POST silently failed and the
    dashboard was left stuck at ``starting / 0%`` forever (until the 10-min
    staleness timeout) — even though the actual update was running fine.

    Fix:
      1. Disable TLS verification (``verify=False``) which matches the
         behaviour of ``agent.py`` (uses ``ssl.CERT_NONE`` on the same URL).
      2. Log every transport error instead of swallowing it.
      3. Write a local heartbeat file (``/var/log/spotbot/agent_update_state.json``)
         so an operator inspecting the Pi over SSH has a ground-truth record of
         what the updater was doing at any point, even if the Gateway POST fails.
    """
    payload = {"status": status, "percent": percent}

    # 1. Local heartbeat (best-effort, never raises)
    try:
        hb_dir = Path("/var/log/spotbot")
        hb_dir.mkdir(parents=True, exist_ok=True)
        (hb_dir / "agent_update_state.json").write_text(
            json.dumps({"updated_at": time.time(), **payload}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e_hb:
        logger.warning(f"[AutoUpdater] \u00e9chec \u00e9criture heartbeat local: {e_hb}")

    # 2. HTTPS POST to the Gateway (verify=False to match agent.py's ssl_ctx)
    url = "https://ha.arthonetwork.fr:44888/system/update/robot/progress"
    try:
        requests.post(
            url,
            json=payload,
            headers={"X-API-Token": API_TOKEN},
            timeout=8,
            verify=False,
        )
    except Exception as e_post:
        # Log explicitly so a `journalctl` or ssh session can see why the
        # dashboard is stuck. Do NOT silently swallow anymore.
        logger.warning(
            f"[AutoUpdater] \u00e9chec POST progr\u00e8s Gateway "
            f"(status={status}, percent={percent}%) : {e_post}"
        )


def check_and_apply_update() -> bool:
    """
    Vérifie et applique la mise à jour si disponible.
    1. Télécharger le .zip de la release avec reprise de téléchargement (curl -C -)
    2. Extraire dans /opt/spotbot
    3. Lancer colcon build (limité à 1 job et exécution séquentielle pour éviter de surcharger le Pi,
       avec les arguments cmake pour trouver Sophus).
    4. Mettre à jour version.txt
    Retourne True si une mise à jour a été appliquée.
    """
    current = get_current_version()
    release = get_latest_release()

    if not release:
        logger.info("[AutoUpdater] Impossible de vérifier les mises à jour.")
        report_progress("idle", 100)
        return False

    latest_tag = release.get("tag_name", "v0.0.0")

    if _version_tuple(latest_tag) <= _version_tuple(current):
        logger.info(f"[AutoUpdater] A jour ({current}). Aucune mise à jour nécessaire.")
        report_progress("idle", 100)
        return False

    logger.info(f"[AutoUpdater] Nouvelle version disponible : {latest_tag} (actuelle : {current})")

    zip_asset = None
    for asset in release.get("assets", []):
        if "core-embedded" in asset["name"] and asset["name"].endswith(".zip"):
            zip_asset = asset
            break
    if not zip_asset:
        for asset in release.get("assets", []):
            if asset["name"].endswith(".zip") and "arduino" not in asset["name"]:
                zip_asset = asset
                break

    if not zip_asset:
        logger.warning("[AutoUpdater] Aucun asset .zip trouvé dans la release.")
        report_progress("idle", 100)
        return False

    try:
        zip_path = Path("/tmp/core_update.zip")
        url = zip_asset["browser_download_url"]

        logger.info(f"[AutoUpdater] Récupération des informations de téléchargement pour {url}...")
        
        # Get actual content-length by following redirects
        total_size = 0
        try:
            head_resp = requests.head(url, allow_redirects=True, timeout=15)
            total_size = int(head_resp.headers.get("content-length", 0))
        except Exception as e:
            logger.warning(f"[AutoUpdater] HEAD request failed: {e}. Trying GET...")
            try:
                get_resp = requests.get(url, stream=True, timeout=15)
                total_size = int(get_resp.headers.get("content-length", 0))
                get_resp.close()
            except Exception as e2:
                logger.warning(f"[AutoUpdater] GET headers check failed: {e2}")

        logger.info(f"[AutoUpdater] Taille attendue du fichier : {total_size} octets")

        # Clear existing file if it's larger than expected
        if zip_path.exists() and total_size > 0 and zip_path.stat().st_size > total_size:
            logger.info("[AutoUpdater] Le fichier local existant est plus grand que la taille attendue. Suppression...")
            zip_path.unlink()

        report_progress("downloading", 0)

        max_attempts = 10
        success = False
        
        for attempt in range(1, max_attempts + 1):
            logger.info(f"[AutoUpdater] Tentative de téléchargement {attempt}/{max_attempts}...")
            
            # Start curl process with automatic speed timeout and connect timeout
            process = subprocess.Popen(
                ["curl", "-L", "-C", "-", "--connect-timeout", "30", "-y", "30", "-Y", "1000", "--retry", "5", "--retry-delay", "5", "-o", str(zip_path), url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Monitor progress based on file size on disk
            while process.poll() is None:
                if zip_path.exists():
                    cur_size = zip_path.stat().st_size
                    if total_size > 0:
                        percent = min(int((cur_size / total_size) * 100), 99)
                        report_progress("downloading", percent)
                time.sleep(1.0)
                
            ret_code = process.returncode
            if ret_code == 0:
                if zip_path.exists() and (total_size == 0 or zip_path.stat().st_size >= total_size):
                    logger.info("[AutoUpdater] Téléchargement réussi.")
                    success = True
                    break
                else:
                    logger.warning("[AutoUpdater] Curl a retourné 0 mais le fichier est incomplet.")
            else:
                logger.warning(f"[AutoUpdater] Curl a échoué avec le code {ret_code}.")
                
            time.sleep(2.0)

        if not success:
            raise Exception("Impossible de télécharger la mise à jour après plusieurs tentatives.")

        report_progress("downloading", 100)

        # Extraire dans le workspace src
        report_progress("extracting", 100)
        CORE_SRC.mkdir(parents=True, exist_ok=True)
        subprocess.run(["unzip", "-o", str(zip_path), "-d", str(CORE_SRC)], check=True)
        zip_path.unlink()

        # Rebuild ROS 2 (exécuté en tant que root pour éviter les conflits de permission)
        report_progress("compiling", 0)
        logger.info("[AutoUpdater] Compilation colcon build (sécurisée)...")
        env = os.environ.copy()
        env["PATH"] = "/opt/ros2_jazzy/install/bin:" + env.get("PATH", "")
        env["MAKEFLAGS"] = "-j1"
        
        process = subprocess.Popen(
            ["bash", "-c", "source /opt/ros2_jazzy/install/setup.bash && colcon build --symlink-install --executor sequential --cmake-args -DSophus_DIR=/opt/ORB_SLAM3/Thirdparty/Sophus/build"],
            cwd=str(WORKSPACE_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env
        )
        
        total_packages = 34
        started_packages = 0
        for line in process.stdout:
            if "Starting >>>" in line:
                started_packages += 1
                percent = min(int((started_packages / total_packages) * 100), 99)
                report_progress("compiling", percent)
                
        process.wait()
        if process.returncode != 0:
            report_progress("failed", 0)
            raise Exception(f"colcon build failed with code {process.returncode}")

        # 1. Corriger et redémarrer bastet-tunnel.service pour utiliser la nouvelle IP publique
        tunnel_path = Path("/etc/systemd/system/bastet-tunnel.service")
        if tunnel_path.exists():
            logger.info("[AutoUpdater] Correction de bastet-tunnel.service...")
            try:
                content = tunnel_path.read_text()
                modified = False
                if "79.94.238.213" in content:
                    logger.info("[AutoUpdater] Remplacement de l'ancienne IP dans bastet-tunnel.service...")
                    content = content.replace("79.94.238.213", "82.67.220.37")
                    modified = True
                if "ha.arthonetwork.fr" in content:
                    logger.info("[AutoUpdater] Remplacement du domaine dans bastet-tunnel.service...")
                    content = content.replace("ha.arthonetwork.fr", "82.67.220.37")
                    modified = True
                if modified:
                    tunnel_path.write_text(content)
                    subprocess.run(["systemctl", "daemon-reload"], check=True)
                    subprocess.run(["systemctl", "restart", "bastet-tunnel.service"], check=True)
                    logger.info("[AutoUpdater] bastet-tunnel.service corrigé et redémarré.")
            except Exception as e_tunnel:
                logger.error(f"[AutoUpdater] Impossible de corriger bastet-tunnel.service : {e_tunnel}")

        # 2. Mettre à jour version.txt
        VERSION_FILE.write_text(latest_tag)
        logger.info(f"[AutoUpdater] Mise à jour {latest_tag} appliquée avec succès.")
        report_progress("idle", 100)

        # 3. Installer/mettre à jour le service agent et le redémarrer en dernier (ce qui tuera ce script)
        agent_svc = CORE_SRC / "spotbot-agent.service"
        if agent_svc.exists():
            logger.info("[AutoUpdater] Installation de spotbot-agent.service...")
            subprocess.run(["cp", str(agent_svc), "/etc/systemd/system/"], check=True)
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "enable", "spotbot-agent.service"], check=True)
            subprocess.run(["systemctl", "restart", "spotbot-agent.service"], check=True)
        return True

    except Exception as e:
        logger.error(f"[AutoUpdater] Erreur lors de la mise à jour : {e}")
        report_progress("failed", 0)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    updated = check_and_apply_update()
    if updated:
        logger.info("Redémarrez les services ROS 2 pour appliquer la mise à jour.")
