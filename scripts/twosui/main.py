#!/usr/bin/env python3
"""2S-UI lifecycle: install, verify, back up and restore a single VPS."""

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
import re
import secrets
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from twosui.api import API, bootstrap, cleanup_probe, create_probe, prepare
from twosui.config import (CERT_NAME, Error, IMAGES, STACK, atomic_write, certificate_domains,
                          new_state, origin, render, save_state, settings)

HOOK = Path('/etc/letsencrypt/renewal-hooks/deploy/proxy-bootstrap-2s-ui')
BACKUPS = Path('/var/backups/proxy-bootstrap/2s-ui')


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


def check_os():
    if platform.system() != 'Linux':
        raise Error('2S-UI installation requires Ubuntu 22.04, 24.04 or 26.04.')
    release = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            release[key] = value.strip('"')
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') not in ('22.04', '24.04', '26.04'):
        raise Error('Supported OS: Ubuntu 22.04, 24.04 and 26.04.')
    if platform.machine() not in ('x86_64', 'aarch64', 'arm64'):
        raise Error('2S-UI images support amd64 and arm64.')


def detect_network(c):
    if not c['server_ipv4']:
        route = json.loads(run(['ip', '-j', '-4', 'route', 'get', '1.1.1.1'], capture=True).stdout)
        address = route[0].get('prefsrc', '') if route else ''
        if not address or not ipaddress.IPv4Address(address).is_global:
            raise Error('Set SERVER_IPV4 to the public IPv4 of this VPS (required behind cloud NAT).')
        c['server_ipv4'] = address
    if not ipaddress.IPv4Address(c['server_ipv4']).is_global:
        raise Error('SERVER_IPV4 must be the public IPv4 of the VPS.')
    for name in certificate_domains(c):
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
                run(['ufw', 'allow', '80/tcp', 'comment', 'proxy-bootstrap-2s-ui-acme'])
        return
    # Isolated chain for host ACME access. Never change policy or flush another application's rules.
    chain = 'PB_2S_HTTP'
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
    runtime = str(STACK / 'runtime/twosui/main.py')
    unit = '''[Unit]
Description=Proxy Bootstrap 2S-UI firewall
After=docker.service network-online.target ufw.service
Requires=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 %s firewall
[Install]
WantedBy=multi-user.target
''' % runtime
    atomic_write('/etc/systemd/system/proxy-bootstrap-2s-ui-firewall.service', unit, 0o644)
    atomic_write(HOOK, '#!/bin/sh\nset -eu\n[ "${RENEWED_LINEAGE:-}" = "/etc/letsencrypt/live/' + CERT_NAME + '" ] || exit 0\nexec /usr/bin/python3 ' + shlex.quote(runtime) + ' renew\n', 0o700)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', 'proxy-bootstrap-2s-ui-firewall.service'])
    run(['systemctl', 'enable', '--now', 'certbot.timer'])


def copy_certificates():
    source = Path('/etc/letsencrypt/live') / CERT_NAME
    for src, dst in [('fullchain.pem', 'fullchain.pem'), ('privkey.pem', 'privkey.key')]:
        atomic_write(STACK / 'certs' / dst, (source / src).read_text())


def obtain_certificate(state):
    c = state['config']
    args = ['certbot', 'certonly', '--standalone', '--non-interactive', '--agree-tos', '--no-directory-hooks',
            '--keep-until-expiring', '--cert-name', CERT_NAME, '--email', c['email']]
    for name in certificate_domains(c):
        args += ['-d', name]
    if c['staging']:
        args += ['--test-cert']
    # certbot performs issuance only when needed; standalone owns TCP/80 during that operation.
    run(args)
    copy_certificates()


def compose(*args, **kwargs):
    return run(['docker', 'compose', '--project-directory', STACK, '-f', STACK / 'compose.json', *args], **kwargs)


