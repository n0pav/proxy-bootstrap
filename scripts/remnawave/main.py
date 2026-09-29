#!/usr/bin/env python3
"""Single-VPS Remnawave lifecycle. Run through scripts/remnawave.sh as root."""

import argparse
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time

from api import API, bootstrap, cleanup_probe, create_probe, prepare, wait_node
from config import (CERT_NAME, Error, IMAGES, STACK, atomic_write, new_state,
                    panel_origin, render, save_state, settings)


HOOK = Path('/etc/letsencrypt/renewal-hooks/deploy/proxy-bootstrap-remnawave')
BACKUPS = Path('/var/backups/proxy-bootstrap/remnawave')


def log(message):
    print(message, flush=True)


def run(args, *, capture=False, input=None, check=True, timeout=600):
    result = subprocess.run([str(arg) for arg in args], input=input, text=True,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None,
                            timeout=timeout, check=False)
    if check and result.returncode:
        # Do not echo arguments/output: stdin, paths and generated config may contain secrets.
        raise Error(f'{Path(args[0]).name} failed with exit code {result.returncode}.')
    return result


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def compose(*args, **kwargs):
    return run(['docker', 'compose', '--project-directory', STACK, '-f', STACK / 'compose.json', *args], **kwargs)


def read_state(directory=None):
    directory = STACK if directory is None else directory
    try:
        state = json.loads((Path(directory) / 'state.json').read_text())
        if state['schema'] != 1:
            raise Error('Unsupported installation state version.')
        return state
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise Error('Installation state is missing or invalid. Restore it from backup; do not regenerate secrets.') from exc


def check_os():
    if platform.system() != 'Linux':
        raise Error('Remnawave installation requires Ubuntu 22.04, 24.04 or 26.04.')
    release = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            release[key] = value.strip('"')
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') not in ('22.04', '24.04', '26.04'):
        raise Error('Supported OS: Ubuntu 22.04, 24.04 and 26.04.')
    if platform.machine() not in ('x86_64', 'aarch64', 'arm64'):
        raise Error('Remnawave images support amd64 and arm64.')


def detect_network(c):
    if not c['server_ipv4']:
        route = json.loads(run(['ip', '-j', '-4', 'route', 'get', '1.1.1.1'], capture=True).stdout)
        address = route[0].get('prefsrc', '') if route else ''
        if not address or not ipaddress.IPv4Address(address).is_global:
            raise Error('Set SERVER_IPV4 to the public IPv4 of this VPS (required behind cloud NAT).')
        c['server_ipv4'] = address
    if not ipaddress.IPv4Address(c['server_ipv4']).is_global:
        raise Error('SERVER_IPV4 must be the public IPv4 of the VPS.')
    domains = {c['panel_domain']}
    if c['hysteria']:
        domains.add(c['hysteria_domain'])
    for name in domains:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(name, None, socket.AF_INET)}
        except socket.gaierror as exc:
            raise Error(f'{name} does not resolve. Create its DNS A record first.') from exc
        if c['server_ipv4'] not in addresses:
            raise Error(f'{name} must resolve directly to SERVER_IPV4; disable CDN proxying.')
        try:
            ipv6 = socket.getaddrinfo(name, None, socket.AF_INET6)
        except socket.gaierror:
            ipv6 = []
        if ipv6:
            raise Error(f'{name} has an AAAA record. Remove it: this deployment publishes IPv4 only.')


def check_ports(c):
    ports = [(c['panel_port'], socket.SOCK_STREAM), (c['api_port'], socket.SOCK_STREAM)]
    if c['vless']:
        ports.append((c['vless_port'], socket.SOCK_STREAM))
    if c['hysteria']:
        ports.append((c['hysteria_port'], socket.SOCK_DGRAM))
    for port, kind in ports:
        with socket.socket(socket.AF_INET, kind) as sock:
            try:
                sock.bind(('0.0.0.0', port))
            except OSError as exc:
                protocol = 'TCP' if kind == socket.SOCK_STREAM else 'UDP'
                raise Error(f'Port {port}/{protocol} is occupied. Existing VPN installations are not migrated automatically.') from exc


