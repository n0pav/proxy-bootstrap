"""2S-UI v1.8.3 API bootstrap. Existing panel-managed objects are preserved."""

from http.cookiejar import CookieJar
import json
import re
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
import uuid

from .config import Error, STACK, base_config, origin, save_state, subscription_template


class API:
    def __init__(self, state):
        self.state = state
        self.token = state['secrets'].get('api_token', '')
        self.base = f"http://127.0.0.1:{state['config']['api_port']}/app/"
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

    def request(self, method, action, data=None, session=False):
        path = ('api/' if session else 'apiv2/') + action
        headers = {'Content-Type': 'application/x-www-form-urlencoded',
                   'Host': self.state['config']['panel_domain'],
                   'Origin': 'http://' + self.state['config']['panel_domain']}
        if self.token and not session:
            headers['Token'] = self.token
        payload = None if data is None else urlencode(data).encode()
        try:
            with self.opener.open(Request(self.base + path, data=payload, headers=headers, method=method), timeout=30) as response:
                result = json.load(response)
            if result.get('success') is not True:
                raise Error(f'2S-UI API {method} {action.split("?")[0]} rejected the request.')
            return result.get('obj')
        except (HTTPError, URLError, TimeoutError, ValueError, OSError):
            raise Error(f'2S-UI API {method} {action.split("?")[0]} failed; inspect the panel logs.') from None

    def login(self, username, password):
        self.request('POST', 'login', {'user': username, 'pass': password}, session=True)

    def save(self, obj, action, data):
        # Match the UI's JSON.stringify: the panel stores RawMessage as SQLite blobs.
        return self.request('POST', 'save', {'object': obj, 'action': action,
                                           'data': json.dumps(data, separators=(',', ':'))})

    def items(self, obj):
        return self.request('GET', obj).get(obj) or []

    def wait(self, timeout=120):
        deadline = time.monotonic() + timeout
        while True:
            try:
                request = Request(self.base, headers={'Host': self.state['config']['panel_domain']})
                with self.opener.open(request, timeout=5) as response:
                    if response.status == 200:
                        return
            except (OSError, URLError):
                pass
            if time.monotonic() >= deadline:
                raise Error('2S-UI did not become ready within 120 seconds.')
            time.sleep(2)


def prepare(state, api, directory=STACK):
    api.wait()
    s = state['secrets']
    if s.get('api_token'):
        api.request('GET', 'settings')
        return
    try:
        api.login(s['admin_username'], s['admin_password'])
    except Error:
        if state.get('admin_initialized'):
            raise Error('Initial administrator credentials no longer work; restore the saved API token.') from None
        api.login('admin', 'admin')
        api.request('POST', 'changePass', {'oldPass': 'admin', 'newUsername': s['admin_username'],
                                        'newPass': s['admin_password']}, session=True)
        api.login(s['admin_username'], s['admin_password'])
    state['admin_initialized'] = True
    save_state(state, directory)
    # Recover a crash between token creation and saving its secret without leaving orphan tokens.
    for token in api.request('GET', 'tokens', session=True) or []:
        if token['desc'] == 'proxy-bootstrap':
            api.request('POST', 'deleteToken', {'id': token['id']}, session=True)
    s['api_token'] = api.request('POST', 'addToken', {'expiry': '3650', 'desc': 'proxy-bootstrap'}, session=True)
    save_state(state, directory)
    api.token = s['api_token']


def ensure_object(state, api, key, kind, body, name_key, directory):
    items = api.items(kind)
    if key in state['objects']:
        matches = [item for item in items if item['id'] == state['objects'][key]]
        if not matches:
            raise Error(f'The {key} object was removed in the panel; it will not be recreated automatically.')
        return matches[0]['id']
    matches = [item for item in items if item[name_key] == body[name_key]]
    if not matches:
        api.save(kind, 'new', body)
        matches = [item for item in api.items(kind) if item[name_key] == body[name_key]]
    if len(matches) != 1:
        raise Error(f'Expected exactly one {key} object in 2S-UI.')
    state['objects'][key] = matches[0]['id']
    save_state(state, directory)
    return matches[0]['id']


