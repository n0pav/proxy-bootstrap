"""Offline lifecycle regression tests. Run: python3 -m unittest discover -s tests -v."""

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/remnawave'))
import api
import config
import main as lifecycle


def example_env():
    return {'PANEL_DOMAIN': 'panel.example.com', 'LETSENCRYPT_EMAIL': 'admin@example.com',
            'REALITY_SERVER_NAME': 'www.example.net',
            'HYSTERIA_MASQUERADE_URL': 'https://www.example.org', 'SERVER_IPV4': '203.0.113.10'}


class SettingsTests(unittest.TestCase):
    def test_protocol_switches_and_shared_tcp_udp_port(self):
        for vless, hysteria in [('true', 'false'), ('false', 'true'), ('true', 'true')]:
            with self.subTest(vless=vless, hysteria=hysteria):
                state = config.new_state(config.settings({**example_env(), 'ENABLE_VLESS': vless, 'ENABLE_HYSTERIA': hysteria}))
                state['secrets']['reality_private_key'] = 'example-key'
                inbound = config.xray_config(state)['inbounds']
                self.assertEqual(len(inbound), (vless == 'true') + (hysteria == 'true'))
                self.assertTrue(all(i['port'] == 443 for i in inbound))

    def test_rejects_invalid_settings_before_any_installation(self):
        cases = [{'PANEL_DOMAIN': 'x;touch.bad'}, {'PANEL_DOMAIN': 'https://panel.example.com'},
                 {'SUBSCRIPTION_DOMAIN': 'https://sub.example.com'}, {'SUBSCRIPTION_DOMAIN': 'sub.example.com:8443'},
                 {'SUBSCRIPTION_DOMAIN': 'sub.example.com/api/sub'}, {'SUBSCRIPTION_DOMAIN': '*.example.com'},
                 {'SUBSCRIPTION_DOMAIN': 'sub.example.com', 'HYSTERIA_MASQUERADE_URL': 'https://sub.example.com'},
                 {'PANEL_HTTPS_PORT': '443'}, {'REMNAWAVE_API_PORT': '80'}, {'VLESS_PORT': '2222'},
                 {'ENABLE_VLESS': 'false', 'ENABLE_HYSTERIA': 'false'}, {'ENABLE_HYSTERIA': 'maybe'},
                 {'HYSTERIA_OBFS_TYPE': 'gecko'}, {'HYSTERIA_MASQUERADE_URL': 'file:///etc/passwd'},
                 {'HYSTERIA_MASQUERADE_URL': 'https://panel.example.com'}, {'SERVER_IPV4': 'invalid'},
                 {'VLESS_UUID': 'existing-user'}, {'REALITY_PRIVATE_KEY': 'existing-private-key'}]
        cases.extend({'REALITY_MIN_CLIENT_VERSION': value} for value in ('', '1.8', '1.8.1.0', '256.1.1', '-1.8.1', 'any'))
        for case in cases:
            with self.subTest(case=case), self.assertRaises(config.Error):
                config.settings({**example_env(), **case})

    def test_hysteria_domain_reuses_panel_and_salamander_is_in_subscription_source(self):
        state = config.new_state(config.settings({**example_env(), 'HYSTERIA_OBFS_TYPE': 'salamander'}))
        state['secrets']['reality_private_key'] = 'key'
        self.assertEqual(state['config']['hysteria_domain'], 'panel.example.com')
        hy = config.xray_config(state)['inbounds'][1]
        self.assertEqual(hy['protocol'], 'hysteria')
        self.assertEqual(hy['settings']['version'], 2)
        self.assertEqual(hy['streamSettings']['hysteriaSettings']['masquerade']['type'], 'proxy')
        self.assertEqual(hy['streamSettings']['finalmask']['udp'][0]['settings']['password'], state['secrets']['obfs_password'])

    def test_reality_client_compatibility_is_explicit_and_legacy_default_is_preserved(self):
        for version in (None, '26.3.27', '1.8.1'):
            with self.subTest(version=version):
                env = example_env()
                if version is not None:
                    env['REALITY_MIN_CLIENT_VERSION'] = version
                state = config.new_state(config.settings(env))
                state['secrets']['reality_private_key'] = 'key'
                reality = config.xray_config(state)['inbounds'][0]['streamSettings']['realitySettings']
                self.assertEqual(reality['minClientVer'], version or '26.3.27')
                self.assertEqual(reality['privateKey'], 'key')
                self.assertEqual(reality['shortIds'], [state['secrets']['short_id']])
        del state['config']['reality_min_client_version']
        self.assertEqual(config.xray_config(state)['inbounds'][0]['streamSettings']['realitySettings']['minClientVer'], '26.3.27')

    def test_compose_does_not_publish_control_plane_or_database(self):
        state = config.new_state(config.settings(example_env()))
        services = config.compose_config(state)['services']
        self.assertNotIn('ports', services['db'])
        self.assertNotIn('ports', services['redis'])
        self.assertEqual(services['panel']['ports'], ['127.0.0.1:3000:3000'])
        self.assertEqual(services['node']['ports'], ['0.0.0.0:443:443/tcp', '0.0.0.0:443:443/udp'])
        self.assertNotIn('network_mode', services['node'])
        self.assertEqual(services['panel']['environment']['SUB_PUBLIC_DOMAIN'], 'panel.example.com:8443/api/sub')
        self.assertEqual(services['panel']['environment']['FRONT_END_DOMAIN'], 'https://panel.example.com:8443')

    def test_optional_subscription_domain_and_https_port(self):
        for name in (None, '', 'panel.example.com', 'SUB.example.com'):
            for port in (443, 8443):
                with self.subTest(name=name, port=port):
                    env = {**example_env(), 'ENABLE_VLESS': 'false', 'PANEL_HTTPS_PORT': str(port)}
                    if name is not None:
                        env['SUBSCRIPTION_DOMAIN'] = name
                    state = config.new_state(config.settings(env))
                    expected = (name or 'panel.example.com').lower() + ('' if port == 443 else ':8443')
                    self.assertEqual(config.subscription_origin(state['config']), 'https://' + expected)
                    services = config.compose_config(state)['services']
                    self.assertEqual(services['panel']['environment']['SUB_PUBLIC_DOMAIN'], expected + '/api/sub')
                    self.assertEqual(services['panel']['environment']['FRONT_END_DOMAIN'], config.panel_origin(state['config']))
                    self.assertEqual(services['nginx']['ports'], [f'0.0.0.0:{port}:8443'])

    def test_certificate_covers_all_domains_without_duplicates(self):
        for hysteria in ('panel.example.com', 'sub.example.com', 'vpn.example.com'):
            with self.subTest(hysteria=hysteria):
                state = config.new_state(config.settings({**example_env(),
                    'SUBSCRIPTION_DOMAIN': 'sub.example.com', 'HYSTERIA_DOMAIN': hysteria}))
                with patch('main.run') as run, patch('main.copy_certificates'):
                    lifecycle.obtain_certificate(state)
                command = run.call_args.args[0]
                domains = [command[i + 1] for i, value in enumerate(command) if value == '-d']
                expected = ['panel.example.com', 'sub.example.com']
                if hysteria == 'vpn.example.com':
                    expected.append(hysteria)
                self.assertEqual(domains, expected)
        c = config.settings({**example_env(), 'ENABLE_HYSTERIA': 'false'})
        self.assertEqual(config.certificate_domains(c), ['panel.example.com'])

    def test_subscription_dns_is_validated_before_installation(self):
        c = config.settings({**example_env(), 'SERVER_IPV4': '8.8.8.8', 'SUBSCRIPTION_DOMAIN': 'sub.example.com'})
        for failure in (None, 'missing', 'wrong_ipv4', 'ipv6'):
            with self.subTest(failure=failure):
                def resolve(name, port, family):
                    if name == 'sub.example.com' and failure == 'missing':
                        raise lifecycle.socket.gaierror('not found')
                    if family == lifecycle.socket.AF_INET6:
                        return [(family, 0, 0, '', ('::1', 0, 0, 0))] if name == 'sub.example.com' and failure == 'ipv6' else []
                    address = '8.8.4.4' if name == 'sub.example.com' and failure == 'wrong_ipv4' else '8.8.8.8'
                    return [(family, 0, 0, '', (address, 0))]
                with patch('main.socket.getaddrinfo', side_effect=resolve) as resolver:
                    if failure:
                        with self.assertRaisesRegex(config.Error, 'sub.example.com'):
                            lifecycle.detect_network(c)
                    else:
                        lifecycle.detect_network(c)
                        self.assertIn('sub.example.com', [call.args[0] for call in resolver.call_args_list])

    def test_state_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            state = config.new_state(config.settings(example_env()))
            config.save_state(state, directory)
            path = Path(directory) / 'state.json'
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), state)
            with patch('config.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    config.atomic_write(path, '{}')
            self.assertEqual(json.loads(path.read_text()), state)
            self.assertEqual(list(Path(directory).iterdir()), [path])


class ObjectAPI:
    def __init__(self):
        self.items = []
        self.created = 0
        self.fail_after_create = False

    def request(self, method, path, body=None):
        if method == 'POST':
            self.created += 1
            result = {**body, 'uuid': 'profile-uuid'}
            self.items.append(result)
            if self.fail_after_create:
                self.fail_after_create = False
                raise config.Error('Lost response after successful creation')
            return result
        if path == '/config-profiles':
            return {'configProfiles': self.items}
        for item in self.items:
            if path.endswith('/' + item['uuid']):
                return item
        raise api.APIError(method, path, 404)


class ResumeTests(unittest.TestCase):
    def test_legacy_state_keeps_original_subscription_urls_and_can_resume(self):
        current = config.settings(example_env())
        legacy = config.new_state(copy.deepcopy(current))
        del legacy['config']['subscription_domain']
        del legacy['config']['reality_min_client_version']
        self.assertEqual(config.subscription_origin(legacy['config']), config.panel_origin(current))
        with tempfile.TemporaryDirectory() as directory:
            config.save_state(legacy, directory)
            upgraded = lifecycle.read_state(directory)
        self.assertEqual(upgraded['config'], current)
        self.assertEqual(upgraded['secrets'], legacy['secrets'])
        self.assertEqual(config.compose_config(upgraded), config.compose_config(legacy))
        self.assertEqual(config.nginx_config(upgraded), config.nginx_config(legacy))

    def test_lost_creation_response_does_not_duplicate_profile(self):
        remote = ObjectAPI()
        remote.fail_after_create = True
        state = {'objects': {}}
        with tempfile.TemporaryDirectory() as directory:
            args = (state, remote, 'profile', '/config-profiles', {'name': 'Proxy Bootstrap', 'config': {'initial': True}})
            with self.assertRaises(config.Error):
                api.ensure_object(*args, list_key='configProfiles', directory=directory)
            api.ensure_object(*args, list_key='configProfiles', directory=directory)
            self.assertEqual(remote.created, 1)
            self.assertEqual(state['objects']['profile'], 'profile-uuid')

    def test_existing_panel_edits_are_preserved_and_deletions_are_not_undone(self):
        remote = ObjectAPI()
        remote.items = [{'uuid': 'profile-uuid', 'name': 'Renamed by admin', 'config': {'edited': True}}]
        state = {'objects': {'profile': 'profile-uuid'}}
        original = copy.deepcopy(remote.items)
        result = api.ensure_object(state, remote, 'profile', '/config-profiles', {'name': 'Old name', 'config': {}})
        self.assertEqual(result['config'], {'edited': True})
        self.assertEqual(remote.items, original)
        remote.items.clear()
        with self.assertRaises(api.APIError):
            api.ensure_object(state, remote, 'profile', '/config-profiles', {'name': 'Old name'})
        self.assertEqual(remote.created, 0)

    def test_finished_bootstrap_does_not_write_to_panel(self):
        class NoCalls:
            def request(self, *args):
                raise AssertionError('Finished bootstrap must not reconcile panel-owned objects')
        api.bootstrap({'bootstrapped': True}, NoCalls())

    def test_aborted_probe_is_cleaned_up_by_saved_name(self):
        calls = []
        class ProbeAPI:
            def request(self, method, path, body=None):
                calls.append((method, path))
                if method == 'GET':
                    return {'id': 42}
        state = {'probe': {'username': 'bootstrap_test_random'}}
        with tempfile.TemporaryDirectory() as directory:
            api.cleanup_probe(state, ProbeAPI(), directory)
        self.assertEqual(calls[-1], ('DELETE', '/users/42'))
        self.assertNotIn('probe', state)


class BackupTests(unittest.TestCase):
    def make_backup(self, directory):
        root = Path(directory)
        state = config.new_state(config.settings(example_env()))
        config.save_state(state, root)
        config.render(state, root)
        for name in ('database.dump', 'certs/fullchain.pem', 'certs/privkey.key',
                     'runtime/main.py', 'runtime/config.py', 'runtime/api.py'):
            config.atomic_write(root / name, 'fixture')
        manifest = {str(p.relative_to(root)): lifecycle.checksum(p) for p in root.rglob('*') if p.is_file()}
        config.atomic_write(root / 'manifest.json', json.dumps(manifest))
        config.atomic_write(root / 'complete', 'proxy-bootstrap-remnawave-v1\n')
        return root, state

    def test_valid_backup_and_corrupted_dump(self):
        with tempfile.TemporaryDirectory() as directory:
            root, state = self.make_backup(directory)
            self.assertEqual(lifecycle.validate_backup(root), state)
            (root / 'database.dump').write_text('truncated dump')
            with self.assertRaisesRegex(config.Error, 'verification failed'):
                lifecycle.validate_backup(root)

    def test_incomplete_backup_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _ = self.make_backup(directory)
            (root / 'complete').unlink()
            with self.assertRaises(config.Error):
                lifecycle.validate_backup(root)

    def test_manifest_cannot_reference_files_outside_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _ = self.make_backup(directory)
            manifest = json.loads((root / 'manifest.json').read_text())
            manifest['../outside'] = 'irrelevant'
            (root / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(config.Error):
                lifecycle.validate_backup(root)


class DispatcherTests(unittest.TestCase):
    def test_mode_detection_does_not_override_standalone_inputs(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / '.env'
            env.write_text('DEPLOYMENT_MODE=standalone\nVLESS_PORT=443\n')
            result = subprocess.run(['bash', '-c',
                'source scripts/deployment-mode.sh; dispatch_deployment healthcheck; printf "%s" "$VLESS_PORT"'],
                cwd=repo, env={**os.environ, 'ENV_FILE': str(env), 'VLESS_PORT': '1443'},
                text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout, '1443')


if __name__ == '__main__':
    unittest.main()