def install_dependencies():
    os.environ['DEBIAN_FRONTEND'] = 'noninteractive'
    run(['apt-get', 'update'])
    run(['apt-get', 'install', '-y', 'ca-certificates', 'curl', 'openssl', 'certbot', 'iproute2', 'iptables'])
    if not shutil.which('docker'):
        run(['apt-get', 'install', '-y', 'docker.io', 'docker-compose-v2'])
    elif run(['docker', 'compose', 'version'], capture=True, check=False).returncode:
        raise Error('Docker is installed without Compose v2. Install the Compose plugin for your Docker distribution and rerun.')
    run(['systemctl', 'enable', '--now', 'docker'])
    run(['docker', 'info'], capture=True)
    # A recent Engine is required for loopback-only published ports to remain private.
    version = run(['docker', 'version', '--format', '{{.Server.Version}}'], capture=True).stdout.strip()
    if int(version.split('.')[0]) < 28:
        raise Error('Docker Engine 28+ is required. Upgrade Docker and rerun the installer.')


def install_runtime():
    target = STACK / 'runtime'
    target.mkdir(exist_ok=True, mode=0o700)
    for name in ('main.py', 'config.py', 'api.py'):
        source = Path(__file__).parent / name
        if source.resolve() != (target / name).resolve():
            atomic_write(target / name, source.read_text())


def firewall(state, remove=False):
    c = state['config']
    selected = c['firewall']
    ufw_active = shutil.which('ufw') and 'Status: active' in run(['ufw', 'status'], capture=True).stdout
    if selected == 'none':
        return
    if selected == 'auto':
        selected = 'ufw' if ufw_active else 'iptables'
    if selected == 'ufw':
        if not shutil.which('ufw'):
            if remove:
                return
            raise Error('FIREWALL_BACKEND=ufw requires UFW to be installed.')
        if remove:
            for rule in state.get('ufw_rules', []):
                run(['ufw', '--force', 'delete', 'allow', rule], check=False)
        else:
            # Only ACME runs in the host network. Docker owns the published VPN/HTTPS ports.
            added = run(['ufw', 'show', 'added'], capture=True).stdout
            if 'ufw allow 80/tcp' not in added:
                state.setdefault('ufw_rules', [])
                if '80/tcp' not in state['ufw_rules']:
                    state['ufw_rules'].append('80/tcp')
                    save_state(state, STACK)
                run(['ufw', 'allow', '80/tcp', 'comment', 'proxy-bootstrap-remnawave-acme'])
        return
    # Isolated chain for host ACME access. Never change policy or flush another application's rules.
    chain = 'PB_REMNA_HTTP'
    if remove and not shutil.which('iptables'):
        return
    hook = ['iptables', '-w', '-C', 'INPUT', '-j', chain]
    if remove:
        if run(hook, capture=True, check=False).returncode == 0:
            run(['iptables', '-w', '-D', 'INPUT', '-j', chain])
        run(['iptables', '-w', '-F', chain], capture=True, check=False)
        run(['iptables', '-w', '-X', chain], capture=True, check=False)
    else:
        run(['iptables', '-w', '-N', chain], capture=True, check=False)
        rule = ['-p', 'tcp', '--dport', '80', '-j', 'ACCEPT']
        if run(['iptables', '-w', '-C', chain, *rule], capture=True, check=False).returncode:
            run(['iptables', '-w', '-A', chain, *rule])
        if run(hook, capture=True, check=False).returncode:
            run(['iptables', '-w', '-I', 'INPUT', '1', '-j', chain])


def install_units(state):
    runtime = str(STACK / 'runtime/main.py')
    unit = '''[Unit]
Description=Proxy Bootstrap Remnawave firewall
After=docker.service network-online.target ufw.service
Requires=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 %s firewall
[Install]
WantedBy=multi-user.target
''' % runtime
    atomic_write('/etc/systemd/system/proxy-bootstrap-remnawave-firewall.service', unit, 0o644)
    atomic_write(HOOK, '#!/bin/sh\nset -eu\n[ "${RENEWED_LINEAGE:-}" = "/etc/letsencrypt/live/' + CERT_NAME + '" ] || exit 0\nexec /usr/bin/python3 ' + shlex.quote(runtime) + ' renew\n', 0o700)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', 'proxy-bootstrap-remnawave-firewall.service'])
    run(['systemctl', 'enable', '--now', 'certbot.timer'])


