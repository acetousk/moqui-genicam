import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

import genicam_bridge


def _read_payload():
    payload_text = sys.stdin.read()
    if not payload_text:
        raise ValueError("Missing JSON payload on stdin.")
    return json.loads(payload_text)


def _sanitize_result(result):
    if result is None:
        return {}

    sanitized = {}
    for key, value in result.items():
        if isinstance(value, (bytes, bytearray)):
            sanitized[f"{key}_size"] = len(value)
        else:
            sanitized[key] = value
    return sanitized


def _camera_output_dir(base_output_dir, camera):
    camera_name = camera.get("device_id") or camera.get("serial_number")
    camera_index = camera.get("camera_index")
    if camera_index is not None:
        camera_name = f"{int(camera_index):02d}_{camera_name}"
    output_dir = os.path.join(base_output_dir, camera_name)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def _run_worker_process(camera, options):
    worker_payload = {
        "camera": camera,
        "options": options,
    }
    process = subprocess.run(
        [sys.executable, __file__, "worker-video-file"],
        input=json.dumps(worker_payload),
        text=True,
        capture_output=True,
        check=False,
    )

    stdout_text = (process.stdout or "").strip()
    stderr_text = (process.stderr or "").strip()
    if process.returncode != 0:
        return {
            "camera_index": camera.get("camera_index"),
            "device_id": camera.get("device_id"),
            "serial_number": camera.get("serial_number"),
            "success": False,
            "error": stderr_text or stdout_text or f"Worker failed with exit code {process.returncode}",
        }

    result = json.loads(stdout_text) if stdout_text else {}
    result["success"] = True
    if stderr_text:
        result["stderr"] = stderr_text
    return result


def _worker_video_file():
    payload = _read_payload()
    camera = payload["camera"]
    options = payload.get("options", {})

    output_dir = _camera_output_dir(options["output_dir"], camera)
    result = genicam_bridge.acquire_video_file(
        camera["cti_path"],
        camera["serial_number"],
        num_frames=options["num_frames"],
        fps=options["fps"],
        output_dir=output_dir,
        video_container=options["video_container"],
        video_codec=options["video_codec"],
        resize_width=options.get("resize_width"),
        resize_height=options.get("resize_height"),
    )

    print(json.dumps({
        "camera_index": camera.get("camera_index"),
        "device_id": camera.get("device_id"),
        "serial_number": camera["serial_number"],
        "result": _sanitize_result(result),
    }, sort_keys=True))
    return 0


def _multi_video_file():
    payload = _read_payload()
    options = payload.get("options", {})
    cameras = payload.get("cameras", [])
    if not cameras:
        raise ValueError("No cameras configured for multi-video-file.")

    results = []
    with ThreadPoolExecutor(max_workers=len(cameras)) as executor:
        futures = [executor.submit(_run_worker_process, camera, options) for camera in cameras]
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda item: (item.get("camera_index") if item.get("camera_index") is not None else 9999,
        item.get("device_id") or item.get("serial_number") or ""))
    successful_count = len([item for item in results if item.get("success")])
    failed_count = len(results) - successful_count

    print(json.dumps({
        "command": "multi-video-file",
        "successful_count": successful_count,
        "failed_count": failed_count,
        "results": results,
    }, sort_keys=True))
    return 0 if failed_count == 0 else 1


def main():
    parser = argparse.ArgumentParser(description="GenICam multi-camera external process coordinator.")
    parser.add_argument("command", choices=["multi-video-file", "worker-video-file"])
    args = parser.parse_args()

    if args.command == "worker-video-file":
        return _worker_video_file()
    if args.command == "multi-video-file":
        return _multi_video_file()
    raise ValueError(f"Unsupported command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
