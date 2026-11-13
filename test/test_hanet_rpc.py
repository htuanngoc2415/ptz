"""Only a matching successful response acknowledges a movement."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import socket
import unittest
from unittest.mock import Mock
from camera.hanet_rpc import HanetPTZClient


class HanetAckTests(unittest.TestCase):
    def client(self, replies):
        client = HanetPTZClient.__new__(HanetPTZClient)
        client.timeout = .1
        client._recv_frame = Mock(side_effect=replies)
        return client

    def test_ignores_unrelated_response(self):
        client = self.client([(1, json.dumps({'jsonrpc':'2.0', 'id':'old','result':True}).encode()),
                              (1, json.dumps({'jsonrpc':'2.0', 'id':'new','result':True}).encode())])
        client._receive_response('new')
        self.assertEqual(client._recv_frame.call_count, 2)

    def test_timeout_is_not_ack(self):
        with self.assertRaises(TimeoutError):
            self.client([socket.timeout()])._receive_response('new')

    def test_error_is_not_ack(self):
        reply = json.dumps({'jsonrpc':'2.0', 'id':'new','error':{'code':1,'message':'synthetic rejection'}}).encode()
        with self.assertRaises(RuntimeError):
            self.client([(1, reply)])._receive_response('new')
