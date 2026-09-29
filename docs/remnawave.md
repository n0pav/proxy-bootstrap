# Remnawave: panel, VPN node and subscriptions on one VPS

[🇺🇸 English](remnawave.md) · [🇷🇺 Русский](remnawave.ru.md) · [Back to README](../README.md)

[Quick start](#quick-start) · [Architecture](#how-the-installation-works) · [Settings](#rerunning-the-installer-and-managing-settings) · [Diagnostics](#checks-and-diagnostics) · [Backups](#updates-and-backups) · [Uninstall](#uninstall) · [For developers](#developer-checks)

This mode installs Remnawave Panel, a local Remnawave Node running Xray,
PostgreSQL, Valkey and Nginx through Docker Compose. After installation, create
a user in the **VPN Users** squad and copy their personal subscription link.
The subscription includes VLESS Reality and Hysteria2 when both protocols are enabled.

The existing `standalone` mode continues to install sing-box. There is no automatic
migration from a running sing-box installation to Remnawave. The first installation
requires a clean Ubuntu 22.04, 24.04 or 26.04 server, amd64/arm64 architecture,
Python 3 and Docker Engine 28+ with Compose v2. If Docker is missing, the installer
installs the Ubuntu packages `docker.io` and `docker-compose-v2`. If an older Docker
version is installed or Compose is missing, the script reports this before starting
the panel.

For a panel and node on the same server, 4 GB RAM is a reasonable starting point;
actual requirements depend on the number of users and traffic.
[Remnawave requirements](https://docs.rw/install/requirements/).

## Quick start

Run all commands below from the repository directory on the VPS.

1. Create a DNS **A** record for `panel.example.com` pointing to the VPS public IPv4.
   Use DNS-only records without CDN proxying. This mode publishes IPv4 only;
   do not add AAAA records for the panel or Hysteria2 domains.
2. Allow inbound ports in the provider firewall:
   `80/tcp`, `8443/tcp`, `443/tcp` for VLESS and `443/udp` for Hysteria2.
   Keep the existing SSH port accessible.
3. Prepare the configuration and install:

```bash
cp .env.remnawave.example .env
nano .env
sudo bash install.sh
```

Set `PANEL_DOMAIN`, `LETSENCRYPT_EMAIL`, `REALITY_SERVER_NAME` and
`HYSTERIA_MASQUERADE_URL`. Behind cloud-provider NAT, you must set
`SERVER_IPV4` to the public IPv4, not the interface's private address.
For VLESS only, set `ENABLE_HYSTERIA=false`; Hysteria2 settings are then unnecessary.
An empty `HYSTERIA_DOMAIN` reuses the panel domain.

The initial administrator credentials are stored in a file with `0600` permissions:

```bash
sudo cat /opt/proxy-bootstrap/remnawave/credentials.txt
```

Open `https://panel.example.com:8443`, sign in and create a user:
set their expiry date, required limits and the **VPN Users** squad. Copy their
subscription link from the panel and import it into a compatible VPN client.
The client receives subsequent changes when it refreshes the subscription.
The subscription link contains an access secret; do not publish it.
This is a configuration subscription, without billing.

## How the installation works

| Purpose | Published port |
| --- | --- |
| Panel and subscriptions over HTTPS | `8443/tcp` |
| VLESS Reality | `443/tcp` |
| Hysteria2 | `443/udp` |
| Certbot standalone for ACME | `80/tcp` during issuance/renewal |
| Panel API for maintenance | `127.0.0.1:3000`, loopback only |
| Node API, PostgreSQL, Valkey | Not published on the host |

The panel and node communicate over a private port in a Docker bridge network.
The node's internal address in the panel is `node:2222`. Public connection
addresses are configured under Hosts. Bridge networking is used for this
single-server setup; node features that require access to the host's firewall
or network namespace are not configured.

The installer creates the administrator before starting public Nginx, generates
keys, and creates the profile, node, squad and Hosts through the API. The initial
profile blocks proxying to private networks. Each user has their own VPN credentials.

Docker publishes the VPN and HTTPS ports itself, and ordinary UFW rules do not
filter these published ports. Here, `FIREWALL_BACKEND` manages access to host TCP/80
for ACME. Set source-IP restrictions for the panel/users in the provider firewall
or through carefully configured Docker rules. The installer does not reset the
firewall or change SSH.

Hysteria2 masquerade and optional `salamander` obfuscation are supported. `gecko`
is explicitly rejected because subscription compatibility is not guaranteed in
this mode. Single-user settings such as `VLESS_UUID`, `HYSTERIA_PASSWORD` and
predefined keys from a standalone configuration are also rejected; start with
the new `.env` example.

## Rerunning the installer and managing settings

State and secrets are saved in `/opt/proxy-bootstrap/remnawave/state.json`.
Secrets, squad membership and object UUIDs are not recreated on subsequent runs.
Once bootstrap is complete, the script does not overwrite profiles, Hosts or
users managed in the panel. Objects deleted in the panel are not silently recreated.

Rerunning `install.sh` resumes an interrupted installation. Its infrastructure
settings must match the original values. Manage users, limits and routing through
the panel. Changing ports also requires changes to Docker port publishing and
the firewall; automatic port/domain changes are not implemented in this version.
Do not delete `state.json` to try to apply a different `.env`.

## Checks and diagnostics

```bash
sudo bash scripts/healthcheck.sh
sudo bash scripts/remnawave.sh smoke-test
sudo bash scripts/remnawave.sh smoke-test --external
sudo docker compose --project-directory /opt/proxy-bootstrap/remnawave \
  -f /opt/proxy-bootstrap/remnawave/compose.json logs --tail 100 panel node nginx
```

The regular health check verifies the containers, panel HTTPS, completed
administrator registration and a running node/Xray. The smoke test creates
a temporary user with a 15-minute lifetime and a 100 MB limit, retrieves a real
JSON subscription over panel HTTPS, starts a temporary Xray client and makes
an HTTPS request through each enabled protocol. The user is deleted afterwards.
If the process is interrupted, cleanup runs on the next smoke test.

By default, the test connects to the node inside the Docker network while
preserving the keys and TLS settings from the subscription. `--external` uses
the public addresses from the subscription, but requests still originate from
the VPS and depend on NAT loopback support. Testing from an external device is
necessary to confirm that the provider allows access to the ports. Test-client
errors are saved in a private `last-probe.log` in the installation directory.
The temporary user and container are not intended for ongoing use.

`CERTBOT_STAGING=true` issues a test certificate. Only in this mode do panel HTTPS
checks skip certificate trust verification, while the test VPN client pins the
SHA-256 hash of the test certificate. Regular clients do not trust these
certificates; use `false` for a production installation.

## Updates and backups

```bash
sudo bash scripts/remnawave.sh backup
sudo bash update.sh
```

`update.sh` first creates a consistent database `pg_dump` and copies the state,
configuration, certificates and runtime, then applies the versions pinned in
the code and runs checks. It does not select an arbitrary `latest` or overwrite
profiles through the API. To upgrade versions, first update the repository to
a revision with a verified set of images.

Backups are stored in `/var/backups/proxy-bootstrap/remnawave/`. They include
secrets: keep them private and copy them off the VPS. Before restoration, file
SHA-256 hashes and PostgreSQL dump readability are checked. Restoration replaces
the current database and configuration; an additional backup is created before
replacing them.

```bash
sudo bash scripts/remnawave.sh restore \
  /var/backups/proxy-bootstrap/remnawave/BACKUP_NAME
```

The image versions and runtime from the backup are also restored. This matters
when the database schema changes: rolling back only the Docker image is not enough.
This workflow restores the same IP/domain configuration; it does not automatically
migrate to a different address. Certbot renews the certificate if necessary.

Certificate renewal is handled by `certbot.timer`. A deploy hook updates the PEM
files, reloads Nginx and asks the panel to restart the node with the current
certificate. It uses copies of the scripts inside the installation directory;
the original repository checkout is not required.

## Uninstall

```bash
sudo bash uninstall.sh
# Remove all managed data, also creating a backup before deletion:
sudo bash uninstall.sh --purge
```

A regular uninstall stops and removes the containers but preserves the database
volume, configuration and secrets. Reinstalling resumes operation. `--purge` also
removes those data. Backups, Docker and Let's Encrypt certificates are retained.

## Developer checks

```bash
python3 -m unittest discover -s tests -v
for script in install.sh update.sh uninstall.sh scripts/*.sh templates/*.sh.tpl; do
  bash -n "$script" || exit 1
done
python3 tests/integration_remnawave.py
```

The last command runs the actual pinned containers with a test certificate and
ports bound only to loopback. It requires Docker, Compose, OpenSSL, registry
access and internet access. The test refuses to run if a Compose project with
the same name already exists; it removes its own containers and volumes after
completion. It does not install packages, change the host firewall or run certbot.
It also verifies real PostgreSQL dump restoration, preservation of subscription
links and profile edits, and Nginx/node reload when the certificate changes.

Full installation on a clean Ubuntu VPS, Let's Encrypt renewal and connectivity
from an external network are tested separately.
