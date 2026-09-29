<div align="center">
  <h1>Proxy Bootstrap</h1>
  <p>Панель Remnawave с локальной VPN-нодой или самостоятельный sing-box.<br>Установка на Ubuntu VPS из одного файла конфигурации.</p>
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

| | Remnawave | Standalone |
| --- | --- | --- |
| Для чего | Управление пользователями, лимитами и подписками в панели | Подключение по готовым клиентским ссылкам |
| Что устанавливается | Панель Remnawave + локальная Xray-нода, PostgreSQL, Valkey, Nginx через Docker Compose | sing-box с systemd |
| Настройка клиента | Персональная подписка для каждого пользователя | Сгенерированные ссылки VLESS / Hysteria2 |
| Пример конфигурации | [`.env.remnawave.example`](.env.remnawave.example) | [`.env.example`](.env.example) |
| Режим установки | `remnawave` | `standalone` (по умолчанию) |
| Подробная инструкция | [Remnawave](docs/remnawave.ru.md) | [Standalone](docs/standalone.ru.md) |

Выберите один режим до установки. Изменение `DEPLOYMENT_MODE` не переносит
существующую установку; скрипт откажется переключать режим на месте.

## Быстрый старт

### 1. Подготовьте VPS

Нужна чистая **Ubuntu 22.04, 24.04 или 26.04**, доступ root или `sudo` и публичный
IPv4. Для Remnawave начальный ориентир — **4 ГБ RAM**; подробности указаны
в [требованиях](docs/remnawave.ru.md).

Направьте домен панели (Remnawave) и домен Hysteria2, если протокол включён,
на VPS через DNS **A**-записи. Для Remnawave используйте DNS-only без
CDN-проксирования и AAAA-записей. Для standalone только с VLESS свой домен не нужен.

Разрешите нужные порты в firewall провайдера:

| Порт по умолчанию | Назначение | Когда нужен |
| --- | --- | --- |
| `443/tcp` | VLESS Reality | Включён VLESS |
| `443/udp` | Hysteria2 | Включён Hysteria2 |
| `8443/tcp` | Панель и подписки | Remnawave |
| `80/tcp` | Выпуск и продление Let's Encrypt | Remnawave или standalone с Hysteria2 |

Сохраните доступ к SSH-порту. TCP и UDP могут использовать один номер порта `443`.
В Remnawave Docker публикует VPN- и HTTPS-порты независимо от UFW;
ограничения по IP задавайте в firewall провайдера.

Склонируйте или скопируйте репозиторий на VPS и перейдите в его каталог:

```bash
git clone https://github.com/n0pav/proxy-bootstrap.git
cd proxy-bootstrap
```

### 2. Настройте и установите один режим

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

Оба примера включают оба протокола. Чтобы отключить один, задайте
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

Перед обновлением Remnawave создаётся копия базы и конфигурации, затем применяются
закреплённые версии образов. Удаление по умолчанию сохраняет данные; `--purge`
удаляет управляемые данные после резервного копирования. Удаление standalone
убирает его управляемую конфигурацию и секреты. Оба режима сохраняют сертификаты
Let's Encrypt.

Перед [восстановлением Remnawave](docs/remnawave.ru.md#обновление-и-резервные-копии)
или [обновлением старой standalone-установки](docs/standalone.ru.md#обновление)
посмотрите соответствующую инструкцию.

## Документация

| Руководство | 🇺🇸 English | 🇷🇺 Русский |
| --- | --- | --- |
| Remnawave: установка, пользователи, подписки, резервные копии и диагностика | [Read](docs/remnawave.md) | [Читать](docs/remnawave.ru.md) |
| Standalone: конфигурация, маскировка, обновление и диагностика | [Read](docs/standalone.md) | [Читать](docs/standalone.ru.md) |

## Проверки

Плашка **Docker: locally tested** отражает выполненный локальный прогон Remnawave.

- **Пройдено:** 13 автоматических тестов и проверка синтаксиса shell-скриптов.
- **Пройдено в Docker:** персональные подписки, реальный трафик через оба протокола,
  восстановление резервной копии и смена сертификата.
- **Предстоит на VPS:** полная установка на чистой Ubuntu, продление Let's Encrypt
  и подключение из внешней сети.

Команды для повторения — в [инструкции по тестированию](docs/remnawave.ru.md#проверки-разработчика).
Автоматический CI пока не настроен; плашка показывает результат локальной проверки.
