"""Offline regression tests for the 2S-UI lifecycle."""

import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from twosui import api, config, main


def example_env():
    return {'PANEL_DOMAIN': 'panel.example.com', 'LETSENCRYPT_EMAIL': 'admin@example.com',
            'REALITY_SERVER_NAME': 'www.example.net', 'HYSTERIA_MASQUERADE_URL': 'https://www.example.org'}


class SettingsTests(unittest.TestCase):
    def test_protocol_combinations_and_private_listeners(self):
        for vless, hysteria in (('true', 'true'), ('true', 'false'), ('false', 'true')):
            with self.subTest(vless=vless, hysteria=hysteria):
                state = config.new_state(config.settings({**example_env(), 'ENABLE_VLESS': vless, 'ENABLE_HYSTERIA': hysteria}))
                services = config.compose_config(state)['services']
                ports = services['panel']['ports']
                self.assertIn('127.0.0.1:2095:2095', ports)
                self.assertFalse(any(':2096' in port for port in ports))
                self.assertEqual('0.0.0.0:443:443/tcp' in ports, vless == 'true')
                self.assertEqual('0.0.0.0:443:443/udp' in ports, hysteria == 'true')
                self.assertNotIn('privileged', services['panel'])
                self.assertNotIn('network_mode', services['panel'])

    def test_rejects_invalid_or_foreign_settings(self):
        cases = [{'SUBSCRIPTION_DOMAIN': 'https://sub.example.com'}, {'PANEL_DOMAIN': 'a;bad.example'},
                 {'PANEL_HTTPS_PORT': '443'}, {'VLESS_PORT': '2095'}, {'VLESS_PORT': '2096'},
                 {'TWOSUI_API_PORT': '80'}, {'ENABLE_VLESS': 'false', 'ENABLE_HYSTERIA': 'false'},
                 {'ENABLE_VLESS': 'nope'}, {'SERVER_IPV4': 'invalid'}, {'LETSENCRYPT_EMAIL': ''},
                 {'HYSTERIA_MASQUERADE_URL': 'file:///tmp/file'}, {'HYSTERIA_MASQUERADE_URL': 'https://panel.example.com'},
                 {'HYSTERIA_OBFS_TYPE': 'gecko'}, {'HYSTERIA_PASSWORD': 'foreign'},
                 {'REALITY_MIN_CLIENT_VERSION': '1.8.1'}]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(config.Error):
                config.settings({**example_env(), **case})

    def test_subscription_domains_and_certificate_names(self):
        for sub in ('', 'panel.example.com', 'SUB.example.com'):
            with self.subTest(sub=sub):
                c = config.settings({**example_env(), 'SUBSCRIPTION_DOMAIN': sub})
                expected = (sub or c['panel_domain']).lower()
                self.assertEqual(config.origin(c, True), 'https://' + expected + ':8443')
                self.assertEqual(config.certificate_domains(c), list(dict.fromkeys(['panel.example.com', expected])))

    def test_dns_checks_reject_unreachable_subscription_hostname(self):
        c = config.settings({**example_env(), 'SUBSCRIPTION_DOMAIN': 'sub.example.com', 'SERVER_IPV4': '8.8.8.8'})
        def resolve(host, port, family):
            if family == main.socket.AF_INET6:
                return []
            ip = '1.1.1.1' if host == 'sub.example.com' else '8.8.8.8'
            return [(family, 0, 0, '', (ip, 0))]
        with patch.object(main.socket, 'getaddrinfo', side_effect=resolve), self.assertRaisesRegex(config.Error, 'sub.example.com'):
            main.detect_network(c)

    def test_state_secrets_and_runtime_are_private(self):
        with tempfile.TemporaryDirectory() as directory:
            state = config.new_state(config.settings(example_env()))
            config.save_state(state, directory)
            config.render(state, directory)
            self.assertEqual(main.read_state(directory), state)
            self.assertEqual((Path(directory) / 'state.json').stat().st_mode & 0o777, 0o600)
            self.assertNotIn(state['secrets']['admin_password'], (Path(directory) / 'compose.json').read_text())
            self.assertNotEqual(state['secrets']['admin_password'], 'admin')

    def test_dispatcher_selects_2s_ui_without_running_installer(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['bash', str(root / 'install.sh'), '--help'],
            env={**os.environ, 'ENV_FILE': '/dev/null', 'DEPLOYMENT_MODE': '2s-ui'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('2S-UI lifecycle', result.stdout)


class ObjectAPI:
    def __init__(self):
        self.objects = {'tls': [], 'clients': []}
        self.fail_after_create = False

    def items(self, kind):
        return copy.deepcopy(self.objects[kind])

    def save(self, kind, action, body):
        if action == 'new':
            self.objects[kind].append({**body, 'id': len(self.objects[kind]) + 1})
            if self.fail_after_create:
                self.fail_after_create = False
                raise config.Error('Response lost')
        elif action == 'del':
            self.objects[kind] = [item for item in self.objects[kind] if item['id'] != body]


class ResumeTests(unittest.TestCase):
    def test_recovers_created_object_without_duplicate_and_preserves_panel_deletion(self):
        state = config.new_state(config.settings(example_env()))
        remote = ObjectAPI()
        remote.fail_after_create = True
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(config.Error):
                api.ensure_object(state, remote, 'tls', 'tls', {'name': 'test'}, 'name', directory)
            api.ensure_object(state, remote, 'tls', 'tls', {'name': 'test'}, 'name', directory)
            self.assertEqual(len(remote.objects['tls']), 1)
            remote.objects['tls'].clear()
            with self.assertRaises(config.Error):
                api.ensure_object(state, remote, 'tls', 'tls', {'name': 'test'}, 'name', directory)

    def test_completed_bootstrap_does_not_overwrite_panel_configuration(self):
        state = config.new_state(config.settings(example_env()))
        state['bootstrapped'] = True
        api.bootstrap(state, None)

    def test_temporary_user_recovery_never_removes_real_users(self):
        state = config.new_state(config.settings(example_env()))
        state['objects'].update(vless=1, hysteria=2)
        remote = ObjectAPI()
        remote.objects['clients'] = [{'id': 50, 'name': 'real-user'}]
        with tempfile.TemporaryDirectory() as directory:
            remote.fail_after_create = True
            with self.assertRaises(config.Error):
                api.create_probe(state, remote, directory)
            self.assertTrue(state.get('probe'))
            api.cleanup_probe(state, remote, directory)
            self.assertEqual(remote.objects['clients'], [{'id': 50, 'name': 'real-user'}])
            self.assertNotIn('probe', main.read_state(directory))


class BackupTests(unittest.TestCase):
    def test_online_sqlite_snapshot_and_corruption_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            stack, backups = Path(temp) / 'stack', Path(temp) / 'backups'
            state = config.new_state(config.settings(example_env()))
            config.save_state(state, stack)
            config.render(state, stack)
            for name in ('fullchain.pem', 'privkey.key'):
                config.atomic_write(stack / 'certs' / name, 'test')
            with sqlite3.connect(stack / 'db/s-ui.db') as db:
                db.execute('CREATE TABLE users(name TEXT)')
                db.execute("INSERT INTO users VALUES ('retained')")
                db.commit()
                with patch.object(main, 'STACK', stack), patch.object(main, 'BACKUPS', backups):
                    destination = main.backup(state)
                db.execute('DELETE FROM users')
                db.commit()
            main.verify_backup(destination)
            with sqlite3.connect(destination / 'db/s-ui.db') as saved:
                self.assertEqual(saved.execute('SELECT name FROM users').fetchone(), ('retained',))
            (destination / 'nginx.conf').write_text('corrupted')
            with self.assertRaises(config.Error):
                main.verify_backup(destination)

    def test_restore_rejects_incomplete_backup_before_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            config.atomic_write(Path(directory) / 'manifest.json', '{}')
            with patch.object(main, 'compose') as compose, patch.object(main, 'backup') as backup:
                with self.assertRaises(config.Error):
                    main.restore(directory)
                compose.assert_not_called()
                backup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
