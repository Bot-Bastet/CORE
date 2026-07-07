#!/usr/bin/env python3
"""
Streaming Engine — unified dual-pipeline node for Bastet robot.
Local VSLAM pipeline: raw ROS topic via IPC (zero-copy).
Gateway remote pipeline: ROS topic → ffmpeg (SW libx264 / HW h264_v4l2m2m) → RTSP → MediaMTX → WebRTC.
The internal RTSP transport is converted to WebRTC by MediaMTX (Gateway), so clients get native WebRTC.
Format-adaptive: reads msg.encoding and maps to ffmpeg pix_fmt.
Health: publishes /streaming/status every 2s with discovered cameras + active streams.
Auto-detection: /dev/v4l/by-id/ at startup, maps /dev/video* → ROS Image topics.
"""
import glob, json, os, re, subprocess
import signal
import sys
import rclpy
import rclpy.executors
import rclpy.logging
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String

VIDEO_FPS = 10
VIDEO_BITRATE = "2M"
RTSP_HOST = "ha.arthonetwork.fr"  # MediaMTX runs on Gateway (docker-compose)
RTSP_PORT = 48554

# Mapping ROS Image encoding → ffmpeg pixel format
ENCODING_MAP = {
    'rgb8': 'rgb24', 'rgb24': 'rgb24',
    'bgr8': 'bgr24', 'bgr24': 'bgr24',
    'yuv422': 'yuyv422', 'yuyv': 'yuyv422',
    'yuyv422': 'yuyv422', 'yuv422_yuy2': 'yuyv422',
    'mono8': 'gray', '8UC1': 'gray',
}


def discover_cameras() -> dict[int, dict]:
    """
    Return dict of discovered USB cameras with stable, deterministic ordering.

    PRIMARY PATH: Read /dev/v4l/by-id/usb-*-video-index0 symlinks (same approach as
    agent.py's get_active_video_devices()). Each USB camera gets ONE video-index0
    entry that points to the actual /dev/videoN image node. Sorting by the stable
    USB serial name guarantees that cam0/cam1 always map to the same physical
    cameras, even after USB re-enumeration or reboot.

    FALLBACK: If /dev/v4l/by-id is empty (cold boot, udev not ready), parse
    v4l2-ctl --list-devices output.

    Returns:
        {cam_index: {device: '/dev/videoX', name: 'Human-readable', topic: '...'}}

    ROS2 topic convention (matches usb_cam stereo launch in start.sh):
      - camera 1 (left)  → /camera/left/image_raw
      - camera 2 (right) → /camera/right/image_raw

    Returns empty dict if no cameras found.
    """
    cameras = {}

    # ── Primary path: /dev/v4l/by-id (stable USB serial names) ──
    by_id_dir = '/dev/v4l/by-id'
    if os.path.isdir(by_id_dir):
        # Group by-id entries by their base camera identity (the part before
        # -video-indexN). Each physical USB camera may expose multiple video
        # nodes (e.g. -video-index0 for image, -video-index1 for metadata).
        # We keep only one image node per camera: prefer -video-index0,
        # fall back to whatever entry exists (some UVC cameras omit the suffix).
        camera_groups: dict[str, tuple] = {}  # base_name -> (raw_entry, device_path)
        try:
            for entry in os.listdir(by_id_dir):
                if not entry.startswith('usb-'):
                    continue
                link = os.path.join(by_id_dir, entry)
                try:
                    real = os.path.realpath(link)
                except OSError:
                    continue
                if not real.startswith('/dev/video'):
                    continue
                # Extract base name: strip usb- prefix and optional -video-indexN suffix
                base = entry[4:]  # drop 'usb-'
                base = re.sub(r'-video-index\d+$', '', base)
                # Keep this entry if it's -video-index0 (preferred) or if no
                # entry has been recorded yet for this camera
                if base not in camera_groups:
                    camera_groups[base] = (entry, real)
                elif '-video-index0' in entry:
                    # Override with the -video-index0 entry
                    camera_groups[base] = (entry, real)
        except Exception:
            pass

        if camera_groups:
            # Sort by the stable base name (USB serial) for deterministic ordering
            sorted_bases = sorted(camera_groups.keys())
            for i, base in enumerate(sorted_bases):
                if i >= 2:
                    break
                _, dev = camera_groups[base]
                name = base.replace('_', ' ')
                topic = '/camera/left/image_raw' if i == 0 else '/camera/right/image_raw'
                # 1-based key: dashboard + agent.ws use cam_id 1/2.
                cameras[i + 1] = {
                    'device': dev,
                    'name': name,
                    'topic': topic,
                }
            return cameras

    # ── Fallback: v4l2-ctl --list-devices (less stable ordering) ──
    try:
        result = subprocess.run(
            ['v4l2-ctl', '--list-devices'],
            capture_output=True, text=True, timeout=5
        )
        raw = result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        raw = ""

    if raw:
        current_name = None
        for line in raw.splitlines():
            line = line.rstrip()
            if not line:
                continue
            if not line.startswith((' ', '\t')):
                current_name = line.rstrip(':')
                continue
            match = re.search(r'/dev/video(\d+)', line)
            if match and current_name:
                dev_path = match.group(0)
                if _is_capture_device(dev_path):
                    # 1-based key: dashboard + agent.ws use cam_id 1/2.
                    cam_id = len(cameras) + 1
                    if cam_id > 2:
                        break
                    topic = '/camera/left/image_raw' if cam_id == 1 else '/camera/right/image_raw'
                    cameras[cam_id] = {
                        'device': dev_path,
                        'name': current_name.strip(),
                        'topic': topic,
                    }
    else:
        # ── Last-resort fallback: glob /dev/video* ──
        devs = sorted(glob.glob('/dev/video*'))
        for d in devs:
            if re.search(r'/dev/video\d+$', d) and _is_capture_device(d):
                # 1-based key: dashboard + agent.ws use cam_id 1/2.
                cam_id = len(cameras) + 1
                if cam_id > 2:
                    break
                topic = '/camera/left/image_raw' if cam_id == 1 else '/camera/right/image_raw'
                cameras[cam_id] = {
                    'device': d,
                    'name': d,
                    'topic': topic,
                }

    return cameras


