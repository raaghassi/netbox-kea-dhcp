import json
import logging
import requests

from .exceptions import KeaServerError, KeaCmdError


class FileAPI:
    """ Fake Kea DHCP4 API that keep configuration in memory and file """

    def __init__(self, uri):
        self.config_file = uri
        if self.config_file:
            try:
                with open(self.config_file, 'rb') as f:
                    self.conf = json.load(f)
            except FileNotFoundError:
                self.conf = {}
        else:
            self.conf = {}

    def get_conf(self):
        return self.conf.get('Dhcp4', {})

    def raise_conf_error(self, config):
        json.dumps(config)

    def set_conf(self, config):
        self.raise_conf_error(config)
        self.conf['Dhcp4'] = config

    def write_conf(self):
        if self.config_file:
            with open(self.config_file, 'w') as f:
                json.dump(self.conf, f, indent=4)

    def del_lease4(self, ip_address):
        """ No-op for file-based API (no lease database) """
        pass


class DHCP4API:
    def __init__(self, url):
        self.url = url
        self.session = requests.Session()

    def _request_kea(self, command, arguments={}):
        """ Send command to Kea APP """

        payload = {'command': command, 'service': ['dhcp4']}
        if arguments:
            payload['arguments'] = arguments
        try:
            r = self.session.post(self.url, json=payload)
            r.raise_for_status()
            rj = r.json()
        except requests.exceptions.RequestException as e:
            raise KeaServerError(f'API error: {e}')
        # One single command should return a list with one single item
        assert len(rj) == 1
        rj = rj.pop(0)
        result, text = rj['result'], rj.get('text')
        if result != 0:
            raise KeaCmdError(f'command "{command}" returns "{text}"')
        else:
            logging.debug(f'command "{command}" OK (text: {text})')
            return rj.get('arguments')

    def get_conf(self):
        """ Return configuration from Kea """

        return self._request_kea('config-get')['Dhcp4']

    def raise_conf_error(self, config):
        """ Test configuration and raise errors """

        self._request_kea('config-test', {'Dhcp4': config})

    def set_conf(self, config):
        """ Set configuration on DHCP server """

        self._request_kea('config-set', {'Dhcp4': config})

    def write_conf(self):
        """ On DHCP server write configuration to persistent storage """

        self._request_kea('config-write')

    def del_lease4(self, ip_address):
        """ Delete an IPv4 lease. Ignores non-existent leases (result 3). """

        payload = {'command': 'lease4-del', 'service': ['dhcp4'],
                   'arguments': {'ip-address': ip_address}}
        try:
            r = self.session.post(self.url, json=payload)
            r.raise_for_status()
            rj = r.json()
        except requests.exceptions.RequestException as e:
            raise KeaServerError(f'API error: {e}')
        assert len(rj) == 1
        rj = rj.pop(0)
        result, text = rj['result'], rj.get('text')
        if result == 0:
            logging.info(f'lease4-del {ip_address}: {text}')
        elif result == 3:
            logging.debug(f'lease4-del {ip_address}: {text}')
        else:
            raise KeaCmdError(f'command "lease4-del" returns "{text}"')