def copy_certificates():
    source = Path('/etc/letsencrypt/live') / CERT_NAME
    for src, dst in [('fullchain.pem', 'fullchain.pem'), ('privkey.pem', 'privkey.key')]:
        atomic_write(STACK / 'certs' / dst, (source / src).read_text())


def obtain_certificate(state):
    c = state['config']
    args = ['certbot', 'certonly', '--standalone', '--non-interactive', '--agree-tos', '--no-directory-hooks',
            '--keep-until-expiring', '--cert-name', CERT_NAME, '--email', c['email'], '-d', c['panel_domain']]
    if c['hysteria'] and c['hysteria_domain'] != c['panel_domain']:
        args += ['-d', c['hysteria_domain']]
    if c['staging']:
        args += ['--test-cert']
    # certbot performs issuance only when needed; standalone owns TCP/80 during that operation.
    run(args)
    copy_certificates()


def healthcheck(state, wait=True):
    api = API(state)
    api.wait_ready(timeout=180 if wait else 1)
    wait_node(state, api, timeout=120 if wait else 1)
    result = compose('ps', '--format', 'json', capture=True).stdout.strip()
    containers = json.loads(result) if result.startswith('[') else [json.loads(line) for line in result.splitlines() if line]
    running = {item['Service'] for item in containers if item['State'] == 'running'}
    if not {'db', 'redis', 'panel', 'node', 'nginx'} <= running:
        raise Error('One or more Remnawave containers are not running.')
    c = state['config']
    command = ['curl', '--fail', '--silent', '--show-error', '--noproxy', '*', '--max-time', '20',
               '--retry', '5', '--retry-connrefused', '--retry-delay', '1',
               '--resolve', f"{c['panel_domain']}:{c['panel_port']}:127.0.0.1",
               panel_origin(c) + '/api/auth/status']
    if c['staging']:
        command.insert(1, '--insecure')
        log('Staging certificate: HTTPS trust verification is skipped for this test installation.')
    response = json.loads(run(command, capture=True).stdout)['response']
    if response['isRegisterAllowed']:
        raise Error('Administrator registration is still open; installation is incomplete.')
    log('OK: panel HTTPS, administrator registration, containers and local Node/Xray.')


