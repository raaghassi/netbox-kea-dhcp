import os
import unittest
from tempfile import NamedTemporaryFile
from unittest.mock import patch

from netboxkea.config import get_config

BASE = 'netbox_url = "http://netbox"\nnetbox_token = "token"\n'


def _get_config(toml_text):
    with NamedTemporaryFile(
            'w', suffix='.toml', delete=False) as f:
        f.write(toml_text)
        path = f.name
    try:
        with patch('sys.argv', ['netbox-kea-dhcp', '-c', path]):
            return get_config()
    finally:
        os.unlink(path)


class TestConfigLegacy(unittest.TestCase):

    def test_01_kea_url_only(self):
        conf = _get_config(BASE + 'kea_url = "http://kea:8000/"\n')
        self.assertEqual(conf.kea_url, 'http://kea:8000/')
        self.assertEqual(conf.kea_servers, {})
        self.assertIsNone(conf.default_server_tag)

    def test_02_kea_url_and_db_allowed(self):
        # The live deployment sets both; kea_db wins in entry_point
        conf = _get_config(
            BASE + 'kea_url = "http://kea:8000/"\nkea_db = "dbname=kea"\n')
        self.assertEqual(conf.kea_db, 'dbname=kea')

    def test_03_no_kea_target_fatal(self):
        with self.assertRaises(SystemExit):
            _get_config(BASE)


