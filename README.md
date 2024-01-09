# Buff

## Что такое и как работает

Сервис, повторяющий функциональность почившего <chgk.bodrovis.tech>: по введённому ID на [турнирном сайте](https://rating.chgk.info) показывает, с кем этот игрок чаще всего играл, также можно посмотреть совместные игры.

Чтобы быстро работало, зеркалит в sqlite базу турнирного сайта, выкачивая её по апи.

Сервис поднят на <buff.pecheny.me>.

## Как поднять самостоятельно

0\. Установить зависимости: `pip install requests dill networkx flask uwsgi`

1\. Скопировать `config_example.py` в `config.py`, указать настоящее секретное значение (например, через `python -c 'import uuid; print(uuid.uuid4())'`)

2\. Разово запустить `db_updater.py`, дождаться, пока он выкачает всю базу с нуля (это занимает порядка 5–6 часов)

3\. Создать `/etc/systemd/system/buff.service`, заменив значения в `{{}}` на нужные:

```
[Unit]
Description=uWSGI instance to serve buff
After=network.target

[Service]
User=ap
Group=www-data
WorkingDirectory={{path/to/buff}}
Environment="PATH={{path/to/python/bin}}"
ExecStart={{path/to/python/bin}}/uwsgi --ini app.ini --logto /tmp/buff.log
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

Запустить сервис: `sudo systemctl enable buff && sudo systemctl start buff`

4\. Настроить, чтобы DNS вашего сайта `your.domain.com` смотрел на IP сервера.

5\. Создать конфиг в `/etc/nginx/sites-available/buff`:

```
server {
    server_name {{your.domain.com}} {{your.domain.com}};

    location / {
        include uwsgi_params;
        uwsgi_pass unix:{{path/to/buff}}/app.sock;
    }

    listen 80;
}
```

Засимлинкать конфиг в `/etc/nginx/sites-enabled`: `sudo ln -s /etc/nginx/sites-available/buff /etc/nginx/sites-enabled`

Перезапустить nginx: `sudo systemctl restart nginx`

6\. Запустить certbot: `sudo certbot --nginx -d {{your.domain.com}}`

Снова перезапустить nginx: `sudo systemctl restart nginx`

После этого сервис должен быть доступен по `https://your.domain.com`

7\. Настроить еженочное обновление базы: `crontab -e` и добавляем строчку `5 1 * * * {{path/to/python}} {{path/to/buff}}/update_db.py`
