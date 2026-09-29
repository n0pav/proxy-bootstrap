#!/usr/bin/env python3
"""Optional real-container test; no root, apt, certbot or host firewall changes.

Run: python3 tests/integration_remnawave.py
Requires Docker Engine 28+, Compose v2, openssl and outbound internet access.
Downloads the pinned images. Uses a self-signed test certificate and loopback ports.
Removes ONLY this test's Compose containers and volumes in finally.
"""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/remnawave'))
from api import API, bootstrap, prepare, wait_node
from config import new_state, render, save_state, settings
import main as lifecycle


def command(*args):
    subprocess.run(args, check=True)


def main():
    existing = subprocess.check_output(['docker', 'ps', '-aq', '--filter',
                                        'label=com.docker.compose.project=proxy-bootstrap-remnawave'], text=True).strip()
    volumes = subprocess.check_output(['docker', 'volume', 'ls', '-q', '--filter',
                                       'label=com.docker.compose.project=proxy-bootstrap-remnawave'], text=True).strip()
    if existing or volumes:
        raise SystemExit('A Remnawave deployment with this project name exists; refusing to touch it.')
    with tempfile.TemporaryDirectory(prefix='proxy-bootstrap-integration-') as directory:
        root = Path(directory)
        lifecycle.STACK = root
        lifecycle.BACKUPS = root / 'backups'
        state = new_state(settings({
            'PANEL_DOMAIN': 'rw.test', 'LETSENCRYPT_EMAIL': 'test@example.com',
            'PANEL_HTTPS_PORT': '25444', 'REMNAWAVE_API_PORT': '25300',
            'VLESS_PORT': '25443', 'HYSTERIA_PORT': '25443', 'SERVER_IPV4': '127.0.0.1',
            'REALITY_SERVER_NAME': 'www.cloudflare.com',
            'HYSTERIA_MASQUERADE_URL': 'https://www.cloudflare.com',
            'HYSTERIA_OBFS_TYPE': 'salamander', 'CERTBOT_STAGING': 'true',
        }))
        save_state(state, root)
        (root / 'certs').mkdir()
        command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-subj', '/CN=rw.test', '-addext', 'subjectAltName=DNS:rw.test',
                '-keyout', str(root / 'certs/privkey.key'), '-out', str(root / 'certs/fullchain.pem'))

        def render_private():
            render(state, root)
            file = root / 'compose.json'
            config = json.loads(file.read_text())
            for service in config['services'].values():
                if 'ports' in service:
                    service['ports'] = [port.replace('0.0.0.0:', '127.0.0.1:') for port in service['ports']]
            file.write_text(json.dumps(config))

        render_private()
        try:
            lifecycle.compose('up', '-d', '--wait', '--wait-timeout', '240', 'db', 'redis', 'panel')
            api = API(state)
            prepare(state, api, root)
            render_private()
            lifecycle.compose('up', '-d', 'node')
            bootstrap(state, api, root)
            wait_node(state, api)
            lifecycle.compose('up', '-d', 'nginx')
            lifecycle.healthcheck(state)
            lifecycle.smoke_test(state, check_private_routes=True)
            # The same API token works after a process restart; bootstrap preserves panel edits.
            identifiers = dict(state['objects'])
            second_api = API(state)
            second_api.request('PATCH', '/config-profiles', {
                'uuid': identifiers['profile'], 'name': 'Edited in panel',
            })
            persistent = second_api.request('POST', '/users', {
                'username': 'persistent_test_user',
                'expireAt': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                'activeInternalSquads': [identifiers['squad']],
            })
            prepare(state, second_api, root)
            bootstrap(state, second_api, root)
            assert state['objects'] == identifiers
            lifecycle.compose('restart', 'panel', 'node')
            lifecycle.healthcheck(state)
            users = second_api.request('GET', '/users')['users']
            assert not any(u['username'].startswith('bootstrap_test_') for u in users)
            lifecycle.install_runtime()
            saved = lifecycle.backup(state)
            # Exercise actual pg_dump/pg_restore and the lifecycle restore ordering, while
            # excluding Ubuntu-specific host operations from this Docker-only test.
            with patch('main.firewall'), patch('main.obtain_certificate'), patch('main.install_units'):
                lifecycle.restore(saved)
            restored = second_api.request('GET', '/users/' + str(persistent['id']))
            assert restored['subscriptionUrl'] == persistent['subscriptionUrl']
            assert restored['vlessUuid'] == persistent['vlessUuid']
            assert second_api.request('GET', '/config-profiles/' + identifiers['profile'])['name'] == 'Edited in panel'
            # Rotate the test certificate and exercise the actual deploy-hook actions.
            command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=rw.test', '-addext', 'subjectAltName=DNS:rw.test',
                    '-keyout', str(root / 'certs/privkey.key'), '-out', str(root / 'certs/fullchain.pem'))
            restored_state = lifecycle.read_state()
            with patch('main.copy_certificates'):
                lifecycle.renew(restored_state)
            lifecycle.smoke_test(restored_state)
            print('PASS: API bootstrap, subscription, both VPN protocols, restart, backup/restore, preserved edits and certificate reload.')
        except Exception:
            if (root / 'last-probe.log').exists():
                shutil.copy2(root / 'last-probe.log', '/tmp/proxy-bootstrap-last-probe.log')
            raise
        finally:
            lifecycle.compose('down', '--volumes', check=False)


if __name__ == '__main__':
    main()