def smoke_test(state, external=False, check_private_routes=False):
    """Use the real per-user subscription, then send HTTPS traffic through every enabled protocol."""
    api = API(state)
    user = None
    container_name = 'proxy-bootstrap-probe-' + str(os.getpid())
    try:
        user = create_probe(state, api, STACK)
        c = state['config']
        # Fetch using curl config on stdin, so the secret URL is absent from process arguments.
        expected = panel_origin(c) + '/api/sub/' + user['shortUuid']
        if user.get('subscriptionUrl') != expected:
            raise Error('The panel generated an unexpected subscription URL. Check SUB_PUBLIC_DOMAIN.')
        url = user['subscriptionUrl'] + '/json'
        curl_config = 'url = ' + json.dumps(url) + '\n'
        cmd = ['curl', '--fail', '--silent', '--show-error', '--noproxy', '*', '--max-time', '30',
               '--resolve', f"{c['panel_domain']}:{c['panel_port']}:127.0.0.1", '--config', '-']
        if c['staging']:
            cmd.insert(1, '--insecure')
        configs = json.loads(run(cmd, input=curl_config, capture=True).stdout)
        if isinstance(configs, dict):
            configs = [configs]
        outbounds = [out for config in configs for out in config.get('outbounds', [])]
        protocols = (['vless'] if c['vless'] else []) + (['hysteria'] if c['hysteria'] else [])
        for protocol in protocols:
            choices = [out for out in outbounds if out.get('protocol') == protocol]
            if not choices:
                raise Error(f'The user subscription is missing {protocol}.')
            outbound = copy.deepcopy(choices[0])
            outbound['tag'] = 'PROXY'
            if not external:
                if protocol == 'vless':
                    outbound['settings']['vnext'][0]['address'] = 'node'
                else:
                    outbound['settings']['address'] = 'node'
            if c['staging'] and protocol == 'hysteria':
                pem = (STACK / 'certs/fullchain.pem').read_text().split('-----END CERTIFICATE-----')[0] + '-----END CERTIFICATE-----\n'
                fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
                outbound['streamSettings']['tlsSettings']['pinnedPeerCertSha256'] = fingerprint
            client = {'log': {'loglevel': 'warning'}, 'inbounds': [{
                'tag': 'SOCKS', 'listen': '0.0.0.0', 'port': 1080, 'protocol': 'socks',
                'settings': {'auth': 'noauth', 'udp': True},
            }], 'outbounds': [outbound]}
            with tempfile.TemporaryDirectory(prefix='probe-', dir=STACK) as temp:
                path = Path(temp) / 'client.json'
                atomic_write(path, json.dumps(client))
                run(['docker', 'run', '--detach', '--name', container_name,
                     '--network', 'proxy-bootstrap-remnawave_default', '-p', '127.0.0.1::1080',
                     '-v', str(path) + ':/client.json:ro', '--entrypoint', '/usr/local/bin/xray',
                     state['images']['node'], 'run', '-c', '/client.json'], capture=True)
                try:
                    port = run(['docker', 'port', container_name, '1080/tcp'], capture=True).stdout.strip().rsplit(':', 1)[1]
                    for attempt in range(12):
                        probe = run(['curl', '--fail', '--silent', '--show-error', '--max-time', '15',
                                     '--proxy', 'socks5h://127.0.0.1:' + port, '--noproxy', '',
                                     'https://www.cloudflare.com/cdn-cgi/trace'], capture=True, check=False, timeout=20)
                        if probe.returncode == 0 and 'ip=' in probe.stdout:
                            break
                        time.sleep(2)
                    else:
                        raise Error(f'Traffic test failed for {protocol}; inspect Node/Xray and client compatibility.')
                    if check_private_routes:
                        private_probe = run(['curl', '--silent', '--max-time', '5',
                                             '--proxy', 'socks5h://127.0.0.1:' + port, '--noproxy', '',
                                             'http://panel:3000/api/auth/status'], capture=True, check=False)
                        # No --fail: even an HTTP 403 would mean the private endpoint was reachable.
                        if private_probe.returncode == 0:
                            raise Error('The generated VPN profile allowed access to the private Docker network.')
                    log(f'OK: personal subscription and HTTPS traffic through {protocol}.')
                except Exception:
                    logs = run(['docker', 'logs', container_name], capture=True, check=False)
                    atomic_write(STACK / 'last-probe.log', logs.stdout + logs.stderr)
                    raise Error(f'{protocol} probe failed. Private diagnostic log: {STACK / "last-probe.log"}') from None
                finally:
                    run(['docker', 'rm', '-f', container_name], capture=True, check=False)
        state['last_smoke_test'] = datetime.now(timezone.utc).isoformat()
        save_state(state, STACK)
        log('This server-side test does not verify the provider firewall from a remote network.')
    finally:
        cleanup_probe(state, api, STACK)


def backup(state):
    destination = BACKUPS / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    destination.mkdir(parents=True, mode=0o700)
    dump = destination / 'database.dump'
    try:
        with dump.open('wb') as output:
            os.chmod(dump, 0o600)
            process = subprocess.run(['docker', 'compose', '--project-directory', str(STACK), '-f',
                                      str(STACK / 'compose.json'), 'exec', '-T', 'db',
                                      'pg_dump', '-U', 'postgres', '-d', 'postgres', '-Fc'],
                                     stdout=output, stderr=subprocess.PIPE, timeout=600)
            if process.returncode:
                raise Error('Database backup failed; update/restore has not started.')
        for name in ('state.json', 'compose.json', 'nginx.conf'):
            shutil.copy2(STACK / name, destination / name)
        shutil.copytree(STACK / 'certs', destination / 'certs')
        shutil.copytree(STACK / 'runtime', destination / 'runtime')
        manifest = {str(path.relative_to(destination)): checksum(path)
                    for path in destination.rglob('*') if path.is_file()}
        atomic_write(destination / 'manifest.json', json.dumps(manifest, indent=2) + '\n')
        # This file is written last; an interrupted dump is never accepted for restoration.
        atomic_write(destination / 'complete', 'proxy-bootstrap-remnawave-v1\n')
    except Exception:
        log(f'Incomplete backup retained for diagnosis: {destination}')
        raise
    log(f'Backup saved: {destination}')
    return destination


