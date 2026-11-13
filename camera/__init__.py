from .ptz_controller import (
    PTZController,
    PTZ_STOP,
    PTZ_ZOOM_IN,
    PTZ_ZOOM_OUT,
    PTZ_FOCUS_PLUS,
    PTZ_FOCUS_MINUS,
    PTZ_AUTO_FOCUS,
)

def __getattr__(name):
    if name == "RTSPPlayer":
        from .stream import RTSPPlayer
        return RTSPPlayer
    raise AttributeError(name)


__all__ = [
    "PTZController",
    "RTSPPlayer",

    "PTZ_STOP",
    "PTZ_ZOOM_IN",
    "PTZ_ZOOM_OUT",
    "PTZ_FOCUS_PLUS",
    "PTZ_FOCUS_MINUS",
    "PTZ_AUTO_FOCUS",
]


def create_controller(backend='dahua', **kwargs):
    """Choose a camera protocol explicitly; never guess from an IP address."""
    if backend == 'hanet':
        from .hanet_controller import HanetController
        return HanetController(**kwargs)
    if backend == 'dahua':
        return PTZController(**kwargs)
    raise ValueError(f'Unknown PTZ backend: {backend}')