class TestConfigKeaServers(unittest.TestCase):

    def test_01_registry_parse_and_normalize(self):
        conf = _get_config(BASE + '''
default_server_tag = "SVCS"
[kea_servers.SVCS]
db = "host=pg dbname=kea"
url = "http://kea-control:8000/"
[kea_servers."site-a"]
url = "http://site-a:8000/"
''')
        self.assertEqual(conf.kea_servers, {
            'svcs': {'db': 'host=pg dbname=kea',
                     'url': 'http://kea-control:8000/'},
            'site-a': {'url': 'http://site-a:8000/'}})
        self.assertEqual(conf.default_server_tag, 'svcs')
        self.assertEqual(conf.server_tag_custom_field, 'kea_server')

    def test_02_reserved_tag_fatal(self):
        with self.assertRaises(SystemExit):
            _get_config(BASE + '''
default_server_tag = "all"
[kea_servers.all]
url = "http://kea:8000/"
''')

    def test_03_overlong_tag_fatal(self):
        tag = 'x' * 65
        with self.assertRaises(SystemExit):
            _get_config(BASE + f'''
default_server_tag = "{tag}"
[kea_servers.{tag}]
url = "http://kea:8000/"
''')

    def test_04_entry_without_target_fatal(self):
        with self.assertRaises(SystemExit):
            _get_config(BASE + '''
default_server_tag = "svcs"
[kea_servers.svcs]
''')

    def test_05_unknown_entry_key_fatal(self):
        with self.assertRaises(SystemExit):
            _get_config(BASE + '''
default_server_tag = "svcs"
[kea_servers.svcs]
url = "http://kea:8000/"
port = 8000
''')

    def test_06_shared_dsn_fatal(self):
        with self.assertRaises(SystemExit):
            _get_config(BASE + '''
default_server_tag = "svcs"
[kea_servers.svcs]
db = "dbname=kea"
[kea_servers.other]
db = "dbname=kea"
''')

    def test_07_missing_default_tag_fatal(self):
        with self.assertRaises(SystemExit):
            _get_config(BASE + '''
[kea_servers.svcs]
url = "http://kea:8000/"
''')

    def test_08_default_tag_not_a_key_fatal(self):
        with self.assertRaises(SystemExit):
            _get_config(BASE + '''
default_server_tag = "ctrl"
[kea_servers.svcs]
url = "http://kea:8000/"
''')

    def test_10_password_env_resolves(self):
        with patch.dict(os.environ, {'KEA_CTRL_PW': 's3cret'}):
            conf = _get_config(BASE + '''
default_server_tag = "svcs"
[kea_servers.svcs]
url = "http://kea:8000/"
username = "syncer"
password_env = "KEA_CTRL_PW"
''')
        self.assertEqual(conf.kea_servers['svcs']['password'], 's3cret')
        self.assertNotIn('password_env', conf.kea_servers['svcs'])

    def test_11_password_env_missing_fatal(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('KEA_CTRL_PW_MISSING', None)
            with self.assertRaises(SystemExit):
                _get_config(BASE + '''
default_server_tag = "svcs"
[kea_servers.svcs]
url = "http://kea:8000/"
password_env = "KEA_CTRL_PW_MISSING"
''')

    def test_12_password_and_env_mutually_exclusive(self):
        with patch.dict(os.environ, {'KEA_CTRL_PW': 's3cret'}):
            with self.assertRaises(SystemExit):
                _get_config(BASE + '''
default_server_tag = "svcs"
[kea_servers.svcs]
url = "http://kea:8000/"
password = "literal"
password_env = "KEA_CTRL_PW"
''')

    def test_13_legacy_password_env_resolves(self):
        with patch.dict(os.environ, {'KEA_CTRL_PW': 's3cret'}):
            conf = _get_config(BASE + '''
kea_url = "http://kea:8000/"
kea_username = "syncer"
kea_password_env = "KEA_CTRL_PW"
''')
        self.assertEqual(conf.kea_password, 's3cret')
        self.assertEqual(conf.kea_username, 'syncer')

    def test_14_literal_password_survives(self):
        conf = _get_config(BASE + '''
default_server_tag = "svcs"
[kea_servers.svcs]
url = "http://kea:8000/"
username = "syncer"
password = "literal-pw"
''')
        self.assertEqual(conf.kea_servers['svcs']['password'], 'literal-pw')

    def test_15_username_without_password_fatal(self):
        with self.assertRaises(SystemExit):
            _get_config(BASE + '''
default_server_tag = "svcs"
[kea_servers.svcs]
url = "http://kea:8000/"
username = "syncer"
''')

    def test_16_registry_excludes_legacy_auth_keys(self):
        with self.assertRaises(SystemExit):
            _get_config(BASE + '''
kea_password_env = "SOME_VAR"
default_server_tag = "svcs"
[kea_servers.svcs]
url = "http://kea:8000/"
''')

    def test_17_legacy_username_without_password_fatal(self):
        with self.assertRaises(SystemExit):
            _get_config(BASE + '''
kea_url = "http://kea:8000/"
kea_username = "syncer"
''')

    def test_18_empty_password_explicitly_allowed(self):
        conf = _get_config(BASE + '''
kea_url = "http://kea:8000/"
kea_username = "syncer"
kea_password = ""
''')
        self.assertEqual(conf.kea_password, '')

    def test_19_lease_sources_parse_and_defaults(self):
        conf = _get_config(BASE + '''
kea_url = "http://kea:8000/"
[lease_sources.Narwhal]
url = "http://fw:8000/"
''')
        self.assertEqual(conf.lease_sources, {
            'narwhal': {'url': 'http://fw:8000/', 'interval': 60}})

    def test_20_lease_sources_url_required(self):
        with self.assertRaises(SystemExit):
            _get_config(BASE + '''
kea_url = "http://kea:8000/"
[lease_sources.narwhal]
interval = 30
''')

    def test_21_lease_sources_bad_interval_fatal(self):
        with self.assertRaises(SystemExit):
            _get_config(BASE + '''
kea_url = "http://kea:8000/"
[lease_sources.narwhal]
url = "http://fw:8000/"
interval = 0
''')

    def test_21b_lease_sources_bool_interval_fatal(self):
        # bool is an int subclass; `interval = true` would poll at 1s
        with self.assertRaises(SystemExit):
            _get_config(BASE + '''
kea_url = "http://kea:8000/"
[lease_sources.narwhal]
url = "http://fw:8000/"
interval = true
''')

    def test_21c_lease_sources_alone_is_not_a_kea_target(self):
        with self.assertRaises(SystemExit):
            _get_config(BASE + '''
[lease_sources.narwhal]
url = "http://fw:8000/"
''')

    def test_22_lease_sources_password_env(self):
        with patch.dict(os.environ, {'FW_PW': 's3cret'}):
            conf = _get_config(BASE + '''
kea_url = "http://kea:8000/"
[lease_sources.narwhal]
url = "http://fw:8000/"
username = "syncer"
password_env = "FW_PW"
''')
        self.assertEqual(conf.lease_sources['narwhal']['password'], 's3cret')

    def test_23_lease_sources_coexist_with_registry(self):
        conf = _get_config(BASE + '''
default_server_tag = "svcs"
[kea_servers.svcs]
url = "http://kea:8000/"
[lease_sources.narwhal]
url = "http://fw:8000/"
''')
        self.assertIn('narwhal', conf.lease_sources)
        self.assertIn('svcs', conf.kea_servers)

    def test_09_registry_excludes_legacy_settings(self):
        with self.assertRaises(SystemExit):
            _get_config(BASE + '''
kea_url = "http://kea:8000/"
default_server_tag = "svcs"
[kea_servers.svcs]
url = "http://kea:8000/"
''')
