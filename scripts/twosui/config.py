"""Pinned 2S-UI deployment and initial sing-box settings."""

import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit

STACK = Path('/opt/proxy-bootstrap/2s-ui')
CERT_NAME = 'proxy-bootstrap-2s-ui'
IMAGES = {'panel': 'ghcr.io/shenaba/2s-ui:v1.8.3', 'nginx': 'nginx:1.30.5-alpine',
          'client': 'ghcr.io/sagernet/sing-box:v1.14.2'}


class Error(Exception):
    """An actionable message without credentials or API response bodies."""


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    try:
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), 'w') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save_state(state, directory=STACK):
    atomic_write(Path(directory) / 'state.json', json.dumps(state, indent=2) + '\n')


def boolean(env, name, default='true'):
    value = env.get(name, default).lower()
    if value in ('true', 'yes', 'on', '1'):
        return True
    if value in ('false', 'no', 'off', '0'):
        return False
    raise Error(f'{name} must be true or false.')


def integer(env, name, default, maximum=65535):
    value = env.get(name, str(default))
    if not re.fullmatch(r'[0-9]+', value) or not 1 <= int(value) <= maximum:
        raise Error(f'{name} must be an integer between 1 and {maximum}.')
    return int(value)


def domain(value, name):
    if len(value) > 253 or '.' not in value or any(
        not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', part)
        for part in value.split('.')
    ):
        raise Error(f'{name} must be a DNS name without a scheme, port or path.')
    return value.lower()


def settings(env):
    c = {'panel_domain': domain(env.get('PANEL_DOMAIN', ''), 'PANEL_DOMAIN'),
         'panel_port': integer(env, 'PANEL_HTTPS_PORT', 8443),
         'api_port': integer(env, 'TWOSUI_API_PORT', 2095),
         'vless': boolean(env, 'ENABLE_VLESS'), 'hysteria': boolean(env, 'ENABLE_HYSTERIA'),
         'vless_port': integer(env, 'VLESS_PORT', 443), 'hysteria_port': integer(env, 'HYSTERIA_PORT', 443),
         'server_ipv4': env.get('SERVER_IPV4', ''), 'email': env.get('LETSENCRYPT_EMAIL', ''),
         'staging': boolean(env, 'CERTBOT_STAGING', 'false'),
         'firewall': env.get('FIREWALL_BACKEND', 'auto')}
    c['subscription_domain'] = domain(env.get('SUBSCRIPTION_DOMAIN') or c['panel_domain'], 'SUBSCRIPTION_DOMAIN')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', c['email']):
        raise Error('LETSENCRYPT_EMAIL is required.')
    if not c['vless'] and not c['hysteria']:
        raise Error('Enable at least one VPN protocol.')
    if c['firewall'] not in ('auto', 'ufw', 'iptables', 'none'):
        raise Error('FIREWALL_BACKEND must be auto, ufw, iptables or none.')
    if c['server_ipv4']:
        try:
            ipaddress.IPv4Address(c['server_ipv4'])
        except ValueError:
            raise Error('SERVER_IPV4 must be an IPv4 address.') from None
    ports = [80, c['api_port'], c['panel_port']]
    if c['vless']:
        ports.append(c['vless_port'])
        if c['vless_port'] in (2095, 2096):
            raise Error('VLESS_PORT conflicts with a private 2S-UI listener (2095/2096).')
        c['reality_server'] = domain(env.get('REALITY_SERVER_NAME', ''), 'REALITY_SERVER_NAME')
        c['reality_port'] = integer(env, 'REALITY_HANDSHAKE_PORT', 443)
        c['fingerprint'] = env.get('REALITY_FINGERPRINT', 'chrome')
        if c['fingerprint'] not in ('chrome', 'firefox', 'edge', 'safari', 'ios', 'android', 'random', 'randomized'):
            raise Error('Unsupported REALITY_FINGERPRINT.')
    if len(ports) != len(set(ports)):
        raise Error('Panel, API, VLESS and ACME ports must not overlap on TCP.')
    if c['hysteria']:
        c['hysteria_domain'] = domain(env.get('HYSTERIA_DOMAIN') or c['panel_domain'], 'HYSTERIA_DOMAIN')
        url = env.get('HYSTERIA_MASQUERADE_URL', '')
        try:
            parsed = urlsplit(url)
            valid = parsed.scheme in ('http', 'https') and parsed.hostname and not re.search(r'\s', url)
        except ValueError:
            valid = False
        if not valid or parsed.hostname in (*certificate_domains(c), c['server_ipv4']):
            raise Error('HYSTERIA_MASQUERADE_URL must be an external HTTP(S) URL.')
        c['masquerade'] = url
        c['obfs'] = env.get('HYSTERIA_OBFS_TYPE', '')
        if c['obfs'] not in ('', 'salamander'):
            raise Error('2S-UI mode supports empty HYSTERIA_OBFS_TYPE or salamander.')
        c['up_mbps'] = integer(env, 'HYSTERIA_UP_MBPS', 100, 100000)
        c['down_mbps'] = integer(env, 'HYSTERIA_DOWN_MBPS', 100, 100000)
    for key in ('VLESS_UUID', 'HYSTERIA_PASSWORD', 'REALITY_PRIVATE_KEY', 'REALITY_PUBLIC_KEY',
                'REALITY_SHORT_ID', 'HYSTERIA_OBFS_PASSWORD', 'REALITY_MIN_CLIENT_VERSION'):
        if env.get(key):
            raise Error(f'{key} is not used by 2S-UI mode; start with .env.2s-ui.example.')
    return c


