import pynetbox
from ipaddress import ip_interface, ip_network


class NetboxApp:

    def __init__(self, url, token, prefix_filter={}, iprange_filter={},
                 ipaddress_filter={'status': 'dhcp'}):
        self.nb = pynetbox.api(url, token=token)
        self.prefix_filter = prefix_filter
        self.iprange_filter = iprange_filter
        self.ipaddress_filter = ipaddress_filter

    def prefix(self, id_):
        return self.nb.ipam.prefixes.get(id=id_, **self.prefix_filter)

    def prefixes(self, contains):
        return self.nb.ipam.prefixes.filter(
            **self.prefix_filter, contains=contains)

    def all_prefixes(self):
        return self.nb.ipam.prefixes.filter(**self.prefix_filter)

    def ip_range(self, id_):
        return self.nb.ipam.ip_ranges.get(id=id_, **self.iprange_filter)

    def ip_ranges(self, parent):
        # Emulate "parent" filter as NetBox API doesn’t support it on
        # ip-ranges objects (v3.4).
        parent_net = ip_network(parent)
        for r in self.nb.ipam.ip_ranges.filter(
                parent=parent, **self.iprange_filter):
            if (ip_interface(r.start_address) in parent_net
                    and ip_interface(r.end_address) in parent_net):
                yield r

    def ip_address(self, id_):
        return self.nb.ipam.ip_addresses.get(id=id_, **self.ipaddress_filter)

    def ip_addresses(self, **filters):
        if not filters:
            raise ValueError(
                'Netboxapp.ip_addresses() requires at least one keyword arg')
        for i in self.nb.ipam.ip_addresses.filter(
                **self.ipaddress_filter, **filters):
            yield i

    # --- Lease reflection (Kea -> NetBox): manage status=dhcp host IPs only, so
    # statically-seeded reservations are never touched. ---

    def upsert_dhcp_ip(self, address, dns_name=None, description=None):
        """Create or update a DHCP-lease IP (status=dhcp). Idempotent on the
        address; matches/creates only status=dhcp objects."""
        data = {'status': 'dhcp'}
        if dns_name:
            data['dns_name'] = dns_name
        if description:
            data['description'] = description
        existing = next(iter(self.nb.ipam.ip_addresses.filter(
            address=address, status='dhcp')), None)
        if existing:
            existing.update(data)
            return existing
        data['address'] = address
        return self.nb.ipam.ip_addresses.create(**data)

    def delete_dhcp_ip(self, address):
        """Delete DHCP-lease IP(s) at address (status=dhcp only)."""
        for ip in self.nb.ipam.ip_addresses.filter(
                address=address, status='dhcp'):
            ip.delete()
