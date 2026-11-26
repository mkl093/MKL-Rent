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
| `nginx`     | Reverse proxy + TLS-терминация (80/443), gzip, ACME-challenge для certbot. |
| `certbot`   | Выпуск и автопродление сертификатов Let's Encrypt.                       |

Почему backup вынесен в отдельный `scheduler`: при нескольких воркерах `web`
планировщик внутри приложения создавал бы копию в каждом воркере. `web` работает с
`BACKUP_AUTO=false`, авто-backup выполняет один `scheduler`. Ручной backup из UI
(`web`) продолжает работать — оба сервиса пишут в общий каталог `/backups`.

Данные переживают пересборку контейнеров: БД в томе `pgdata`, файлы (фото и их
миниатюры, логотип, PDF) в томе `storage`, резервные копии — в каталоге хоста (`BACKUP_PATH`).

Кеширование и сжатие (ускорение страниц со списками):

- nginx сжимает gzip HTML, CSS, JS, JSON и SVG (шаблоны `nginx/*.conf.template`);
- статика из `static_url()` (ссылка с `?v=<mtime>`) отдаётся с
  `Cache-Control: public, max-age=31536000, immutable` — после деплоя изменённый файл
  получает новый URL, сбрасывать кеш браузера не нужно;
- фото моделей и логотип (`/media/models/…`, `/media/logo/…`, миниатюры
  `/media/thumb/…`) — `private, max-age=31536000, immutable`: имена уникальны (uuid) и
  не переиспользуются; `private` — общие прокси/CDN их не кешируют;
- остальные файлы `/media` (документы и т. п.) — `private, no-cache`.

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

> ⚠️ Указывайте целевой каталог явно (`git clone <repo-url> /opt/mkl-rent`), а не
> просто `git clone <repo-url>` из `/opt/mkl-rent` — иначе git создаст вложенный
> `/opt/mkl-rent/MKL-Rent` (по имени репозитория), и все пути ниже (`BACKUP_PATH`,
> `docker compose` и т.д.) сместятся на один уровень. Если каталог уже склонирован
> с вложенностью — команды из этого документа выполняйте из фактического каталога
> с `docker-compose.yml` (`cd /opt/mkl-rent/MKL-Rent` или где он на самом деле лежит),
> переносить его необязательно.

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
NGINX_TEMPLATE=./nginx/http.conf.template   # HTTP-шаблон nginx
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

Домен подставляется в конфиг nginx из переменной `SERVER_NAME` (envsubst) — файлы
в `nginx/` править не нужно. В `.env`:

   ```text
   APP_BASE_URL=https://your.domain.com
   APP_ALLOWED_HOSTS=your.domain.com
   SESSION_COOKIE_SECURE=                       # авто (Secure), можно явно true
   SERVER_NAME=your.domain.com                  # server_name + путь к сертификату
   NGINX_TEMPLATE=./nginx/tls.conf.template     # переключить nginx на TLS-шаблон
   ```

Применить:

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

> **Однократно после обновления шаблонов nginx** (динамический резолвинг апстрима):
> `docker compose up -d --force-recreate nginx`. Конфиг `nginx/*.template` теперь
> резолвит `web` через Docker DNS (`resolver 127.0.0.11`), поэтому при последующих
> пересозданиях `web` nginx подхватывает новый IP автоматически (без 502). Раньше
> закешированный при старте IP давал 502 после каждого `up -d` — лечилось
> `docker compose restart nginx`.

> **Однократно после обновления с миниатюрами фото и gzip** (ускорение списка склада):
>
> ```bash
> docker compose up -d --force-recreate nginx          # включить gzip из шаблона nginx
> docker compose exec web python -m scripts.make_thumbnails
> ```
>
> Скрипт создаёт миниатюры `storage/models/<имя>.thumb.jpg` для уже загруженных фото
> (повторный запуск безопасен). Без него миниатюры появятся при первом открытии
> списков, но первая загрузка склада будет медленной. Новые фото получают миниатюру
> сразу при загрузке.

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
(если архив старее текущей схемы). Если архив снят до появления миниатюр фото —
`docker compose exec web python -m scripts.make_thumbnails` (иначе первое открытие склада
будет медленным, пока миниатюры создаются по запросу).

> ⚠️ Если `pg_restore` падает с `unrecognized configuration parameter
> "transaction_timeout"` — версия `pg_restore` в образе `web` новее сервера `db`
> (эта GUC появилась в PostgreSQL 17). На актуальном образе клиент закреплён на
> версии сервера (см. [Dockerfile](Dockerfile)); если ошибка всё же возникла —
> пересоберите образ (`docker compose build web scheduler`) и повторите restore.
> Обычно это единственная ошибка на весь дамп (`errors ignored on restore: 1`),
> но скрипт `scripts.restore` прерывается до восстановления файлов `storage/` —
> после исправления клиента прогоните restore заново целиком, не только БД.

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

## 10. Переезд на другой VPS

Переносится через штатный backup: архив содержит дамп PostgreSQL и все файлы `storage`
(фото, мануалы, сертификаты). Отдельно переносятся `.env` и, при желании, старые
бэкапы; сертификат проще выпустить заново. Данные не теряются, если финальный backup
снят **после остановки** `web` и `scheduler` на старом сервере. Простой — от остановки
старого сервера до переключения DNS, обычно 10–30 минут.

### 10.1. Заранее

- Понизьте TTL A-записи домена до 300 с (за сутки), чтобы DNS переключился быстро.
- Запомните версию кода на старом сервере — на новом нужна та же (схема БД = дамп):

```bash
cd /opt/mkl-rent && git rev-parse HEAD
```

### 10.2. Подготовить новый VPS

