"""Image-settings compatibility boundary for the 37x RPC2 camera.

The reference establishes VideoInFocus and VideoInZoom only. The old camera's
CGI exposure/WDR keys have no verified Dahua mapping and must never be sent.
"""


def validate_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError('backlight must be an object')
    if type(settings.get('enabled', False)) is not bool:
        raise ValueError('backlight.enabled must be boolean')
    values = settings.get('values', {})
    if not isinstance(values, dict):
        raise ValueError('backlight.values must be an object')
    return settings.get('enabled', False), {k:v for k,v in values.items() if v is not None}, {}


class ImageSettingsClient:
    """Reports unsupported legacy settings without opening any camera connection."""
    def __init__(self, ip, username, password, channel=0, timeout=2., session=None, stop_event=None):
        self.stop_event = stop_event

    def close(self):
        pass

    def apply(self, settings, log=lambda message: None):
        enabled, values, _ = validate_settings(settings)
        if not enabled:
            return {}
        log('[BACKLIGHT] Camera 37x: cấu hình CGI cũ chưa có ánh xạ RPC2; không áp dụng')
        return {key: 'unsupported' for key in values} or {'config': 'unsupported'}
