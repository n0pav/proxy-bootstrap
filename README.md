# Proxy Bootstrap for Ubuntu VPS

[**English 🇺🇸**](README.md) / [**Русский 🇷🇺**](README.ru.md)

Production-ready bootstrap for a clean Ubuntu `22.04` or `24.04` VPS that installs:

- `VLESS + Reality` on `443/tcp`
- `Hysteria2` on `8443/udp`
- `certbot` certificate issuance for Hysteria2
- managed logging, health checks, systemd, sysctl and firewall/NAT setup

All automation is non-interactive and driven by `.env`.

## Quick start

1. Copy the repo to the server.
2. Copy `.env.example` to `.env`.
3. Fill in at least `HYSTERIA_DOMAIN` and `LETSENCRYPT_EMAIL`.
4. Make sure the domain already points to the server IPv4.
5. Run:

```bash
sudo bash install.sh
```

Generated client links are written to `/etc/sing-box/client-links.txt`.

## Configuration (.env)

Required variables:

- `ENABLE_VLESS`
- `ENABLE_HYSTERIA`

Important optional variables:

- `ENABLE_VLESS` / `ENABLE_HYSTERIA`: choose `VLESS only`, `Hysteria2 only`, or `both`
- `PRIMARY_INTERFACE`: override auto-detected outbound interface for NAT
- `SERVER_IPV4`: override auto-detected public IPv4
- `FIREWALL_BACKEND`: `auto`, `ufw`, `iptables`, `none`
- `CERTBOT_STAGING`: set to `true` during dry runs to avoid Let's Encrypt rate limits
- `VLESS_UUID`, `REALITY_PRIVATE_KEY`, `REALITY_PUBLIC_KEY`, `REALITY_SHORT_ID`, `HYSTERIA_PASSWORD`: pre-seed secrets instead of generating them

Protocol-specific requirements:

- when `ENABLE_VLESS=true`, `REALITY_SERVER_NAME` is required
- when `ENABLE_HYSTERIA=true`, `HYSTERIA_DOMAIN` and `LETSENCRYPT_EMAIL` are required
- at least one of the two protocol flags must be `true`

State for generated secrets and managed UFW changes is stored in `/etc/sing-box/bootstrap.env`.

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
- writes `/etc/sing-box/config.json`
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
sudo ss -tulpn | grep -E ':(80|443|8443)'
sudo sing-box check -c /etc/sing-box/config.json
```

If certificate issuance fails:

- verify the A record of `HYSTERIA_DOMAIN`
- verify `80/tcp` is reachable from the internet
- verify nothing else is listening on `80/tcp`

If Hysteria2 fails:

- verify `8443/udp` is reachable
- verify the certificate matches `HYSTERIA_DOMAIN`
- verify the password from `/etc/sing-box/client-links.txt`
