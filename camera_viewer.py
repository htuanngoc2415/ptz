#!/usr/bin/env python3
"""Giao dien xem RTSP va dieu khien PTZ cho camera HANET.

Mac dinh script ket noi den 192.168.0.108. Tai khoan duoc lay theo
thu tu: tham so dong lenh, bien moi truong CAMERA_USERNAME/CAMERA_PASSWORD,
sau do den file camera.json cua du an.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import socket
import struct
import sys
import threading
import time
from urllib.parse import quote
from uuid import uuid4

# Ban OpenCV trong Conda dung Qt nhung khong kem font. Tro Qt den bo font he
# thong truoc khi import cv2 de tranh canh bao QFontDatabase lap lai.
SYSTEM_FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
if SYSTEM_FONT_DIR.is_dir():
    os.environ.setdefault("QT_QPA_FONTDIR", str(SYSTEM_FONT_DIR))

import cv2
import requests


DEFAULT_IP = "192.168.0.108"
# Sub-stream thuong la H.264 va nhe hon luong chinh 4K H.265/HEVC.
DEFAULT_RTSP_PATH = "/cam/realmonitor?channel=1&subtype=1"
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATHS = (
    SCRIPT_DIR / "camera.json",
)


def load_saved_credentials() -> tuple[str, str]:
    """Doc tai khoan hien co ma khong lam hong script neu config bi thieu."""
    for config_path in CONFIG_PATHS:
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        username = str(config.get("username") or config.get("ptz_username") or "admin")
        password = str(config.get("password") or config.get("ptz_password") or "")
        return username, password
    return "admin", ""


def parse_args() -> argparse.Namespace:
    saved_username, saved_password = load_saved_credentials()
    try:
        saved = json.loads((SCRIPT_DIR / 'camera.json').read_text())
    except (OSError, ValueError):
        saved = {}
    from urllib.parse import urlsplit
    stream = urlsplit(saved.get('display_uri') or saved.get('uri', ''))
    saved_path = stream.path + ('?' + stream.query if stream.query else '')
    parser = argparse.ArgumentParser(
        description="Xem RTSP va dieu khien pan/tilt/zoom camera 192.168.0.108."
    )
    parser.add_argument("--ip", default=saved.get('ptz_ip', DEFAULT_IP), help="Dia chi IP camera")
    parser.add_argument("--port", type=int, default=stream.port or 554, help="Cong RTSP")
    parser.add_argument("--http-port", type=int, default=saved.get('ptz_port', 80), help="Cong HTTP/JSON-RPC dieu khien PTZ")
    parser.add_argument(
        "--username",
        default=os.getenv("CAMERA_USERNAME", saved_username),
        help="Tai khoan camera",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("CAMERA_PASSWORD", saved_password),
        help="Mat khau camera (nen dung bien moi truong CAMERA_PASSWORD)",
    )
    parser.add_argument(
        "--path",
        default=saved_path or DEFAULT_RTSP_PATH,
        help="Duong dan RTSP cua camera",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Chi doc mot frame de kiem tra ket noi, khong mo cua so",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=2.0,
        help="So giay cho truoc khi ket noi lai",
    )
    parser.add_argument(
        "--max-read-failures",
        type=int,
        default=5,
        help="So frame loi lien tiep truoc khi ket noi lai",
    )
    parser.add_argument(
        "--ptz-speed",
        type=int,
        choices=range(1, 9),
        default=4,
        metavar="1-8",
        help="Toc do pan/tilt/zoom ban dau",
    )
    return parser.parse_args()


def build_rtsp_url(args: argparse.Namespace) -> str:
    username = quote(args.username, safe="")
    password = quote(args.password, safe="")
    credentials = username
    if password:
        credentials += f":{password}"
    credentials += "@"

    path = args.path if args.path.startswith("/") else f"/{args.path}"
    return f"rtsp://{credentials}{args.ip}:{args.port}{path}"


def open_camera(rtsp_url: str) -> cv2.VideoCapture:
    # Uu tien TCP de luong on dinh hon tren mang LAN.
    os.environ.setdefault(
        "OPENCV_FFMPEG_CAPTURE_OPTIONS",
        "rtsp_transport;tcp|stimeout;5000000",
    )
    capture = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG, [
        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000,
        cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000,
    ])
    return capture


class VideoWorker(threading.Thread):
    """Doc RTSP o luong nen va chi giu frame moi nhat cho giao dien."""

    def __init__(self, args: argparse.Namespace):
        super().__init__(name="rtsp-video", daemon=True)
        self.args = args
        self.rtsp_url = build_rtsp_url(args)
        self._stop_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._frame = None
        self._status = "Dang khoi dong video..."

    def stop(self) -> None:
        self._stop_event.set()

    def latest_frame(self):
        with self._frame_lock:
            return None if self._frame is None else self._frame.copy()

    def status(self) -> str:
        with self._status_lock:
            return self._status

    def _set_status(self, message: str) -> None:
        with self._status_lock:
            self._status = message

    def run(self) -> None:
        endpoint = f"{self.args.ip}:{self.args.port}{self.args.path}"
        while not self._stop_event.is_set():
            self._set_status(f"Dang ket noi video {endpoint}...")
            capture = open_camera(self.rtsp_url)
            if not capture.isOpened():
                capture.release()
                self._set_status("Khong mo duoc RTSP; se thu lai")
                self._stop_event.wait(max(0.1, self.args.retry_delay))
                continue

            failures = 0
            announced = False
            try:
                while not self._stop_event.is_set():
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        failures += 1
                        if failures >= max(1, self.args.max_read_failures):
                            self._set_status("Mat luong video; dang ket noi lai...")
                            break
                        continue

                    failures = 0
                    with self._frame_lock:
                        self._frame = frame
                    if not announced:
                        height, width = frame.shape[:2]
                        self._set_status(f"Video da ket noi - {width}x{height}")
                        announced = True
            finally:
                capture.release()

            self._stop_event.wait(max(0.1, self.args.retry_delay))


from camera.hanet_rpc import HanetPTZClient


class PTZWorker(threading.Thread):
    """Gui lenh PTZ tuan tu, khong chan luong giao dien Tk."""

    MOTION_CODES = (
        "kCmdUp",
        "kCmdDown",
        "kCmdLeft",
        "kCmdRight",
        "kCmdZoomTele",
        "kCmdZoomWide",
    )

    def __init__(self, args: argparse.Namespace):
        super().__init__(name="ptz-control", daemon=True)
        self.args = args
        self.commands: queue.Queue[tuple[str, str | None, int]] = queue.Queue()
        self._status_lock = threading.Lock()
        self._status = "PTZ san sang"
        self._rpc = None
        self._active_code: str | None = None

    def status(self) -> str:
        with self._status_lock:
            return self._status

    def _set_status(self, message: str) -> None:
        with self._status_lock:
            self._status = message

    def start_motion(self, code: str, speed: int) -> None:
        if code in self.MOTION_CODES:
            self.commands.put(("start", code, max(1, min(8, int(speed)))))

    def stop_motion(self, code: str) -> None:
        if code in self.MOTION_CODES:
            self.commands.put(("stop", code, 0))

    def emergency_stop(self) -> None:
        self.commands.put(("stop_all", None, 0))

    def shutdown(self) -> None:
        self.commands.put(("shutdown", None, 0))

    def _connect(self):
        if self._rpc is not None:
            return self._rpc
        self._set_status("Dang ket noi bo dieu khien PTZ...")
        self._rpc = HanetPTZClient(
            self.args.ip,
            self.args.http_port,
            self.args.username,
            self.args.password,
            timeout=5.0,
        )
        self._set_status("PTZ da ket noi")
        return self._rpc

    def _stop_code(self, code: str) -> None:
        if self._rpc is not None:
            self._rpc.motion(code, start=False, speed=1)

    def _stop_all(self) -> None:
        if self._rpc is None:
            self._active_code = None
            return

        codes = []
        if self._active_code:
            codes.append(self._active_code)
        codes.extend(code for code in self.MOTION_CODES if code not in codes)
        failures = []
        for code in codes:
            try:
                self._stop_code(code)
            except Exception:
                failures.append(code)
        self._active_code = None
        if failures:
            raise RuntimeError("STOP khong duoc xac nhan")

    def _disconnect(self) -> None:
        rpc, self._rpc = self._rpc, None
        if rpc is None:
            return
        try:
            rpc.close()
        except Exception:
            pass

    def run(self) -> None:
        while True:
            try:
                action, code, speed = self.commands.get(timeout=1.0)
            except queue.Empty:
                if self._rpc is not None:
                    try:
                        self._rpc.heartbeat()
                    except Exception as exc:
                        self._set_status(f"Mat phien PTZ ({type(exc).__name__})")
                        self._disconnect()
                continue
            if action == "shutdown":
                try:
                    self._stop_all()
                except Exception as exc:
                    self._set_status(f"Loi STOP khi thoat ({type(exc).__name__})")
                self._disconnect()
                return

            try:
                if action == "start" and code is not None:
                    rpc = self._connect()
                    if self._active_code == code:
                        continue
                    if self._active_code is not None:
                        self._stop_code(self._active_code)
                    # Gan truoc khi gui: neu mat ACK, cleanup van thu STOP ma lenh nay.
                    self._active_code = code
                    rpc.motion(code, start=True, speed=speed)
                    self._set_status(f"Dang chay {code} - toc do {speed}")
                elif action == "stop" and code == self._active_code:
                    self._stop_code(code)
                    self._active_code = None
                    self._set_status("PTZ da dung")
                elif action == "stop_all":
                    self._stop_all()
                    self._set_status("Da gui dung khan cap")
            except Exception as exc:
                # Khong hien URL, tai khoan hoac noi dung phan hoi RPC tren UI.
                self._set_status(f"Loi dieu khien PTZ ({type(exc).__name__})")
                try:
                    self._stop_all()
                except Exception:
                    pass
                self._disconnect()


def check_camera(args: argparse.Namespace) -> int:
    endpoint = f"{args.ip}:{args.port}{args.path}"
    print(f"Dang ket noi camera: {endpoint}")
    capture = open_camera(build_rtsp_url(args))
    try:
        if not capture.isOpened():
            print("Khong mo duoc luong RTSP. Kiem tra IP, tai khoan va mat khau.")
            return 1
        ok, frame = capture.read()
        if not ok or frame is None:
            print("Da mo RTSP nhung khong doc duoc frame video.")
            return 1
        height, width = frame.shape[:2]
        print(f"Ket noi thanh cong, do phan giai {width}x{height}.")
        return 0
    finally:
        capture.release()


def run_gui(args: argparse.Namespace) -> int:
    import tkinter as tk
    from tkinter import ttk
    from PIL import Image, ImageTk

    root = tk.Tk()
    root.title(f"Dieu khien PTZ - {args.ip}")
    root.geometry("1040x760")
    root.minsize(760, 620)

    video = VideoWorker(args)
    ptz = PTZWorker(args)
    video.start()
    ptz.start()

    container = ttk.Frame(root, padding=10)
    container.pack(fill="both", expand=True)
    container.columnconfigure(0, weight=1)
    container.rowconfigure(0, weight=1)

    video_label = ttk.Label(container, text="Dang cho video...", anchor="center")
    video_label.grid(row=0, column=0, sticky="nsew")

    controls = ttk.Frame(container, padding=(0, 10, 0, 0))
    controls.grid(row=1, column=0, sticky="ew")
    controls.columnconfigure(0, weight=1)
    controls.columnconfigure(1, weight=1)
    controls.columnconfigure(2, weight=1)

    direction = ttk.LabelFrame(controls, text="Pan / Tilt", padding=8)
    direction.grid(row=0, column=0, padx=(0, 8), sticky="n")

    zoom = ttk.LabelFrame(controls, text="Zoom", padding=8)
    zoom.grid(row=0, column=1, padx=8, sticky="n")

    settings = ttk.LabelFrame(controls, text="Dieu khien", padding=8)
    settings.grid(row=0, column=2, padx=(8, 0), sticky="nsew")

    speed_var = tk.DoubleVar(value=float(args.ptz_speed))

    def press(code: str):
        ptz.start_motion(code, int(round(speed_var.get())))

    def release(code: str):
        ptz.stop_motion(code)

    def motion_button(parent, text: str, code: str, row: int, column: int):
        button = ttk.Button(parent, text=text, width=10)
        button.grid(row=row, column=column, padx=3, pady=3, ipady=6)
        button.bind("<ButtonPress-1>", lambda _event: press(code))
        button.bind("<ButtonRelease-1>", lambda _event: release(code))
        return button

    motion_button(direction, "▲  LEN", "kCmdUp", 0, 1)
    motion_button(direction, "◀  TRAI", "kCmdLeft", 1, 0)
    stop_button = ttk.Button(direction, text="■  DUNG", width=10, command=ptz.emergency_stop)
    stop_button.grid(row=1, column=1, padx=3, pady=3, ipady=6)
    motion_button(direction, "PHAI  ▶", "kCmdRight", 1, 2)
    motion_button(direction, "▼  XUONG", "kCmdDown", 2, 1)

    motion_button(zoom, "＋  ZOOM IN", "kCmdZoomTele", 0, 0)
    motion_button(zoom, "－  ZOOM OUT", "kCmdZoomWide", 1, 0)

    ttk.Label(settings, text="Toc do (1-8)").pack(anchor="w")
    ttk.Scale(
        settings,
        from_=1,
        to=8,
        orient="horizontal",
        variable=speed_var,
        length=210,
    ).pack(fill="x", pady=(4, 0))
    speed_label = ttk.Label(settings, text=f"Muc {int(round(speed_var.get()))}")
    speed_label.pack(anchor="center", pady=(2, 6))
    speed_var.trace_add(
        "write",
        lambda *_: speed_label.config(text=f"Muc {int(round(speed_var.get()))}"),
    )
    ttk.Label(settings, text="Giu nut de di chuyen, nha nut de dung.").pack(anchor="w")
    ttk.Label(settings, text="Phim: mui ten, + va -").pack(anchor="w")

    video_status = tk.StringVar(value="Dang khoi dong video...")
    ptz_status = tk.StringVar(value="PTZ san sang")
    status_frame = ttk.Frame(container, padding=(0, 8, 0, 0))
    status_frame.grid(row=2, column=0, sticky="ew")
    ttk.Label(status_frame, textvariable=video_status).pack(side="left")
    ttk.Label(status_frame, textvariable=ptz_status).pack(side="right")

    key_codes = {
        "Up": "kCmdUp",
        "Down": "kCmdDown",
        "Left": "kCmdLeft",
        "Right": "kCmdRight",
        "plus": "kCmdZoomTele",
        "equal": "kCmdZoomTele",
        "KP_Add": "kCmdZoomTele",
        "minus": "kCmdZoomWide",
        "KP_Subtract": "kCmdZoomWide",
    }
    held_keys: set[str] = set()

    def on_key_press(event):
        code = key_codes.get(event.keysym)
        if code and event.keysym not in held_keys:
            held_keys.add(event.keysym)
            press(code)

    def on_key_release(event):
        code = key_codes.get(event.keysym)
        if code:
            held_keys.discard(event.keysym)
            release(code)

    root.bind_all("<KeyPress>", on_key_press)
    root.bind_all("<KeyRelease>", on_key_release)

    closing = False
    cleanup_done = threading.Event()

    def cleanup():
        video.stop()
        ptz.shutdown()
        ptz.join(timeout=8.0)
        video.join(timeout=6.0)
        cleanup_done.set()

    def finish_close():
        if cleanup_done.is_set():
            root.destroy()
        else:
            root.after(100, finish_close)

    def on_close():
        nonlocal closing
        if closing:
            return
        closing = True
        ptz_status.set("Dang gui STOP va dong ket noi...")
        threading.Thread(target=cleanup, name="camera-cleanup", daemon=True).start()
        finish_close()

    root.protocol("WM_DELETE_WINDOW", on_close)

    def update_ui():
        if closing:
            return
        frame = video.latest_frame()
        if frame is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            available_width = max(320, video_label.winfo_width() - 8)
            available_height = max(240, video_label.winfo_height() - 8)
            image.thumbnail((available_width, available_height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image=image)
            video_label.configure(image=photo, text="")
            video_label.image = photo
        video_status.set(video.status())
        ptz_status.set(ptz.status())
        root.after(33, update_ui)

    update_ui()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        on_close()
    return 0


def main() -> int:
    args = parse_args()
    if args.check:
        return check_camera(args)
    return run_gui(args)


if __name__ == "__main__":
    sys.exit(main())
