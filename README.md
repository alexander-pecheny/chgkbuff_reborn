> **Основной репозиторий этого проекта — https://code.pecheny.me/pecheny/chgkbuff_reborn, пожалуйста, создавайте issues там.**

# Buff

## Что такое и как работает

Сервис, повторяющий функциональность почившего [chgk.bodrovis.tech](https://chgk.bodrovis.tech): по введённому ID на [турнирном сайте](https://rating.chgk.info) показывает, с кем этот игрок чаще всего играл, также можно посмотреть совместные игры.

Чтобы быстро работало, зеркалит в sqlite базу турнирного сайта, выкачивая её по апи.

Сервис поднят на [buff.pecheny.me](https://buff.pecheny.me).

Использован CSS из проекта [water.css](https://github.com/kognise/water.css) (fun fact: авторке проекта на момент создания было что-то типа 14 лет!)

## Из чего состоит

Веб-сервер написан на Go (`cmd/buff`), обновление базы — на Python (`update_db.py`, `create_graph.py`): оно ходит по апи раз в сутки, памяти ему не жалко, а поведение апи оно знает во всех подробностях. Почему так — в [docs/adr](docs/adr).

`create_graph.py` собирает `graph.bin` — упакованные списки соседей для поиска рукопожатий, сервер отображает этот файл в память и не держит граф в куче.

Термины предметной области — в [CONTEXT.md](CONTEXT.md).

## Как поднять самостоятельно

0\. Установить зависимости для обновлятора: `pip install requests`

1\. Разово запустить `update_db.py`, дождаться, пока он выкачает всю базу с нуля (это занимает порядка 5–6 часов), затем `create_graph.py`

2\. Собрать сервер и положить его на сервер:

```
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o buff ./cmd/buff
scp buff {{server}}:{{path/to/buff}}/buff
```

Бинарник статический, шаблоны и стили лежат внутри него — на сервере не нужны ни Go, ни Python для веба.

3\. Создать `/etc/systemd/system/buff.service`, заменив значения в `{{}}` на нужные:

```
[Unit]
Description=Buff
After=network.target

[Service]
User=ap
WorkingDirectory={{path/to/buff}}
ExecStart={{path/to/buff}}/buff -dir {{path/to/buff}} -addr 127.0.0.1:8080
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

Запустить сервис: `sudo systemctl enable buff && sudo systemctl start buff`

4\. Настроить, чтобы DNS вашего сайта `your.domain.com` смотрел на IP сервера.

5\. Добавить в `Caddyfile`:

```
your.domain.com {
    reverse_proxy 127.0.0.1:8080
}
```

Перезагрузить конфиг: `sudo systemctl reload caddy`. Сертификат Caddy получит сам.

6\. Настроить еженочное обновление базы: `crontab -e` и добавляем строчку

```
5 1 * * * cd {{path/to/buff}} && python update_db.py && python create_graph.py && sudo systemctl restart buff
```

Рестарт обязателен: сервер держит `graph.bin` отображённым в память и не увидит новый файл сам.

## Разработка

Тесты сверяют ответы Go-версии с тем, что отвечал Flask: `go test ./...`. Ожидания лежат в `testdata/` и снимаются с работающего Flask-приложения через `gen_fixtures.py`. Тесты, которым нужны `buff.db` или `graph.bin`, пропускаются, если этих файлов нет.

`flask_app.py` — уходящая реализация веба, она остаётся до переключения продакшена на Go.
