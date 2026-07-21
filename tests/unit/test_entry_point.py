import unittest
from unittest.mock import Mock, patch

from netboxkea.entry_point import (build_ddns, build_kea_backends,
                                   build_lease_pollers)


def _conf(**kw):
    defaults = {'kea_servers': {}, 'kea_url': None, 'kea_db': None,
                'default_server_tag': None, 'kea_username': None,
                'kea_password': None, 'lease_sources': {}}
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
        cb.assert_called_once_with(
            'dbname=kea', api_url='http://svcs:8000/', api_username=None,
            api_password=None)
        app.assert_called_once_with(
            'http://site-a:8000/', username=None, password=None)
        self.assertIs(kea['svcs'], cb.return_value)
        self.assertIs(kea['site-a'], app.return_value)

    def test_02_legacy_db_backend(self, cb, app):
        kea, default_tag = build_kea_backends(_conf(kea_db='dbname=kea'))
        self.assertIsNone(default_tag)
        cb.assert_called_once_with(
            'dbname=kea', api_url=None, api_username=None, api_password=None)
        app.assert_not_called()
        self.assertIs(kea, cb.return_value)

    def test_03_legacy_url_backend(self, cb, app):
        kea, default_tag = build_kea_backends(
            _conf(kea_url='http://kea:8000/'))
        self.assertIsNone(default_tag)
        app.assert_called_once_with(
            'http://kea:8000/', username=None, password=None)
        cb.assert_not_called()
        self.assertIs(kea, app.return_value)

    def test_04_legacy_db_wins_over_url(self, cb, app):
        # both set (the live deployment shape): CB mode, with the control
        # socket wired in as the host_cmds/lease_cmds API channel
        kea, _ = build_kea_backends(
            _conf(kea_db='dbname=kea', kea_url='http://kea:8000/'))
        cb.assert_called_once_with(
            'dbname=kea', api_url='http://kea:8000/', api_username=None,
            api_password=None)
        app.assert_not_called()

    def test_05_registry_credentials_passed_through(self, cb, app):
        conf = _conf(kea_servers={
            'svcs': {'db': 'dbname=kea', 'url': 'http://svcs:8000/',
                     'username': 'syncer', 'password': 's3cret'},
            'site-a': {'url': 'http://site-a:8000/', 'username': 'syncer',
                       'password': 'other'}},
            default_server_tag='svcs')
        build_kea_backends(conf)
        cb.assert_called_once_with(
            'dbname=kea', api_url='http://svcs:8000/',
            api_username='syncer', api_password='s3cret')
        app.assert_called_once_with(
            'http://site-a:8000/', username='syncer', password='other')

    def test_06_legacy_credentials_passed_through(self, cb, app):
        build_kea_backends(_conf(
            kea_db='dbname=kea', kea_url='http://kea:8000/',
            kea_username='syncer', kea_password='s3cret'))
        cb.assert_called_once_with(
            'dbname=kea', api_url='http://kea:8000/',
            api_username='syncer', api_password='s3cret')


@patch('netboxkea.entry_point.DdnsManager')
class TestBuildDdns(unittest.TestCase):

    def test_01_none_without_url(self, ddns):
        conf = _conf(ddns_d2_url=None)
        self.assertIsNone(build_ddns(conf))
        ddns.assert_not_called()

    def test_02_credentials_passed_through(self, ddns):
        conf = _conf(netbox_url='http://nb', netbox_token='tok',
                     ddns_d2_url='http://d2:8001/',
                     ddns_d2_username='syncer', ddns_d2_password='s3cret')
        result = build_ddns(conf)
        self.assertIs(result, ddns.return_value)
        ddns.assert_called_once_with(
            'http://nb', 'tok', 'http://d2:8001/', username='syncer',
            password='s3cret')


@patch('netboxkea.entry_point.NetboxApp')
@patch('netboxkea.entry_point.LeasePoller')
class TestBuildLeasePollers(unittest.TestCase):

    def test_01_builds_with_creds_and_own_netbox(self, lp, nba):
        conf = _conf(netbox_url='http://nb', netbox_token='tok',
                     lease_sources={'narwhal': {
                         'url': 'http://fw:8000/', 'username': 'u',
                         'password': 'p', 'interval': 30}})
        pollers = build_lease_pollers(conf)
        self.assertEqual(len(pollers), 1)
        nba.assert_called_once_with('http://nb', 'tok')
        lp.assert_called_once_with(
            'narwhal', 'http://fw:8000/', nba.return_value,
            username='u', password='p', interval=30)

    def test_02_none_configured(self, lp, nba):
        self.assertEqual(build_lease_pollers(_conf()), [])
        lp.assert_not_called()