def read_state(directory=None):
    try:
        state = json.loads((Path(directory or STACK) / 'state.json').read_text())
        if state['schema'] != 1 or state['mode'] != '2s-ui':
            raise Error('Unsupported 2S-UI installation state.')
        return state
    except (OSError, KeyError, ValueError):
        raise Error('2S-UI state is missing or invalid; restore a backup instead of regenerating secrets.') from None


def install_runtime():
    target = STACK / 'runtime/twosui'
    for name in ('__init__.py', 'main.py', 'config.py', 'api.py'):
        source = Path(__file__).parent / name
        if source.resolve() != (target / name).resolve():
            atomic_write(target / name, source.read_text())


def wait_core(state, timeout=90):
    api = API(state)
    deadline = time.monotonic() + timeout
    while True:
        try:
            if api.request('GET', 'status?r=sbd')['sbd']['running']:
                return
        except Error:
            pass
        if time.monotonic() >= deadline:
            raise Error('Embedded sing-box is not running; inspect the 2S-UI logs and configuration.')
        time.sleep(2)


def https(state, path, subscription=False, status_only=False):
    c = state['config']
    host = c['subscription_domain'] if subscription else c['panel_domain']
    args = ['curl', '--silent', '--show-error', '--noproxy', '*', '--max-time', '20',
            '--retry', '3', '--retry-connrefused', '--retry-delay', '1',
            '--resolve', f"{host}:{c['panel_port']}:127.0.0.1"]
    if c['staging']:
        args.append('--insecure')
    if status_only:
        args += ['--output', '/dev/null', '--write-out', '%{http_code}']
    else:
        args.append('--fail')
    # Subscription paths are access secrets: pass the URL over stdin, never argv.
    return run([*args, '--config', '-'], input='url = ' + json.dumps(origin(c, subscription) + path) + '\n', capture=True).stdout


def healthcheck(state):
    if not state['bootstrapped']:
        raise Error('Initial bootstrap is incomplete; rerun install.sh.')
    wait_core(state)
    if https(state, '/app/login', status_only=True) != '200':
        raise Error('Panel HTTPS is not healthy.')
    if state['config']['subscription_domain'] != state['config']['panel_domain']:
        for path in ('/app/', '/app/api/login', '/'):
            if https(state, path, subscription=True, status_only=True) != '404':
                raise Error('The subscription hostname exposes a panel endpoint.')
    log('OK: panel HTTPS and embedded sing-box; subscription hostname is isolated when enabled.')


