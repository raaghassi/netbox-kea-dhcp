import unittest
from unittest.mock import Mock, patch

from netboxkea.netbox import NetboxApp


class TestUpsertDhcpIp(unittest.TestCase):

    def setUp(self):
        with patch('netboxkea.netbox.pynetbox'):
            self.app = NetboxApp('http://nb', 'tok')
        self.app.nb = Mock()

    def test_01_absent_dns_name_clears_stored_value(self):
        # Reflection semantics: a lease that stopped sending a hostname
        # must clear the stale dns_name, or the poller's change-detection
        # re-upserts every cycle forever.
        existing = Mock()
        self.app.nb.ipam.ip_addresses.filter.return_value = iter([existing])
        self.app.upsert_dhcp_ip('10.0.0.5/32', dns_name=None,
                                description='kea lease [narwhal]')
        existing.update.assert_called_once_with(
            {'status': 'dhcp', 'dns_name': '',
             'description': 'kea lease [narwhal]'})

    def test_02_create_when_absent(self):
        self.app.nb.ipam.ip_addresses.filter.return_value = iter([])
        self.app.upsert_dhcp_ip('10.0.0.5/32', dns_name='pc.lan',
                                description='kea lease [narwhal]')
        self.app.nb.ipam.ip_addresses.create.assert_called_once_with(
            status='dhcp', dns_name='pc.lan',
            description='kea lease [narwhal]', address='10.0.0.5/32')
