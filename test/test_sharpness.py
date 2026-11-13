#!/usr/bin/env python3

import sys
import json
from pathlib import Path
import threading
import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

try:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

except (ImportError, ValueError) as exc:
    print(
        "Không import được GStreamer GI.\n"
        "Cài bằng:\n"
        "sudo apt install python3-gi gir1.2-gstreamer-1.0\n"
        f"Chi tiết: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(2)


# ============================================================
# CAMERA CONFIG
# ============================================================

CAMERA_CONFIG = json.loads((Path(__file__).resolve().parents[1] / "camera.json").read_text())
RTSP_URI = CAMERA_CONFIG["uri"]
RTSP_USERNAME = CAMERA_CONFIG.get("username", "")
RTSP_PASSWORD = CAMERA_CONFIG.get("password", "")

RTSP_LATENCY_MS = 200


# ============================================================
# SHARPNESS METRICS
# ============================================================

def variance_of_laplacian(frame_bgr):
    """
    Độ nét dựa trên variance của Laplacian.

    Giá trị càng lớn -> ảnh thường càng sắc nét.
    """

    gray = cv2.cvtColor(
        frame_bgr,
        cv2.COLOR_BGR2GRAY
    )

    laplacian = cv2.Laplacian(
        gray,
        cv2.CV_64F
    )

    return float(
        laplacian.var()
    )


def tenengrad(frame_bgr):
    """
    Tenengrad dựa trên gradient Sobel.
    """

    gray = cv2.cvtColor(
        frame_bgr,
        cv2.COLOR_BGR2GRAY
    )

    gx = cv2.Sobel(
        gray,
        cv2.CV_64F,
        1,
        0,
        ksize=3
    )

    gy = cv2.Sobel(
        gray,
        cv2.CV_64F,
        0,
        1,
        ksize=3
    )

    magnitude_squared = (
        gx * gx
        +
        gy * gy
    )

    return float(
        np.mean(magnitude_squared)
    )


# ============================================================
# RTSP PLAYER
# ============================================================

class RTSPPlayer:

    def __init__(
        self,
        uri,
        frame_callback,
        status_callback
    ):

        self.uri = uri

        self.frame_callback = (
            frame_callback
        )

        self.status_callback = (
            status_callback
        )

        self.player = None
        self.appsink = None

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    def start(self):

        Gst.init(None)

        self.player = (
            Gst.ElementFactory.make(
                "playbin",
                "camera-player"
            )
        )

        if self.player is None:
            raise RuntimeError(
                "Không tạo được GStreamer playbin"
            )

        # ----------------------------------------------------
        # playbin
        #   ↓
        # queue
        #   ↓
        # videoconvert
        #   ↓
        # BGR
        #   ↓
        # appsink
        #
        # Chuyển thẳng sang BGR vì OpenCV sử dụng BGR.
        # ----------------------------------------------------

        sink_description = (
            "queue "
            "leaky=downstream "
            "max-size-buffers=2 "
            "! "
            "videoconvert "
            "! "
            "video/x-raw,format=BGR "
            "! "
            "appsink "
            "name=gui_sink "
            "emit-signals=true "
            "sync=false "
            "max-buffers=1 "
            "drop=true"
        )

        video_sink = (
            Gst.parse_bin_from_description(
                sink_description,
                True
            )
        )

        self.appsink = (
            video_sink.get_by_name(
                "gui_sink"
            )
        )

        self.appsink.connect(
            "new-sample",
            self.on_new_sample
        )

        self.player.set_property(
            "uri",
            self.uri
        )

        self.player.set_property(
            "video-sink",
            video_sink
        )

        # Disable audio / subtitle
        flags = self.player.get_property(
            "flags"
        )

        self.player.set_property(
            "flags",
            flags
            & ~(1 << 1)
            & ~(1 << 2)
        )

        self.player.connect(
            "source-setup",
            self.on_source_setup
        )

        # ----------------------------------------------------
        # BUS
        # ----------------------------------------------------

        bus = self.player.get_bus()

        bus.add_signal_watch()

        bus.connect(
            "message",
            self.on_bus_message
        )

        # ----------------------------------------------------
        # PLAY
        # ----------------------------------------------------

        result = self.player.set_state(
            Gst.State.PLAYING
        )

        if (
            result
            == Gst.StateChangeReturn.FAILURE
        ):
            raise RuntimeError(
                "Không thể chạy RTSP"
            )

    # --------------------------------------------------------
    # RTSP CONFIG
    # --------------------------------------------------------

    def on_source_setup(
        self,
        player,
        source
    ):

        if source.find_property(
            "latency"
        ):
            source.set_property(
                "latency",
                RTSP_LATENCY_MS
            )

        if source.find_property(
            "drop-on-latency"
        ):
            source.set_property(
                "drop-on-latency",
                True
            )

        # TCP = 4
        if source.find_property(
            "protocols"
        ):
            source.set_property(
                "protocols",
                4
            )

        if (
            RTSP_USERNAME
            and source.find_property(
                "user-id"
            )
        ):
            source.set_property(
                "user-id",
                RTSP_USERNAME
            )

        if (
            RTSP_PASSWORD
            and source.find_property(
                "user-pw"
            )
        ):
            source.set_property(
                "user-pw",
                RTSP_PASSWORD
            )

    # --------------------------------------------------------
    # NEW FRAME
    # --------------------------------------------------------

    def on_new_sample(
        self,
        sink
    ):

        sample = sink.emit(
            "pull-sample"
        )

        if sample is None:
            return Gst.FlowReturn.ERROR

        caps = sample.get_caps()

        structure = caps.get_structure(
            0
        )

        width = structure.get_value(
            "width"
        )

        height = structure.get_value(
            "height"
        )

        buffer = sample.get_buffer()

        success, map_info = buffer.map(
            Gst.MapFlags.READ
        )

        if not success:
            return Gst.FlowReturn.ERROR

        try:

            data = np.frombuffer(
                map_info.data,
                dtype=np.uint8
            ).copy()

            frame = data.reshape(
                (
                    height,
                    width,
                    3
                )
            )

            # callback nhận frame BGR
            self.frame_callback(
                frame
            )

        finally:

            buffer.unmap(
                map_info
            )

        return Gst.FlowReturn.OK

    # --------------------------------------------------------
    # BUS
    # --------------------------------------------------------

    def on_bus_message(
        self,
        bus,
        message
    ):

        if (
            message.type
            == Gst.MessageType.ERROR
        ):

            error, debug = (
                message.parse_error()
            )

            self.status_callback(
                f"RTSP ERROR: {error.message}"
            )

            if debug:
                print(
                    debug,
                    file=sys.stderr
                )

        elif (
            message.type
            == Gst.MessageType.STATE_CHANGED
            and message.src == self.player
        ):

            old, new, pending = (
                message.parse_state_changed()
            )

            if (
                new
                == Gst.State.PLAYING
            ):
                self.status_callback(
                    "RTSP connected"
                )

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    def stop(self):

        if self.player:

            self.player.set_state(
                Gst.State.NULL
            )


# ============================================================
# GUI
# ============================================================

class SharpnessGUI:

    def __init__(
        self,
        root
    ):

        self.root = root

        self.root.title(
            "CCTV Sharpness Monitor"
        )

        self.root.geometry(
            "1280x760"
        )

        self.root.minsize(
            900,
            600
        )

        # ----------------------------------------------------
        # FRAME BUFFER
        # ----------------------------------------------------

        self.frame_lock = (
            threading.Lock()
        )

        self.latest_frame = None

        self.tk_image = None

        # ----------------------------------------------------
        # METRIC VALUES
        # ----------------------------------------------------

        self.laplacian_value = (
            tk.StringVar(
                value="0.00"
            )
        )

        self.tenengrad_value = (
            tk.StringVar(
                value="0.00"
            )
        )

        self.status_value = (
            tk.StringVar(
                value="Starting..."
            )
        )

        # Running average để xem score ổn định hơn
        self.laplacian_history = []
        self.tenengrad_history = []

        self.history_length = 10

        # ----------------------------------------------------
        # GUI
        # ----------------------------------------------------

        self.create_gui()

        # ----------------------------------------------------
        # RTSP
        # ----------------------------------------------------

        self.rtsp = RTSPPlayer(
            RTSP_URI,
            self.receive_frame,
            self.set_status
        )

        threading.Thread(
            target=self.start_rtsp,
            daemon=True
        ).start()

        # GUI refresh
        self.update_gui()

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.close
        )

    # ========================================================
    # GUI
    # ========================================================

    def create_gui(self):

        self.root.columnconfigure(
            0,
            weight=1
        )

        self.root.columnconfigure(
            1,
            weight=0
        )

        self.root.rowconfigure(
            0,
            weight=1
        )

        # ----------------------------------------------------
        # VIDEO
        # ----------------------------------------------------

        video_frame = ttk.Frame(
            self.root
        )

        video_frame.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=8,
            pady=8
        )

        video_frame.rowconfigure(
            0,
            weight=1
        )

        video_frame.columnconfigure(
            0,
            weight=1
        )

        self.video_label = tk.Label(
            video_frame,
            bg="black"
        )

        self.video_label.grid(
            row=0,
            column=0,
            sticky="nsew"
        )

        # ----------------------------------------------------
        # RIGHT PANEL
        # ----------------------------------------------------

        panel = ttk.Frame(
            self.root,
            width=320
        )

        panel.grid(
            row=0,
            column=1,
            sticky="ns",
            padx=15,
            pady=15
        )

        # ----------------------------------------------------
        # TITLE
        # ----------------------------------------------------

        ttk.Label(
            panel,
            text="FOCUS MONITOR",
            font=(
                "Arial",
                18,
                "bold"
            )
        ).pack(
            pady=(10, 25)
        )

        # ----------------------------------------------------
        # LAPLACIAN
        # ----------------------------------------------------

        lap_box = ttk.LabelFrame(
            panel,
            text="Variance of Laplacian"
        )

        lap_box.pack(
            fill="x",
            pady=10
        )

        ttk.Label(
            lap_box,
            textvariable=
                self.laplacian_value,
            font=(
                "Arial",
                30,
                "bold"
            )
        ).pack(
            pady=20
        )

        ttk.Label(
            lap_box,
            text=(
                "Giá trị lớn hơn thường "
                "tương ứng ảnh nét hơn"
            ),
            wraplength=260
        ).pack(
            pady=(0, 15)
        )

        # ----------------------------------------------------
        # TENENGRAD
        # ----------------------------------------------------

        ten_box = ttk.LabelFrame(
            panel,
            text="Tenengrad"
        )

        ten_box.pack(
            fill="x",
            pady=10
        )

        ttk.Label(
            ten_box,
            textvariable=
                self.tenengrad_value,
            font=(
                "Arial",
                30,
                "bold"
            )
        ).pack(
            pady=20
        )

        ttk.Label(
            ten_box,
            text=(
                "Gradient energy dựa "
                "trên Sobel"
            ),
            wraplength=260
        ).pack(
            pady=(0, 15)
        )

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        status_box = ttk.LabelFrame(
            panel,
            text="Status"
        )

        status_box.pack(
            fill="x",
            pady=15
        )

        ttk.Label(
            status_box,
            textvariable=
                self.status_value,
            wraplength=260
        ).pack(
            anchor="w",
            padx=10,
            pady=10
        )

    # ========================================================
    # START RTSP
    # ========================================================

    def start_rtsp(self):

        try:

            self.rtsp.start()

        except Exception as exc:

            self.set_status(
                f"RTSP error: {exc}"
            )

    # ========================================================
    # RECEIVE FRAME
    # ========================================================

    def receive_frame(
        self,
        frame
    ):

        with self.frame_lock:

            # Chỉ giữ frame mới nhất
            self.latest_frame = frame

    # ========================================================
    # UPDATE GUI
    # ========================================================

    def update_gui(self):

        frame = None

        with self.frame_lock:

            if (
                self.latest_frame
                is not None
            ):

                frame = (
                    self.latest_frame
                )

                self.latest_frame = None

        if frame is not None:

            # ------------------------------------------------
            # SHARPNESS
            # ------------------------------------------------

            lap = (
                variance_of_laplacian(
                    frame
                )
            )

            ten = tenengrad(
                frame
            )

            # ------------------------------------------------
            # HISTORY
            #
            # Running average 10 frame.
            # Giúp giảm dao động do noise/compression.
            # ------------------------------------------------

            self.laplacian_history.append(
                lap
            )

            self.tenengrad_history.append(
                ten
            )

            if (
                len(
                    self.laplacian_history
                )
                > self.history_length
            ):
                self.laplacian_history.pop(
                    0
                )

            if (
                len(
                    self.tenengrad_history
                )
                > self.history_length
            ):
                self.tenengrad_history.pop(
                    0
                )

            lap_avg = (
                sum(
                    self.laplacian_history
                )
                /
                len(
                    self.laplacian_history
                )
            )

            ten_avg = (
                sum(
                    self.tenengrad_history
                )
                /
                len(
                    self.tenengrad_history
                )
            )

            self.laplacian_value.set(
                f"{lap_avg:.2f}"
            )

            self.tenengrad_value.set(
                f"{ten_avg:.2f}"
            )

            # ------------------------------------------------
            # BGR -> RGB
            # ------------------------------------------------

            frame_rgb = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2RGB
            )

            image = Image.fromarray(
                frame_rgb
            )

            # ------------------------------------------------
            # RESIZE VIDEO
            # ------------------------------------------------

            widget_width = (
                self.video_label.winfo_width()
            )

            widget_height = (
                self.video_label.winfo_height()
            )

            if (
                widget_width > 10
                and widget_height > 10
            ):

                image_width, image_height = (
                    image.size
                )

                scale = min(
                    widget_width
                    / image_width,

                    widget_height
                    / image_height
                )

                new_width = max(
                    1,
                    int(
                        image_width
                        * scale
                    )
                )

                new_height = max(
                    1,
                    int(
                        image_height
                        * scale
                    )
                )

                image = image.resize(
                    (
                        new_width,
                        new_height
                    ),
                    Image.Resampling.BILINEAR
                )

            self.tk_image = (
                ImageTk.PhotoImage(
                    image=image
                )
            )

            self.video_label.configure(
                image=self.tk_image
            )

        # GUI ~30 FPS
        self.root.after(
            33,
            self.update_gui
        )

    # ========================================================
    # STATUS
    # ========================================================

    def set_status(
        self,
        text
    ):

        self.root.after(
            0,
            lambda:
            self.status_value.set(
                text
            )
        )

    # ========================================================
    # CLOSE
    # ========================================================

    def close(self):

        try:
            self.rtsp.stop()

        except Exception:
            pass

        self.root.destroy()


# ============================================================
# MAIN
# ============================================================

def main():

    root = tk.Tk()

    app = SharpnessGUI(
        root
    )

    root.mainloop()


if __name__ == "__main__":
    main()