def _is_capture_device(device_path: str) -> bool:
    """Check if a /dev/videoX device supports Video Capture (not just metadata)."""
    try:
        result = subprocess.run(
            ['v4l2-ctl', '--device', device_path, '--info'],
            capture_output=True, text=True, timeout=2
        )
        return 'Video Capture' in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # v4l2-ctl absent → accept the device anyway
        return True


class StreamingEngine(Node):
    def __init__(self):
        super().__init__('streaming_engine')
        hw_dev_exists = os.path.exists('/dev/video10')
        self.declare_parameter('hw_accel', hw_dev_exists)
        self.hw_accel = self.get_parameter('hw_accel').value

        # ── Camera auto-detection ──
        self.cameras = discover_cameras()
        if self.cameras:
            names = [c['name'] for c in self.cameras.values()]
            self.get_logger().info(f"Caméras détectées: {names}")
        else:
            self.get_logger().warn("Aucune caméra USB détectée — le streaming n'aura pas de source ROS")

        self.subs = {}   # camId → Subscription
        self.procs = {}  # camId → subprocess.Popen

        self.create_subscription(String, '/streaming/command', self.cmd_callback, 10)
        self.status_pub = self.create_publisher(String, '/streaming/status', 10)
        self.timer = self.create_timer(2.0, self.publish_status)
        self.hotplug_timer = self.create_timer(30.0, self._check_hotplug)
        self._by_id_mtime = 0  # cached mtime of /dev/v4l/by-id for hotplug skip
        self.get_logger().info(f"Streaming Engine démarré (HW Accel: {self.hw_accel})")

    def _check_hotplug(self):
        """Periodic re-detection (30s). Skips discover_cameras() if
        /dev/v4l/by-id mtime hasn't changed (filesystem-level cache).
        If cameras appear or disappear, logs warnings but does NOT
        interrupt running streams."""
        # Fast path: skip if by-id mtime unchanged (avoids expensive
        # v4l2-ctl fallback when primary path is already stable).
        by_id_dir = '/dev/v4l/by-id'
        if os.path.isdir(by_id_dir):
            try:
                cur_mtime = os.stat(by_id_dir).st_mtime
                if cur_mtime == self._by_id_mtime:
                    return  # nothing changed
                self._by_id_mtime = cur_mtime
            except OSError:
                pass  # stat failed → fall through and re-detect

        new_cameras = discover_cameras()

        # Build device-path sets for comparison
        old_devices = {c['device'] for c in self.cameras.values()}
        new_devices = {c['device'] for c in new_cameras.values()}

        appeared = new_devices - old_devices
        disappeared = old_devices - new_devices

        if appeared:
            names = [c['name'] for c in new_cameras.values() if c['device'] in appeared]
            self.get_logger().warn(f"Nouvelles caméras détectées (hotplug): {names}")
        if disappeared:
            names = [c['name'] for c in self.cameras.values() if c['device'] in disappeared]
            self.get_logger().warn(f"Caméras débranchées (hotplug): {names}")

        if appeared or disappeared:
            self.cameras = new_cameras

    def publish_status(self):
        active = [cam for cam, proc in list(self.procs.items()) if proc.poll() is None]
        # Clean up dead procs
        for cam, proc in list(self.procs.items()):
            if proc.poll() is not None:
                del self.procs[cam]

        msg_out = {
            "status": "streaming" if active else "idle",
            "active_cameras": active,
            "hw_accel": self.hw_accel,
            "discovered_cameras": [
                {"id": idx, "device": c["device"], "name": c["name"], "topic": c["topic"]}
                for idx, c in self.cameras.items()
            ],
        }
        self.status_pub.publish(String(data=json.dumps(msg_out)))

    def cmd_callback(self, msg: String):
        try:
            cmd = json.loads(msg.data)
        except json.JSONDecodeError:
            parts = msg.data.strip().split('_')
            cmd = {"command": parts[0], "camera": int(parts[1])} if len(parts) >= 2 else {}
        cam = cmd.get("camera", 1)
        action = cmd.get("command", "")

        # ── Guard: only subscribe if camera is in the discovered list ──
        if action == "start":
            if cam not in self.cameras:
                self.get_logger().warn(f"Caméra {cam} non détectée — start ignoré. Détectées: {list(self.cameras.keys())}")
                return
            if cam not in self.subs:
                topic = self.cameras[cam]['topic']
                self.subs[cam] = self.create_subscription(
                    Image, topic,
                    lambda m, cid=cam: self._image_callback(m, cid), 10)
                self.get_logger().info(f"Stream cam{cam} START (device={self.cameras[cam]['device']}, topic={topic})")

        elif action == "stop":
            if cam in self.subs:
                self.destroy_subscription(self.subs.pop(cam))
            if cam in self.procs:
                proc = self.procs.pop(cam)
                try:
                    proc.stdin.close()
                except Exception:
                    pass
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                self.get_logger().info(f"Stream cam{cam} STOP")

    def _image_callback(self, msg: Image, cam: int):
        proc = self.procs.get(cam)
        if proc is None or proc.poll() is not None:
            self._start_ffmpeg(cam, msg.encoding, msg.width, msg.height)
            proc = self.procs.get(cam)
        if proc and proc.poll() is None:
            try:
                proc.stdin.write(bytes(msg.data))
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                self._cleanup_proc(cam)

    def _cleanup_proc(self, cam: int):
        """Terminate ffmpeg process and remove from dict."""
        proc = self.procs.pop(cam, None)
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _cleanup_all_procs(self):
        """Terminate every running ffmpeg subprocess. Called from the SIGTERM
        handler in main() so child processes get a graceful exit before
        systemd's cgroup reaper SIGKILLs them after TimeoutStopSec."""
        for cam, proc in list(self.procs.items()):
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self.procs.clear()

    def _start_ffmpeg(self, cam: int, encoding: str, width: int, height: int):
        # Map ROS Image encoding to ffmpeg pixel format
        pix_fmt = ENCODING_MAP.get(encoding, 'rgb24')
        if 'yuv' in encoding.lower() or 'yuyv' in encoding.lower():
            pix_fmt = 'yuyv422'

        if self.hw_accel:
            encoder_args = f"-c:v h264_v4l2m2m -b:v {VIDEO_BITRATE}"
        else:
            encoder_args = "-c:v libx264 -preset ultrafast -tune zerolatency -profile:v baseline -level 3.0 -g 10 -crf 28"

        cmd = (f"ffmpeg -y -f rawvideo -pix_fmt {pix_fmt} "
               f"-s {width}x{height} -r {VIDEO_FPS} -i - "
               f"{encoder_args} -pix_fmt yuv420p -f rtsp -rtsp_transport tcp "
               f"rtsp://{RTSP_HOST}:{RTSP_PORT}/robot/cam{cam}")

        self.procs[cam] = subprocess.Popen(
            cmd.split(), stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=os.setpgrp)
        self.get_logger().info(f"ffmpeg cam{cam} lancé: {pix_fmt} {width}x{height}")


