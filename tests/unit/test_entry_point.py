import unittest
from unittest.mock import Mock, patch

from netboxkea.entry_point import build_kea_backends


def _conf(**kw):
    defaults = {'kea_servers': {}, 'kea_url': None, 'kea_db': None,
                'default_server_tag': None}
    defaults.update(kw)
    conf = Mock()
    for k, v in defaults.items():
        setattr(conf, k, v)
    return conf


@patch('netboxkea.entry_point.DHCP4App')
@patch('netboxkea.entry_point.DHCP4CB')
class TestBuildKeaBackends(unittest.TestCase):

    def test_01_registry_db_wins_over_url(self, cb, app):
        conf = _conf(kea_servers={
            'svcs': {'db': 'dbname=kea', 'url': 'http://svcs:8000/'},
            'site-a': {'url': 'http://site-a:8000/'}},
            default_server_tag='svcs')
        kea, default_tag = build_kea_backends(conf)
        self.assertEqual(default_tag, 'svcs')
        self.assertEqual(set(kea), {'svcs', 'site-a'})
        cb.assert_called_once_with('dbname=kea', api_url='http://svcs:8000/')
        app.assert_called_once_with('http://site-a:8000/')
        self.assertIs(kea['svcs'], cb.return_value)
        self.assertIs(kea['site-a'], app.return_value)

    def test_02_legacy_db_backend(self, cb, app):
        kea, default_tag = build_kea_backends(_conf(kea_db='dbname=kea'))
        self.assertIsNone(default_tag)
        cb.assert_called_once_with('dbname=kea', api_url=None)
        app.assert_not_called()
        self.assertIs(kea, cb.return_value)

    def test_03_legacy_url_backend(self, cb, app):
        kea, default_tag = build_kea_backends(
            _conf(kea_url='http://kea:8000/'))
        self.assertIsNone(default_tag)
        app.assert_called_once_with('http://kea:8000/')
        cb.assert_not_called()
        self.assertIs(kea, app.return_value)

    def test_04_legacy_db_wins_over_url(self, cb, app):
        # both set (the live deployment shape): CB mode, with the control
        # socket wired in as the host_cmds/lease_cmds API channel
        kea, _ = build_kea_backends(
            _conf(kea_db='dbname=kea', kea_url='http://kea:8000/'))
        cb.assert_called_once_with('dbname=kea', api_url='http://kea:8000/')
        app.assert_not_called()
