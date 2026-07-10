# Деплой MKL-Rent на VPS (production)

Пошаговый runbook: воспроизводимый запуск в Docker Compose с PostgreSQL, несколькими
воркерами за nginx и реальным HTTPS (Let's Encrypt). Бизнес-логика MVP не меняется —
только инфраструктура (ТЗ §35, §36, §41).

Источник истины — CLI-команды ниже. Блоки «💡 PyCharm» — удобная альтернатива через GUI,
не единственный путь. Секреты (`.env`, ключи, сертификаты) в репозиторий не попадают.

---

## 1. Архитектура развёртывания

`docker compose` поднимает пять сервисов (см. [docker-compose.yml](docker-compose.yml)):

| Сервис      | Роль                                                                    |
|-------------|-------------------------------------------------------------------------|
| `db`        | PostgreSQL 16, том `pgdata`. Порты наружу не публикуются.                |
| `web`       | FastAPI/Uvicorn, несколько воркеров, `--proxy-headers`. Миграции — в entrypoint. |
| `scheduler` | Тот же образ; единственный экземпляр авто-backup (ТЗ §36).              |
| `nginx`     | Reverse proxy + TLS-терминация (80/443), ACME-challenge для certbot.     |
| `certbot`   | Выпуск и автопродление сертификатов Let's Encrypt.                       |

Почему backup вынесен в отдельный `scheduler`: при нескольких воркерах `web`
планировщик внутри приложения создавал бы копию в каждом воркере. `web` работает с
`BACKUP_AUTO=false`, авто-backup выполняет один `scheduler`. Ручной backup из UI
(`web`) продолжает работать — оба сервиса пишут в общий каталог `/backups`.

Данные переживают пересборку контейнеров: БД в томе `pgdata`, файлы (фото, логотип,
PDF) в томе `storage`, резервные копии — в каталоге хоста (`BACKUP_PATH`).

---

## 2. Подготовка VPS (Ubuntu 22.04 / 24.04)

Действия по SSH под пользователем с `sudo`.

> 💡 PyCharm: **Tools → Start SSH session…** открывает терминал прямо к VPS.

### 2.1. Пользователь без root

```bash
sudo adduser deploy
sudo usermod -aG sudo deploy
# скопировать свой ключ (с локальной машины):  ssh-copy-id deploy@SERVER_IP
```

### 2.2. SSH-хардненинг

В `/etc/ssh/sshd_config` (или `/etc/ssh/sshd_config.d/99-hardening.conf`):

```text
PermitRootLogin no
PasswordAuthentication no
```

```bash
sudo systemctl restart ssh
```

### 2.3. Firewall (ufw)

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

### 2.4. Docker + compose-plugin

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker deploy          # перелогиниться, чтобы группа применилась
docker --version && docker compose version
```

### 2.5. (Опционально) fail2ban для SSH

```bash
sudo apt-get update && sudo apt-get install -y fail2ban
sudo systemctl enable --now fail2ban
```

---

## 3. Код и конфигурация

### 3.1. Получить код

```bash
sudo mkdir -p /opt/mkl-rent && sudo chown deploy:deploy /opt/mkl-rent
git clone <repo-url> /opt/mkl-rent
cd /opt/mkl-rent
```

### 3.2. Заполнить `.env`

```bash
cp .env.production.example .env
```

Сгенерировать сильные секреты и вписать в `.env` (`APP_SECRET_KEY`, `POSTGRES_PASSWORD`):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # или: openssl rand -base64 36
```

Пока домена нет, оставьте:

```text
APP_ENV=production
APP_BASE_URL=http://SERVER_IP          # временно, до выпуска TLS
APP_ALLOWED_HOSTS=                      # пусто до появления домена
SESSION_COOKIE_SECURE=false            # ОБЯЗАТЕЛЬНО по HTTP: иначе вход/CSRF ломаются
NGINX_CONF=./nginx/app.conf            # HTTP-конфиг
```

> ⚠️ По HTTP сессионная `Secure`-cookie до сервера не доходит, и вход падает с
> «Недействительный CSRF-токен». Поэтому на HTTP-этапе `SESSION_COOKIE_SECURE=false`.
> После включения TLS (§5) верните `SESSION_COOKIE_SECURE=` (авто) или `true`.

> 💡 PyCharm: залить `.env` на сервер удобно через **Tools → Deployment** (SFTP/SSH).
> Не коммитьте `.env` и не держите его в проекте под гитом — только на сервере.

### 3.3. Каталог резервных копий и права

Контейнеры работают под непривилегированным пользователем (UID `1000` по умолчанию).
Каталог для бэкапов на хосте должен быть доступен ему на запись:

```bash
mkdir -p backups
sudo chown -R 1000:1000 backups
```

Если основной пользователь VPS уже имеет `id -u = 1000` (типично для первого пользователя
Ubuntu), каталог и так будет писаться. Иначе задайте UID при сборке образа:
`docker compose build --build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)`.

---

## 4. Первый запуск (HTTP)

```bash
docker compose build
docker compose up -d db
docker compose up -d web scheduler nginx
```

Entrypoint `web` дожидается готовности БД и сам применяет миграции
(`alembic upgrade head`) — отдельный шаг не нужен.

### 4.1. Первый администратор (ТЗ §4)

```bash
docker compose exec web python -m scripts.create_user --username admin
# пароль спросит интерактивно
```

### 4.2. Проверка

```bash
curl -fsS http://SERVER_IP/healthz            # {"status":"ok"}
```

Откройте `http://SERVER_IP/`, войдите под `admin`, проверьте:
вход, генерацию PDF (смета/packing), создание ручного backup в разделе бэкапов.

> 💡 PyCharm: **Services** (плагин Docker) умеет подключаться к Docker-хосту на VPS по SSH —
> видно статусы контейнеров, логи, можно перезапускать сервисы из GUI.

---

## 5. HTTPS (когда домен и DNS готовы)

### 5.1. DNS

Создайте **A-запись** домена на IP VPS и дождитесь распространения:

```bash
dig +short your.domain.com        # должен вернуть IP вашего VPS
```

### 5.2. Выпуск сертификата (webroot через работающий nginx на :80)

`nginx` на этом шаге уже поднят с HTTP-конфигом и отдаёт `/.well-known/acme-challenge/`.

```bash
# --entrypoint certbot обязателен: у сервиса certbot свой entrypoint (цикл renew),
# иначе разовый certonly не выполнится ("No renewals were attempted").
docker compose run --rm --entrypoint certbot certbot certonly \
  --webroot -w /var/www/certbot \
  -d your.domain.com \
  --email you@example.com --agree-tos --no-eff-email
```

Для отладки можно добавить `--staging` (тестовый CA, без лимитов), затем перевыпустить без него.

### 5.3. Включить TLS-конфиг

1. В [nginx/app-ssl.conf](nginx/app-ssl.conf) заменить `example.com` на ваш домен
   (три места: два `server_name` и путь `ssl_certificate`).
2. В `.env`:

   ```text
   APP_BASE_URL=https://your.domain.com
   APP_ALLOWED_HOSTS=your.domain.com
   SESSION_COOKIE_SECURE=            # авто (Secure), можно явно true
   NGINX_CONF=./nginx/app-ssl.conf
   ```

3. Применить:

   ```bash
   docker compose up -d web                        # web перечитает APP_BASE_URL/ALLOWED_HOSTS
   docker compose up -d --force-recreate nginx     # переключить nginx на TLS-конфиг
   ```

   `--force-recreate nginx` важен: если контейнер nginx уже запущен, обычный
   `up -d` может посчитать его актуальным и не пересоздать — тогда останется старый
   HTTP-конфиг. Проверьте `docker compose ps`: у nginx должен обновиться аптайм.

### 5.4. Проверка TLS

```bash
curl -fsS https://your.domain.com/healthz
curl -sI http://your.domain.com | grep -i location          # 301 → https
curl -sI https://your.domain.com | grep -i strict-transport  # HSTS присутствует
```

Продление сертификатов автоматическое: контейнер `certbot` каждые 12 ч выполняет
`certbot renew`, а `nginx` периодически делает `reload`, подхватывая новый сертификат.

---

## 6. Обновление до новой версии

```bash
cd /opt/mkl-rent
git pull
docker compose build
docker compose up -d          # web поднимется с новым образом, миграции — автоматически
docker compose ps
```

Порядок «git pull → build → migrate → up» соблюдается автоматически: миграции
выполняются в entrypoint `web` до старта приложения.

---

## 7. Логи

```bash
docker compose logs -f web            # приложение
docker compose logs -f scheduler      # авто-backup
docker compose logs -f nginx certbot  # прокси и сертификаты
docker compose ps                     # статусы и healthcheck
```

---

## 8. Резервное копирование и восстановление (ТЗ §36)

### 8.1. Что и куда

Авто-backup (сервис `scheduler`) ежедневно в `BACKUP_TIME` собирает архив
`backup_YYYYMMDD_HHMMSS.tar.gz` (дамп PostgreSQL + файлы `storage`) в `BACKUP_PATH`
на хосте и удаляет копии старше `BACKUP_RETENTION_DAYS`. Ручной backup — из UI или:

```bash
docker compose exec web python -m scripts.backup
```

### 8.2. Offsite-копия (вынос за пределы VPS)

Регулярно копируйте архивы на другой хост/хранилище (cron на рабочей машине или NAS):

```bash
rsync -avz deploy@SERVER_IP:/opt/mkl-rent/backups/ /local/mkl-rent-backups/
```

### 8.3. Восстановление (перезаписывает БД и файлы!)

```bash
docker compose stop web scheduler                 # остановить пишущие сервисы
docker compose up -d db                            # БД должна быть поднята
docker compose run --rm -e RUN_MIGRATIONS=false web \
  python -m scripts.restore /backups/backup_YYYYMMDD_HHMMSS.tar.gz --yes
docker compose up -d web scheduler nginx
```

После восстановления при необходимости выполните `docker compose exec web alembic upgrade head`
(если архив старее текущей схемы).

> 💡 PyCharm: **Database tool** можно подключить к PostgreSQL на VPS через SSH-туннель
> (Tools → Deployment / SSH configuration) для инспекции или ручного `pg_dump`.

---

## 9. Откат к предыдущей версии

```bash
cd /opt/mkl-rent
git log --oneline -n 5
git checkout <предыдущий-тег-или-коммит>
docker compose build
docker compose up -d
```

Внимание: если новая версия применила несовместимую миграцию, простой откат кода
может не подойти. Надёжный откат данных — восстановление из backup, снятого перед
обновлением (см. §8.3). Рекомендуется делать backup непосредственно перед `git pull`.

---

## 10. Чек-лист хардненинга (ТЗ §41)

- [x] Контейнеры работают под непривилегированным пользователем (не root).
- [x] БД и `web` не публикуют порты наружу — доступ только через nginx.
- [x] `server_tokens off`, security-заголовки (в приложении), HSTS (в nginx на 443).
- [x] Сессионная cookie `Secure`+`HttpOnly`+`SameSite=Lax` в production.
- [x] `APP_ALLOWED_HOSTS` защищает от подмены Host за прокси.
- [x] Rate-limit входа (ТЗ §41, реализован в приложении).
- [x] Секреты только в `.env` на сервере, вне Git.
- [ ] ufw 22/80/443, SSH по ключу, `PermitRootLogin no` (§2).
- [ ] (Опц.) fail2ban для SSH (§2.5).
- [ ] Offsite-копии бэкапов настроены (§8.2).

---

## 11. Troubleshooting

- **`web` рестартует, в логах «БД не готова»** — проверьте `docker compose logs db`
  и что `POSTGRES_PASSWORD` в `.env` задан (compose требует его явно).
- **nginx не стартует после переключения на TLS** — сертификат ещё не выпущен либо
  домен в `app-ssl.conf` не совпадает с `certbot certonly`. Верните `NGINX_CONF=./nginx/app.conf`.
- **Let's Encrypt не проходит проверку** — DNS A-запись ещё не указывает на VPS, либо
  закрыт порт 80 (ufw). Проверьте `dig +short домен` и `curl http://домен/.well-known/acme-challenge/test`.
- **`Permission denied` при записи backup** — каталог `BACKUP_PATH` на хосте не принадлежит
  UID контейнера: `sudo chown -R 1000:1000 backups` (или пересоберите с `--build-arg APP_UID`).
- **Редиректы/ссылки ведут на http за TLS** — проверьте, что `APP_BASE_URL=https://…` и
  что `web` запущен с `--proxy-headers` (так в compose по умолчанию).
- **«Недействительный CSRF-токен» при входе** — заходите по HTTP с `Secure`-cookie.
  Поставьте `SESSION_COOKIE_SECURE=false` в `.env` и `docker compose up -d web`, либо
  завершите настройку TLS (§5) и заходите по https.
- **`web` рестартует, в логах `password authentication failed` или `failed to resolve
  host`** — пароль БД. Спецсимволы (`@ ! # : /`) в пароле поддерживаются (экранируются
  автоматически). Но `POSTGRES_PASSWORD` применяется к тому `pgdata` только при **первой**
  инициализации: если БД уже создавалась, смена пароля в `.env` не меняет пароль в самой БД.
  Приведите их в соответствие — либо сменив пароль в БД:
  `docker compose exec db psql -U rental -d rental -c "ALTER USER rental PASSWORD 'НОВЫЙ';"`
  (и то же значение в `.env`), либо, если данными можно пожертвовать, пересоздав том:
  `docker compose down && docker volume rm <проект>_pgdata && docker compose up -d`.
- **certbot пишет «No renewals were attempted» вместо выпуска** — забыт `--entrypoint
  certbot` в разовой команде выпуска (см. §5.2); без него запускается цикл продления.
