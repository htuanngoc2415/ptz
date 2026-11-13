"""Old-camera exposure fields must never be written to the 37x camera."""
import unittest
from unittest.mock import Mock
from camera.image_settings import ImageSettingsClient, validate_settings


class ImageSettingsTests(unittest.TestCase):
    def test_old_cgi_fields_report_unsupported_without_network(self):
        session = Mock()
        client = ImageSettingsClient('camera', 'admin', 'secret', session=session)
        logs = []
        result = client.apply({'enabled': True, 'values': {'wdr_sensor': 1, 'blc_level': 32}}, logs.append)
        self.assertEqual(result, {'wdr_sensor': 'unsupported', 'blc_level': 'unsupported'})
        self.assertFalse(session.mock_calls)
        self.assertNotIn('secret', ''.join(logs))

    def test_disabled_and_empty_enabled_config_are_not_success_claims(self):
        client = ImageSettingsClient('camera', 'admin', 'secret')
        self.assertEqual(client.apply({'enabled': False}), {})
        self.assertEqual(client.apply({'enabled': True}), {'config':'unsupported'})

    def test_invalid_configuration_is_rejected(self):
        for value in (None, [], {'enabled': 'true'}, {'values': []}):
            with self.assertRaises(ValueError):
                validate_settings(value)
