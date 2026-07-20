import logging

from .config import get_config
from .connector import Connector
from .ddns import DdnsManager
from .kea.app import DHCP4App
from .kea.cb import DHCP4CB
from .logger import init_logger
from .netbox import NetboxApp


def build_kea_backends(conf):
    """Build the Kea backend(s) from config. Returns (kea, default_tag):
    a {tag: backend} registry when kea_servers is set — config backend
    (direct DB) when 'db' is set, else control agent via the instance's
    HTTP control socket — or the legacy single backend with no tag routing
    (default_tag None). 'db' wins over 'url', matching legacy kea_db
    precedence."""

    if conf.kea_servers:
        kea = {
            tag: DHCP4CB(spec['db'], api_url=spec.get('url'),
                         api_username=spec.get('username'),
                         api_password=spec.get('password'))
            if spec.get('db')
            else DHCP4App(spec['url'], username=spec.get('username'),
                          password=spec.get('password'))
            for tag, spec in conf.kea_servers.items()}
        return kea, conf.default_server_tag
    if conf.kea_db:
        return DHCP4CB(conf.kea_db, api_url=conf.kea_url,
                       api_username=conf.kea_username,
                       api_password=conf.kea_password), None
    return DHCP4App(conf.kea_url, username=conf.kea_username,
                    password=conf.kea_password), None


def run():
    conf = get_config()
    init_logger(conf.log_level, conf.ext_log_level, conf.syslog_level_prefix)

    # Instanciate source, sink and connector
    nb = NetboxApp(
        conf.netbox_url, conf.netbox_token, prefix_filter=conf.prefix_filter,
        iprange_filter=conf.iprange_filter,
        ipaddress_filter=conf.ipaddress_filter)
    kea, default_tag = build_kea_backends(conf)
    if isinstance(kea, dict):
        logging.info(
            f'netbox: {conf.netbox_url}, kea servers: {", ".join(kea)} '
            f'(default tag: {default_tag})')
    else:
        kea_target = conf.kea_db if conf.kea_db else conf.kea_url
        logging.info(f'netbox: {conf.netbox_url}, kea: {kea_target}')
    # Optional: manage kea-dhcp-ddns (D2) forward/reverse-ddns zones from netbox-dns.
    ddns = None
    if conf.ddns_d2_url:
        logging.info(f'ddns: managing kea-dhcp-ddns at {conf.ddns_d2_url}')
        ddns = DdnsManager(conf.netbox_url, conf.netbox_token,
                           conf.ddns_d2_url,
                           username=conf.ddns_d2_username,
                           password=conf.ddns_d2_password)
    conn = Connector(
        nb, kea, conf.subnet_prefix_map, conf.pool_iprange_map,
        conf.reservation_ipaddr_map, check=conf.check_only, ddns=ddns,
        default_tag=default_tag, tag_field=conf.server_tag_custom_field)

    if not conf.full_sync_at_startup and not conf.listen:
        logging.warning('Neither full sync nor listen mode has been asked')

    # Start a full synchronisation
    if conf.full_sync_at_startup:
        logging.info('Start full sync')
        conn.sync_all()
        if ddns:
            try:
                ddns.sync()
            except Exception as e:
                logging.error(f'initial D2 ddns sync failed: {e}')

    # Start listening for events
    if conf.listen:
        # Deferred: bottle (pinned 0.12) imports stdlib cgi, removed in
        # python 3.13+, so importing the listener at module level makes the
        # whole package unimportable there. Only listen mode needs it.
        from .listener import WebhookListener
        logging.info(f'Listen for events on {conf.bind}:{conf.port}')
        server = WebhookListener(
            connector=conn, host=conf.bind, port=conf.port, secret=conf.secret,
            secret_header=conf.secret_header)
        server.run()
