import logging
import os
import sys
from argparse import ArgumentParser
from dataclasses import dataclass, field
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from .__about__ import __version__


@dataclass(frozen=True)
class Config:
    config_file: str = None
    check_only: bool = False
    full_sync_at_startup: bool = False
    listen: bool = False
    bind: str = '127.0.0.1'
    port: int = 8001
    secret: str = None
    secret_header: str = 'X-netbox2kea-secret'
    log_level: str = 'warning'
    ext_log_level: str = 'warning'
    syslog_level_prefix: bool = False
    kea_url: str = None
    # libpq DSN for the Kea config backend (alternative to kea_url's control
    # agent). When set, subnets are written directly to Kea's PostgreSQL CB.
    kea_db: str = None
    # HTTP basic auth toward the kea control socket (legacy single-server
    # settings; registry entries carry their own username/password[_env]).
    # *_password_env names an environment variable holding the password —
    # keeps secrets out of the config file; resolved at startup.
    kea_username: str = None
    kea_password: str = None
    kea_password_env: str = None
    ddns_d2_username: str = None
    ddns_d2_password: str = None
    ddns_d2_password_env: str = None
    # HTTP control URL of kea-dhcp-ddns (D2). When set, the syncer derives D2's
    # forward/reverse-ddns domains from netbox-dns zones (ddns_enabled) and
    # config-sets them. Unset = no DDNS-zone management.
    ddns_d2_url: str = None
    # Multi-server registry: maps a Kea server tag to the backend that serves
    # it. Each entry is a table with 'db' (libpq DSN -> config-backend mode)
    # and/or 'url' (HTTP control socket -> control-agent mode). Mutually
    # exclusive with kea_url/kea_db; requires default_server_tag.
    kea_servers: dict = field(default_factory=dict)
    # Tag applied to prefixes that don't carry the custom field below.
    default_server_tag: str = None
    # NetBox custom field on prefixes naming the serving Kea tag.
    server_tag_custom_field: str = 'kea_server'
    netbox_url: str = None
    netbox_token: str = None
    prefix_filter: dict = field(default_factory=lambda: {
        'cf_dhcp_enabled': True})
    ipaddress_filter: dict = field(default_factory=lambda: {'status': 'dhcp'})
    iprange_filter: dict = field(default_factory=lambda: {'status': 'dhcp'})
    subnet_prefix_map: dict = field(default_factory=lambda: {
        'option-data.routers': 'custom_fields.dhcp_option_data_routers',
        'option-data.domain-search':
            'custom_fields.dhcp_option_data_domain_search',
        'option-data.domain-name-servers':
            'custom_fields.dhcp_option_data_domain_name_servers',
        'next-server': 'custom_fields.dhcp_next_server',
        'boot-file-name': 'custom_fields.dhcp_boot_file_name',
        'valid-lifetime': 'custom_fields.dhcp_valid_lifetime'})
    pool_iprange_map: dict = field(default_factory=lambda: {})
    reservation_ipaddr_map: dict = field(default_factory=lambda: {
        # Get MAC address from custom field, fallback to assigned interface
        'hw-address': ['custom_fields.dhcp_reservation_hw_address',
                       'assigned_object.mac_address'],
        # Get hostname from DNS name, fallback to device/vm name
        'hostname': ['dns_name', 'assigned_object.device.name',
                     'assigned_object.virtual_machine.name']
        })


# Reserved words in Kea's server-tag model (remote-server4-set refuses them;
# tag comparison is case-insensitive and createAuditRevisionDHCP4 takes
# VARCHAR(64), hence the normalization and length cap below).
_RESERVED_TAGS = ('all', 'any')


def _fatal(msg):
    logging.fatal(msg)
    sys.exit(1)


def _password_from_env(env_name, ctx):
    """Resolve a *_password_env setting. Fatal when the variable is unset:
    silently running unauthenticated is worse than failing to start."""

    value = os.environ.get(env_name)
    if not value:
        _fatal(f'{ctx}: environment variable "{env_name}" (named by '
               'password_env) is unset or empty')
    return value


