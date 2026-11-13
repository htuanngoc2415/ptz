"""Select the video receiver without changing the inference device."""


def RTSPPlayer(config, frame_callback, status_callback=None, disconnect_callback=None):
    backend = config.get('video_backend', 'gstreamer')
    if backend == 'opencv':
        from .opencv_player import OpenCVPlayer
        receiver = OpenCVPlayer
    elif backend == 'mpv':
        from .mpv_player import MPVPlayer
        receiver = MPVPlayer
    elif backend == 'gstreamer':
        from .rtsp_player import RTSPPlayer as receiver
    elif backend == 'gstreamer_cpu':
        from .rtsp_player import CPUStreamPlayer as receiver
    else:
        raise ValueError(f'Unknown video_backend: {backend}')
    return receiver(config, frame_callback, status_callback, disconnect_callback)


class DualStreamPlayer:
    """Independent main-stream display and sub-stream inference receivers."""
    def __init__(self, config, frame_callback, display_callback,
                 status_callback=None, disconnect_callback=None,
                 display_disconnect_callback=None):
        self.ai = RTSPPlayer(config, frame_callback, status_callback, disconnect_callback)
        display_config = dict(config, uri=config.get('display_uri', config['uri']))
        self.display = RTSPPlayer(display_config, display_callback,
                                  status_callback, display_disconnect_callback)

    def start(self):
        try:
            self.ai.start()
            self.display.start()
        except Exception:
            self.stop()
            raise

    def stop(self):
        try:
            self.ai.stop()
        finally:
            self.display.stop()
