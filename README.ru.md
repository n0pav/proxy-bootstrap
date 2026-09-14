# Proxy Bootstrap для Ubuntu VPS

[**English 🇺🇸**](README.md) / [**Русский 🇷🇺**](README.ru.md)

`Proxy Bootstrap` это non-interactive bootstrap-репозиторий для чистой VPS на Ubuntu `22.04` или `24.04`, который устанавливает:

- `VLESS + Reality` на `443/tcp`
- `Hysteria2` на `443/udp` (номер порта можно делить с VLESS на TCP)
- сертификат `Let's Encrypt` для `Hysteria2`
- `systemd`, `sysctl`, firewall/NAT, логирование и healthcheck

Все параметры задаются через `.env`.

## Быстрый старт

1. Склонируйте репозиторий на VPS.
2. Скопируйте `.env.example` в `.env`.
3. Для VLESS задайте `REALITY_SERVER_NAME`. Для Hysteria2 задайте `HYSTERIA_DOMAIN`, `LETSENCRYPT_EMAIL` и `HYSTERIA_MASQUERADE_URL`.
4. Убедитесь, что домен уже указывает на IPv4 этой VPS.
5. Запустите установку:

```bash
sudo bash install.sh
```

Готовые клиентские ссылки будут записаны в `/etc/sing-box/client-links.txt`.

## Конфигурация (.env)

Обязательные переменные:

- `ENABLE_VLESS`
- `ENABLE_HYSTERIA`

Важные необязательные переменные:

- `ENABLE_VLESS` / `ENABLE_HYSTERIA`: выбрать режим `только VLESS`, `только Hysteria2` или `оба сразу`
- `VLESS_PORT` / `HYSTERIA_PORT`: TCP и UDP могут одновременно использовать стандартный номер порта `443`
- `REALITY_FINGERPRINT`: клиентская подсказка uTLS fingerprint; `chrome` используется по умолчанию ради совместимости
- `HYSTERIA_OBFS_TYPE`: пустое значение, `salamander` или `gecko`
- `PRIMARY_INTERFACE`: вручную задать исходящий интерфейс для NAT
- `SERVER_IPV4`: вручную задать IPv4 сервера, если автоопределение не подходит
- `FIREWALL_BACKEND`: `auto`, `ufw`, `iptables`, `none`
- `CERTBOT_STAGING`: `true` для тестовых прогонов без риска упереться в rate limits Let's Encrypt
- `VLESS_UUID`, `REALITY_PRIVATE_KEY`, `REALITY_PUBLIC_KEY`, `REALITY_SHORT_ID`, `HYSTERIA_PASSWORD`, `HYSTERIA_OBFS_PASSWORD`: заранее заданные секреты вместо автогенерации

Зависимости по режимам:

- если `ENABLE_VLESS=true`, обязателен `REALITY_SERVER_NAME`
- если `ENABLE_HYSTERIA=true`, обязательны `HYSTERIA_DOMAIN`, `LETSENCRYPT_EMAIL` и `HYSTERIA_MASQUERADE_URL`
- хотя бы один из двух флагов должен быть `true`

Состояние установки и сгенерированные секреты хранятся в `/etc/sing-box/bootstrap.env`.

Значения `.env` с shell-символами заключайте в кавычки, например `HYSTERIA_MASQUERADE_URL='https://example.org/?a=1&b=2'`.

### Настройки маскировки

- У `REALITY_SERVER_NAME` больше нет общего fallback. Для каждой VPS явно выбирайте стабильный target с TLS 1.3 и HTTP/2, желательно сетево близкий к серверу.
- `REALITY_FINGERPRINT` теперь настраивается, но эмуляция uTLS остаётся fingerprintable: это параметр совместимости клиента, а не гарантия невидимости.
- Без Hysteria2 obfs неаутентифицированные HTTP/3-запросы проксируются на `HYSTERIA_MASQUERADE_URL`.
- `salamander` поддерживается стабильным пакетом sing-box. При включённом obfs обычный HTTP/3 probe получит timeout до обработчика masquerade: obfs и masquerade предназначены для разных профилей трафика.
- `gecko` сейчас требует тестовую ветку sing-box `1.14` и совместимый клиент. До стабильного релиза 1.14 задайте на сервере `SING_BOX_PACKAGE=sing-box-beta`. Режим экспериментальный.
- Включение или смена obfs требует заново импортировать сгенерированную Hysteria2-ссылку на всех клиентах.

## Установка

```bash
cp .env.example .env
nano .env
sudo bash install.sh
```

Что делает `install.sh`:

- проверяет root, ОС, `.env` и DNS
- устанавливает базовые пакеты и официальный apt-репозиторий `sing-box`
- включает `net.ipv4.ip_forward=1`
- настраивает firewall и NAT без дублирования правил
- генерирует ключи только если их ещё нет
- выпускает или переиспользует сертификат `Let's Encrypt`
- проверяет временный конфиг sing-box и затем атомарно пишет `/etc/sing-box/config.json`
- включает и запускает `sing-box`
- запускает `scripts/healthcheck.sh`

Скрипт идемпотентный: повторный запуск должен приводить систему к нужному состоянию, а не ломать существующую установку.

Примеры режимов:

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

## Обновление

```bash
sudo bash update.sh
```

`update.sh` повторно запускает установщик, пытается обновить сертификаты, перезапускает `sing-box` и затем запускает healthcheck.

Перед обновлением старой установки задайте `REALITY_SERVER_NAME`, если VLESS раньше полагался на неявный fallback `github.com`, и добавьте `HYSTERIA_MASQUERADE_URL`, если включён Hysteria2. Явно заданный старый `HYSTERIA_PORT=8443` не изменится; переключите его на `443`, когда клиенты и firewall провайдера будут готовы к миграции.

## Удаление

```bash
sudo bash uninstall.sh
```

Скрипт удаляет управляемую установку `sing-box`, systemd/NAT-артефакты, managed-конфиги и apt source для SagerNet.

Сертификаты в `/etc/letsencrypt` намеренно не удаляются.

## Диагностика

Основной лог:

```bash
sudo tail -f /var/log/proxy-bootstrap.log
```

Логи сервиса:

```bash
sudo journalctl -u sing-box -f
```

Ручная проверка:

```bash
sudo bash scripts/healthcheck.sh
```

Полезные команды:

```bash
sudo systemctl status sing-box --no-pager
sudo ss -tulpn | grep -E ':(80|443)'
sudo sing-box check -c /etc/sing-box/config.json
```

Если не выпускается сертификат:

- проверьте `A`-запись `HYSTERIA_DOMAIN`
- проверьте, что `80/tcp` доступен снаружи
- проверьте, что на `80/tcp` не слушает другой процесс

Если не работает `Hysteria2`:

- проверьте доступность настроенного `HYSTERIA_PORT` (по умолчанию `443/udp`)
- проверьте, что сертификат выписан на `HYSTERIA_DOMAIN`
- проверьте, что `HYSTERIA_MASQUERADE_URL` доступен с VPS
- если включён obfs, проверьте совпадение его типа и пароля на клиенте
- проверьте пароль из `/etc/sing-box/client-links.txt`
