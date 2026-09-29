"""Validated installation settings and generated files (Python standard library only)."""

import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit


STACK = Path('/opt/proxy-bootstrap/remnawave')
CERT_NAME = 'proxy-bootstrap-remnawave'
CERT_PATH = '/var/lib/remnawave/configs/xray/ssl'
IMAGES = {
    'panel': 'remnawave/backend:3.4.4',
    'node': 'remnawave/node:3.4.1',
    'db': 'postgres:18.4',
    'redis': 'valkey/valkey:9.1.2-alpine',
    'nginx': 'nginx:1.30.5-alpine',
}


class Error(Exception):
    """An actionable error safe to display without dumping secrets."""


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, 'w') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save_state(state, directory=STACK):
    atomic_write(Path(directory) / 'state.json', json.dumps(state, indent=2) + '\n')


def boolean(env, name, default):
    value = env.get(name, default).lower()
    if value in ('true', 'yes', '1', 'on'):
        return True
    if value in ('false', 'no', '0', 'off'):
        return False
    raise Error(f'{name} must be true or false.')


def integer(env, name, default, minimum=1, maximum=65535):
    value = env.get(name, str(default))
    if not re.fullmatch(r'[0-9]+', value) or not minimum <= int(value) <= maximum:
        raise Error(f'{name} must be an integer between {minimum} and {maximum}.')
    return int(value)


def domain(value, name):
    if len(value) > 253 or '.' not in value or any(
        not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', part)
        for part in value.split('.')
    ):
        raise Error(f'{name} must be a DNS name without a scheme, port or path.')
    return value.lower()


def settings(env):
    panel = domain(env.get('PANEL_DOMAIN', ''), 'PANEL_DOMAIN')
    email = env.get('LETSENCRYPT_EMAIL', '')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise Error('LETSENCRYPT_EMAIL is required for panel HTTPS.')
    config = {
        'panel_domain': panel,
        'subscription_domain': domain(env.get('SUBSCRIPTION_DOMAIN') or panel, 'SUBSCRIPTION_DOMAIN'),
        'panel_port': integer(env, 'PANEL_HTTPS_PORT', 8443),
        'api_port': integer(env, 'REMNAWAVE_API_PORT', 3000),
        'email': email,
        'vless': boolean(env, 'ENABLE_VLESS', 'true'),
        'hysteria': boolean(env, 'ENABLE_HYSTERIA', 'true'),
        'vless_port': integer(env, 'VLESS_PORT', 443),
        'hysteria_port': integer(env, 'HYSTERIA_PORT', 443),
        'staging': boolean(env, 'CERTBOT_STAGING', 'false'),
        'firewall': env.get('FIREWALL_BACKEND', 'auto'),
        'server_ipv4': env.get('SERVER_IPV4', ''),
    }
    if not config['vless'] and not config['hysteria']:
        raise Error('Enable at least one VPN protocol.')
    if config['firewall'] not in ('auto', 'ufw', 'iptables', 'none'):
        raise Error('FIREWALL_BACKEND must be auto, ufw, iptables or none.')
    if config['server_ipv4']:
        try:
            ipaddress.IPv4Address(config['server_ipv4'])
        except ValueError as exc:
            raise Error('SERVER_IPV4 must be an IPv4 address.') from exc
    tcp_ports = [80, config['panel_port'], config['api_port']]
    if config['vless']:
        tcp_ports.append(config['vless_port'])
        config['reality_server'] = domain(env.get('REALITY_SERVER_NAME', ''), 'REALITY_SERVER_NAME')
        config['reality_port'] = integer(env, 'REALITY_HANDSHAKE_PORT', 443)
        version = env.get('REALITY_MIN_CLIENT_VERSION', '26.3.27')
        if not re.fullmatch(r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}', version) or any(int(part) > 255 for part in version.split('.')):
            raise Error('REALITY_MIN_CLIENT_VERSION must contain three numbers from 0 to 255, e.g. 26.3.27 or 1.8.1.')
        config['reality_min_client_version'] = version
        config['fingerprint'] = env.get('REALITY_FINGERPRINT', 'chrome')
        if config['fingerprint'] not in ('chrome', 'firefox', 'edge', 'safari', '360', 'qq', 'ios', 'android', 'random', 'randomized'):
            raise Error('Unsupported REALITY_FINGERPRINT.')
    if len(tcp_ports) != len(set(tcp_ports)):
        raise Error('Panel, API, VLESS and ACME HTTP ports must not overlap on TCP.')
    if config['vless_port'] == 2222 and config['vless']:
        raise Error('VLESS_PORT=2222 conflicts with the private Node API inside its container.')
    if config['hysteria']:
        config['hysteria_domain'] = domain(env.get('HYSTERIA_DOMAIN') or panel, 'HYSTERIA_DOMAIN')
        url = env.get('HYSTERIA_MASQUERADE_URL', '')
        try:
            parsed = urlsplit(url)
            valid_url = parsed.scheme in ('http', 'https') and parsed.hostname and not re.search(r'\s', url)
        except ValueError:
            valid_url = False
        if not valid_url:
            raise Error('HYSTERIA_MASQUERADE_URL must be an absolute HTTP(S) URL.')
        if parsed.hostname in (panel, config['subscription_domain'], config['hysteria_domain'], config['server_ipv4']):
            raise Error('HYSTERIA_MASQUERADE_URL must not point back to this installation.')
        config['masquerade'] = url
        config['obfs'] = env.get('HYSTERIA_OBFS_TYPE', '')
        if config['obfs'] not in ('', 'salamander'):
            raise Error('Remnawave mode supports no obfs or salamander; gecko is not supported by this bootstrap.')
        config['up_mbps'] = integer(env, 'HYSTERIA_UP_MBPS', 100, 1, 1000000)
        config['down_mbps'] = integer(env, 'HYSTERIA_DOWN_MBPS', 100, 1, 1000000)
    # User credentials belong to Remnawave. Do not silently reuse a standalone user's identity.
    for name in ('VLESS_UUID', 'HYSTERIA_PASSWORD', 'REALITY_PRIVATE_KEY', 'REALITY_PUBLIC_KEY', 'REALITY_SHORT_ID', 'HYSTERIA_OBFS_PASSWORD'):
        if env.get(name):
            raise Error(f'{name} must be empty in Remnawave mode; keys are generated and users are managed in the panel.')
    return config


