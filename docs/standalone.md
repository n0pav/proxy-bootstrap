# Standalone: sing-box on an Ubuntu VPS

[🇺🇸 English](standalone.md) · [🇷🇺 Русский](standalone.ru.md) · [Back to README](../README.md)

The default `DEPLOYMENT_MODE=standalone` installs sing-box with VLESS Reality,
Hysteria2, or both. It generates client links without a management panel.
For a panel with users and personal subscriptions, see the [Remnawave guide](remnawave.md).

Start with a clean Ubuntu 22.04, 24.04 or 26.04 VPS and the
[standalone configuration example](../.env.example). Run the commands below
from the repository directory on the VPS. See the [quick start](../README.md#quick-start)
for server preparation and required ports.

[Configuration](#configuration-env) · [Install](#install) · [Update](#update) · [Uninstall](#uninstall) · [Troubleshooting](#troubleshooting)

## Configuration (.env)

Protocol selection:

- `ENABLE_VLESS`: enable VLESS Reality
- `ENABLE_HYSTERIA`: enable Hysteria2

Important optional variables:

- `VLESS_PORT` / `HYSTERIA_PORT`: TCP and UDP may both use the default port number `443`
- `REALITY_FINGERPRINT`: client-side uTLS fingerprint hint; `chrome` is the compatibility default
- `HYSTERIA_OBFS_TYPE`: empty, `salamander`, or `gecko`
- `PRIMARY_INTERFACE`: override auto-detected outbound interface for NAT
- `SERVER_IPV4`: override auto-detected public IPv4
- `FIREWALL_BACKEND`: `auto`, `ufw`, `iptables`, `none`
- `CERTBOT_STAGING`: set to `true` during dry runs to avoid Let's Encrypt rate limits
- `VLESS_UUID`, `REALITY_PRIVATE_KEY`, `REALITY_PUBLIC_KEY`, `REALITY_SHORT_ID`, `HYSTERIA_PASSWORD`, `HYSTERIA_OBFS_PASSWORD`: pre-seed secrets instead of generating them

Protocol-specific requirements:

- when `ENABLE_VLESS=true`, `REALITY_SERVER_NAME` is required
- when `ENABLE_HYSTERIA=true`, `HYSTERIA_DOMAIN`, `LETSENCRYPT_EMAIL`, and `HYSTERIA_MASQUERADE_URL` are required
- at least one of the two protocol flags must be `true`

State for generated secrets and managed UFW changes is stored in `/etc/sing-box/bootstrap.env`.

Quote `.env` values containing shell characters, for example `HYSTERIA_MASQUERADE_URL='https://example.org/?a=1&b=2'`.

### Camouflage settings

- `REALITY_SERVER_NAME` has no global fallback. Choose a stable TLS 1.3 + HTTP/2 target explicitly for each VPS, preferably one that is network-close to the server.
- `REALITY_FINGERPRINT` is configurable, but uTLS emulation remains fingerprintable; treat it as a client-compatibility setting, not an invisibility guarantee.
- Without Hysteria2 obfs, unauthenticated HTTP/3 requests are reverse-proxied to `HYSTERIA_MASQUERADE_URL`.
- `salamander` is supported by the stable sing-box package. With obfs enabled, ordinary HTTP/3 probes time out before reaching the masquerade handler; obfs and masquerade protect different traffic profiles.
- `gecko` currently requires sing-box `1.14` testing and a Gecko-capable client. Set `SING_BOX_PACKAGE=sing-box-beta` on the server until 1.14 is stable. Treat it as experimental.
- Enabling or changing obfs requires re-importing the generated Hysteria2 link on every client.

## Install

```bash
cp .env.example .env
nano .env
sudo bash install.sh
```

What `install.sh` does:

- validates root, OS, `.env` and DNS
- installs base packages and the official `sing-box` apt repository
- enables `net.ipv4.ip_forward=1`
- configures firewall rules and managed NAT without duplication
- generates secrets only when missing
- issues or reuses a Let's Encrypt certificate
- validates a temporary sing-box config, then atomically writes `/etc/sing-box/config.json`
- enables and starts `sing-box`
- runs `scripts/healthcheck.sh`

The script is idempotent. Re-running it should reconcile the install instead of breaking it.

Example protocol modes:

```env
ENABLE_VLESS=true
ENABLE_HYSTERIA=false
```

```env
ENABLE_VLESS=false
ENABLE_HYSTERIA=true
```

```env
ENABLE_VLESS=true
ENABLE_HYSTERIA=true
```

## Update

```bash
sudo bash update.sh
```

`update.sh` re-runs the installer, attempts certificate renewal, restarts `sing-box`, then runs the healthcheck.

Before updating an older installation, set `REALITY_SERVER_NAME` if VLESS previously relied on the implicit `github.com` fallback, and add `HYSTERIA_MASQUERADE_URL` when Hysteria2 is enabled. An explicitly configured old `HYSTERIA_PORT=8443` remains unchanged; set it to `443` when clients and provider firewall rules are ready to migrate.

## Uninstall

```bash
sudo bash uninstall.sh
```

This removes the managed `sing-box` install, systemd/NAT artifacts, managed config files and the SagerNet apt source.

It intentionally leaves Let's Encrypt certificates on disk in `/etc/letsencrypt`.

## Troubleshooting

Main log:

```bash
sudo tail -f /var/log/proxy-bootstrap.log
```

Service logs:

```bash
sudo journalctl -u sing-box -f
```

Manual health check:

```bash
sudo bash scripts/healthcheck.sh
```

Useful checks:

```bash
sudo systemctl status sing-box --no-pager
sudo ss -tulpn | grep -E ':(80|443)'
sudo sing-box check -c /etc/sing-box/config.json
```

If certificate issuance fails:

- verify the A record of `HYSTERIA_DOMAIN`
- verify `80/tcp` is reachable from the internet
- verify nothing else is listening on `80/tcp`

If Hysteria2 fails:

- verify the configured `HYSTERIA_PORT` (default `443/udp`) is reachable
- verify the certificate matches `HYSTERIA_DOMAIN`
- verify `HYSTERIA_MASQUERADE_URL` is reachable from the VPS
- if obfs is enabled, verify the client uses the same type and password
- verify the password from `/etc/sing-box/client-links.txt`
