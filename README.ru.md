<div align="center">
  <h1>Proxy Bootstrap</h1>
  <p>Панель sing-box или Xray с подписками либо самостоятельный sing-box.<br>Установка на Ubuntu VPS из одного файла конфигурации.</p>
  <p>
    <a href="README.md">🇺🇸 English</a>
    &nbsp;·&nbsp;
    <a href="README.ru.md">🇷🇺 <strong>Русский</strong></a>
  </p>
  <p>
    <a href="#быстрый-старт"><img src="https://img.shields.io/badge/Ubuntu-22.04%20%7C%2024.04%20%7C%2026.04-E95420?style=flat-square&amp;logo=ubuntu&amp;logoColor=white" alt="Ubuntu 22.04, 24.04, 26.04"></a>
    <a href="docs/remnawave.ru.md"><img src="https://img.shields.io/badge/VLESS-Reality-2563EB?style=flat-square" alt="VLESS Reality"></a>
    <a href="docs/remnawave.ru.md"><img src="https://img.shields.io/badge/Hysteria-2-0891B2?style=flat-square" alt="Hysteria2"></a>
    <a href="#проверки"><img src="https://img.shields.io/badge/Docker-locally_tested-2EA44F?style=flat-square&amp;logo=docker&amp;logoColor=white" alt="Docker: locally tested"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-64748B?style=flat-square" alt="License: MIT"></a>
  </p>
  <p>
    <a href="#быстрый-старт">Быстрый старт</a>
    &nbsp;·&nbsp; <a href="#управление">Управление</a>
    &nbsp;·&nbsp; <a href="#документация">Документация</a>
    &nbsp;·&nbsp; <a href="#проверки">Проверки</a>
  </p>
</div>

Все параметры задаются через `.env`; установщик работает без интерактивных вопросов.

## Выберите режим

| | 2S-UI | Remnawave | Standalone |
| --- | --- | --- | --- |
| Для чего | Панель sing-box с пользователями и подписками | Панель Xray с пользователями и подписками | Прямое подключение без панели |
| Что устанавливается | 2S-UI (sing-box + SQLite), Nginx через Docker | Remnawave + Xray-нода, PostgreSQL, Valkey, Nginx через Docker | sing-box с systemd |
| Настройка клиента | Персональная подписка | Персональная подписка | Ссылки VLESS / Hysteria2 |
| Пример конфигурации | [`.env.2s-ui.example`](.env.2s-ui.example) | [`.env.remnawave.example`](.env.remnawave.example) | [`.env.example`](.env.example) |
| Режим установки | `2s-ui` | `remnawave` | `standalone` (по умолчанию) |
| Подробная инструкция | [2S-UI](docs/2s-ui.ru.md) | [Remnawave](docs/remnawave.ru.md) | [Standalone](docs/standalone.ru.md) |

Выберите один режим до установки. Изменение `DEPLOYMENT_MODE` не переносит
существующую установку; скрипт откажется переключать режим на месте.

## Быстрый старт

### 1. Подготовьте VPS

Нужна чистая **Ubuntu 22.04, 24.04 или 26.04**, доступ root или `sudo` и публичный
IPv4. Для Remnawave начальный ориентир — **4 ГБ RAM**; подробности указаны
в [требованиях](docs/remnawave.ru.md).

Направьте домен панели (2S-UI или Remnawave) и домен Hysteria2, если протокол включён,
на VPS через DNS **A**-записи. Для панелей используйте DNS-only без
CDN-проксирования и AAAA-записей. Для standalone только с VLESS свой домен не нужен.

Разрешите нужные порты в firewall провайдера:

| Порт по умолчанию | Назначение | Когда нужен |
| --- | --- | --- |
| `443/tcp` | VLESS Reality | Включён VLESS |
| `443/udp` | Hysteria2 | Включён Hysteria2 |
| `8443/tcp` | Панель и подписки | 2S-UI или Remnawave |
| `80/tcp` | Выпуск и продление Let's Encrypt | Любая панель или standalone с Hysteria2 |

Сохраните доступ к SSH-порту. TCP и UDP могут использовать один номер порта `443`.
В режимах с панелью Docker публикует VPN- и HTTPS-порты независимо от UFW;
ограничения по IP задавайте в firewall провайдера.

Склонируйте или скопируйте репозиторий на VPS и перейдите в его каталог:

```bash
git clone https://github.com/n0pav/proxy-bootstrap.git
cd proxy-bootstrap
```

### 2. Настройте и установите один режим

#### 2S-UI — панель sing-box и персональные подписки

```bash
cp .env.2s-ui.example .env
nano .env
sudo bash install.sh
sudo cat /opt/proxy-bootstrap/2s-ui/credentials.txt
```

Задайте `PANEL_DOMAIN`, `LETSENCRYPT_EMAIL`, `REALITY_SERVER_NAME` и
`HYSTERIA_MASQUERADE_URL`. За NAT облачного провайдера также укажите `SERVER_IPV4`.
При желании до установки задайте `SUBSCRIPTION_DOMAIN` и добавьте его A-запись.
Откройте `https://ДОМЕН_ПАНЕЛИ:8443/app/`, создайте клиента в **Clients**, выберите
подготовленные подключения VLESS/Hysteria2 и скопируйте ссылку подписки.