def bootstrap(state, api, directory=STACK):
    if state['bootstrapped']:
        return
    c, s = state['config'], state['secrets']
    if c['vless'] and 'reality_private_key' not in s:
        values = api.request('GET', 'keypairs?k=reality')
        keys = dict(value.split(': ', 1) for value in values)
        for name in ('PrivateKey', 'PublicKey'):
            if not re.fullmatch(r'[A-Za-z0-9_-]{43}', keys.get(name, '')):
                raise Error('2S-UI returned an invalid Reality keypair.')
        s['reality_private_key'], s['reality_public_key'] = keys['PrivateKey'], keys['PublicKey']
        save_state(state, directory)
    api.save('settings', 'edit', {'webDomain': c['panel_domain'], 'webURI': origin(c) + '/app/',
        'subURI': origin(c, True) + '/sub/', 'timeLocation': 'UTC',
        'subJsonExt': json.dumps(subscription_template()), 'subClashUdp': 'true'})
    ensure_object(state, api, 'direct', 'outbounds', {'type': 'direct', 'tag': 'direct'}, 'tag', directory)
    api.save('config', 'edit', base_config())
    # A config save restarts the embedded core asynchronously; wait before adding live inbounds.
    time.sleep(2)
    if c['vless']:
        tls = ensure_object(state, api, 'reality_tls', 'tls', {'name': 'PB Reality',
            'server': {'enabled': True, 'server_name': c['reality_server'], 'reality': {'enabled': True,
                'handshake': {'server': c['reality_server'], 'server_port': c['reality_port']},
                'private_key': s['reality_private_key'], 'short_id': [s['short_id']]}},
            'client': {'enabled': True, 'server_name': c['reality_server'],
                'utls': {'enabled': True, 'fingerprint': c['fingerprint']},
                'reality': {'enabled': True, 'public_key': s['reality_public_key'], 'short_id': s['short_id']}}}, 'name', directory)
        ensure_object(state, api, 'vless', 'inbounds', {'type': 'vless', 'tag': 'VLESS Reality',
            'listen': '0.0.0.0', 'listen_port': c['vless_port'], 'tls_id': tls,
            'addrs': [{'server': c['server_ipv4'], 'server_port': c['vless_port']}]}, 'tag', directory)
    if c['hysteria']:
        tls = ensure_object(state, api, 'hysteria_tls', 'tls', {'name': 'PB Hysteria2',
            'server': {'enabled': True, 'server_name': c['hysteria_domain'],
                       'certificate_path': '/app/cert/fullchain.pem', 'key_path': '/app/cert/privkey.key'},
            'client': {'enabled': True, 'server_name': c['hysteria_domain']}}, 'name', directory)
        body = {'type': 'hysteria2', 'tag': 'Hysteria2', 'listen': '0.0.0.0',
                'listen_port': c['hysteria_port'], 'tls_id': tls,
                'up_mbps': c['up_mbps'], 'down_mbps': c['down_mbps'],
                'masquerade': {'type': 'proxy', 'url': c['masquerade'], 'rewrite_host': True},
                'addrs': [{'server': c['hysteria_domain'], 'server_port': c['hysteria_port']}]}
        if c['obfs']:
            body['obfs'] = {'type': c['obfs'], 'password': s['obfs_password']}
        ensure_object(state, api, 'hysteria', 'inbounds', body, 'tag', directory)
    state['bootstrapped'] = True
    save_state(state, directory)


def cleanup_probe(state, api, directory=STACK):
    name = state.get('probe')
    if name is None:
        return
    for client in api.items('clients'):
        if client['name'] == name:
            api.save('clients', 'del', client['id'])
    del state['probe']
    save_state(state, directory)


def create_probe(state, api, directory=STACK):
    cleanup_probe(state, api, directory)
    name = 'bootstrap_test_' + secrets.token_hex(16)
    state['probe'] = name
    save_state(state, directory)
    inbounds = [state['objects'][key] for key in ('vless', 'hysteria') if state['config'][key]]
    api.save('clients', 'new', {'name': name, 'enable': True, 'inbounds': inbounds,
        'volume': 100 * 1024 * 1024, 'expiry': int(time.time()) + 900,
        'config': {'vless': {'name': name, 'uuid': str(uuid.uuid4()), 'flow': 'xtls-rprx-vision'},
                   'hysteria2': {'name': name, 'password': secrets.token_urlsafe(24)}}, 'remark': 'Bootstrap probe'})
    return name
