"""REST SPI for upstreams implementing the governed page protocol.

DNS is resolved, validated and pinned for each request while TLS verifies the
original host. Redirects and environment proxy inheritance are disabled.
"""
import ipaddress
import json
import socket
from urllib.parse import urlsplit

import httpx
from .backends import StructuredPage, eval_filter
from .context import current_context


def pinned_url(url, allowed_hosts):
    value = urlsplit(url)
    if value.scheme != 'https' or value.username or value.password or value.query or value.fragment:
        raise ValueError('connector endpoint must be a credential-free HTTPS URL')
    if value.hostname not in allowed_hosts or value.port not in {None, 443}:
        raise ValueError('connector host/port is not allowlisted')
    addresses = {row[4][0] for row in socket.getaddrinfo(value.hostname, 443, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('connector DNS resolves to a prohibited address')
    address = sorted(addresses)[0]
    address = f'[{address}]' if ':' in address else address
    return f'https://{address}{value.path}', value.hostname


class RESTConnector:
    def __init__(self, registration, secrets, *, client=None):
        from .connectors import ConnectorCapabilities
        self.registration, self.secrets = registration, secrets
        self.capabilities = ConnectorCapabilities(bulk=True, native_nulls_last=True)
        self.client = client or httpx.Client(limits=httpx.Limits(max_connections=registration.pool_size,
            max_keepalive_connections=registration.pool_size), follow_redirects=False, trust_env=False)

    def _request(self, suffix, payload):
        if not self.registration.endpoint:
            raise ValueError('REST source endpoint is required')
        url, hostname = pinned_url(self.registration.endpoint.rstrip('/') + suffix, self.registration.allowed_hosts)
        context = current_context.get()
        timeout = min(self.registration.statement_timeout_seconds, context.remaining() if context else 30)
        with self.client.stream('POST', url, headers={'Host': hostname,
            'Authorization': 'Bearer ' + self.secrets.resolve(self.registration.secret_ref)}, json=payload,
            timeout=timeout, extensions={'sni_hostname': hostname}) as response:
            response.raise_for_status()
            body = bytearray()
            for block in response.iter_bytes():
                if context:
                    context.remaining()
                body.extend(block)
                if len(body) > 8_000_000:
                    raise ValueError('REST response exceeds byte budget')
        return json.loads(body)

    def query(self, *, product, fields, filter_expr, order_by, limit, position, count_mode):
        if count_mode.value != 'none':
            raise ValueError('REST count pushdown is not supported')
        value = self._request('/query', {'resource': product.source.object_name, 'select': list(fields),
            'filter': filter_expr, 'order_by': [x.model_dump() for x in order_by], 'limit': limit,
            'position': position})
        rows = value['rows']
        if not isinstance(rows, list) or len(rows) > limit or any(not eval_filter(r, filter_expr) for r in rows):
            raise ValueError('REST connector violated bounded/policy page contract')
        return StructuredPage(rows=rows, next_position=value.get('next_position'))

    def schema(self, product):
        return self._request('/schema', {'resource': product.source.object_name})['fields']

    def statistics(self, product, filter_expr=None):
        from .connectors import SourceStatistics
        return SourceStatistics()

    def partitions(self, product):
        return []

    def bulk_read(self, **kwargs):
        position = None
        while True:
            page = self.query(**kwargs, position=position)
            yield page.rows
            if not page.next_position:
                break
            if page.next_position == position:
                raise ValueError('REST source returned a non-progressing cursor')
            position = page.next_position

    def changes(self, product, checkpoint):
        raise NotImplementedError('REST CDC requires an explicit source plugin')

    def health(self):
        return self._request('/health', {}).get('status') == 'healthy'

    def close(self):
        self.client.close()