def install():
    check_os()
    if Path('/etc/sing-box/bootstrap.env').exists():
        raise Error('A standalone installation exists. Use a clean VPS; automatic migration is not supported.')
    if (STACK / 'state.json').exists():
        state = read_state()
        requested = settings(os.environ)
        if not requested['server_ipv4']:
            requested['server_ipv4'] = state['config']['server_ipv4']
        if requested != state['config']:
            raise Error('Installation settings changed. Manage VPN settings in the panel; restore the original .env to resume setup.')
        if state.get('uninstalled'):
            del state['uninstalled']
            save_state(state, STACK)
    else:
        c = settings(os.environ)
        detect_network(c)
        check_ports(c)
        if STACK.exists() and any(STACK.iterdir()):
            raise Error('Installation directory already contains files but no state; restore state or choose a clean VPS.')
        STACK.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(STACK, 0o700)
        state = new_state(c)
        save_state(state, STACK)
    install_dependencies()
    if not state.get('resources_owned'):
        owned_filter = 'label=com.docker.compose.project=proxy-bootstrap-remnawave'
        containers = run(['docker', 'ps', '-aq', '--filter', owned_filter], capture=True).stdout.strip()
        volumes = run(['docker', 'volume', 'ls', '-q', '--filter', owned_filter], capture=True).stdout.strip()
        if containers or volumes:
            raise Error('A Docker project with this name already exists without bootstrap ownership. Restore its state instead of adopting it.')
        state['resources_owned'] = True
        save_state(state, STACK)
    install_runtime()
    firewall(state)
    # Register the hook after initial issuance to avoid calling a not-yet-started panel.
    obtain_certificate(state)
    install_units(state)
    render(state, STACK)
    compose('config', '--quiet', capture=True)
    log('Starting PostgreSQL, Valkey and the panel on loopback.')
    compose('up', '-d', '--wait', '--wait-timeout', '240', 'db', 'redis', 'panel')
    api = API(state)
    prepare(state, api, STACK)
    write_credentials(state)
    render(state, STACK)
    compose('up', '-d', 'node')
    bootstrap(state, api, STACK)
    wait_node(state, api)
    compose('up', '-d', 'nginx')
    healthcheck(state)
    smoke_test(state)
    write_credentials(state)
    log('Installation complete. Create users in the VPN Users squad and copy their personal subscription links.')


def write_credentials(state):
    s = state['secrets']
    text = (f"Panel: {panel_origin(state['config'])}\nInitial administrator: {s['admin_username']}\n"
            f"Initial password: {s['admin_password']}\nUser squad: {state.get('squad_name', 'VPN Users')}\n"
            'Create a user in this squad, then copy the personal subscription URL from the panel.\n'
            'The initial password may have been changed in the panel.\n')
    atomic_write(STACK / 'credentials.txt', text)
    log(f"Panel: {panel_origin(state['config'])}")
    log(f'Initial administrator credentials: {STACK / "credentials.txt"} (root only).')


def update(state):
    if not state['bootstrapped']:
        raise Error('Setup is incomplete. Rerun install.sh before updating.')
    if state['images']['db'].split(':')[-1].split('.')[0] != IMAGES['db'].split(':')[-1].split('.')[0]:
        raise Error('PostgreSQL major-version changes require an explicit database migration.')
    destination = backup(state)
    state['images'] = dict(IMAGES)
    save_state(state, STACK)
    install_runtime()
    render(state, STACK)
    compose('config', '--quiet', capture=True)
    try:
        compose('pull')
        compose('up', '-d', '--wait', '--wait-timeout', '240', 'db', 'redis', 'panel')
        API(state).wait_ready()
        compose('up', '-d', 'node', 'nginx')
        healthcheck(state)
        smoke_test(state)
    except Exception:
        log(f'Update failed. Backup: {destination}. Database migrations require an explicit restore, not an image-only rollback.')
        raise
    log('Update verified. Users, subscriptions and panel-managed profiles were preserved.')


