"""
Auto-updater pour CORE (Raspberry Pi / ROS 2).
Vérifie GitHub Releases et effectue git pull + colcon build si une nouvelle version existe.
Conçu pour tourner comme un nœud ROS 2 ou un service systemd.
"""
import os
import subprocess
import requests
import logging
from pathlib import Path

logger = logging.getLogger("core_auto_updater")

GITHUB_REPO = "Bot-Bastet/CORE"
WORKSPACE_ROOT = Path.home() / "ros2_ws"
CORE_SRC = WORKSPACE_ROOT / "src" / "CORE"
VERSION_FILE = CORE_SRC / "version.txt"


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


def check_and_apply_update() -> bool:
    """
    Vérifie et applique la mise à jour si disponible.
    1. Télécharger le .zip de la release
    2. Extraire dans ~/ros2_ws/src/CORE
    3. Lancer colcon build
    4. Mettre à jour version.txt
    Retourne True si une mise à jour a été appliquée.
    """
    current = get_current_version()
    release = get_latest_release()

    if not release:
        logger.info("[AutoUpdater] Impossible de vérifier les mises à jour.")
        return False

    latest_tag = release.get("tag_name", "v0.0.0")

    if _version_tuple(latest_tag) <= _version_tuple(current):
        logger.info(f"[AutoUpdater] A jour ({current}). Aucune mise à jour nécessaire.")
        return False

    logger.info(f"[AutoUpdater] Nouvelle version disponible : {latest_tag} (actuelle : {current})")

    zip_asset = None
    for asset in release.get("assets", []):
        if asset["name"].endswith(".zip"):
            zip_asset = asset
            break

    if not zip_asset:
        logger.warning("[AutoUpdater] Aucun asset .zip trouvé dans la release.")
        return False

    try:
        zip_path = Path("/tmp/core_update.zip")

        logger.info(f"[AutoUpdater] Téléchargement de {zip_asset['browser_download_url']}...")
        resp = requests.get(zip_asset["browser_download_url"], stream=True, timeout=120)
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        # Extraire dans le workspace src
        CORE_SRC.mkdir(parents=True, exist_ok=True)
        subprocess.run(["unzip", "-o", str(zip_path), "-d", str(CORE_SRC)], check=True)
        zip_path.unlink()

        # Rebuild ROS 2
        logger.info("[AutoUpdater] Compilation colcon build...")
        env = os.environ.copy()
        env["PATH"] = "/opt/ros2_jazzy/install/bin:" + env.get("PATH", "")
        subprocess.run(
            ["bash", "-c", "source /opt/ros2_jazzy/install/setup.bash && colcon build --symlink-install"],
            cwd=str(WORKSPACE_ROOT),
            shell=False,
            env=env,
            check=True
        )

        VERSION_FILE.write_text(latest_tag)
        logger.info(f"[AutoUpdater] Mise à jour {latest_tag} appliquée avec succès.")
        return True

    except Exception as e:
        logger.error(f"[AutoUpdater] Erreur lors de la mise à jour : {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    updated = check_and_apply_update()
    if updated:
        logger.info("Redémarrez les services ROS 2 pour appliquer la mise à jour.")
