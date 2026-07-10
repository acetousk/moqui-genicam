import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

import genicam_bridge


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


def _camera_output_dir(base_output_dir, serial_number):
    output_dir = os.path.join(base_output_dir, serial_number)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def _run_single_image(cti_path, serial_number, base_output_dir):
    result = genicam_bridge.acquire_single_image(
        cti_path, serial_number, _camera_output_dir(base_output_dir, serial_number))
    return {"serial_number": serial_number, "command": "single-image", "result": _sanitize_result(result)}


def _run_video_file(cti_path, serial_number, base_output_dir, num_frames, fps):
    result = genicam_bridge.acquire_video_file(
        cti_path, serial_number, num_frames=num_frames, fps=fps,
        output_dir=_camera_output_dir(base_output_dir, serial_number))
    return {"serial_number": serial_number, "command": "video-file", "result": _sanitize_result(result)}


def main():
    parser = argparse.ArgumentParser(description="Standalone GenICam multi-camera test helper.")
    parser.add_argument("--cti-path", required=True, help="Absolute path to the GenTL CTI driver.")
    parser.add_argument("--serial-number", action="append", required=True,
        help="Camera serial number. Repeat the option for multiple devices.")
    parser.add_argument("--output-dir", default=os.path.join("runtime", "genicam", "manual-tests", "multi-camera"),
        help="Base output directory for captured files.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("single-image", help="Capture one image per camera in parallel.")

    video_file_parser = subparsers.add_parser("video-file", help="Record one MP4 file per camera in parallel.")
    video_file_parser.add_argument("--num-frames", type=int, default=30)
    video_file_parser.add_argument("--fps", type=float, default=5.0)

    args = parser.parse_args()
    serial_numbers = list(dict.fromkeys(args.serial_number))
    os.makedirs(args.output_dir, exist_ok=True)

    futures = []
    results = []

    try:
        with ThreadPoolExecutor(max_workers=len(serial_numbers)) as executor:
            for serial_number in serial_numbers:
                if args.command == "single-image":
                    futures.append(executor.submit(
                        _run_single_image, args.cti_path, serial_number, args.output_dir))
                elif args.command == "video-file":
                    futures.append(executor.submit(
                        _run_video_file, args.cti_path, serial_number, args.output_dir,
                        args.num_frames, args.fps))
                else:
                    raise ValueError(f"Unsupported command {args.command}")

            for future in as_completed(futures):
                results.append(future.result())

        results.sort(key=lambda item: item["serial_number"])
        print(json.dumps({"command": args.command, "results": results}, indent=2, sort_keys=True))
        return 0
    finally:
        genicam_bridge.close_all_connections()


if __name__ == "__main__":
    raise SystemExit(main())
