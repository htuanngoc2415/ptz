#!/usr/bin/env python3

import subprocess
import os
import shlex
import signal
import json
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================

CAMERA_CONFIG = json.loads((Path(__file__).resolve().parents[1] / "camera.json").read_text())
RTSP_URL = CAMERA_CONFIG["uri"]
_parts = urlsplit(RTSP_URL)
if _parts.username is None and CAMERA_CONFIG.get("username"):
    _auth = quote(CAMERA_CONFIG["username"], safe="") + ":" + quote(CAMERA_CONFIG.get("password", ""), safe="")
    RTSP_URL = urlunsplit(_parts._replace(netloc=_auth + "@" + _parts.netloc))

RTSP_LATENCY_MS = 200

OUTPUT_DIR = "./recordings"

# Thời gian record, đơn vị giây.
# None = record liên tục cho đến khi Ctrl+C
RECORD_DURATION = None

RECORD_FPS = 25
BITRATE_KBPS = 4000
FINALIZE_TIMEOUT = 15

# ============================================================
# RECORD
# ============================================================


def build_output_path():

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    return os.path.join(
        OUTPUT_DIR,
        f"camera_{timestamp}.mp4"
    )


def build_pipeline(output_path):

    pipeline = (
        f'rtspsrc {shlex.quote("location=" + RTSP_URL)} '
        f'protocols=tcp '
        f'latency={RTSP_LATENCY_MS} '
        f'! application/x-rtp,media=video,encoding-name=H264 '
        f'! rtph264depay '
        f'! h264parse '
        # Camera packets can produce H.264 buffers without PTS. Decode and
        # normalize raw frame timing before encoding a fresh MP4-compatible stream.
        f'! avdec_h264 '
        f'! videoconvert '
        f'! videorate '
        f'! video/x-raw,format=I420,framerate={RECORD_FPS}/1 '
        f'! x264enc tune=zerolatency speed-preset=ultrafast '
        f'bitrate={BITRATE_KBPS} key-int-max={RECORD_FPS * 2} bframes=0 '
        f'! h264parse '
        f'! video/x-h264,stream-format=avc,alignment=au '
        f'! mp4mux '
        f'! filesink {shlex.quote("location=" + output_path)} sync=false'
    )

    return pipeline


def run_pipeline(pipeline):
    # Isolate the child so Ctrl+C is forwarded exactly once. gst-launch -e
    # sends EOS on SIGINT, allowing mp4mux to write the final MP4 index.
    process = subprocess.Popen(
        ["gst-launch-1.0", "-e"] + shlex.split(pipeline),
        start_new_session=True,
    )
    try:
        return process.wait(timeout=RECORD_DURATION)
    except subprocess.TimeoutExpired:
        print("\nRecord duration reached. Finalizing MP4...", flush=True)
    except KeyboardInterrupt:
        print("\nStopping recording. Finalizing MP4...", flush=True)

    if process.poll() is None:
        process.send_signal(signal.SIGINT)
    try:
        return process.wait(timeout=FINALIZE_TIMEOUT)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        process.kill()
        process.wait()
        print("MP4 finalization interrupted; the output may be incomplete.")
        return 1


def main():

    output_path = build_output_path()

    pipeline = build_pipeline(output_path)

    print("=" * 60)
    print("CCTV 37X RECORD")
    print("=" * 60)
    print("RTSP   : camera.json")
    print(f"Output : {output_path}")
    print()
    print("Press Ctrl+C to stop recording")
    print("=" * 60, flush=True)

    try:

        returncode = run_pipeline(pipeline)
    except OSError as exc:
        print(f"Could not start recording: {exc}")
        return 1

    if returncode != 0:
        print(f"\nRecording failed (exit code {returncode}).")
        print(f"Output may be incomplete: {output_path}")
        return 1
    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        print("\nRecording failed: no output data was written.")
        return 1

    print()
    print(f"Saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