def main(args=None):
    # Module-level logger: works even if StreamingEngine() construction fails,
    # so shutdown events are always observable.
    mod_log = rclpy.logging.get_logger('streaming_engine')
    rclpy.init(args=args)
    try:
        node = StreamingEngine()
    except Exception as e:
        mod_log.error(f"StreamingEngine construction failed: {e}")
        raise
    log = node.get_logger()

    # SIGTERM handler: clean up ffmpeg subprocesses before exiting, so they
    # don't get SIGKILL'd by the cgroup reaper after TimeoutStopSec=20.
    def _on_sigterm(signum, frame):
        log.info("SIGTERM received — cleaning up ffmpeg subprocesses")
        try:
            node._cleanup_all_procs()
        except Exception as e:
            log.warning(f"SIGTERM cleanup error (ignored): {e}")
        sys.exit(0)
    signal.signal(signal.SIGTERM, _on_sigterm)

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — shutting down")
        node._cleanup_all_procs()
    except rclpy.executors.ExternalShutdownException:
        # SIGTERM received (rclpy's default handler converts it to rclpy.shutdown(),
        # which raises this from inside spin). With KillMode=mixed + Restart=always
        # in spotbot.service, systemd will relaunch us cleanly. The custom SIGTERM
        # handler above already cleaned up ffmpeg.
        log.info("External shutdown — exiting cleanly")
        node._cleanup_all_procs()
    finally:
        # rclpy.shutdown() can raise a wide variety of errors after a signal:
        # RuntimeError, RCLError, or even a torn-down pybind11 handle. Catch broad.
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()