def start_proxy(state):
    api = API(state)
    api.wait()
    # Recreate so an atomically replaced nginx.conf is remounted, even if its path is unchanged.
    compose('up', '-d', '--force-recreate', 'nginx')
    container = compose('ps', '-q', 'nginx', capture=True).stdout.strip()
    networks = json.loads(run(['docker', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', container], capture=True).stdout)
    address = str(ipaddress.IPv4Address(next(iter(networks.values()))['IPAddress'])) + '/32'
    current = api.request('GET', 'settings')['webTrustedProxies']
    peers = [p for p in re.split(r'[,\s]+', current) if p and p != state.get('trusted_proxy')]
    if address not in peers:
        peers.append(address)
    api.save('settings', 'edit', {'webTrustedProxies': ','.join(peers)})
    state['trusted_proxy'] = address
    save_state(state, STACK)


def smoke_test(state, external=False):
    api = API(state)
    old_container = state.pop('probe_container', None)
    if old_container:
        run(['docker', 'rm', '-f', old_container], capture=True, check=False)
    cleanup_probe(state, api, STACK)
    try:
        name = create_probe(state, api, STACK)
        subscription = json.loads(https(state, '/sub/' + name + '?format=json', subscription=True))
        panel_id = compose('ps', '-q', 'panel', capture=True).stdout.strip()
        network = next(iter(json.loads(run(['docker', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', panel_id], capture=True).stdout)))
        protocols = (['vless'] if state['config']['vless'] else []) + (['hysteria2'] if state['config']['hysteria'] else [])
        run(['docker', 'pull', state['images']['client']], capture=True)
        for protocol in protocols:
            matches = [o for o in subscription['outbounds'] if o['type'] == protocol]
            if len(matches) != 1:
                raise Error(f'Subscription must contain exactly one {protocol} outbound.')
            outbound = copy.deepcopy(matches[0])
            outbound['tag'] = 'proxy'
            if not external:
                outbound['server'] = 'panel'
            if state['config']['staging']:
                # Explicit test mode only; regular installations never bypass VPN TLS validation.
                outbound['tls']['insecure'] = True
            config = {'log': {'level': 'warn'},
                'dns': {'servers': [{'type': 'local', 'tag': 'local'}]},
                'inbounds': [{'type': 'socks', 'listen': '0.0.0.0', 'listen_port': 1080}],
                'outbounds': [outbound], 'route': {'default_domain_resolver': 'local', 'final': 'proxy'}}
            with tempfile.TemporaryDirectory(prefix='probe-', dir=STACK) as directory:
                config_file = Path(directory) / 'client.json'
                atomic_write(config_file, json.dumps(config))
                container = 'proxy-bootstrap-2s-probe-' + secrets.token_hex(6)
                state['probe_container'] = container
                save_state(state, STACK)
                try:
                    run(['docker', 'run', '-d', '--name', container, '--network', network,
                         '-p', '127.0.0.1::1080', '-v', f'{config_file}:/config.json:ro',
                         state['images']['client'], 'run', '-c', '/config.json'], capture=True)
                    address = run(['docker', 'port', container, '1080/tcp'], capture=True).stdout.strip()
                    if not address.startswith('127.0.0.1:') or '\n' in address:
                        raise Error('Unexpected test SOCKS port binding.')
                    command = ['curl', '--fail', '--silent', '--show-error', '--connect-timeout', '8',
                               '--max-time', '15', '--noproxy', '', '--proxy', 'socks5h://' + address]
                    last = None
                    for attempt in range(3):
                        last = run([*command, 'https://www.cloudflare.com/cdn-cgi/trace'], capture=True, check=False)
                        if last.returncode == 0:
                            break
                        time.sleep(1)
                    if last.returncode:
                        diagnostic = run(['docker', 'logs', '--tail', '50', container], capture=True, check=False)
                        atomic_write(STACK / 'last-probe.log', diagnostic.stdout + diagnostic.stderr)
                        raise Error(f'{protocol} traffic check failed; inspect the private last-probe.log.')
                    private = run([*command, '--max-time', '5', 'http://panel:2095/app/'], capture=True, check=False)
                    if private.returncode == 0:
                        raise Error('VPN users can reach the private panel API; restore private-network blocking.')
                    log(f'OK: personal subscription, {protocol} HTTPS traffic and private-network blocking.')
                finally:
                    run(['docker', 'rm', '-f', container], capture=True, check=False)
                    state.pop('probe_container', None)
                    save_state(state, STACK)
    finally:
        cleanup_probe(state, api, STACK)
    log('Server-side checks cannot verify access from the subscriber’s Wi-Fi/mobile network.')


def write_credentials(state):
    c, s = state['config'], state['secrets']
    atomic_write(STACK / 'credentials.txt', f"Panel: {origin(c)}/app/\n"
        f"Initial administrator: {s['admin_username']}\nInitial password: {s['admin_password']}\n"
        f"Subscriptions: {origin(c, True)}/sub/<client-name>\n"
        'Create Clients and select the prepared inbounds. Keep generated random client names private.\n'
        'The initial password may have been changed in the panel.\n')


def install():
    check_os()
    c = settings(os.environ)
    if Path('/opt/proxy-bootstrap/remnawave/state.json').exists() or Path('/etc/sing-box/bootstrap.env').exists():
        raise Error('Another deployment is installed; changing DEPLOYMENT_MODE is not a migration.')
    install_dependencies()
    detect_network(c)
    if (STACK / 'state.json').exists():
        state = read_state()
        if state['config'] != c:
            raise Error('Infrastructure settings differ from saved state. Manage VPN settings in the panel.')
    else:
        if (STACK / 'db/s-ui.db').exists():
            raise Error('An unmanaged 2S-UI database exists; restore its installation state first.')
        check_ports(c)
        state = new_state(c)
        save_state(state, STACK)
    render(state, STACK)
    install_runtime()
    firewall(state)
    obtain_certificate(state)
    install_units(state)
    compose('pull')
    # Nginx stays stopped until the default administrator is replaced and bootstrap succeeds.
    compose('up', '-d', '--wait', '--wait-timeout', '180', 'panel')
    api = API(state)
    prepare(state, api, STACK)
    bootstrap(state, api, STACK)
    compose('restart', 'panel')
    wait_core(state)
    start_proxy(state)
    state.pop('uninstalled', None)
    save_state(state, STACK)
    write_credentials(state)
    healthcheck(state)
    smoke_test(state)
    log(f'2S-UI is ready. Credentials: {STACK / "credentials.txt"}. Create clients in the panel.')


def backup(state):
    destination = BACKUPS / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    destination.mkdir(parents=True, mode=0o700)
    for name in ('state.json', 'compose.json', 'nginx.conf', 'credentials.txt', 'certs', 'runtime'):
        source = STACK / name
        if source.is_dir():
            shutil.copytree(source, destination / name)
        elif source.is_file():
            shutil.copy2(source, destination / name)
    database = STACK / 'db/s-ui.db'
    if database.exists():
        (destination / 'db').mkdir(mode=0o700)
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(destination / 'db/s-ui.db') as target:
                source.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                    raise Error('SQLite backup integrity check failed.')
    manifest = {str(p.relative_to(destination)): checksum(p) for p in destination.rglob('*') if p.is_file()}
    atomic_write(destination / 'manifest.json', json.dumps(manifest, indent=2))
    log(f'Backup saved: {destination}')
    return destination


def verify_backup(directory):
    directory = Path(directory).resolve()
    try:
        manifest = json.loads((directory / 'manifest.json').read_text())
        required = {'state.json', 'compose.json', 'nginx.conf', 'db/s-ui.db',
                    'certs/fullchain.pem', 'certs/privkey.key'}
        if not required.issubset(manifest):
            raise Error('Backup is incomplete; required files are missing.')
        for name, expected in manifest.items():
            path = directory / name
            if path.is_symlink() or not path.resolve().is_relative_to(directory) or checksum(path) != expected:
                raise Error('Backup paths or checksums are invalid.')
        state = read_state(directory)
        with sqlite3.connect((directory / 'db/s-ui.db').as_uri() + '?mode=ro', uri=True) as db:
            if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise Error('Backup SQLite database is invalid.')
        return directory, manifest, state
    except (OSError, ValueError, sqlite3.Error):
        raise Error('Cannot read or validate this backup.') from None


def restore(directory):
    directory, manifest, state = verify_backup(directory)
    if (STACK / 'state.json').exists():
        backup(read_state())
        compose('down')
    elif Path('/opt/proxy-bootstrap/remnawave/state.json').exists() or Path('/etc/sing-box/bootstrap.env').exists():
        raise Error('A different deployment is installed; restore does not migrate it.')
    STACK.mkdir(parents=True, exist_ok=True, mode=0o700)
    for suffix in ('-wal', '-shm'):
        (STACK / ('db/s-ui.db' + suffix)).unlink(missing_ok=True)
    for name in manifest:
        target = STACK / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(directory / name, target)
    state.pop('probe_container', None)
    state.pop('uninstalled', None)
    save_state(state, STACK)
    firewall(state)
    obtain_certificate(state)
    install_units(state)
    compose('up', '-d', '--wait', '--wait-timeout', '180', 'panel')
    start_proxy(state)
    cleanup_probe(state, API(state), STACK)
    healthcheck(state)
    log('2S-UI database, users, certificates and pinned images restored.')


def update(state):
    if not state['bootstrapped']:
        raise Error('Finish initial installation before updating.')
    backup(state)
    state['images'] = dict(IMAGES)
    save_state(state, STACK)
    install_runtime()
    render(state, STACK)
    compose('pull')
    compose('up', '-d', '--wait', '--wait-timeout', '180', 'panel')
    start_proxy(state)
    install_units(state)
    healthcheck(state)
    smoke_test(state)
    log('2S-UI update verified; panel-managed users and configuration preserved.')


def renew(state):
    copy_certificates()
    if state['bootstrapped'] and not state.get('uninstalled'):
        compose('exec', '-T', 'nginx', 'nginx', '-s', 'reload')
        api = API(state)
        for tls in api.items('tls'):
            if (tls['id'] == state['objects'].get('hysteria_tls') and
                    tls['server'].get('certificate_path') == '/app/cert/fullchain.pem'):
                # 2S-UI recalculates certificate pins and subscription links when TLS is saved.
                # Preserve any panel edits; a replaced/custom certificate remains user-managed.
                api.save('tls', 'edit', tls)
        api.request('POST', 'restartSb', {})
        wait_core(state)


def uninstall(state, purge=False):
    backup(state)
    if (STACK / 'compose.json').exists():
        compose('down')
    firewall(state, remove=True)
    unit = '/etc/systemd/system/proxy-bootstrap-2s-ui-firewall.service'
    run(['systemctl', 'disable', '--now', 'proxy-bootstrap-2s-ui-firewall.service'], check=False)
    Path(unit).unlink(missing_ok=True)
    HOOK.unlink(missing_ok=True)
    run(['systemctl', 'daemon-reload'])
    if purge:
        shutil.rmtree(STACK)
        log('2S-UI removed with its data. Backups and Let’s Encrypt certificates retained.')
    else:
        state['uninstalled'] = True
        save_state(state, STACK)
        log('Containers removed. Database and configuration retained; rerun install.sh to resume.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'update', 'healthcheck', 'smoke-test', 'backup',
                                          'restore', 'uninstall', 'renew', 'firewall'])
    parser.add_argument('backup_directory', nargs='?')
    parser.add_argument('--purge', action='store_true')
    parser.add_argument('--external', action='store_true')
    args = parser.parse_args()
    if bool(args.backup_directory) != (args.action == 'restore'):
        raise Error('Only restore requires a backup directory argument.')
    if args.purge and args.action != 'uninstall' or args.external and args.action != 'smoke-test':
        raise Error('--purge is for uninstall; --external is for smoke-test.')
    if os.geteuid() != 0:
        raise Error('Run with sudo on the target Ubuntu VPS.')
    os.umask(0o077)
    with open('/run/lock/proxy-bootstrap-2s-ui.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if args.action == 'renew' else fcntl.LOCK_NB))
        except BlockingIOError:
            raise Error('Another 2S-UI lifecycle operation is running.') from None
        if args.action == 'install':
            install()
        elif args.action == 'restore':
            restore(args.backup_directory)
        else:
            state = read_state()
            if args.action == 'uninstall':
                uninstall(state, args.purge)
            elif args.action == 'smoke-test':
                healthcheck(state)
                smoke_test(state, args.external)
            else:
                {'update': update, 'healthcheck': healthcheck, 'backup': backup,
                 'renew': renew, 'firewall': firewall}[args.action](state)


if __name__ == '__main__':
    try:
        main()
    except (Error, OSError, subprocess.TimeoutExpired, sqlite3.Error) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Interrupted. Saved state is retained; rerun to continue.', file=sys.stderr)
        sys.exit(130)