def validate_backup(source):
    source = Path(source).resolve()
    if not (source / 'complete').is_file() or not (source / 'database.dump').is_file():
        raise Error('Expected a completed backup directory produced by this tool.')
    state = read_state(source)
    try:
        manifest = json.loads((source / 'manifest.json').read_text())
    except (OSError, ValueError) as exc:
        raise Error('Backup checksum manifest is missing or invalid.') from exc
    required = {'database.dump', 'state.json', 'compose.json', 'nginx.conf',
                'certs/fullchain.pem', 'certs/privkey.key', 'runtime/main.py', 'runtime/config.py', 'runtime/api.py'}
    if not required <= manifest.keys():
        raise Error('Backup manifest is incomplete.')
    for name, expected in manifest.items():
        path = source / name
        if not path.resolve().is_relative_to(source) or not path.is_file() or checksum(path) != expected:
            raise Error('Backup verification failed; the existing installation has not been changed.')
    # Backups are trusted administrator-controlled files, but must never overwrite unrelated paths.
    for name in ('state.json', 'compose.json', 'nginx.conf', 'certs', 'runtime'):
        path = source / name
        if not path.exists() or path.is_symlink() or any(p.is_symlink() for p in path.rglob('*')):
            raise Error('Backup files are incomplete or contain symbolic links.')
    return state


def restore(source):
    source = Path(source).resolve()
    state = validate_backup(source)
    if Path('/etc/sing-box/bootstrap.env').exists():
        raise Error('Restore Remnawave onto its original host or a clean VPS, not over an existing standalone installation.')
    if not (STACK / 'state.json').exists():
        check_os()
        install_dependencies()
    # Validate the dump before removing any volume. Pin the PostgreSQL image from the backup.
    with (source / 'database.dump').open('rb') as dump:
        validation = subprocess.run(['docker', 'run', '--rm', '-i', '--entrypoint', 'pg_restore',
                                     state['images']['db'], '--list'], stdin=dump,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=600)
        if validation.returncode:
            raise Error('The database dump is invalid; the existing installation has not been changed.')
    if (STACK / 'state.json').exists():
        backup(read_state())
        compose('down', '--volumes')
    else:
        STACK.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ('state.json', 'compose.json', 'nginx.conf'):
        atomic_write(STACK / name, (source / name).read_text())
    for name in ('certs', 'runtime'):
        shutil.copytree(source / name, STACK / name, dirs_exist_ok=True)
    state.pop('uninstalled', None)
    state['resources_owned'] = True
    save_state(state, STACK)
    compose('up', '-d', '--wait', '--wait-timeout', '180', 'db', 'redis')
    with (source / 'database.dump').open('rb') as dump:
        result = subprocess.run(['docker', 'compose', '--project-directory', str(STACK), '-f',
                                 str(STACK / 'compose.json'), 'exec', '-T', 'db', 'pg_restore',
                                 '-U', 'postgres', '-d', 'postgres', '--no-owner', '--exit-on-error'],
                                stdin=dump, timeout=600)
        if result.returncode:
            raise Error('Database restoration failed. The original installation was backed up before replacement.')
    firewall(state)
    # Recreate certbot's managed lineage on a different server; the backup includes current PEMs.
    obtain_certificate(state)
    install_units(state)
    compose('up', '-d', '--wait', '--wait-timeout', '240', 'panel')
    compose('up', '-d', 'node', 'nginx')
    healthcheck(state)
    write_credentials(state)


def renew(state):
    copy_certificates()
    if state['bootstrapped'] and not state.get('uninstalled'):
        compose('exec', '-T', 'nginx', 'nginx', '-s', 'reload')
        API(state).request('POST', '/nodes/' + state['objects']['node'] + '/actions/restart', {'forceRestart': True})
        wait_node(state, API(state))


