#!/usr/bin/env python3

from __future__ import annotations
from event_logging import event_print as print

import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog

import cv2
import numpy as np
from PIL import Image, ImageTk


# ============================================================
# PROJECT MODULES
# ============================================================

from camera.image_settings import ImageSettingsClient
from control.orchestrator import ControlOrchestrator
from control.manual import ManualController
from runtime.frame_manager import FrameManager
from telemetry import latency
from runtime.inference_worker import InferenceWorker
from app.application import configure_controls
from camera.capabilities import CameraImageCapabilities
from camera.stream import DualStreamPlayer

from camera import (
    PTZ_ZOOM_IN,
    PTZ_ZOOM_OUT,
    PTZ_FOCUS_PLUS,
    PTZ_FOCUS_MINUS,
)

from ai.hybrid_reid import HybridReIDTargetSelector as ReIDTargetSelector

import config as cfg


# ============================================================
# MAIN GUI
# ============================================================

class CCTVApp:

    def __init__(
        self,
        root: tk.Tk,
    ):
        self.root = root

        self.root.title(
            "AI PTZ Camera"
        )

        self.root.geometry(
            cfg.WINDOW_GEOMETRY
        )

        self.root.minsize(
            1100,
            650
        )

        # ====================================================
        # APPLICATION STATE
        # ====================================================

        from telemetry import zoom_trace
        zoom_trace.configure(directory=cfg.LOG_DIRECTORY, **cfg.ZOOM_LOGGING)
        latency.configure(directory=cfg.LOG_DIRECTORY, **cfg.PROFILING)
        self.orchestrator = ControlOrchestrator()
        self.frames = FrameManager()
        self.display_frames = FrameManager(profile=False)
        self.running = True
        self.image_settings_stop = threading.Event()
        self.backlight_results = None
        self.backlight_text = tk.StringVar(value="Chờ cấu hình")

        self.frame_lock = threading.Lock()
        self.ai_lock = threading.Lock()
        self.zoom_lock = threading.Lock()
        self.tracking_reset = threading.Event()
        self.reid_retry = threading.Event()
        self.target_request_lock = threading.Lock()
        self.pending_target_path = None
        self.ai_initialization_error = None
        self.reid_target = ReIDTargetSelector(cfg)

        self.latest_frame_rgb = None
        self.latest_frame_time = None
        self.frame_sequence = 0

        self.ai_result = {
            "persons": [],
            "faces": [],
            "person_target": None,
            "face_target": None,
            "control": None,
            "sharpness": 0.0,
            "recognition": None,
            #"ai_ms": 0.0,
        }

        # ====================================================
        # OPTICAL ZOOM TELEMETRY
        # ====================================================

        self.min_zoom = float(
            cfg.MIN_OPTICAL_ZOOM
        )

        self.max_zoom = float(
            cfg.MAX_OPTICAL_ZOOM
        )

        self.full_zoom_time = float(
            cfg.FULL_ZOOM_TIME
        )

        self.zoom_level = (
            self.min_zoom
        )

        # Used by manual zoom.
        self.zoom_direction = 0
        self.zoom_started = None
        self.zoom_start_level = self.zoom_level

        # ====================================================
        # LOST TARGET
        # ====================================================

        self.last_lost_zoom_out = 0.0

        # ====================================================
        # Face readiness follows the configured target size and state machine.
        # Re-evaluate it as the subject moves instead of latching forever.
        # ====================================================
        self.face_zoom_locked = False

        self.last_face_recognition_time = 0.0
        self.face_recognition_result = None
        self.face_display_recognition = None

        # ====================================================
        # TK VARIABLES
        # ====================================================

        self.status_text = tk.StringVar(
            value="Starting..."
        )

        self.tracking_state_text = tk.StringVar(
            value="SEARCH_PERSON"
        )

        self.target_type_text = tk.StringVar(
            value="---"
        )

        self.person_count_text = tk.StringVar(
            value="0"
        )

        self.face_count_text = tk.StringVar(
            value="0"
        )

        self.person_ratio_text = tk.StringVar(
            value="---"
        )

        self.face_ratio_text = tk.StringVar(
            value="---"
        )

        self.sharpness_text = tk.StringVar(
            value="---"
        )

        self.latency_text = tk.StringVar(value="AI latency: N/A")
        self.profiling_var = tk.BooleanVar(value=latency.profiler.enabled)
        self.ai_time_text = tk.StringVar(
            value="---"
        )

        self.optical_zoom_text = tk.StringVar(
            value=f"{self.zoom_level:.2f}x"
        )

        self.digital_zoom_text = tk.StringVar(
            value="1.00x"
        )

        self.auto_zoom_var = tk.BooleanVar(
            value=cfg.AUTO_ZOOM_DEFAULT
        )

        self.auto_focus_var = tk.BooleanVar(
            value=cfg.AUTO_FOCUS_DEFAULT
        )

        self.digital_ptz_var = tk.BooleanVar(
            value=cfg.DIGITAL_PTZ_DEFAULT
        )

        self.auto_pan_tilt_var = tk.BooleanVar(value=cfg.AUTO_PAN_TILT_ENABLED)

        self.motor_speed_var = tk.IntVar(
            value=cfg.PTZ_SPEED
        )

        # ====================================================
        # LOW LEVEL CAMERA
        # ====================================================

        configure_controls(self)

        # ====================================================
        # BUILD UI
        # ====================================================

        self.manual_controller = ManualController(self.ptz, self.auto_zoom, self.auto_focus,
                                                  self.orchestrator, self.set_status, cfg.AUTO_ZOOM_SETTLE_TIME)
        self.auto_pan_tilt_var.set(self.auto_pan_tilt.enabled)
        self.build_ui()
        print("[STARTUP] GUI widgets ready")

        # ====================================================
        # RTSP
        # ====================================================

        rtsp_config = {
            **cfg.CAMERA_CONFIG,
            "uri": cfg.RTSP_URI,
            "username": cfg.RTSP_USERNAME,
            "password": cfg.RTSP_PASSWORD,
            "latency_ms": cfg.RTSP_LATENCY_MS,
            "reconnect_initial": cfg.RTSP_RECONNECT_INITIAL_SEC,
            "reconnect_max": cfg.RTSP_RECONNECT_MAX_SEC,
        }

        self.rtsp = DualStreamPlayer(
            disconnect_callback=self.on_camera_disconnect,
            config=rtsp_config,
            frame_callback=self.on_frame,
            display_callback=self.on_display_frame,
            display_disconnect_callback=self.display_frames.clear,
            status_callback=self.set_status,
        )

        # ====================================================
        # AI THREAD
        # ====================================================

        self.ai_thread = threading.Thread(
            target=self.ai_worker,
            name="detection-tracking",
            daemon=True,
        )

        self._ai_started = False

        self.image_settings_thread = threading.Thread(
            target=self.apply_backlight_settings, name="camera-backlight", daemon=True,
        )
        self.image_settings_thread.start()
        self.root.after(100, self.poll_backlight_settings)
        self.root.after(500, self.poll_latency)

        # ====================================================
        # START VIDEO
        # ====================================================

        try:

            self.rtsp.start()

        except Exception as exc:

            self.set_status(
                f"RTSP ERROR: {exc}"
            )

        # ====================================================
        # PERIODIC UI
        # ====================================================

        self.root.after(
            cfg.GUI_UPDATE_MS,
            self.update_video,
        )

        self.root.after(
            50,
            self.update_zoom_position,
        )

        # ====================================================
        # CLOSE
        # ====================================================

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.on_close,
        )
        # Let Tk map and paint its widgets before model loading starts.
        self.root.after(100, self.start_ai_worker)

    def start_ai_worker(self):
        if not self.running or self._ai_started:
            return
        self._ai_started = True
        self.status_text.set("Loading AI models...")
        print("[STARTUP] GUI event loop ready; starting AI worker")
        self.ai_thread.start()

    # ========================================================
    # UI
    # ========================================================

    def apply_backlight_settings(self):
        if getattr(cfg, 'BENCHMARK_READ_ONLY', False):
            self.backlight_results = {}
            return
        if cfg.PTZ_BACKEND == "hanet" or cfg.BACKLIGHT_CONFIG.get("enabled") is False:
            self.backlight_results = {}
            print("[BACKLIGHT] Disabled: no image configuration sent")
            return
        client = ImageSettingsClient(
            cfg.PTZ_IP, cfg.PTZ_USERNAME, cfg.PTZ_PASSWORD,
            channel=cfg.PTZ_CHANNEL, timeout=cfg.PTZ_TIMEOUT,
            stop_event=self.image_settings_stop,
        )
        acquired = False
        try:
            while not self.image_settings_stop.is_set():
                if self.orchestrator.acquire('image'):
                    acquired = True
                    break
                self.image_settings_stop.wait(.1)
            if not acquired:
                return
            self.backlight_results = client.apply(cfg.BACKLIGHT_CONFIG, log=print)
            self.image_capabilities = CameraImageCapabilities.from_results(self.backlight_results)
        except ValueError as exc:
            print(f"[BACKLIGHT] ERROR: {exc}")
            self.backlight_results = {"config": "error"}
        except Exception as exc:
            print(f"[BACKLIGHT] ERROR: {type(exc).__name__}")
            self.backlight_results = {"connection": "error"}
        finally:
            if acquired:
                self.orchestrator.release("image", cfg.AUTO_ZOOM_SETTLE_TIME)
            client.close()

    def poll_backlight_settings(self):
        # Only the Tk main thread touches widgets; worker owns its HTTP session.
        if not self.running:
            return
        results = self.backlight_results
        if results is None:
            self.root.after(100, self.poll_backlight_settings)
            return
        if not results:
            self.backlight_text.set("Không áp dụng")
        else:
            verified = sum(v == "verified" for v in results.values())
            text = f"Đã xác nhận {verified}/{len(results)}"
            if verified != len(results):
                text += " — xem log"
            self.backlight_text.set(text)

    def build_ui(self):

        self.root.columnconfigure(
            0,
            weight=1,
        )

        self.root.columnconfigure(
            1,
            weight=0,
        )

        self.root.rowconfigure(
            0,
            weight=1,
        )

        # ====================================================
        # VIDEO
        # ====================================================

        video_frame = ttk.Frame(
            self.root
        )

        video_frame.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=8,
            pady=8,
        )

        video_frame.columnconfigure(
            0,
            weight=1,
        )

        video_frame.rowconfigure(
            0,
            weight=1,
        )

        self.video_label = tk.Label(
            video_frame,
            bg="black",
            text="Waiting for RTSP...",
            fg="white",
        )

        self.video_label.grid(
            row=0,
            column=0,
            sticky="nsew",
        )

        # ====================================================
        # CONTROL PANEL
        # ====================================================

        sidebar = ttk.Frame(self.root)
        sidebar.grid(row=0, column=1, sticky="nsew", padx=(0, 8), pady=8)
        sidebar.rowconfigure(0, weight=1)
        sidebar.columnconfigure(0, weight=1)
        self.control_canvas = tk.Canvas(sidebar, width=330, highlightthickness=0)
        scrollbar = ttk.Scrollbar(sidebar, orient="vertical",
                                  command=self.control_canvas.yview)
        self.control_canvas.configure(yscrollcommand=scrollbar.set)
        self.control_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        panel = ttk.Frame(self.control_canvas)
        panel_window = self.control_canvas.create_window((0, 0), window=panel, anchor="nw")
        panel.bind("<Configure>", lambda _event: self.control_canvas.configure(
            scrollregion=self.control_canvas.bbox("all")))
        self.control_canvas.bind("<Configure>", lambda event:
                                self.control_canvas.itemconfigure(panel_window, width=event.width))

        def scroll_controls(event):
            # Toplevel bindings see child events; scroll only inside the sidebar.
            widget = event.widget
            while widget is not None and widget != sidebar:
                widget = getattr(widget, "master", None)
            if widget != sidebar or panel.winfo_height() <= self.control_canvas.winfo_height():
                return
            if getattr(event, "num", None) == 4:
                units = -1
            elif getattr(event, "num", None) == 5:
                units = 1
            else:
                delta = getattr(event, "delta", 0)
                if not delta:
                    return
                units = -max(1, abs(int(delta / 120))) if delta > 0 else max(1, abs(int(delta / 120)))
            self.control_canvas.yview_scroll(units, "units")
            return "break"

        for event_name in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind(event_name, scroll_controls, add="+")

        # ====================================================
        # AI INFORMATION
        # ====================================================

        ai_box = ttk.LabelFrame(
            panel,
            text="AI Detection",
            padding=10,
        )

        ai_box.pack(
            fill="x",
            pady=(0, 8),
        )

        self._info_row(
            ai_box,
            "State:",
            self.tracking_state_text,
        )

        self._info_row(
            ai_box,
            "Control target:",
            self.target_type_text,
        )

        self._info_row(
            ai_box,
            "Persons:",
            self.person_count_text,
        )

        self._info_row(
            ai_box,
            "Faces:",
            self.face_count_text,
        )

        #self._info_row(
        #    ai_box,
        #    "Person ratio:",
        #    self.person_ratio_text,
        #)

        #self._info_row(
        #    ai_box,
        #    "Face ratio:",
        #    self.face_ratio_text,
       #)

        #self._info_row(ai_box, "Ngược sáng:", self.backlight_text)

        #self._info_row(
        #    ai_box,
        #    "Sharpness:",
        #    self.sharpness_text,
        #)

        #self._info_row(
        #    ai_box,
        #    "AI time:",
        #    self.ai_time_text,
        #)

        #self._info_row(
        #    ai_box,
        #    "Digital zoom:",
        #    self.digital_zoom_text,
        #)

        ttk.Label(ai_box, textvariable=self.latency_text, justify='left').pack(anchor='w')
        ttk.Checkbutton(ai_box, text='Latency profiling', variable=self.profiling_var,
                        command=self.toggle_profiling).pack(anchor='w')

        # Runtime target replacement uses the existing AI worker/ReID pipeline.
        target_box = ttk.LabelFrame(panel, text="Target Person", padding=10)
        target_box.pack(fill="x", pady=(0, 8))
        self.target_name_text = tk.StringVar(value="Default target")
        self.target_status_text = tk.StringVar(value="TARGET: LOADING...")
        self.target_preview_label = tk.Label(target_box, text="Drag image here\nor click Select Target", width=28, height=7, relief="groove", anchor="center")
        self.target_preview_label.pack(fill="x", pady=(0, 6))
        self.target_preview_label.bind("<Button-1>", lambda _event: self.browse_target_image())
        ttk.Button(target_box, text="Select Target", command=self.browse_target_image).pack(fill="x")
        ttk.Label(target_box, textvariable=self.target_name_text, wraplength=290).pack(anchor="w", pady=(6, 0))
        ttk.Label(target_box, textvariable=self.target_status_text, wraplength=290).pack(anchor="w")
        self._enable_target_drop(self.target_preview_label)

        # ====================================================
        # AUTOMATIC CONTROL
        # ====================================================

        automatic_box = ttk.LabelFrame(
            panel,
            text="Automatic Control",
            padding=10,
        )

        automatic_box.pack(
            fill="x",
            pady=(0, 8),
        )

        ttk.Checkbutton(automatic_box, text="Auto Pan/Tilt (follow person)",
                        variable=self.auto_pan_tilt_var,
                        command=lambda: self.auto_pan_tilt.set_enabled(self.auto_pan_tilt_var.get()),
                        state="normal" if getattr(self.ptz, "supports_pan_tilt", False) else "disabled").pack(anchor="w")

        ttk.Checkbutton(
            automatic_box,
            text="Auto Zoom",
            variable=self.auto_zoom_var,
            command=self.toggle_auto_zoom,
        ).pack(
            anchor="w"
        )

        ttk.Checkbutton(
            automatic_box,
            text="Auto Focus",
            state="disabled" if cfg.PTZ_BACKEND == "hanet" else "normal",
            variable=self.auto_focus_var,
            command=self.toggle_auto_focus,
        ).pack(
            anchor="w"
        )

        ttk.Checkbutton(
            automatic_box,
            text="Digital PTZ",
            variable=self.digital_ptz_var,
            command=self.toggle_digital_ptz,
        ).pack(
            anchor="w"
        )

        # ====================================================
        # OPTICAL ZOOM
        # ====================================================

        zoom_box = ttk.LabelFrame(
            panel,
            text="Optical Zoom",
            padding=10,
        )

        zoom_box.pack(
            fill="x",
            pady=(0, 8),
        )

        ttk.Label(
            zoom_box,
            textvariable=self.optical_zoom_text,
            font=(
                "TkDefaultFont",
                16,
                "bold",
            ),
        ).pack(
            pady=(0, 6)
        )

        self.zoom_progress = ttk.Progressbar(
            zoom_box,
            orient="horizontal",
            mode="determinate",
            maximum=self.max_zoom,
            value=self.zoom_level,
        )

        self.zoom_progress.pack(
            fill="x",
            pady=(0, 8),
        )

        zoom_buttons = ttk.Frame(
            zoom_box
        )

        zoom_buttons.pack(
            fill="x"
        )

        zoom_in_button = ttk.Button(
            zoom_buttons,
            text="Zoom +",
        )

        zoom_in_button.pack(
            side="left",
            expand=True,
            fill="x",
            padx=(0, 4),
        )

        zoom_out_button = ttk.Button(
            zoom_buttons,
            text="Zoom -",
        )

        zoom_out_button.pack(
            side="left",
            expand=True,
            fill="x",
            padx=(4, 0),
        )

        self._bind_hold_button(
            zoom_in_button,
            PTZ_ZOOM_IN,
            +1,
        )

        self._bind_hold_button(
            zoom_out_button,
            PTZ_ZOOM_OUT,
            -1,
        )

        if getattr(self.ptz, 'supports_pan_tilt', False):
            direction_box = ttk.LabelFrame(panel, text="Pan / Tilt", padding=6)
            direction_box.pack(fill="x", pady=(0, 8))
            for label, command, row, col in (
                ('▲', 'tilt_up', 0, 1), ('◀', 'pan_left', 1, 0),
                ('▶', 'pan_right', 1, 2), ('▼', 'tilt_down', 2, 1)):
                button = ttk.Button(direction_box, text=label, width=5)
                button.grid(row=row, column=col)
                self._bind_focus_button(button, command)

        self.zoom_target_var = tk.StringVar(value="10")
        zoom_target_row = ttk.Frame(zoom_box)
        zoom_target_row.pack(fill="x", pady=4)
        ttk.Entry(zoom_target_row, textvariable=self.zoom_target_var, width=7).pack(side="left")
        ttk.Button(zoom_target_row, text="Đến zoom (1–37x)", command=self.goto_zoom,
                   state="disabled" if cfg.PTZ_BACKEND == "hanet" else "normal").pack(side="left")

        # ====================================================
        # FOCUS
        # ====================================================

        focus_box = ttk.LabelFrame(
            panel,
            text="Focus",
            padding=10,
        )

        focus_box.pack(
            fill="x",
            pady=(0, 8),
        )

        self.focus_position_text = tk.StringVar(value="Focus: --")
        ttk.Label(focus_box, textvariable=self.focus_position_text).pack()
        self.focus_target_var = tk.StringVar(value="12000")
        focus_target_row = ttk.Frame(focus_box)
        focus_target_row.pack(fill="x", pady=4)
        ttk.Entry(focus_target_row, textvariable=self.focus_target_var, width=7).pack(side="left")
        ttk.Button(focus_target_row, text="Đến focus", command=self.goto_focus,
                   state="disabled" if cfg.PTZ_BACKEND == "hanet" else "normal").pack(side="left")
        focus_buttons = ttk.Frame(
            focus_box
        )

        focus_buttons.pack(
            fill="x"
        )

        focus_minus = ttk.Button(
            focus_buttons,
            text="Focus -",
            state="disabled" if cfg.PTZ_BACKEND == "hanet" else "normal",
        )

        focus_minus.pack(
            side="left",
            expand=True,
            fill="x",
            padx=(0, 4),
        )

        focus_plus = ttk.Button(
            focus_buttons,
            text="Focus +",
            state="disabled" if cfg.PTZ_BACKEND == "hanet" else "normal",
        )

        focus_plus.pack(
            side="left",
            expand=True,
            fill="x",
            padx=(4, 0),
        )

        self._bind_focus_button(
            focus_minus,
            PTZ_FOCUS_MINUS,
        )

        self._bind_focus_button(
            focus_plus,
            PTZ_FOCUS_PLUS,
        )

        ttk.Button(
            focus_box,
            text="Lấy nét CNN",
            state="disabled" if cfg.PTZ_BACKEND == "hanet" else "normal",
            command=self.trigger_camera_autofocus,
        ).pack(
            fill="x",
            pady=(8, 0),
        )

        # ====================================================
        # MOTOR SPEED
        # ====================================================

        speed_box = ttk.LabelFrame(
            panel,
            text="Motor Speed",
            padding=10,
        )

        speed_box.pack(
            fill="x",
            pady=(0, 8),
        )

        self.speed_label = ttk.Label(
            speed_box,
            text=f"{cfg.PTZ_SPEED}",
        )

        self.speed_label.pack()

        speed_scale = ttk.Scale(
            speed_box,
            from_=1,
            to=getattr(self.ptz, "max_speed", 100),
            orient="horizontal",
            command=self.on_speed_changed,
        )

        speed_scale.set(
            cfg.PTZ_SPEED
        )

        speed_scale.pack(
            fill="x"
        )

        # ====================================================
        # SYSTEM
        # ====================================================

        system_box = ttk.LabelFrame(
            panel,
            text="System",
            padding=10,
        )

        system_box.pack(
            fill="x",
            pady=(0, 8),
        )

        ttk.Button(
            system_box,
            text="Reset Zoom → 1x",
            state="disabled" if cfg.PTZ_BACKEND == "hanet" else "normal",
            command=self.reset_zoom,
        ).pack(
            fill="x",
            pady=(0, 5),
        )

        ttk.Button(
            system_box,
            text="STOP",
            command=self.emergency_stop,
        ).pack(
            fill="x"
        )

        # ====================================================
        # STATUS
        # ====================================================

        status_box = ttk.LabelFrame(
            panel,
            text="Status",
            padding=10,
        )

        status_box.pack(
            fill="x"
        )

        ttk.Label(
            status_box,
            textvariable=self.status_text,
            wraplength=290,
        ).pack(
            fill="x"
        )

    # ========================================================
    # UI HELPER
    # ========================================================

    @staticmethod
    def _info_row(
        parent,
        name,
        variable,
    ):

        row = ttk.Frame(
            parent
        )

        row.pack(
            fill="x",
            pady=1,
        )

        ttk.Label(
            row,
            text=name,
        ).pack(
            side="left"
        )

        ttk.Label(
            row,
            textvariable=variable,
        ).pack(
            side="right"
        )

    # ========================================================
    # THREAD-SAFE STATUS
    # ========================================================

    def set_status(
        self,
        message: str,
    ):
        if not self.running:
            return
        try:

            self.root.after(
                0,
                self.status_text.set,
                str(message),
            )

        except Exception:
            pass

    # ========================================================
    # RUNTIME TARGET UI
    # ========================================================

    def _enable_target_drop(self, widget):
        """Use tkinterdnd2 when installed; browse remains a guaranteed fallback."""
        try:
            from tkinterdnd2 import DND_FILES
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", lambda event: self.set_target_from_image(self.root.tk.splitlist(event.data)[0]))
        except Exception:
            pass

    def browse_target_image(self):
        path = filedialog.askopenfilename(title="Select Target", filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp")])
        if path:
            self.set_target_from_image(path)

    def set_target_from_image(self, path):
        """One UI entry point for browse and native drop; inference remains on AI worker."""
        path = str(path).strip()
        if not path:
            return
        with self.target_request_lock:
            error = self.ai_initialization_error
            if error:
                message = f"AI initialization failed; restart after resolving the error: {error}"
                self._set_target_panel(error=message)
                self.set_status(f"TARGET ERROR: {message}")
                print(f"[TARGET] LOAD_FAILED reason={message}")
                return
            self.pending_target_path = path
            self.target_status_text.set("TARGET: LOADING...")
        self.set_status("TARGET: LOADING...")
        print(f"[TARGET] LOAD_REQUEST path={path}")

    def _set_target_panel(self, target=None, error=None):
        def update():
            if error:
                self.target_status_text.set(f"TARGET ERROR: {error}")
                return
            self.target_name_text.set(target["path"].rsplit("/", 1)[-1])
            self.target_status_text.set("TARGET READY")
            crop = target["crop"]
            rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            image.thumbnail((260, 150), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            self.target_preview_label.configure(image=photo, text="")
            self.target_preview_label.image = photo
        try:
            self.root.after(0, update)
        except Exception:
            pass

    # ========================================================
    # RTSP FRAME CALLBACK
    # ========================================================

    def on_camera_disconnect(self):
        self._profile_disconnect_ns = latency.profiler.event('RTSP_DISCONNECTED')
        self.frames.clear()
        with self.frame_lock:
            self.latest_frame_rgb = None
            self.latest_frame_time = None
        self.tracking_reset.set()
        self.auto_pan_tilt.cancel()
        self.auto_zoom.request_stop(reason='CAMERA_DISCONNECTED')

    def on_display_frame(self, frame_rgb):
        if self.running:
            self.display_frames.publish(frame_rgb)

    def on_frame(
        self,
        frame_rgb: np.ndarray,
    ):

        if not self.running:
            return

        packet = self.frames.publish(frame_rgb)
        disconnected = getattr(self, '_profile_disconnect_ns', None)
        if disconnected is not None:
            latency.profiler.elapsed('RTSP_RECOVERED', disconnected, 'rtsp_recovery_ms', frame_id=packet.frame_id)
            self._profile_disconnect_ns = None
        with self.frame_lock:
            self.latest_frame_rgb = frame_rgb
            self.latest_frame_time = packet.receive_timestamp
            self.frame_sequence = packet.frame_id
            self.latest_frame_received_ns = packet.received_ns

    # ========================================================
    # AI WORKER
    # ========================================================

    def notify_auto_zoom_disabled(self):
        self.root.after(0, self.auto_zoom_var.set, False)

    def reset_tracking_worker(self):
        return InferenceWorker(self).reset_tracking_worker()

    def disable_reid_auto_zoom(self):
        return InferenceWorker(self).disable_reid_auto_zoom()

    def log_face_recognition(self, key, message):
        return InferenceWorker(self).log_face_recognition(key, message)

    def ai_worker(self):
        try:
            return InferenceWorker(self).ai_worker()
        finally:
            if hasattr(self, 'auto_pan_tilt'):
                self.auto_pan_tilt.cancel()
            if hasattr(self, 'face_worker'):
                self.face_worker.close(timeout=None)

    def toggle_profiling(self):
        latency.profiler.enabled = bool(self.profiling_var.get())
        if latency.profiler.enabled:
            latency.profiler.start()

    def poll_latency(self):
        if not self.running:
            return
        values = latency.profiler.snapshot() if latency.profiler.enabled else {}
        def ms(name):
            value = values.get(name)
            return 'N/A' if value is None else f'{value:.1f} ms'
        fps = values.get('detection_fps')
        self.latency_text.set(
            f"YOLO {ms('yolo_total_ms')} | OSNet {ms('reid_inference_ms')}\n"
            f"SCRFD {ms('scrfd_inference_ms')} | ArcFace {ms('arcface_inference_ms')}\n"
            f"Frame age {ms('frame_age_since_receive_ms')} | Lock {ms('target_lock_latency_ms')}\n"
            f"Identity {ms('time_to_identity_ms')} | FPS {fps:.1f}\n" if fps is not None else 'AI latency: N/A\n')
        gui_fps = values.get('gui_unique_fps')
        if gui_fps is not None:
            self.latency_text.set(self.latency_text.get()+
                f"GUI submit {gui_fps:.1f} FPS | receive→Tk {ms('receive_to_gui_submit_ms')}\n")
        self.latency_text.set(self.latency_text.get()+
            f"Zoom: {self.auto_zoom.last_action} | Focus: {self.auto_focus.last_action}")
        if latency.profiler.error:
            self.latency_text.set('Latency logging error: '+latency.profiler.error)
        self.root.after(500, self.poll_latency)

    def update_ai_text(
        self,
        persons,
        faces,
        control,
        sharpness,
        ai_ms,
        reid_debug,
    ):

        def update():

            self.person_count_text.set(
                str(
                    len(persons)
                )
            )

            self.face_count_text.set(
                str(
                    len(faces)
                )
            )

            if control is None:
                return

            self.tracking_state_text.set(
                reid_debug["state"]
            )

            self.target_type_text.set(
                control.target_type
                or
                "---"
            )

            if (
                control.person_ratio
                is not None
            ):

                self.person_ratio_text.set(
                    f"{control.person_ratio:.3f}"
                )

            else:

                self.person_ratio_text.set(
                    "---"
                )

            if (
                control.face_ratio
                is not None
            ):

                self.face_ratio_text.set(
                    f"{control.face_ratio:.3f}"
                )

            else:

                self.face_ratio_text.set(
                    "---"
                )

            self.sharpness_text.set(
                f"{sharpness:.1f}"
            )

            self.ai_time_text.set(
                f"{ai_ms:.1f} ms"
            )

        try:

            self.root.after(
                0,
                update,
            )

        except Exception:
            pass

    # ========================================================
    # VIDEO DISPLAY
    # ========================================================

    def update_video(self):

        if not self.running:
            return

        packet = self.display_frames.latest()
        frame_rgb = packet.image if packet is not None else None
        if packet is not None:
            display_frame_id = packet.frame_id
            display_received_ns = packet.received_ns

        if frame_rgb is not None:

            frame_bgr = cv2.cvtColor(
                frame_rgb,
                cv2.COLOR_RGB2BGR,
            )

            with self.ai_lock:

                result = dict(
                    self.ai_result
                )

            from runtime.display_mapping import display_result
            result = display_result(result, frame_bgr.shape, time.monotonic(),
                                    cfg.MAX_CONTROL_OBSERVATION_AGE)

            # ------------------------------------------------
            # DIGITAL PTZ
            # ------------------------------------------------

            display_bgr, transform = (
                self.digital_ptz.render(
                    frame_bgr
                )
            )

            digital_zoom = (
                transform.get(
                    "zoom",
                    1.0
                )
            )

            self.digital_zoom_text.set(
                f"{digital_zoom:.2f}x"
            )

            # ------------------------------------------------
            # TRANSFORM DETECTIONS
            # ------------------------------------------------

            persons = (
                self.digital_ptz
                .transform_detections(
                    result.get(
                        "persons",
                        []
                    ),
                    transform,
                )
            )

            faces = (
                self.digital_ptz
                .transform_detections(
                    result.get(
                        "faces",
                        []
                    ),
                    transform,
                )
            )

            person_target = (
                self.digital_ptz
                .transform_detection(
                    result.get(
                        "person_target"
                    ),
                    transform,
                )
            )

            face_target = (
                self.digital_ptz
                .transform_detection(
                    result.get(
                        "face_target"
                    ),
                    transform,
                )
            )

            # ------------------------------------------------
            # DRAW
            # ------------------------------------------------

            self.draw_detections(
                display_bgr,
                persons,
                faces,
                person_target,
                face_target,
                result.get("control"),
                result.get("recognition"),
                result.get("display_recognition"),
                result.get("reid"),
            )

            # ------------------------------------------------
            # BGR -> RGB
            # ------------------------------------------------

            if cfg.FACE_PANEL_ENABLED:
                from gui.face_panel import draw_face_panel
                draw_face_panel(display_bgr, result.get('face_panel'), cfg.FACE_PANEL_DEBUG)
            display_rgb = cv2.cvtColor(
                display_bgr,
                cv2.COLOR_BGR2RGB,
            )

            with latency.correlated(frame_id=display_frame_id):
                with latency.span('GUI_RENDER', 'gui_render_ms'):
                    displayed = self.show_frame(display_rgb)
                if displayed:
                    latency.profiler.count('gui_submissions')
                    latency.profiler.elapsed('GUI_SUBMITTED', display_received_ns,
                        'receive_to_gui_submit_ms', evidence='Tk_image_configure_not_physical_display')
                    if display_frame_id != getattr(self, '_profile_display_id', None):
                        latency.profiler.count('gui_unique_frames')
                        self._profile_display_id = display_frame_id

        self.root.after(
            cfg.GUI_UPDATE_MS,
            self.update_video,
        )

    # ========================================================
    # DRAW DETECTIONS
    # ========================================================

    def draw_detections(
        self,
        frame,
        persons,
        faces,
        person_target,
        face_target,
        control,
        recognition,
        display_recognition=None,
        reid_debug=None,
    ):

        # ----------------------------------------------------
        # ALL PERSONS
        # ----------------------------------------------------

        for det in persons:

            x1, y1, x2, y2 = (
                det["bbox"]
            )

            cv2.rectangle(
                frame,
                (x1, y1),
                (x2, y2),
                (255, 0, 0),
                2,
            )

        # ----------------------------------------------------
        # ALL FACES
        # ----------------------------------------------------

        for det in faces:

            x1, y1, x2, y2 = (
                det["bbox"]
            )

            cv2.rectangle(
                frame,
                (x1, y1),
                (x2, y2),
                (0, 255, 255),
                2,
            )

            if cfg.FACE_DRAW_LANDMARKS:
                for point in det.get("landmarks", []):
                    cv2.circle(
                        frame,
                        tuple(np.rint(point).astype(int)),
                        2,
                        (255, 0, 255),
                        -1,
                    )

        # ----------------------------------------------------
        # TARGET PERSON
        # ----------------------------------------------------

        if person_target is not None:

            x1, y1, x2, y2 = (
                person_target["bbox"]
            )

            cv2.rectangle(
                frame,
                (x1, y1),
                (x2, y2),
                (0, 255, 0),
                3,
            )

        # ----------------------------------------------------
        # TARGET FACE
        # ----------------------------------------------------

        if face_target is not None:

            x1, y1, x2, y2 = (
                face_target["bbox"]
            )

            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 4)

        # Compact, single overlay: identity and target confirmation only.
        # Detailed scores, state transitions and errors remain in diagnostics.
        identity = 'WAIT'
        identity_source = display_recognition or recognition
        confirmed = bool(identity_source and identity_source.get('confirmed'))
        if identity_source:
            identity = identity_source['identity'] if confirmed else (
                'CANDIDATE ' + identity_source['identity'] if identity_source.get('matched') else 'UNKNOWN')
        lines = [f"ID: {identity}" + (" | CONFIRMED" if confirmed else "")]
        if reid_debug:
            lines.append('Target: LOCKED' if reid_debug.get('locked') else
                         f"Confirm {reid_debug.get('confirm', 0)}/{reid_debug.get('required', 3)}")
            if reid_debug.get('error'):
                lines.append('AI unavailable - see Status / log')
        scale = max(.35, min(.65, frame.shape[1] / 1100.))
        for row, text in enumerate(lines):
            # Fit long identity names inside the video as well.
            text_scale = scale
            (width, height), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, text_scale, 1)
            if width > frame.shape[1] - 28:
                text_scale *= max(1, frame.shape[1] - 28) / width
                (width, height), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, text_scale, 1)
            y = 14 + height + row * int(32 * scale / .65)
            cv2.rectangle(frame, (8, y-height-5), (20+width, y+baseline+4), (20, 20, 20), -1)
            color = (0, 255, 0) if row == 0 and confirmed else (255, 255, 255)
            cv2.putText(frame, text, (14, y), cv2.FONT_HERSHEY_SIMPLEX,
                        text_scale, color, 1, cv2.LINE_AA)

    # ========================================================
    # DISPLAY FRAME IN TKINTER
    # ========================================================

    def show_frame(
        self,
        frame_rgb,
    ):

        label_w = (
            self.video_label.winfo_width()
        )

        label_h = (
            self.video_label.winfo_height()
        )

        if (
            label_w <= 1
            or
            label_h <= 1
        ):

            return

        frame_h, frame_w = (
            frame_rgb.shape[:2]
        )

        scale = min(
            label_w / frame_w,
            label_h / frame_h,
        )

        display_w = max(
            1,
            int(
                frame_w * scale
            )
        )

        display_h = max(
            1,
            int(
                frame_h * scale
            )
        )

        resized = cv2.resize(
            frame_rgb,
            (
                display_w,
                display_h
            ),
            interpolation=cv2.INTER_LINEAR,
        )

        image = Image.fromarray(
            resized
        )

        photo = ImageTk.PhotoImage(
            image=image
        )

        self.video_label.configure(
            image=photo,
            text="",
        )

        self.video_label.image = photo
        return True

    # ========================================================
    # AUTOMATIC CONTROL BUTTONS
    # ========================================================

    def toggle_auto_zoom(self):

        enabled = (
            self.auto_zoom_var.get()
        )

        with self.ai_lock:
            ready = self.ai_result.get("reid", {}).get("valid", False)
        if enabled and not ready:
            self.auto_zoom_var.set(False)
            self.auto_zoom.set_enabled(False, wait=False)
            self.reid_retry.set()
            self.set_status("Auto Zoom OFF: retrying target initialization in AI worker...")
            return
        self.tracking_reset.set()
        self.auto_zoom.set_enabled(enabled, wait=False)

        if enabled:

            self.auto_focus.unlock()

        else:

            self.auto_zoom.request_stop(reason="OPERATOR_DISABLED")

        self.set_status(
            (
                "Auto Zoom ON"
                if enabled
                else
                "Auto Zoom OFF"
            )
        )

    def toggle_auto_focus(self):
        if cfg.PTZ_BACKEND == "hanet":
            self.auto_focus_var.set(False)
            self.auto_focus.set_enabled(False)
            return

        enabled = (
            self.auto_focus_var.get()
        )

        self.auto_focus.set_enabled(
            enabled
        )

        self.set_status(
            (
                "Auto Focus ON"
                if enabled
                else
                "Auto Focus OFF"
            )
        )

    def toggle_digital_ptz(self):

        enabled = (
            self.digital_ptz_var.get()
        )

        self.digital_ptz.set_enabled(
            enabled
        )

        self.set_status(
            (
                "Digital PTZ ON"
                if enabled
                else
                "Digital PTZ OFF"
            )
        )

    # ========================================================
    # SPEED
    # ========================================================

    def on_speed_changed(
        self,
        value,
    ):

        speed = int(
            float(value)
        )

        self.motor_speed_var.set(
            speed
        )

        if hasattr(
            self,
            "speed_label",
        ):

            self.speed_label.configure(
                text=str(speed)
            )

        self.ptz.set_speed(
            speed
        )

    def get_motor_speed(self):

        return int(
            self.motor_speed_var.get()
        )

    # ========================================================
    # MANUAL ZOOM BUTTON BINDING
    # ========================================================

    def _bind_hold_button(
        self,
        button,
        command,
        direction,
    ):

        button.bind(
            "<ButtonPress-1>",
            lambda event:
                self.start_manual_zoom(
                    command,
                    direction,
                ),
        )

        button.bind(
            "<ButtonRelease-1>",
            lambda event:
                self.stop_manual_zoom(),
        )

        button.bind(
            "<Leave>",
            lambda event:
                self.stop_manual_zoom(),
        )

    # ========================================================
    # MANUAL FOCUS BUTTON BINDING
    # ========================================================

    def _bind_focus_button(
        self,
        button,
        command,
    ):

        button.bind(
            "<ButtonPress-1>",
            lambda event:
                self.start_manual_focus(
                    command
                ),
        )

        button.bind(
            "<ButtonRelease-1>",
            lambda event:
                self.stop_manual_focus(),
        )

        button.bind(
            "<Leave>",
            lambda event:
                self.stop_manual_focus(),
        )

    # ========================================================
    # MANUAL ZOOM
    # ========================================================

    def _manual_override(self):
        self.auto_pan_tilt_var.set(False)
        self.auto_pan_tilt.set_enabled(False, reason="MANUAL_OVERRIDE")
        self.set_status("Manual control: Auto Pan/Tilt is off; enable it again to resume following")
        self.auto_zoom_var.set(False)
        self.auto_focus_var.set(False)
        self.tracking_reset.set()

    def start_manual_zoom(self, command, direction):
        self._manual_override()
        self.manual_controller.request(command, self.get_motor_speed())
        with self.zoom_lock:
            self.zoom_direction = direction
            self.zoom_started = time.monotonic()
            self.zoom_start_level = self.zoom_level

    def stop_manual_zoom(self):
        with self.zoom_lock:
            if self.zoom_started is None:
                return
            self._update_manual_zoom_locked()
            self.zoom_started = None
            self.zoom_direction = 0
        self.manual_controller.request()

    def start_manual_focus(self, command):
        self._manual_override()
        self._manual_focus_active = True
        self.manual_controller.request(command, self.get_motor_speed())

    def stop_manual_focus(self):
        if not getattr(self, '_manual_focus_active', False):
            return
        self._manual_focus_active = False
        self.manual_controller.request()

    def trigger_camera_autofocus(self):
        from camera.ptz_controller import PTZ_AUTO_FOCUS
        self._manual_override()
        self.manual_controller.request(PTZ_AUTO_FOCUS, self.get_motor_speed(), cfg.AUTO_FOCUS_SETTLE_TIME)

    # ========================================================
    # OPTICAL ZOOM TELEMETRY
    # ========================================================

    def apply_auto_zoom_estimate(self, direction, duration):
        # Compatibility callback name; actual position comes only from RPC2.
        self.auto_focus.unlock()

    def _update_manual_zoom_locked(self):
        telemetry = self.ptz.telemetry()
        if telemetry is not None:
            self.zoom_level = telemetry['zoom']

    def update_zoom_position(self):
        if not self.running:
            return
        telemetry = self.ptz.telemetry()
        if telemetry is not None:
            with self.zoom_lock:
                self.zoom_level = telemetry['zoom']
            self.optical_zoom_text.set(f"{telemetry['zoom']:.2f}x")
            self.zoom_progress['value'] = telemetry['zoom']
            focus = telemetry['focus']
            self.focus_position_text.set('--' if focus is None else f"Focus: {focus:.0f} / 32768")
        else:
            self.optical_zoom_text.set('Zoom: -- (không có vị trí)' if cfg.PTZ_BACKEND == 'hanet' else 'Zoom: -- (chờ camera)')
            self.focus_position_text.set('Focus: --')
        self.root.after(100, self.update_zoom_position)

    def refresh_zoom_ui_async(self):

        def refresh():

            with self.zoom_lock:

                zoom = (
                    self.zoom_level
                )

            self.optical_zoom_text.set(
                f"{zoom:.2f}x"
            )

            self.zoom_progress[
                "value"
            ] = zoom

        try:

            self.root.after(
                0,
                refresh,
            )

        except Exception:
            pass

    def set_zoom_level(
        self,
        zoom,
    ):

        with self.zoom_lock:

            self.zoom_level = max(
                self.min_zoom,
                min(
                    self.max_zoom,
                    float(zoom)
                )
            )

        self.refresh_zoom_ui_async()

    def get_zoom_level(self):
        telemetry = self.ptz.telemetry()
        return float('nan') if telemetry is None else telemetry['zoom']

    def goto_zoom(self):
        try:
            target = float(self.zoom_target_var.get())
            if not 1 <= target <= 37:
                raise ValueError()
        except ValueError:
            self.set_status('Zoom phải nằm trong 1–37x')
            return
        self._manual_override()
        self.manual_controller.request(('zoom_target', target))

    def goto_focus(self):
        try:
            target = float(self.focus_target_var.get())
            if not 0 <= target <= 32768:
                raise ValueError()
        except ValueError:
            self.set_status('Focus phải nằm trong 0–32768')
            return
        self._manual_override()
        self.manual_controller.request(('focus_target', target))

    def reset_zoom(self):
        self._manual_override()
        self.set_status('Đang đưa zoom về 1x...')
        def completed():
            self.tracking_reset.set()
            self.auto_focus.reset()
            self.set_status('Đã xác nhận zoom về gần 1x qua camera')
        self.manual_controller.request(('zoom_target', self.min_zoom), completed=completed)

    # ========================================================
    # EMERGENCY STOP
    # ========================================================

    def emergency_stop(self):
        self._manual_override()
        self.digital_ptz_var.set(False)
        self.digital_ptz.set_enabled(False)
        self.manual_controller.request()
        self.set_status('EMERGENCY STOP requested')

    # ========================================================
    # CLOSE
    # ========================================================

    def on_close(self):
        if getattr(self, '_closing', False):
            return
        self._closing = True
        self.running = False
        self.orchestrator.begin_shutdown()
        begin = getattr(type(self.ptz), 'begin_shutdown', None)
        if begin:
            begin(self.ptz)
        self.root.title('AI PTZ Camera — Đang dừng camera…')
        self.image_settings_stop.set()
        self.auto_pan_tilt.set_enabled(False)
        self.manual_controller.close()
        self.auto_zoom.request_stop(reason='APPLICATION_CLOSE')
        self.auto_focus.set_enabled(False)
        motor_done = threading.Event()
        stream_done = threading.Event()

        latency.profiler.event('SHUTDOWN_REQUESTED', shutdown_phase='gui')
        shutdown_result = {}

        def close_motor():
            from runtime.shutdown import close_controls
            try:
                shutdown_result.update(close_controls(self.auto_pan_tilt, self.auto_zoom,
                    self.auto_focus, self.manual_controller, self.ptz, self.orchestrator,
                    timeout=12., status=self.set_status))
            except Exception as exc:
                shutdown_result['unconfirmed_motor'] = True
                latency.profiler.event('SHUTDOWN_CLEANUP_ERROR', shutdown_phase='controls',
                    exception_type=type(exc).__name__, exception_message='Control teardown failed')
            finally:
                try:
                    if self._ai_started:
                        self.ai_thread.join(timeout=1.)
                        if self.ai_thread.is_alive():
                            latency.profiler.event('SHUTDOWN_CLEANUP_ERROR', shutdown_phase='ai_worker',
                                                   exception_message='Worker join timed out')
                    if hasattr(self, 'face_worker') and self.face_worker.close(timeout=1.) is False:
                        latency.profiler.event('SHUTDOWN_CLEANUP_ERROR', shutdown_phase='face_worker',
                                               exception_message='Worker join timed out')
                except Exception as exc:
                    latency.profiler.event('SHUTDOWN_CLEANUP_ERROR', shutdown_phase='inference',
                                           exception_type=type(exc).__name__)
                motor_done.set()

        def close_stream():
            try:
                self.rtsp.stop()
            except Exception as exc:
                print(f'[CLOSE] RTSP cleanup failed: {type(exc).__name__}')
            finally:
                stream_done.set()

        threading.Thread(target=close_motor, name='close-camera', daemon=True).start()
        threading.Thread(target=close_stream, name='close-rtsp', daemon=True).start()
        deadline = time.monotonic() + 15.

        def finish_close():
            expired = time.monotonic() >= deadline
            if (motor_done.is_set() and stream_done.is_set()) or expired:
                if expired or shutdown_result.get('unconfirmed_motor', False):
                    from runtime.shutdown import UNCONFIRMED_WARNING
                    # Warning is also written to the application log, which
                    # survives Tk teardown. Do not claim physical motor STOP.
                    print('[CLOSE] '+UNCONFIRMED_WARNING)
                    self.root.title('AI PTZ — STOP CHƯA XÁC NHẬN')
                    latency.profiler.event('SHUTDOWN_COMPLETED_WITH_UNCONFIRMED_MOTOR',
                        shutdown_phase='gui', physical_motor_stopped=None,
                        controls_finished=motor_done.is_set(), stream_finished=stream_done.is_set())
                logs_done = threading.Event()
                log_deadline = time.monotonic()+2.
                def close_logs():
                    try:
                        if latency.profiler.close(timeout=1.) is False:
                            print('[CLOSE] Latency writer shutdown timed out')
                        from telemetry import zoom_trace
                        if zoom_trace.recorder.close(timeout=1.) is False:
                            print('[CLOSE] Zoom writer shutdown timed out')
                    except Exception as exc:
                        print(f'[CLOSE] Log cleanup failed: {type(exc).__name__}')
                    finally:
                        logs_done.set()
                def finish_logs():
                    if logs_done.is_set() or time.monotonic() >= log_deadline:
                        self.root.destroy()
                    else:
                        self.root.after(50, finish_logs)
                threading.Thread(target=close_logs, name='close-logs', daemon=True).start()
                finish_logs()
            else:
                self.root.after(50, finish_close)

        finish_close()
