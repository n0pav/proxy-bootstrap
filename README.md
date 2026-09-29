<div align="center">
  <h1>Proxy Bootstrap</h1>
  <p>A Remnawave panel with a local VPN node, or standalone sing-box.<br>Set up your Ubuntu VPS from one configuration file.</p>
  <p>
    <a href="README.md">🇺🇸 <strong>English</strong></a>
    &nbsp;·&nbsp;
    <a href="README.ru.md">🇷🇺 Русский</a>
  </p>
  <p>
    <a href="#quick-start"><img src="https://img.shields.io/badge/Ubuntu-22.04%20%7C%2024.04%20%7C%2026.04-E95420?style=flat-square&amp;logo=ubuntu&amp;logoColor=white" alt="Ubuntu 22.04, 24.04, 26.04"></a>
    <a href="docs/remnawave.md"><img src="https://img.shields.io/badge/VLESS-Reality-2563EB?style=flat-square" alt="VLESS Reality"></a>
    <a href="docs/remnawave.md"><img src="https://img.shields.io/badge/Hysteria-2-0891B2?style=flat-square" alt="Hysteria2"></a>
    <a href="#verification"><img src="https://img.shields.io/badge/Docker-locally_tested-2EA44F?style=flat-square&amp;logo=docker&amp;logoColor=white" alt="Docker: locally tested"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-64748B?style=flat-square" alt="License: MIT"></a>
  </p>
  <p>
    <a href="#quick-start">Quick start</a>
    &nbsp;·&nbsp; <a href="#management">Management</a>
    &nbsp;·&nbsp; <a href="#documentation">Documentation</a>
    &nbsp;·&nbsp; <a href="#verification">Verification</a>
  </p>
</div>

Configuration comes from `.env`; the installer runs without interactive prompts.

## Choose a mode

| | Remnawave | Standalone |
| --- | --- | --- |
| Best for | Managing users, limits and subscriptions in a panel | Connecting directly using generated client links |
| Installed software | Remnawave panel + local Xray node, PostgreSQL, Valkey, Nginx via Docker Compose | sing-box with systemd |
| Client configuration | Personal subscription per user | Generated VLESS / Hysteria2 links |
| Configuration example | [`.env.remnawave.example`](.env.remnawave.example) | [`.env.example`](.env.example) |
| Deployment mode | `remnawave` | `standalone` (default) |
| Full guide | [Remnawave](docs/remnawave.md) | [Standalone](docs/standalone.md) |

Choose one mode before installing. Changing `DEPLOYMENT_MODE` does not migrate
an existing installation; the installer refuses to switch modes in place.

## Quick start

### 1. Prepare the VPS

Use a clean **Ubuntu 22.04, 24.04 or 26.04** server with root or `sudo` access
and a public IPv4 address. For Remnawave, allow roughly **4 GB RAM** as a starting
point; see its [requirements](docs/remnawave.md) for details.

Point the panel domain (Remnawave) and the Hysteria2 domain, if enabled, to the
VPS using DNS **A** records. For Remnawave, use DNS-only records without CDN
proxying or AAAA records. Standalone with VLESS only does not need your own domain.

Allow the ports needed by your configuration in the provider firewall:

| Default port | Purpose | When needed |
| --- | --- | --- |
| `443/tcp` | VLESS Reality | VLESS enabled |
| `443/udp` | Hysteria2 | Hysteria2 enabled |
| `8443/tcp` | Panel and subscriptions | Remnawave |
| `80/tcp` | Let's Encrypt issuance and renewal | Remnawave, or standalone with Hysteria2 |

Keep your SSH port accessible. TCP and UDP can share the port number `443`.
For Remnawave, Docker publishes VPN and HTTPS ports independently of UFW;
use the provider firewall for source-IP restrictions.

Clone or copy the repository to the VPS, then enter its directory:

```bash
git clone https://github.com/n0pav/proxy-bootstrap.git
cd proxy-bootstrap
```

### 2. Configure and install one mode

#### Remnawave — panel and personal subscriptions

```bash
cp .env.remnawave.example .env
nano .env
sudo bash install.sh
```

In `.env`, set `PANEL_DOMAIN` and `LETSENCRYPT_EMAIL`. Set `REALITY_SERVER_NAME`
when VLESS is enabled and `HYSTERIA_MASQUERADE_URL` when Hysteria2 is enabled.
An empty `HYSTERIA_DOMAIN` reuses the panel domain. Set `SERVER_IPV4` explicitly
when the VPS is behind cloud NAT.

Read the generated administrator credentials:

```bash
sudo cat /opt/proxy-bootstrap/remnawave/credentials.txt
```

Open `https://YOUR_PANEL_DOMAIN:8443`, sign in, and create a user in the
**VPN Users** squad with the required expiry date and limits. Copy their
personal subscription link into a compatible VPN client. This is a configuration
subscription; it does not include billing.

[Full Remnawave guide →](docs/remnawave.md)

#### Standalone — sing-box and direct links

```bash
cp .env.example .env
nano .env
sudo bash install.sh
```

In `.env`, set `REALITY_SERVER_NAME` for VLESS. For Hysteria2, set
`HYSTERIA_DOMAIN`, `LETSENCRYPT_EMAIL` and `HYSTERIA_MASQUERADE_URL`.
Read the generated links and import them into your VPN client:

```bash
sudo cat /etc/sing-box/client-links.txt
```

[Full standalone guide →](docs/standalone.md)

Both examples enable both protocols. Set `ENABLE_VLESS=false` or
`ENABLE_HYSTERIA=false` to disable one; at least one must remain enabled.
Keep credentials and client/subscription links private.

## Management

Run these commands from the repository directory on the VPS:

| Action | Command |
| --- | --- |
| Check service health | `sudo bash scripts/healthcheck.sh` |
| Update | `sudo bash update.sh` |
| Uninstall | `sudo bash uninstall.sh` |
| Test VPN traffic through Remnawave | `sudo bash scripts/remnawave.sh smoke-test` |
| Back up Remnawave | `sudo bash scripts/remnawave.sh backup` |

Remnawave updates back up the database and configuration before applying pinned
image versions. Its uninstall preserves data by default; `--purge` removes managed
data after a backup. Standalone uninstall removes its managed configuration and
secrets. Both modes keep Let's Encrypt certificates.

See the mode-specific guides before [restoring Remnawave](docs/remnawave.md#updates-and-backups)
or [updating an older standalone installation](docs/standalone.md#update).

## Documentation

| Guide | 🇺🇸 English | 🇷🇺 Русский |
| --- | --- | --- |
| Remnawave: setup, users, subscriptions, backups and diagnostics | [Read](docs/remnawave.md) | [Читать](docs/remnawave.ru.md) |
| Standalone: configuration, camouflage, updates and troubleshooting | [Read](docs/standalone.md) | [Читать](docs/standalone.ru.md) |

## Verification

The **Docker: locally tested** badge records a completed local Remnawave test run.

- **Passed:** 13 automated tests and shell script syntax checks.
- **Passed in Docker:** personal subscriptions, real traffic through both protocols,
  backup restoration and certificate reload.
- **Pending on a VPS:** full installation on clean Ubuntu, Let's Encrypt renewal
  and connectivity from an external network.

See the [test guide](docs/remnawave.md#developer-checks) for commands to reproduce the checks.
Automated CI is not configured yet; the badge represents a local test result.
