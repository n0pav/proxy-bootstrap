"""Remnawave 3.4.4 API bootstrap. Never edits an existing profile or user."""

from datetime import datetime, timedelta, timezone
import json
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from config import Error, STACK, save_state, xray_config


class APIError(Error):
    def __init__(self, method, path, status):
        self.status = status
        # API bodies and subscription URLs can contain credentials.
        super().__init__(f'Remnawave API {method} {path.split("?")[0]} failed (HTTP {status}).')


class API:
    def __init__(self, state):
        self.base = f"http://127.0.0.1:{state['config']['api_port']}/api"
        self.token = state['secrets'].get('api_token', '')
        self.admin_session = False
        self.host = state['config']['panel_domain']
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method, path, body=None):
        headers = {'Content-Type': 'application/json', 'X-Forwarded-Proto': 'https',
                   'X-Forwarded-For': '127.0.0.1', 'Host': self.host}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if self.admin_session:
            # Token administration uses the same authenticated admin session as the UI.
            headers['X-Remnawave-Client-Type'] = 'browser'
        payload = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=payload, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read()
                return json.loads(data)['response'] if data else None
        except HTTPError as exc:
            raise APIError(method, path, exc.code) from None
        except (URLError, TimeoutError, OSError) as exc:
            raise Error('Cannot reach the local Remnawave API; inspect panel container logs.') from exc
        except (ValueError, KeyError) as exc:
            raise Error('Unexpected Remnawave API response; check the supported panel version.') from exc

    def wait_ready(self, timeout=180):
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self.request('GET', '/auth/status')
            except Error:
                if time.monotonic() >= deadline:
                    raise Error('Panel API did not become ready within 180 seconds.') from None
                time.sleep(3)


def prepare(state, api, directory=STACK):
    s = state['secrets']
    status = api.wait_ready()
    if s.get('api_token'):
        api.request('GET', '/nodes')
    else:
        path = '/auth/register' if status['isRegisterAllowed'] else '/auth/login'
        result = api.request('POST', path, {'username': s['admin_username'], 'password': s['admin_password']})
        api.token = result['accessToken']
        api.admin_session = True
        name = 'proxy-bootstrap'
        # A process killed between token creation and fsync may have left an orphan token.
        tokens = api.request('GET', '/tokens')['tokens']
        for token in tokens:
            if token['name'] == name:
                api.request('DELETE', '/tokens/' + token['uuid'])
        result = api.request('POST', '/tokens', {
            'name': name, 'expiresInDays': 3650,
            'scopes': ['nodes:*', 'config-profiles:*', 'internal-squads:read', 'internal-squads:create',
                       'hosts:read', 'hosts:create', 'keygen:read', 'system:read',
                       'users:read', 'users:create', 'users:delete', 'subscriptions:read'],
        })
        s['api_token'] = result['token']
        s['api_token_uuid'] = result['uuid']
        save_state(state, directory)
        api.token = s['api_token']
        api.admin_session = False
    if not s.get('node_secret'):
        s['node_secret'] = api.request('GET', '/keygen')['secretKey']
        save_state(state, directory)
    if state['config']['vless'] and not s.get('reality_private_key'):
        keypair = api.request('GET', '/system/tools/x25519/generate')['keypairs'][0]
        s['reality_private_key'] = keypair['privateKey']
        save_state(state, directory)


def ensure_object(state, api, key, path, body, list_key=None, name_key='name', directory=STACK):
    objects = state['objects']
    if key in objects:
        # A deleted object is a deliberate panel edit: do not silently resurrect it.
        return api.request('GET', path + '/' + objects[key])
    existing = api.request('GET', path)
    if list_key:
        existing = existing[list_key]
    matches = [item for item in existing if item[name_key] == body[name_key]]
    if len(matches) > 1:
        raise Error(f'Multiple bootstrap objects found for {key}; resolve the duplicate in the panel.')
    result = matches[0] if matches else api.request('POST', path, body)
    objects[key] = result['uuid']
    save_state(state, directory)
    return result


def bootstrap(state, api, directory=STACK):
    if state['bootstrapped']:
        return
    c = state['config']
    profile = ensure_object(state, api, 'profile', '/config-profiles', {
        'name': 'Proxy Bootstrap', 'config': xray_config(state),
    }, list_key='configProfiles', directory=directory)
    inbounds = {item['tag']: item['uuid'] for item in profile['inbounds']}
    expected = (['PB_VLESS_REALITY'] if c['vless'] else []) + (['PB_HYSTERIA2'] if c['hysteria'] else [])
    if set(expected) != set(inbounds):
        raise Error('The bootstrap profile was edited before setup finished; reconcile it in the panel.')
    node = ensure_object(state, api, 'node', '/nodes', {
        'name': 'Local VPS', 'address': 'node', 'port': 2222,
        'configProfile': {'activeConfigProfileUuid': profile['uuid'], 'activeInbounds': list(inbounds.values())},
    }, directory=directory)
    squad = ensure_object(state, api, 'squad', '/internal-squads', {
        'name': 'VPN Users', 'inbounds': list(inbounds.values()),
    }, list_key='internalSquads', directory=directory)
    for tag in expected:
        vless = tag == 'PB_VLESS_REALITY'
        body = {
            'inbound': {'configProfileUuid': profile['uuid'], 'configProfileInboundUuid': inbounds[tag]},
            'remark': 'VLESS Reality' if vless else 'Hysteria2',
            'address': c['server_ipv4'] if vless else c['hysteria_domain'],
            'port': c['vless_port'] if vless else c['hysteria_port'],
            'sni': c['reality_server'] if vless else c['hysteria_domain'],
            'nodes': [node['uuid']],
        }
        if vless:
            body['fingerprint'] = c['fingerprint']
        ensure_object(state, api, tag, '/hosts', body, name_key='remark', directory=directory)
    state['bootstrapped'] = True
    state['squad_name'] = squad['name']
    save_state(state, directory)


def wait_node(state, api, timeout=120):
    deadline = time.monotonic() + timeout
    while True:
        node = api.request('GET', '/nodes/' + state['objects']['node'])
        if node['isConnected'] and not node['isDisabled'] and node.get('xrayUptime', 0) > 0:
            return node
        if time.monotonic() >= deadline:
            raise Error('Local Node/Xray is not ready. Inspect the node logs and profile in the panel.')
        time.sleep(3)


def cleanup_probe(state, api, directory=STACK):
    probe = state.get('probe')
    if not probe:
        return
    # Recover the user by our saved random username even if creation succeeded but its response was lost.
    try:
        user = api.request('GET', '/users/by-username/' + probe['username'])
        api.request('DELETE', '/users/' + str(user['id']))
    except APIError as exc:
        if exc.status != 404:
            raise
    del state['probe']
    save_state(state, directory)


def create_probe(state, api, directory=STACK):
    cleanup_probe(state, api, directory)
    state['probe'] = {'username': 'bootstrap_test_' + secrets.token_hex(6)}
    save_state(state, directory)
    return api.request('POST', '/users', {
        'username': state['probe']['username'], 'status': 'ACTIVE',
        'expireAt': (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        'trafficLimitBytes': 100 * 1024 * 1024,
        'activeInternalSquads': [state['objects']['squad']],
    })
