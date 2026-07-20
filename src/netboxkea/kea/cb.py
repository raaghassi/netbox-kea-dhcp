"""Kea Config Backend writer.

Writes subnets / pools / options directly to Kea's PostgreSQL config backend
(the same DB Kea uses for leases) following Kea's own audit protocol:

    createAuditRevisionDHCP4(now(), 'all', msg, true)   -- opens an audit revision
    INSERT/UPDATE dhcp4_subnet / dhcp4_pool / dhcp4_options / dhcp4_subnet_server
    -- the table AINS/AUPD/ADEL triggers create dhcp4_audit entries automatically

so Kea's `config-fetch` picks the changes up on its timer (kea pulls; no push to
a control agent, self-healing across restarts).

This reuses DHCP4App's in-memory subnet/pool manipulation verbatim; only
pull()/commit()/push() touch the database. Host reservations live in the Kea
*host* backend (hosts table), not the config backend, so they are deferred here.
"""

import logging
from copy import deepcopy

import psycopg  # psycopg 3 (present in the NetBox runtime image)
from psycopg.types.json import Json

from .app import DHCP4App, SUBNETS, POOLS, RESAS, USR_CTX, PREFIX, IP_RANGE

# DHCPv4 option name -> code. Extend alongside the syncer's subnet_prefix_map.
OPTION_CODES = {
    'routers': 3,
    'time-servers': 4,
    'domain-name-servers': 6,
    'ntp-servers': 42,
    'domain-search': 119,
}
CODE_NAMES = {code: name for name, code in OPTION_CODES.items()}

# Kea subnet scalar key -> dhcp4_subnet column (the subset the syncer emits).
SCALAR_COLS = {
    'valid-lifetime': 'valid_lifetime',
    'next-server': 'next_server',
    'boot-file-name': 'boot_file_name',
    'ddns-qualifying-suffix': 'ddns_qualifying_suffix',
    'ddns-send-updates': 'ddns_send_updates',
    'ddns-override-no-update': 'ddns_override_no_update',
    'ddns-override-client-update': 'ddns_override_client_update',
}
COL_KEYS = {col: key for key, col in SCALAR_COLS.items()}

# Config associated with the "all" server applies to every Kea instance; the
# audit revision is opened against the same tag.
SERVER_TAG = 'all'