def new_state(config):
    return {
        'schema': 1,
        'config': config,
        'images': dict(IMAGES),
        'secrets': {
            'admin_username': 'admin',
            'admin_password': 'Aa9' + secrets.token_urlsafe(30),
            'app_secret': secrets.token_hex(64),
            'db_password': secrets.token_hex(24),
            'metrics_password': secrets.token_hex(24),
            'webhook_secret': secrets.token_hex(32),
            'obfs_password': secrets.token_hex(16),
            'short_id': secrets.token_hex(8),
        },
        'objects': {},
        'bootstrapped': False,
    }


def panel_origin(config):
    port = '' if config['panel_port'] == 443 else ':' + str(config['panel_port'])
    return 'https://' + config['panel_domain'] + port


def subscription_domain(config):
    # States written before separate subscription domains existed have no such key.
    return config.get('subscription_domain', config['panel_domain'])


def subscription_origin(config):
    port = '' if config['panel_port'] == 443 else ':' + str(config['panel_port'])
    return 'https://' + subscription_domain(config) + port


def certificate_domains(config):
    names = [config['panel_domain'], subscription_domain(config)]
    if config['hysteria']:
        names.append(config['hysteria_domain'])
    return list(dict.fromkeys(names))


def xray_config(state):
    c, s = state['config'], state['secrets']
    inbounds = []
    if c['vless']:
        inbounds.append({
            'tag': 'PB_VLESS_REALITY', 'listen': '0.0.0.0', 'port': c['vless_port'],
            'protocol': 'vless', 'settings': {'clients': [], 'decryption': 'none', 'flow': 'xtls-rprx-vision'},
            'streamSettings': {
                'network': 'tcp', 'security': 'reality',
                'realitySettings': {
                    'show': False, 'dest': f"{c['reality_server']}:{c['reality_port']}",
                    # Keep the Xray default explicit; sing-box compatibility is an opt-in.
                    'minClientVer': c.get('reality_min_client_version', '26.3.27'),
                    'xver': 0, 'serverNames': [c['reality_server']],
                    'privateKey': s['reality_private_key'], 'shortIds': [s['short_id']],
                },
            },
        })
    if c['hysteria']:
        stream = {
            'network': 'hysteria', 'security': 'tls',
            'tlsSettings': {'alpn': ['h3'], 'certificates': [{
                'certificateFile': CERT_PATH + '/fullchain.pem',
                'keyFile': CERT_PATH + '/privkey.key',
            }]},
            'hysteriaSettings': {'version': 2, 'masquerade': {
                'type': 'proxy', 'url': c['masquerade'], 'rewriteHost': True,
            }},
            'finalmask': {'quicParams': {
                'congestion': 'brutal',
                'brutalUp': f"{c['up_mbps']} mbps", 'brutalDown': f"{c['down_mbps']} mbps",
            }},
        }
        if c['obfs']:
            stream['finalmask']['udp'] = [{'type': 'salamander', 'settings': {'password': s['obfs_password']}}]
        inbounds.append({
            'tag': 'PB_HYSTERIA2', 'listen': '0.0.0.0', 'port': c['hysteria_port'],
            'protocol': 'hysteria', 'settings': {'version': 2, 'clients': []}, 'streamSettings': stream,
        })
    return {
        'log': {'loglevel': 'warning'}, 'inbounds': inbounds,
        'outbounds': [{'tag': 'DIRECT', 'protocol': 'freedom'}, {'tag': 'BLOCK', 'protocol': 'blackhole'}],
        'routing': {'domainStrategy': 'IPIfNonMatch',
                    'rules': [{'type': 'field', 'ip': ['geoip:private'], 'outboundTag': 'BLOCK'}]},
    }


