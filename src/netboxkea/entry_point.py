import logging

from .config import get_config
from .connector import Connector
from .ddns import DdnsManager
from .kea.app import DHCP4App
from .kea.cb import DHCP4CB
from .listener import WebhookListener
from .logger import init_logger
from .netbox import NetboxApp


def run():
    conf = get_config()
    init_logger(conf.log_level, conf.ext_log_level, conf.syslog_level_prefix)

    # Instanciate source, sink and connector
    kea_target = conf.kea_db if conf.kea_db else conf.kea_url
    logging.info(f'netbox: {conf.netbox_url}, kea: {kea_target}')
    nb = NetboxApp(
        conf.netbox_url, conf.netbox_token, prefix_filter=conf.prefix_filter,
        iprange_filter=conf.iprange_filter,
        ipaddress_filter=conf.ipaddress_filter)
    # config backend (direct DB) when kea_db is set, else control agent (config-set)
    kea = DHCP4CB(conf.kea_db) if conf.kea_db else DHCP4App(conf.kea_url)
    # Optional: manage kea-dhcp-ddns (D2) forward/reverse-ddns zones from netbox-dns.
    ddns = None
    if conf.ddns_d2_url:
        logging.info(f'ddns: managing kea-dhcp-ddns at {conf.ddns_d2_url}')
        ddns = DdnsManager(conf.netbox_url, conf.netbox_token, conf.ddns_d2_url)
    conn = Connector(
        nb, kea, conf.subnet_prefix_map, conf.pool_iprange_map,
        conf.reservation_ipaddr_map, check=conf.check_only, ddns=ddns)

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
        logging.info(f'Listen for events on {conf.bind}:{conf.port}')
        server = WebhookListener(
            connector=conn, host=conf.bind, port=conf.port, secret=conf.secret,
            secret_header=conf.secret_header)
        server.run()