def origin(c, subscription=False):
    name = c['subscription_domain'] if subscription else c['panel_domain']
    return 'https://' + name + ('' if c['panel_port'] == 443 else ':' + str(c['panel_port']))


def certificate_domains(c):
    return list(dict.fromkeys([c['panel_domain'], c['subscription_domain']] +
                             ([c['hysteria_domain']] if c['hysteria'] else [])))


def new_state(c):
    return {'schema': 1, 'mode': '2s-ui', 'config': c, 'images': dict(IMAGES), 'objects': {},
            'bootstrapped': False, 'secrets': {'admin_username': 'admin_' + secrets.token_hex(4),
                'admin_password': secrets.token_urlsafe(24), 'short_id': secrets.token_hex(8),
                'obfs_password': secrets.token_urlsafe(24)}}


def compose_config(state):
    c = state['config']
    ports = [f"127.0.0.1:{c['api_port']}:2095"]
    if c['vless']:
        ports.append(f"0.0.0.0:{c['vless_port']}:{c['vless_port']}/tcp")
    if c['hysteria']:
        ports.append(f"0.0.0.0:{c['hysteria_port']}:{c['hysteria_port']}/udp")
    return {'name': 'proxy-bootstrap-2s-ui', 'services': {
        'panel': {'image': state['images']['panel'], 'restart': 'unless-stopped',
                  'environment': {'TZ': 'UTC', 'SUI_LOG_LEVEL': 'warn'}, 'ports': ports,
                  'volumes': ['./db:/app/db', './certs:/app/cert:ro'],
                  'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}},
        'nginx': {'image': state['images']['nginx'], 'restart': 'unless-stopped',
                  'ports': [f"0.0.0.0:{c['panel_port']}:8443"],
                  'volumes': ['./nginx.conf:/etc/nginx/nginx.conf:ro', './certs:/etc/nginx/certs:ro']}}}


def nginx_config(state):
    c = state['config']
    sub = 'location = /sub { return 404; }\nlocation /sub/ { limit_except GET { deny all; } proxy_pass http://panel:2096; }'
    extra = ''
    if c['subscription_domain'] != c['panel_domain']:
        extra = 'server { listen 8443 ssl; server_name %s; %s location / { return 404; } }' % (c['subscription_domain'], sub)
    return '''events {}
http {
    server_tokens off;
    access_log off;
    # Error logs can contain secret subscription paths too.
    error_log /dev/null crit;
    map $http_upgrade $connection_upgrade { default upgrade; '' close; }
    ssl_certificate /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 20m;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_read_timeout 120s;
    server { listen 8443 ssl default_server; ssl_reject_handshake on; return 404; }
    server {
        listen 8443 ssl;
        server_name %s;
        %s
        location = / { return 302 /app/; }
        location /app/ { proxy_pass http://panel:2095; }
        location / { return 404; }
    }
    %s
}
''' % (c['panel_domain'], sub, extra)


def render(state, directory=STACK):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ('db', 'certs'):
        (directory / name).mkdir(exist_ok=True, mode=0o700)
    atomic_write(directory / 'compose.json', json.dumps(compose_config(state), indent=2) + '\n')
    atomic_write(directory / 'nginx.conf', nginx_config(state))


def base_config():
    return {'log': {'level': 'warn'}, 'dns': {'servers': [{'type': 'local', 'tag': 'local'}]},
            'route': {'default_domain_resolver': {'server': 'local', 'strategy': 'ipv4_only'},
                      'rules': [{'action': 'resolve', 'strategy': 'ipv4_only'},
                                {'ip_is_private': True, 'action': 'reject'}], 'final': 'direct'}}


def subscription_template():
    return {'dns': {'servers': [{'type': 'udp', 'tag': 'remote', 'server': '1.1.1.1', 'detour': 'proxy'},
                               {'type': 'local', 'tag': 'local'}], 'final': 'remote', 'strategy': 'ipv4_only'},
            'default_domain_resolver': 'local', 'final': 'proxy',
            'rules': [{'action': 'sniff'}, {'protocol': 'dns', 'action': 'hijack-dns'}],
            'inbounds': [{'type': 'tun', 'address': ['172.19.0.1/30'], 'mtu': 1500,
                          'auto_route': True, 'strict_route': True, 'stack': 'system'}]}