class DHCP4CB(DHCP4App):

    def __init__(self, dsn):
        # libpq DSN, e.g. "host=kea-leases-rw.kea.svc dbname=kea user=kea-leases".
        # Password/TLS come from PGPASSWORD / PGSSLMODE / PGSSLROOTCERT env.
        self.dsn = dsn
        self.conf = None
        self.commit_conf = None
        self._has_commit = False
        self.auto_commit = True
        self.ip_uniqueness = True

    def _connect(self):
        return psycopg.connect(self.dsn)

    def pull(self):
        """Load the current CB subnets into the in-memory conf (so the inherited
        set_/update_/del_ logic and push()'s reconcile round-trip correctly)."""

        logging.info('pull running config from Kea config backend (DB)')
        cols = list(SCALAR_COLS.values())
        subnets = {}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT subnet_id, subnet_prefix, {} '
                    'FROM dhcp4_subnet'.format(', '.join(cols)))
                for row in cur.fetchall():
                    sid, prefix = row[0], row[1]
                    s = {PREFIX: sid, 'subnet': prefix, POOLS: [], RESAS: []}
                    for col, val in zip(cols, row[2:]):
                        if val is None:
                            continue
                        if col == 'next_server':
                            val = str(val).split('/')[0]
                        s[COL_KEYS[col]] = val
                    subnets[sid] = s

                cur.execute(
                    'SELECT subnet_id, host(start_address), host(end_address), '
                    'user_context FROM dhcp4_pool')
                for sid, start, end, uctx in cur.fetchall():
                    s = subnets.get(sid)
                    if s is None:
                        continue
                    irid = uctx.get(IP_RANGE) if isinstance(uctx, dict) else None
                    pool = {'pool': '{}-{}'.format(start, end)}
                    pool.setdefault(USR_CTX, {})[IP_RANGE] = irid
                    s[POOLS].append(pool)

                cur.execute(
                    'SELECT dhcp4_subnet_id, code, formatted_value FROM '
                    'dhcp4_options WHERE scope_id=1 AND dhcp4_subnet_id IS NOT NULL')
                for sid, code, val in cur.fetchall():
                    name = CODE_NAMES.get(code)
                    s = subnets.get(sid)
                    if s is not None and name:
                        s.setdefault('option-data', []).append(
                            {'name': name, 'data': val})

        self.conf = {SUBNETS: list(subnets.values())}
        self.commit_conf = deepcopy(self.conf)

    def commit(self):
        """Record the working conf. No control-agent validation — Kea validates
        on config-fetch; a bad row would surface in the kea-dhcp4 logs."""

        self.commit_conf = deepcopy(self.conf)
        self._has_commit = True
        return True

    def push(self):
        """Reconcile the CB to match the committed conf, in one audit revision."""

        if not self._has_commit:
            logging.debug('no commit to push')
            return

        desired = self.commit_conf[SUBNETS]
        desired_ids = {s[PREFIX] for s in desired}
        logging.info(
            'push {} subnet(s) to Kea config backend (DB)'.format(len(desired)))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT createAuditRevisionDHCP4(now(), %s, %s, true)',
                    (SERVER_TAG, 'netbox-kea-dhcp sync'))
                cur.execute(
                    'SELECT id FROM dhcp4_server WHERE tag=%s', (SERVER_TAG,))
                server_id = cur.fetchone()[0]

                cur.execute('SELECT subnet_id FROM dhcp4_subnet')
                for (sid,) in cur.fetchall():
                    if sid not in desired_ids:
                        self._delete_subnet(cur, sid)
                for s in desired:
                    self._upsert_subnet(cur, s, server_id)
        self._has_commit = None

    def _delete_subnet(self, cur, sid):
        # Delete children explicitly (don't rely on FK cascade); the dhcp4_subnet
        # ADEL trigger records the audit entry config-fetch needs.
        cur.execute('DELETE FROM dhcp4_options WHERE dhcp4_subnet_id=%s', (sid,))
        cur.execute('DELETE FROM dhcp4_pool WHERE subnet_id=%s', (sid,))
        cur.execute('DELETE FROM dhcp4_subnet_server WHERE subnet_id=%s', (sid,))
        cur.execute('DELETE FROM dhcp4_subnet WHERE subnet_id=%s', (sid,))

    def _upsert_subnet(self, cur, s, server_id):
        sid = s[PREFIX]
        cols = list(SCALAR_COLS.values())
        vals = [s.get(key) for key in SCALAR_COLS]
        placeholders = ', '.join(['%s'] * (2 + len(cols)))
        updates = ', '.join('{0}=EXCLUDED.{0}'.format(c) for c in cols)
        cur.execute(
            'INSERT INTO dhcp4_subnet (subnet_id, subnet_prefix, {}, '
            'modification_ts) VALUES ({}, now()) '
            'ON CONFLICT (subnet_id) DO UPDATE SET '
            'subnet_prefix=EXCLUDED.subnet_prefix, {}, modification_ts=now()'
            .format(', '.join(cols), placeholders, updates),
            [sid, s['subnet'], *vals])

        cur.execute('DELETE FROM dhcp4_subnet_server WHERE subnet_id=%s', (sid,))
        cur.execute(
            'INSERT INTO dhcp4_subnet_server (subnet_id, server_id, '
            'modification_ts) VALUES (%s, %s, now())', (sid, server_id))

        cur.execute('DELETE FROM dhcp4_pool WHERE subnet_id=%s', (sid,))
        for p in s.get(POOLS, []):
            start, _, end = p['pool'].partition('-')
            # user_context carries the netbox_ip_range_id provenance that
            # pull() reads back for reconcile matching; omitting it here
            # erased it on every push.
            uctx = p.get(USR_CTX)
            cur.execute(
                'INSERT INTO dhcp4_pool (start_address, end_address, subnet_id, '
                'user_context, modification_ts) VALUES (%s, %s, %s, %s, now())',
                (start.strip(), end.strip(), sid,
                 Json(uctx) if uctx else None))

        cur.execute(
            'DELETE FROM dhcp4_options WHERE dhcp4_subnet_id=%s AND scope_id=1',
            (sid,))
        for od in s.get('option-data', []):
            code = OPTION_CODES.get(od.get('name'))
            if code is None:
                logging.warning(
                    "CB: unknown option '{}' skipped".format(od.get('name')))
                continue
            # client_classes '[ ]' matches what Kea's own CB writer stores
            # unconditionally; the schema-v30 upgrade makes the column
            # NOT NULL, so omitting it breaks on the first db-upgrade
            # past Kea 3.0.
            cur.execute(
                'INSERT INTO dhcp4_options (code, formatted_value, space, '
                'persistent, dhcp4_subnet_id, scope_id, client_classes, '
                'modification_ts, cancelled) '
                'VALUES (%s, %s, %s, false, %s, 1, %s, now(), false)',
                (code, od.get('data'), 'dhcp4', sid, '[ ]'))

    # Host reservations belong to the Kea host backend, not the config backend.
    def set_reservation(self, prefix_id, ipaddr_id, resa_item):
        logging.warning(
            'CB backend: host reservations not supported yet, skipping IP id '
            '{}'.format(ipaddr_id))

    def del_resa(self, ipaddr_id):
        logging.debug('CB backend: del reservation {} (no-op)'.format(ipaddr_id))