def uninstall(state, purge=False):
    has_compose = (STACK / 'compose.json').exists()
    if has_compose:
        # Normal uninstall retains the volume. A later --purge must also work when
        # all containers have already been removed by that first uninstall.
        if not shutil.which('docker'):
            raise Error('Start Docker before removing an existing deployment.')
        run(['docker', 'info'], capture=True)
        has_database = run(['docker', 'volume', 'inspect', 'proxy-bootstrap-remnawave_db-data'], capture=True, check=False).returncode == 0
    else:
        has_database = False
    if has_database:
        compose('up', '-d', '--wait', '--wait-timeout', '180', 'db')
        backup(state)
    else:
        # A failure before database initialization must still be removable.
        destination = BACKUPS / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-incomplete')
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copytree(STACK, destination)
        log(f'No database volume exists. Incomplete installation files saved: {destination}')
    if Path('/etc/systemd/system/proxy-bootstrap-remnawave-firewall.service').exists():
        run(['systemctl', 'disable', '--now', 'proxy-bootstrap-remnawave-firewall.service'], check=False)
    firewall(state, remove=True)
    if has_compose:
        compose('down', *(['--volumes'] if purge else []))
    HOOK.unlink(missing_ok=True)
    Path('/etc/systemd/system/proxy-bootstrap-remnawave-firewall.service').unlink(missing_ok=True)
    run(['systemctl', 'daemon-reload'])
    if purge:
        shutil.rmtree(STACK)
        log('Managed containers, data and runtime removed. Backups and Let’s Encrypt certificates were retained.')
    else:
        state['uninstalled'] = True
        save_state(state, STACK)
        log('Containers removed; database volume, secrets and configuration retained. Rerun install.sh to resume.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'update', 'healthcheck', 'smoke-test', 'backup', 'restore', 'uninstall', 'renew', 'firewall', 'firewall-remove'])
    parser.add_argument('backup_directory', nargs='?')
    parser.add_argument('--purge', action='store_true', help='Remove managed data after making a backup (uninstall only).')
    parser.add_argument('--external', action='store_true', help='Probe public VPN addresses instead of Docker DNS (smoke-test only).')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise Error('Run with sudo. This command manages a VPS installation.')
    if args.backup_directory and args.action != 'restore':
        raise Error('A backup directory is only accepted by restore.')
    if args.action == 'restore' and not args.backup_directory:
        raise Error('Usage: remnawave.sh restore /var/backups/proxy-bootstrap/remnawave/BACKUP')
    if args.purge and args.action != 'uninstall':
        raise Error('--purge is only accepted by uninstall.')
    if args.external and args.action != 'smoke-test':
        raise Error('--external is only accepted by smoke-test.')
    os.umask(0o077)
    # The lock lives outside STACK so removing the installation never removes an active lock.
    with open('/run/lock/proxy-bootstrap-remnawave.lock', 'w') as lock:
        try:
            # A certbot deploy hook must eventually apply renewed certificates even when
            # an update is running. Installer certbot calls disable directory hooks.
            flags = fcntl.LOCK_EX if args.action == 'renew' else fcntl.LOCK_EX | fcntl.LOCK_NB
            fcntl.flock(lock, flags)
        except BlockingIOError as exc:
            raise Error('Another Remnawave lifecycle operation is running. Try again after it completes.') from exc
        if args.action == 'install':
            install()
            return
        if args.action == 'restore':
            restore(args.backup_directory)
            return
        state = read_state()
        if args.action == 'update':
            update(state)
        elif args.action == 'healthcheck':
            healthcheck(state)
        elif args.action == 'smoke-test':
            healthcheck(state)
            smoke_test(state, args.external)
        elif args.action == 'backup':
            backup(state)
        elif args.action == 'uninstall':
            uninstall(state, args.purge)
        elif args.action in ('firewall', 'firewall-remove'):
            firewall(state, remove=args.action == 'firewall-remove')
        elif args.action == 'renew':
            renew(state)


if __name__ == '__main__':
    try:
        main()
    except (Error, subprocess.TimeoutExpired, OSError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Interrupted. Saved state is retained; rerun the command to continue.', file=sys.stderr)
        sys.exit(130)
