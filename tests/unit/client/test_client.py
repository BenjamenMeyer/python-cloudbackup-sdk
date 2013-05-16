import json
import unittest

from httpretty import HTTPretty, httprettified
from requests.exceptions import HTTPError

from rcbu.client.client import Connection
from rcbu.common.constants import IDENTITY_TOKEN_URL


MOCK_KEY = 'key'
MOCK_ENDPOINT = 'http://tacobackup.com/v1.0/912371'
MOCK_ENDPOINT_STRIPPED = 'http://tacobackup.com/v1.0'


def _mock_auth(status):
    backup_endpoint = [
        {
            'type': 'rax:backup',
            'endpoints': [
                {
                    'publicURL': MOCK_ENDPOINT
                }
            ]
        },
        {
            'type': 'something:else',
            'endpoints': [
                {
                    'publicURL': MOCK_ENDPOINT + '/not-right'
                }
            ]
        }
    ]

    reply = {
        'access': {
            'token': {
                'id': MOCK_KEY
            },
            'serviceCatalog': backup_endpoint
        }
    }

    HTTPretty.register_uri(HTTPretty.POST, IDENTITY_TOKEN_URL,
                           status=status, body=json.dumps(reply))


class TestValidConnection(unittest.TestCase):

    @httprettified
    def setUp(self):
        _mock_auth(200)
        self.conn = Connection('a', password='a')

    def test_connection_has_correct_properties(self):
        self.assertEqual(self.conn.token, MOCK_KEY)
        self.assertEqual(self.conn.endpoint, MOCK_ENDPOINT_STRIPPED)

    @httprettified
    def test_agents_raises_403_on_invalid_auth(self):
        url = self.conn.endpoint + '/user/agents'
        HTTPretty.register_uri(HTTPretty.GET, url, status=403)
        with self.assertRaises(HTTPError):
            self.conn.agents

    @httprettified
    def test_backup_configurations_raises_403_on_invalid_auth(self):
        url = self.conn.endpoint + '/backup-configuration'
        HTTPretty.register_uri(HTTPretty.GET, url, status=403)
        with self.assertRaises(HTTPError):
            self.conn.backup_configurations

    def test_host(self):
        self.assertEqual(self.conn.host, MOCK_ENDPOINT_STRIPPED)


    def test_version(self):
        self.assertEqual(self.conn.api_version, '1.0')

    def test_version_tuple(self):
        self.assertEqual(self.conn.api_version_tuple, (1, 0))