Выполните §2–§3.1 и §3.3, затем переключитесь на тот же коммит и скопируйте `.env`
со старого сервера (обязательно с тем же `APP_SECRET_KEY` — иначе сбросятся все сессии):

```bash
git checkout <коммит-со-старого-сервера>
scp deploy@OLD_IP:/opt/mkl-rent/.env /opt/mkl-rent/.env
```

На время проверки по IP переведите `.env` в HTTP-режим, как в §3.2:
`APP_BASE_URL=http://NEW_IP`, пустой `APP_ALLOWED_HOSTS=`, `SESSION_COOKIE_SECURE=false`,
`NGINX_TEMPLATE=./nginx/http.conf.template`.

> 💡 Сначала сделайте **пробный прогон**: свежий backup со старого сервера (он
> продолжает работать) → перенос → восстановление (§10.3–§10.4) → проверка. Проблемы
> всплывут до настоящего переезда; финальный прогон затем перезапишет данные.

### 10.3. Финальный backup на старом VPS

Остановить запись и снять backup — после этого изменений не будет:

```bash
docker compose stop web scheduler
docker compose run --rm -e RUN_MIGRATIONS=false web python -m scripts.backup
ls -lh backups/ | tail -3
```

Перенести архив на новый сервер (или весь каталог `backups/`, если нужна история)
и сверить контрольную сумму на обоих серверах:

```bash
rsync -avz --progress deploy@OLD_IP:/opt/mkl-rent/backups/backup_YYYYMMDD_HHMMSS.tar.gz /opt/mkl-rent/backups/
sha256sum backups/backup_YYYYMMDD_HHMMSS.tar.gz
```

### 10.4. Восстановление на новом VPS

```bash
docker compose build
docker compose up -d db
docker compose run --rm -e RUN_MIGRATIONS=false web \
  python -m scripts.restore /backups/backup_YYYYMMDD_HHMMSS.tar.gz --yes
docker compose up -d web scheduler nginx
docker compose exec web python -m scripts.make_thumbnails
```

Проверьте `http://NEW_IP/`: вход, проекты и сметы, фото и мануалы, генерация PDF,
ручной backup. Ошибка `pg_restore` про `transaction_timeout` — см. §8.3.

### 10.5. Переключить домен

1. Смените A-запись домена на новый IP и дождитесь: `dig +short your.domain.com`.
2. Выпустите сертификат и включите TLS на новом сервере — §5.2–§5.4 (верните в `.env`
   домен, `APP_ALLOWED_HOSTS`, `SESSION_COOKIE_SECURE`, `SERVER_NAME`, TLS-шаблон).

### 10.6. После переезда

- Старый VPS не удаляйте хотя бы неделю — держите остановленным (`docker compose stop`)
  как готовый откат.
- Перенастройте offsite-копирование (§8.2) на новый IP.
- На следующий день проверьте авто-backup: `docker compose logs scheduler`.

---

## 11. Чек-лист хардненинга (ТЗ §41)

- [x] Контейнеры работают под непривилегированным пользователем (не root).
- [x] БД и `web` не публикуют порты наружу — доступ только через nginx.
- [x] `server_tokens off`, security-заголовки (в приложении), HSTS (в nginx на 443).
- [x] Сессионная cookie `Secure`+`HttpOnly`+`SameSite=Lax` в production.
- [x] gzip для HTML (условие BREACH: CSRF-токен + отражённый поиск) допустим благодаря
      `SameSite=Lax`: сторонний сайт не может массово слать запросы с сессией. Если нужен
      максимально осторожный вариант — уберите HTML из сжатия (`gzip off` в
      `location /` и сжатие только в `location /static/`).
- [x] Приватные файлы `/media` кешируются только в браузере (`Cache-Control: private`).
- [x] `APP_ALLOWED_HOSTS` защищает от подмены Host за прокси.
- [x] Rate-limit входа (ТЗ §41, реализован в приложении).
- [x] Секреты только в `.env` на сервере, вне Git.
- [ ] ufw 22/80/443, SSH по ключу, `PermitRootLogin no` (§2).
- [ ] (Опц.) fail2ban для SSH (§2.5).
- [ ] Offsite-копии бэкапов настроены (§8.2).

---

## 12. Troubleshooting

- **`web` рестартует, в логах «БД не готова»** — проверьте `docker compose logs db`
  и что `POSTGRES_PASSWORD` в `.env` задан (compose требует его явно).
- **nginx не стартует после переключения на TLS** — сертификат ещё не выпущен либо
  `SERVER_NAME` не совпадает с доменом из `certbot certonly` (путь
  `/etc/letsencrypt/live/$SERVER_NAME/` не найден). Временно верните
  `NGINX_TEMPLATE=./nginx/http.conf.template` и `docker compose up -d --force-recreate nginx`.
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
- **Склад медленно открывается первый раз после деплоя/restore** — не созданы миниатюры
  фото: `docker compose exec web python -m scripts.make_thumbnails`. Скрипт печатает
  фото, для которых миниатюру сделать не удалось (файл отсутствует или повреждён) — в
  списке для них показывается исходное фото, если оно есть.
- **Ответы не сжаты (`Content-Encoding` нет)** — nginx запущен со старым конфигом:
  `docker compose up -d --force-recreate nginx`. Проверка:
  `curl -sI -H "Accept-Encoding: gzip" https://домен/static/vendor/bootstrap.min.css`
  должен вернуть `Content-Encoding: gzip`.
- **nginx не стартует после обновления шаблонов** — `docker compose logs nginx` покажет
  строку конфига с ошибкой; проверить конфиг без перезапуска:
  `docker compose exec nginx nginx -t`.