def _normalize_kea_servers(raw):
    """Validate and normalize the kea_servers table. Exits on bad config."""

    if not isinstance(raw, dict) or not raw:
        _fatal('Setting "kea_servers" must be a non-empty table of '
               'tag -> {db = "...", url = "..."} entries')
    servers = {}
    seen_dsn = {}
    for tag, spec in raw.items():
        ntag = str(tag).strip().lower()
        if ntag != tag:
            logging.warning(f'kea_servers: tag "{tag}" normalized to "{ntag}"')
        if not ntag or len(ntag) > 64:
            _fatal(f'kea_servers: tag "{tag}" must be 1-64 characters')
        if ntag in _RESERVED_TAGS:
            _fatal(f'kea_servers: tag "{ntag}" is reserved in Kea')
        if ntag in servers:
            _fatal(f'kea_servers: duplicate tag "{ntag}" after normalization')
        if not isinstance(spec, dict):
            _fatal(f'kea_servers.{ntag}: entry must be a table')
        unknown = set(spec) - {'db', 'url', 'username', 'password',
                               'password_env'}
        if unknown:
            _fatal(f'kea_servers.{ntag}: unknown keys {sorted(unknown)}')
        if not spec.get('db') and not spec.get('url'):
            _fatal(f'kea_servers.{ntag}: needs "db" (config backend DSN) '
                   'and/or "url" (HTTP control socket)')
        spec = dict(spec)
        if spec.get('password_env'):
            if spec.get('password'):
                _fatal(f'kea_servers.{ntag}: "password" and "password_env" '
                       'are mutually exclusive')
            spec['password'] = _password_from_env(
                spec.pop('password_env'), f'kea_servers.{ntag}')
        dsn = spec.get('db')
        if dsn:
            if dsn in seen_dsn:
                _fatal(f'kea_servers: tags "{seen_dsn[dsn]}" and "{ntag}" '
                       'share one config-backend DSN — by design each Kea '
                       'instance owns its own config-backend database')
            seen_dsn[dsn] = ntag
        servers[ntag] = spec
    return servers


def get_config():
    settings = {}

    parser = ArgumentParser()
    parser.add_argument(
        '--version', action='version', version=f'Version {__version__}')
    parser.add_argument('-c', '--config-file', help='configuration file')
    parser.add_argument('-n', '--netbox-url', help='')
    parser.add_argument('-t', '--netbox-token', help='')
    parser.add_argument('-k', '--kea-url', help='')
    parser.add_argument(
        '-d', '--kea-db',
        help='libpq DSN for the Kea config backend (alternative to --kea-url)')
    parser.add_argument(
        '--ddns-d2-url',
        help='HTTP control URL of kea-dhcp-ddns; enables netbox-dns -> D2 '
             'forward/reverse-ddns zone management')
    parser.add_argument(
        '-l', '--listen', action='store_true', default=None, help='')
    parser.add_argument('-b', '--bind', help='')
    parser.add_argument('-p', '--port', type=int, help='')
    parser.add_argument(
        '--secret', help=f'Default header: {Config.secret_header}')
    parser.add_argument(
        '-s', '--sync-now', action='store_true', dest='full_sync_at_startup',
        default=None, help='')
    parser.add_argument(
        '--check', action='store_true', dest='check_only', default=None,
        help='')
    parser.add_argument(
        '-v', '--verbose', action='count', default=0,
        help='Increase verbosity. May be specified up to 3 times')
    # TODO: parser.add_argument('-f', '--foreground', help='')
    args = parser.parse_args()

    # Load TOML config file
    if args.config_file is not None:
        with open(args.config_file, 'rb') as f:
            tomlconf = tomllib.load(f)
        settings.update(tomlconf)

    # Load non-None command line arguments
    if args.verbose == 1:
        args.log_level = 'info'
    elif args.verbose == 2:
        args.log_level = 'debug'
        settings['ext_log_level'] = 'info'
    elif args.verbose >= 3:
        args.log_level = 'debug'
        settings['ext_log_level'] = 'debug'
    del args.verbose
    settings.update({k: v for k, v in args.__dict__.items() if v is not None})

    # Check existence of required settings
    if 'netbox_url' not in settings:
        logging.fatal(
            'Setting "netbox_url" not found, neither on command line '
            'arguments nor in configuration file (if any)')
        sys.exit(1)
    if 'kea_servers' in settings:
        if 'kea_url' in settings or 'kea_db' in settings:
            _fatal('"kea_servers" replaces "kea_url"/"kea_db" — remove the '
                   'single-server settings when using the registry')
        settings['kea_servers'] = _normalize_kea_servers(
            settings['kea_servers'])
        if 'default_server_tag' not in settings:
            _fatal('"default_server_tag" is required with "kea_servers" (tag '
                   'applied to prefixes without the custom field)')
        settings['default_server_tag'] = str(
            settings['default_server_tag']).strip().lower()
        if settings['default_server_tag'] not in settings['kea_servers']:
            _fatal(f'default_server_tag "{settings["default_server_tag"]}" '
                   'is not a kea_servers tag')
    elif 'kea_url' not in settings and 'kea_db' not in settings:
        logging.fatal(
            'Either "kea_url" (control agent), "kea_db" (config backend DSN) '
            'or a "kea_servers" registry must be set, on command line '
            'arguments or in the config file')
        sys.exit(1)

    # Resolve legacy/D2 password_env settings the same way as registry
    # entries: env var wins the file out of the config, fatal when missing.
    for base in ('kea_password', 'ddns_d2_password'):
        env_key = f'{base}_env'
        if settings.get(env_key):
            if settings.get(base):
                _fatal(f'"{base}" and "{env_key}" are mutually exclusive')
            settings[base] = _password_from_env(settings[env_key], env_key)

    conf = Config(**settings)

    if not set(['hw-address', 'hostname']).issubset(
            conf.reservation_ipaddr_map):
        logging.fatal(
            'Setting "reservation_ipaddr_map" must have a mapping for '
            '"hw-address" and "hostname" DHCP parameters')
        sys.exit(1)

    return conf