def compose_config(state):
    c, s, images = state['config'], state['secrets'], state['images']
    common = {'restart': 'unless-stopped', 'logging': {
        'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'},
    }}
    vpn_ports = []
    if c['vless']:
        vpn_ports.append(f"0.0.0.0:{c['vless_port']}:{c['vless_port']}/tcp")
    if c['hysteria']:
        vpn_ports.append(f"0.0.0.0:{c['hysteria_port']}:{c['hysteria_port']}/udp")
    return {
        'name': 'proxy-bootstrap-remnawave',
        'services': {
            'db': {**common, 'image': images['db'], 'environment': {
                'POSTGRES_USER': 'postgres', 'POSTGRES_DB': 'postgres', 'POSTGRES_PASSWORD': s['db_password'], 'TZ': 'UTC',
            }, 'volumes': ['db-data:/var/lib/postgresql'],
                'healthcheck': {'test': ['CMD-SHELL', 'pg_isready -U postgres -d postgres'], 'interval': '3s', 'timeout': '5s', 'retries': 30}},
            'redis': {**common, 'image': images['redis'],
                'volumes': ['valkey-socket:/var/run/valkey'],
                'command': ['valkey-server', '--save', '', '--appendonly', 'no', '--maxmemory-policy', 'noeviction',
                            '--unixsocket', '/var/run/valkey/valkey.sock', '--unixsocketperm', '777', '--port', '0'],
                'healthcheck': {'test': ['CMD', 'valkey-cli', '-s', '/var/run/valkey/valkey.sock', 'ping'], 'interval': '3s', 'timeout': '5s', 'retries': 30}},
            'panel': {**common, 'image': images['panel'], 'environment': {
                'APP_PORT': '3000', 'METRICS_PORT': '3001', 'API_INSTANCES': '1',
                'DATABASE_URL': f"postgresql://postgres:{s['db_password']}@db:5432/postgres",
                'REDIS_SOCKET': '/var/run/valkey/valkey.sock', 'APP_SECRET': s['app_secret'],
                'METRICS_USER': 'bootstrap', 'METRICS_PASS': s['metrics_password'],
                'WEBHOOK_SECRET_HEADER': s['webhook_secret'], 'IS_TELEGRAM_NOTIFICATIONS_ENABLED': 'false',
                'FRONT_END_DOMAIN': panel_origin(c),
                'PANEL_DOMAIN': panel_origin(c).removeprefix('https://'),
                'SUB_PUBLIC_DOMAIN': subscription_origin(c).removeprefix('https://') + '/api/sub',
            }, 'ports': [f"127.0.0.1:{c['api_port']}:3000"],
                'volumes': ['valkey-socket:/var/run/valkey', './certs:' + CERT_PATH + ':ro'],
                'depends_on': {'db': {'condition': 'service_healthy'}, 'redis': {'condition': 'service_healthy'}},
                'healthcheck': {'test': ['CMD', 'curl', '-f', 'http://localhost:3001/health'], 'interval': '10s', 'timeout': '5s', 'retries': 30, 'start_period': '30s'}},
            'node': {**common, 'image': images['node'], 'environment': {
                'NODE_PORT': '2222', 'SECRET_KEY': s.get('node_secret', ''),
            }, 'ports': vpn_ports,
                'volumes': ['./certs:' + CERT_PATH + ':ro']},
            'nginx': {**common, 'image': images['nginx'],
                'ports': [f"0.0.0.0:{c['panel_port']}:8443"],
                'volumes': ['./nginx.conf:/etc/nginx/nginx.conf:ro', './certs:/etc/nginx/certs:ro'],
                'depends_on': {'panel': {'condition': 'service_healthy'}}},
        },
        'volumes': {'db-data': {}, 'valkey-socket': {}},
    }


def nginx_config(state):
    c = state['config']
    subscription_server = ''
    if subscription_domain(c) != c['panel_domain']:
        subscription_server = '''
    server {
        listen 8443 ssl;
        http2 on;
        server_name %s;
        # Only subscription endpoints are public on this hostname.
        location = /api/sub { return 404; }
        location /api/sub/ {
            limit_except GET { deny all; }
            proxy_pass http://panel:3000;
        }
        location / { return 404; }
    }
''' % subscription_domain(c)
    return '''events {}
http {
    server_tokens off;
    # Subscription paths contain credentials. Do not retain them in access logs.
    access_log off;
    map $http_upgrade $connection_upgrade { default upgrade; '' close; }
    ssl_certificate /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 20m;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_read_timeout 120s;
    server {
        listen 8443 ssl default_server;
        ssl_reject_handshake on;
        server_name _;
        return 404;
    }
    server {
        listen 8443 ssl;
        http2 on;
        server_name %s;
        location / {
            proxy_pass http://panel:3000;
        }
    }
%s
}
''' % (c['panel_domain'], subscription_server)


def render(state, directory=STACK):
    directory = Path(directory)
    # JSON is valid Compose input and avoids YAML interpolation/quoting ambiguity.
    atomic_write(directory / 'compose.json', json.dumps(compose_config(state), indent=2) + '\n')
    atomic_write(directory / 'nginx.conf', nginx_config(state))
