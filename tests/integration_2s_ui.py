#!/usr/bin/env python3
"""Real 2S-UI Docker test; no root, apt, certbot or host firewall changes.

Run with --separate-subscription-domain to test the second hostname.
Requires Docker Engine 28+, Compose v2, openssl, curl and internet access.
Only this test's containers are removed; all published ports use loopback.
"""

import argparse
import base64
import json
from pathlib import Path
import secrets
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from twosui import api, config, main as lifecycle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--separate-subscription-domain', action='store_true')
    args = parser.parse_args()
    existing = lifecycle.run(['docker', 'ps', '-aq', '--filter',
        'label=com.docker.compose.project=proxy-bootstrap-2s-ui'], capture=True).stdout.strip()
    if existing:
        raise SystemExit('A 2S-UI deployment with this project name exists; refusing to touch it.')
    with tempfile.TemporaryDirectory(prefix='proxy-bootstrap-2s-integration-') as directory:
        root = Path(directory)
        lifecycle.STACK = root
        lifecycle.BACKUPS = root / 'backups'
        state = config.new_state(config.settings({'PANEL_DOMAIN': 'panel.sui.test',
            'SUBSCRIPTION_DOMAIN': 'sub.sui.test' if args.separate_subscription_domain else '',
            'LETSENCRYPT_EMAIL': 'test@example.com', 'SERVER_IPV4': '127.0.0.1',
            'PANEL_HTTPS_PORT': '28443', 'TWOSUI_API_PORT': '28095', 'VLESS_PORT': '29443',
            'HYSTERIA_PORT': '29443', 'REALITY_SERVER_NAME': 'www.cloudflare.com',
            'HYSTERIA_MASQUERADE_URL': 'https://www.cloudflare.com', 'HYSTERIA_OBFS_TYPE': 'salamander',
            'CERTBOT_STAGING': 'true'}))
        config.save_state(state, root)

        def render_private(current, directory=root):
            config.render(current, directory)
            path = Path(directory) / 'compose.json'
            value = json.loads(path.read_text())
            for service in value['services'].values():
                service['ports'] = [port.replace('0.0.0.0:', '127.0.0.1:') for port in service['ports']]
            config.atomic_write(path, json.dumps(value))

        def certificate():
            san = 'subjectAltName=' + ','.join('DNS:' + host for host in config.certificate_domains(state['config']))
            lifecycle.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-subj', '/CN=panel.sui.test', '-addext', san, '-keyout', root / 'certs/privkey.key',
                '-out', root / 'certs/fullchain.pem'], capture=True)

        def request(path, host=None, method='GET', body=None, headers=None):
            host = host or state['config']['panel_domain']
            command = ['curl', '--fail', '--silent', '--show-error', '--noproxy', '*', '--max-time', '20',
                '--cacert', root / 'certs/fullchain.pem', '--resolve', f'{host}:28443:127.0.0.1',
                '--request', method, '--config', '-']
            cfg = 'url = ' + json.dumps('https://' + host + ':28443' + path) + '\n'
            for header in headers or []:
                cfg += 'header = ' + json.dumps(header) + '\n'
            if body is not None:
                cfg += 'data = ' + json.dumps(urlencode(body)) + '\n'
            return lifecycle.run(command, input=cfg, capture=True).stdout

        render_private(state)
        certificate()
        try:
            lifecycle.compose('up', '-d', '--wait', '--wait-timeout', '180', 'panel')
            client = api.API(state)
            api.prepare(state, client, root)
            api.bootstrap(state, client, root)
            lifecycle.compose('restart', 'panel')
            lifecycle.wait_core(state)
            lifecycle.start_proxy(state)
            lifecycle.compose('exec', '-T', 'nginx', 'nginx', '-t')
            lifecycle.healthcheck(state)
            # Browser-style login through HTTPS must pass Origin checks after webDomain is set.
            body = {'user': state['secrets']['admin_username'], 'pass': state['secrets']['admin_password']}
            assert json.loads(request('/app/api/login', method='POST', body=body,
                headers=['Origin: https://panel.sui.test:28443']))['success']
            default = json.loads(request('/app/api/login', method='POST', body={'user': 'admin', 'pass': 'admin'},
                headers=['Origin: https://panel.sui.test:28443']))
            assert not default['success'], 'Default admin credentials still work'
            if args.separate_subscription_domain:
                for path in ('/', '/app/', '/app/api/login', '/app/apiv2/settings', '/sub'):
                    assert lifecycle.https(state, path, subscription=True, status_only=True) == '404'
            lifecycle.smoke_test(state)
            identifiers = dict(state['objects'])
            persistent = api.create_probe(state, client, root)
            state.pop('probe')  # Retain one account across update and restore.
            config.save_state(state, root)
            user = next(c for c in client.items('clients') if c['name'] == persistent)
            expected = json.loads(lifecycle.https(state, '/sub/' + persistent + '?format=json', subscription=True))
            complete_config = root / 'full-subscription.json'
            config.atomic_write(complete_config, json.dumps(expected))
            lifecycle.run(['docker', 'run', '--rm', '-v', f'{complete_config}:/config.json:ro',
                state['images']['client'], 'check', '-c', '/config.json'], capture=True)
            complete_config.unlink()
            for host in config.certificate_domains(state['config']):
                assert json.loads(request('/sub/' + persistent + '?format=json', host=host)) == expected
                links = base64.b64decode(request('/sub/' + persistent, host=host)).decode()
                assert 'vless://' in links and ('hysteria2://' in links or 'hy2://' in links)
            # Keep a deliberate panel edit and a changed administrator password.
            client.save('settings', 'edit', {'subUpdates': '7'})
            client.login(state['secrets']['admin_username'], state['secrets']['admin_password'])
            client.request('POST', 'changePass', {'oldPass': state['secrets']['admin_password'],
                'newUsername': state['secrets']['admin_username'], 'newPass': secrets.token_urlsafe(24)}, session=True)
            api.prepare(state, api.API(state), root)
            api.bootstrap(state, client, root)
            assert state['objects'] == identifiers
            assert client.request('GET', 'settings')['subUpdates'] == '7'
            lifecycle.install_runtime()
            snapshot = lifecycle.backup(state)
            with patch.object(lifecycle, 'install_units'), patch.object(lifecycle, 'firewall'), \
                    patch.object(lifecycle, 'obtain_certificate'), patch.object(lifecycle, 'render', render_private):
                lifecycle.update(state)
                client.save('clients', 'del', user['id'])
                lifecycle.restore(snapshot)
            restored = lifecycle.read_state()
            assert client.request('GET', 'settings')['subUpdates'] == '7'
            actual = json.loads(lifecycle.https(restored, '/sub/' + persistent + '?format=json', subscription=True))
            assert actual == expected, 'Restoration changed subscription credentials or addresses'
            certificate()
            with patch.object(lifecycle, 'copy_certificates'):
                lifecycle.renew(restored)
            lifecycle.smoke_test(restored)
            assert [u['name'] for u in client.items('clients')] == [persistent], 'Temporary client leaked'
            mode = 'separate subscription domain' if args.separate_subscription_domain else 'shared domain'
            print(f'PASS ({mode}): secure bootstrap, HTTPS login, subscriptions, both protocols, private-network blocking, preserved edits, update, backup/restore and certificate reload.')
        finally:
            lifecycle.compose('down', '--volumes', check=False)


if __name__ == '__main__':
    main()
