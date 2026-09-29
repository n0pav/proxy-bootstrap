# 2S-UI: a sing-box panel with personal subscriptions

[🇺🇸 English](2s-ui.md) · [🇷🇺 Русский](2s-ui.ru.md) · [Back to README](../README.md)

[Install](#installation) · [Subscription domain](#separate-subscription-domain) · [Manage](#management-and-backups) · [Diagnose](#diagnostics) · [Tests](#developer-checks)

This mode installs [2S-UI v1.8.3](https://github.com/shenaba/2s-ui/releases/tag/v1.8.3)
with embedded sing-box 1.14.1, SQLite and Nginx through Docker Compose. The installer
prepares VLESS Reality and Hysteria2, HTTPS, random administrator credentials and
personal subscriptions. You then create clients in the panel and choose their limits.
The panel is a fork of S-UI; it is distinct from the Xray-based 3x-ui.

## Installation

Use a clean Ubuntu 22.04, 24.04 or 26.04 VPS, amd64/arm64, with Python 3 and root
access. Docker Engine 28+ and Compose v2 are required. The script installs Ubuntu's
Docker packages when Docker is absent; an existing older Engine must be upgraded
before installation. It does not migrate an existing Remnawave or standalone deployment.

1. Create a DNS-only **A** record for `panel.example.com` pointing directly to the
   VPS public IPv4. Do not use CDN proxying or AAAA records with this deployment.
2. Allow `80/tcp` for Let's Encrypt, `8443/tcp` for HTTPS, `443/tcp` for VLESS and
   `443/udp` for Hysteria2 in the provider firewall. Keep SSH accessible.
3. From the repository directory on the VPS:

```bash
cp .env.2s-ui.example .env
nano .env
sudo bash install.sh
sudo cat /opt/proxy-bootstrap/2s-ui/credentials.txt
```

Set `PANEL_DOMAIN`, `LETSENCRYPT_EMAIL`, `REALITY_SERVER_NAME` and
`HYSTERIA_MASQUERADE_URL`. Set `SERVER_IPV4` explicitly behind cloud NAT. Empty
`HYSTERIA_DOMAIN` reuses the panel domain. Disable either protocol with
`ENABLE_VLESS=false` or `ENABLE_HYSTERIA=false`; at least one must remain enabled.

Open `https://panel.example.com:8443/app/` and sign in with the generated credentials.
In **Clients**, create a client, select the prepared **VLESS Reality** and/or
**Hysteria2** inbounds, and set the expiry date and traffic limit. Copy the client's
subscription URL to Hiddify/Happ. Keep the randomly generated client name: 2S-UI uses
it in `/sub/<client-name>`, so it is an access secret, not a public display name.
Use the remark field for a readable label.

The normal subscription contains encoded protocol links. Add `?format=json` for
a sing-box JSON configuration or `?format=clash` for Clash. The JSON template uses
IPv4 DNS; clients importing protocol links manage their own DNS and routing.
This deployment's Docker network has no IPv6 egress. Configure IPv4-only in those
clients unless you separately enable and verify Docker IPv6.

`HYSTERIA_OBFS_TYPE=salamander` enables generated shared obfuscation credentials;
leave it empty for no obfuscation. Other obfuscation types are not supported by
this bootstrap mode. There is no Xray `minClientVer` setting here.

## Separate subscription domain

Before installation, optionally configure:

```env
PANEL_DOMAIN=panel.example.com
SUBSCRIPTION_DOMAIN=sub.example.com
PANEL_HTTPS_PORT=8443
```

Point `sub.example.com` to the same VPS with a DNS-only A record. The certificate
covers both names. The panel stays at `https://panel.example.com:8443/app/`;
subscriptions use `https://sub.example.com:8443/sub/<client-name>`. Both share the
HTTPS port. Only GET/HEAD subscription requests are forwarded on the subscription
hostname; panel and administration endpoints return `404`. The panel hostname also
continues to serve subscriptions. Empty `SUBSCRIPTION_DOMAIN` uses the panel hostname.

Changing domains or ports on an installed deployment is not automated. Editing
`.env` does not migrate existing subscriptions or rewrite panel-managed settings.

## Management and backups

| Action | Command |
| --- | --- |
| Check panel and core | `sudo bash scripts/healthcheck.sh` |
| Test subscriptions and traffic | `sudo bash scripts/2s-ui.sh smoke-test` |
| Back up | `sudo bash scripts/2s-ui.sh backup` |
| Update to the repository's pinned images | `sudo bash update.sh` |
| Stop and remove containers, retain data | `sudo bash uninstall.sh` |
| Remove managed data after backup | `sudo bash uninstall.sh --purge` |

State, secrets, SQLite, certificates and runtime live in `/opt/proxy-bootstrap/2s-ui`.
The bootstrap saves generated keys before creating objects and resumes incomplete
setup. Once bootstrap finishes, repeated installation or updates preserve clients,
inbounds, TLS settings and routing edited in the panel. Changing modes is refused;
do not delete state files to bypass this check. Stop/uninstall retains the installed
mode until its managed data is explicitly purged.

Updates create a consistent SQLite backup before applying pinned image versions.
Use `update.sh` for managed updates; a panel self-update changes the container's
writable layer and is lost when Docker recreates it. Source versions should be
updated and tested before changing image pins.

Backups include the database, state, runtime, configuration and certificates.
They contain secrets and are stored under `/var/backups/proxy-bootstrap/2s-ui/`.
Copy them off the VPS. Restore on the same server/address configuration with:

```bash
sudo bash scripts/2s-ui.sh restore /var/backups/proxy-bootstrap/2s-ui/BACKUP_NAME
```

Restore validates file checksums and SQLite integrity, backs up the current state,
then replaces it and restarts the pinned containers. The current database is
replaced, including any users created after the backup. Restoration to an empty
server still requires Docker, dependencies, DNS and firewall prerequisites.

Certbot renews the shared certificate through `certbot.timer`. The deploy hook
copies renewed files, reloads Nginx and restarts the embedded sing-box core.
Runtime copies of the scripts make the original checkout unnecessary for renewal.
Backups and Let's Encrypt certificates are retained even after `--purge`.

## Diagnostics

```bash
sudo bash scripts/2s-ui.sh healthcheck
sudo bash scripts/2s-ui.sh smoke-test
sudo bash scripts/2s-ui.sh smoke-test --external
sudo docker compose --project-directory /opt/proxy-bootstrap/2s-ui \
  -f /opt/proxy-bootstrap/2s-ui/compose.json logs --tail 100 panel nginx
```

The smoke test creates a temporary client valid for 15 minutes with a 100 MB limit,
fetches its real HTTPS subscription and sends HTTPS traffic through each enabled
protocol using a pinned sing-box client. It also checks that VPN users cannot
reach the private panel API. Temporary clients/containers are removed afterwards;
an interrupted probe is cleaned up on the next run. Failures save a private
`last-probe.log` in the installation directory.

Normally the probe reaches the panel through Docker networking. `--external`
uses the public addresses, but the probe still runs on the VPS: neither test proves
reachability from a user's network. Compare Wi-Fi and mobile data with each protocol
selected explicitly. If the subscription and SSH both time out before reaching
the VPS, investigate address reachability/provider filtering as well as the server.
Changing the panel or VPN core alone does not resolve an unreachable IP.

Docker publishes VPN and HTTPS ports independently of ordinary UFW rules. The
maintenance API is bound to loopback only; the raw subscription port `2096` is not
published. Use provider firewall rules for source restrictions. The bootstrap's
firewall rules only open host TCP/80 for ACME and do not reset policy or SSH rules.
The managed Nginx address is trusted for forwarded client IPs.

`CERTBOT_STAGING=true` is for testing only. In that mode HTTPS and test-client TLS
trust validation are relaxed; regular clients will not trust staging certificates.
Use `false` for a working installation.

## Developer checks

```bash
python3 -m unittest discover -s tests -v
python3 tests/integration_2s_ui.py
python3 tests/integration_2s_ui.py --separate-subscription-domain
```

The optional integration tests require local Docker, Compose, curl, openssl and
internet access. They use loopback ports and test bootstrap, HTTPS login,
subscriptions, traffic, updates, backup/restore and certificate reload without
modifying host firewall rules or invoking apt/certbot. Full Ubuntu installation,
public ACME issuance/renewal and real Hiddify/Happ devices require a separate VPS test.
