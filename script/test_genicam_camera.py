import argparse
import json
import os
import sys

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


def main():
    parser = argparse.ArgumentParser(description="Standalone GenICam camera test helper.")
    parser.add_argument("--cti-path", required=True, help="Absolute path to the GenTL CTI driver.")
    parser.add_argument("--serial-number", required=True, help="Camera serial number.")
    parser.add_argument("--output-dir", default=os.path.join("runtime", "genicam", "manual-tests"),
        help="Output directory for captured files.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("single-image", help="Capture a single image.")

    video_stream_parser = subparsers.add_parser("video-stream", help="Capture a frame sequence to disk.")
    video_stream_parser.add_argument("--num-frames", type=int, default=10)

    video_file_parser = subparsers.add_parser("video-file", help="Record a short MP4 file.")
    video_file_parser.add_argument("--num-frames", type=int, default=60)
    video_file_parser.add_argument("--fps", type=float, default=15.0)

    visual_servo_parser = subparsers.add_parser("visual-servo-frame",
        help="Fetch the latest frame payload for visual servoing.")
    visual_servo_parser.add_argument("--use-cached-frame", action="store_true",
        help="Prefer the latest cached streaming frame if available.")
    visual_servo_parser.add_argument("--save-snapshot", action="store_true",
        help="Also save the returned payload to disk for inspection.")

    acquire_3d_parser = subparsers.add_parser("frame-3d", help="Capture a single 3D payload.")
    acquire_3d_parser.add_argument("--save-snapshot", action="store_true",
        help="Also save the returned .npy payload to disk.")

    try:
        if args := parser.parse_args():
            os.makedirs(args.output_dir, exist_ok=True)

            if args.command == "single-image":
                result = genicam_bridge.acquire_single_image(args.cti_path, args.serial_number, args.output_dir)
            elif args.command == "video-stream":
                result = genicam_bridge.acquire_video_stream(args.cti_path, args.serial_number,
                    num_frames=args.num_frames, output_dir=args.output_dir)
            elif args.command == "video-file":
                result = genicam_bridge.acquire_video_file(args.cti_path, args.serial_number,
                    num_frames=args.num_frames, fps=args.fps, output_dir=args.output_dir)
            elif args.command == "visual-servo-frame":
                result = genicam_bridge.acquire_visual_servo_frame(args.cti_path, args.serial_number,
                    use_cached=args.use_cached_frame, save_snapshot=args.save_snapshot, output_dir=args.output_dir)
            elif args.command == "frame-3d":
                result = genicam_bridge.acquire_3d_frame(args.cti_path, args.serial_number)
                if args.save_snapshot:
                    result.update(genicam_bridge._write_frame_entry_files(args.serial_number,
                        genicam_bridge._build_frame_entry(result["npy_bytes"], 0, result["data_format"],
                            "application/octet-stream", result["width"], result["height"], "npy"),
                        args.output_dir, "manual_3d"))
            else:
                raise ValueError(f"Unsupported command {args.command}")

            print(json.dumps(_sanitize_result(result), indent=2, sort_keys=True))
            return 0
    finally:
        genicam_bridge.close_all_connections()

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