[Полная инструкция 2S-UI →](docs/2s-ui.ru.md)

#### Remnawave — панель и персональные подписки

```bash
cp .env.remnawave.example .env
nano .env
sudo bash install.sh
```

В `.env` задайте `PANEL_DOMAIN` и `LETSENCRYPT_EMAIL`. Укажите `REALITY_SERVER_NAME`,
если включён VLESS, и `HYSTERIA_MASQUERADE_URL`, если включён Hysteria2.
Пустой `HYSTERIA_DOMAIN` использует домен панели. Если VPS находится за NAT
облачного провайдера, явно задайте `SERVER_IPV4`.

При желании задайте `SUBSCRIPTION_DOMAIN=sub.example.com`, чтобы вынести подписки
на отдельный домен на той же VPS. Добавьте его DNS A-запись; порт общий —
`PANEL_HTTPS_PORT` (по умолчанию `8443`). Пустое значение использует домен панели.
Выберите это до установки; подробнее — в [настройке отдельного домена](docs/remnawave.ru.md#отдельный-домен-подписок-необязательно).

Посмотрите сгенерированные данные администратора:

```bash
sudo cat /opt/proxy-bootstrap/remnawave/credentials.txt
```

Откройте `https://ДОМЕН_ПАНЕЛИ:8443`, войдите и создайте пользователя в группе
**VPN Users** с нужным сроком действия и лимитами. Скопируйте его персональную
ссылку подписки в совместимый VPN-клиент. Это подписка на конфигурацию;
биллинг в неё не входит.

[Полная инструкция Remnawave →](docs/remnawave.ru.md)

#### Standalone — sing-box и прямые ссылки

```bash
cp .env.example .env
nano .env
sudo bash install.sh
```

В `.env` задайте `REALITY_SERVER_NAME` для VLESS. Для Hysteria2 укажите
`HYSTERIA_DOMAIN`, `LETSENCRYPT_EMAIL` и `HYSTERIA_MASQUERADE_URL`.
Посмотрите готовые ссылки и импортируйте их в VPN-клиент:

```bash
sudo cat /etc/sing-box/client-links.txt
```

[Полная инструкция standalone →](docs/standalone.ru.md)

Все три примера включают оба протокола. Чтобы отключить один, задайте
`ENABLE_VLESS=false` или `ENABLE_HYSTERIA=false`; хотя бы один должен остаться
включённым. Не публикуйте пароли, клиентские ссылки и ссылки подписки.

## Управление

Выполняйте команды из каталога репозитория на VPS:

| Действие | Команда |
| --- | --- |
| Проверить работу сервисов | `sudo bash scripts/healthcheck.sh` |
| Обновить | `sudo bash update.sh` |
| Удалить | `sudo bash uninstall.sh` |
| Проверить VPN-трафик через Remnawave | `sudo bash scripts/remnawave.sh smoke-test` |
| Создать резервную копию Remnawave | `sudo bash scripts/remnawave.sh backup` |
| Проверить VPN-трафик через 2S-UI | `sudo bash scripts/2s-ui.sh smoke-test` |
| Создать резервную копию 2S-UI | `sudo bash scripts/2s-ui.sh backup` |

Перед обновлением любой из панелей создаётся копия базы и конфигурации, затем применяются
закреплённые версии образов. Удаление по умолчанию сохраняет данные; `--purge`
удаляет управляемые данные после резервного копирования. Удаление standalone
убирает его управляемую конфигурацию и секреты. Все режимы сохраняют сертификаты
Let's Encrypt.

Перед [восстановлением Remnawave](docs/remnawave.ru.md#обновление-и-резервные-копии)
или [обновлением старой standalone-установки](docs/standalone.ru.md#обновление)
посмотрите соответствующую инструкцию.

## Документация

| Руководство | 🇺🇸 English | 🇷🇺 Русский |
| --- | --- | --- |
| 2S-UI: панель sing-box, подписки, резервные копии и диагностика | [Read](docs/2s-ui.md) | [Читать](docs/2s-ui.ru.md) |
| Remnawave: установка, пользователи, подписки, резервные копии и диагностика | [Read](docs/remnawave.md) | [Читать](docs/remnawave.ru.md) |
| Standalone: конфигурация, маскировка, обновление и диагностика | [Read](docs/standalone.md) | [Читать](docs/standalone.ru.md) |

## Проверки

Плашка **Docker: locally tested** отражает выполненные локальные проверки обеих панелей.

- **Пройдено:** автоматические тесты и проверка синтаксиса shell-скриптов.
- **Пройдено в Docker:** персональные подписки, реальный трафик через оба протокола,
  восстановление резервной копии и смена сертификата.
- **Предстоит на VPS:** полная установка на чистой Ubuntu, продление Let's Encrypt
  и подключение из внешней сети.

Команды для повторения — в инструкциях [Remnawave](docs/remnawave.ru.md#проверки-разработчика)
и [2S-UI](docs/2s-ui.ru.md#проверки-разработчика).
Автоматический CI пока не настроен; плашка показывает результат локальной проверки.
