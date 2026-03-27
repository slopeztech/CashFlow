# CashFlow Installation Recipe (Debian/Ubuntu SBC)

## 1. Base System Preparation

```bash
sudo apt update
sudo apt upgrade

sudo hostnamectl set-hostname cashflow
sudo nano /etc/hosts
```

Set hosts entries:

```text
127.0.0.1 localhost
127.0.1.1 cashflow
```

Reboot:

```bash
sudo reboot
```

## 2. Install Required Packages

```bash
sudo apt install -y git nginx avahi-daemon
sudo apt install -y python3 python3-pip python3-venv
sudo apt install -y python3-dev build-essential libffi-dev
sudo apt install -y libjpeg-dev zlib1g-dev libpng-dev libfreetype6-dev liblcms2-dev libopenjp2-7-dev libtiff-dev tk-dev tcl-dev build-essential
sudo apt install -y python3-cryptography python3-openssl pkg-config libssl-dev cargo
```

Enable Avahi:

```bash
sudo systemctl enable avahi-daemon
sudo systemctl start avahi-daemon
```

At this point, you should be able to access the server as `cashflow.local` on your local network.
Change default credentials.

## 3. Clone and Set Up CashFlow

From your user home directory:

```bash
git clone https://github.com/slopeztech/CashFlow
cd CashFlow
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py createsuperuser
```

Optional quick test:

```bash
python manage.py runserver 0.0.0.0:8000
```

## 4. Static Files and Gunicorn Test

```bash
python manage.py collectstatic
```

Gunicorn test commands:

```bash
gunicorn --bind 0.0.0.0:8000 CashFlow.wsgi:application
gunicorn --bind 0.0.0.0:8000 CashFlow.asgi:application -k uvicorn.workers.UvicornWorker
```

If it does not work and you are using RISC-V64, create `.env` and add:

```env
ENABLE_REALTIME=0
```

Then run:

```bash
python manage.py collectstatic --noinput
```

## 5. Local SSL Certificate

```bash
mkdir ~/certs
cd ~/certs

openssl genrsa -out server.key 2048
openssl req -new -x509 -key server.key -out server.crt -days 99999
```

SSL test from the CashFlow folder:

```bash
gunicorn CashFlow.asgi:application \
    --bind 0.0.0.0:8000 \
    --certfile ~/certs/server.crt \
    --keyfile ~/certs/server.key
```

It is normal for the browser to warn that this certificate is not trusted.

## 6. Production `.env` Notes

In `.env`:

```env
WHITENOISE_MANIFEST_STRICT=0
WHITENOISE_USE_MANIFEST=0
ENABLE_REALTIME=0
SERVE_MEDIA_WITH_DJANGO=1
MEDIA_URL=/media/
MEDIA_ROOT=/home/orangepi/CashFlow/media
AUTO_COMPILE_LOCALES=1
```

At this point, this should work:

```bash
gunicorn CashFlow.asgi:application --bind 0.0.0.0:8000 --certfile ~/certs/server.crt --keyfile ~/certs/server.key
```

## 7. Nginx Reverse Proxy (Required)

Create an Nginx site file (for example in `/etc/nginx/sites-available/cashflow`) with this content:

```nginx
server {
    listen 8443 ssl;
    server_name cashflow.local;

    ssl_certificate     /home/orangepi/certs/server.crt;
    ssl_certificate_key /home/orangepi/certs/server.key;

    access_log /var/log/nginx/cashflow_access.log;
    error_log  /var/log/nginx/cashflow_error.log debug;

    location /media/ {
        alias /home/orangepi/CashFlow/media/;
    }

    location /static/ {
        alias /home/orangepi/CashFlow/staticfiles/;
    }

    location / {
        proxy_pass https://127.0.0.1:8000;
        proxy_ssl_verify off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto https;

        # Avoid timeouts on long backend operations.
        proxy_connect_timeout 120s;
        proxy_send_timeout 1800s;
        proxy_read_timeout 1800s;
        send_timeout 1800s;
    }
}
```

Enable and reload Nginx:

```bash
sudo ln -s /etc/nginx/sites-available/cashflow /etc/nginx/sites-enabled/cashflow
sudo nginx -t
sudo systemctl reload nginx
```

## 8. Required Permissions for `media` and `staticfiles`

Run these commands:

```bash
sudo chmod o+rx /home/orangepi
sudo chmod o+rx /home/orangepi/CashFlow
sudo chmod -R o+rx /home/orangepi/CashFlow/staticfiles
sudo chmod -R o+rx /home/orangepi/CashFlow/media
```

## 9. Create systemd Service

```bash
sudo nano /etc/systemd/system/cashflow.service
```

Example service file:

```ini
[Unit]
Description=Gunicorn ASGI server for CashFlow
After=network.target

[Service]
User=orangepi
Group=orangepi
WorkingDirectory=/home/orangepi/CashFlow
Environment="PATH=/home/orangepi/CashFlow/venv/bin"
ExecStart=/home/orangepi/CashFlow/venv/bin/gunicorn \
    CashFlow.asgi:application \
    --bind 0.0.0.0:8000 \
    --certfile /home/orangepi/certs/server.crt \
    --keyfile /home/orangepi/certs/server.key \
    --workers 3
Restart=always

[Install]
WantedBy=multi-user.target
```

## 10. Enable and Operate the Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable cashflow.service
sudo systemctl start cashflow.service
```

Check status:

```bash
sudo systemctl status cashflow.service
```

Follow logs in real time:

```bash
journalctl -u cashflow.service -f
```

Restart after `git pull`:

```bash
sudo systemctl restart cashflow.service
```

## 11. Allow Non-Interactive Service Restart for In-App Updates

If updates are triggered from the admin panel, the app may need to restart `cashflow.service`
without interactive password prompts.

Open sudoers with `visudo`:

```bash
sudo visudo -f /etc/sudoers.d/cashflow-update
```

Add this line (adjust username if needed):

```sudoers
orangepi ALL=(root) NOPASSWD: /usr/bin/systemctl restart cashflow.service, /usr/bin/systemctl is-active cashflow.service, /usr/bin/systemctl daemon-reload
```

This allows only the required `systemctl` operations for CashFlow updates.

Apply safe permissions and verify:

```bash
sudo chown root:root /etc/sudoers.d/cashflow-update
sudo chmod 440 /etc/sudoers.d/cashflow-update
sudo visudo -c
sudo -l -U orangepi
sudo -n /usr/bin/systemctl is-active cashflow.service
```

If `sudo -n` asks for password, the rule is not being applied (wrong username, wrong path, or overridden by another sudoers rule).

Troubleshooting checklist for `sudo: a password is required`:

```bash
# 1) Ensure the file name loads late (to avoid PASSWD override by later files)
sudo mv /etc/sudoers.d/cashflow-update /etc/sudoers.d/zz-cashflow-update

# 2) Re-validate syntax and effective rules
sudo visudo -c
sudo -l -U orangepi

# 3) Test as orangepi (non-interactive)
sudo -u orangepi -H sh -c 'sudo -n /usr/bin/systemctl is-active cashflow.service'
sudo -u orangepi -H sh -c 'sudo -n /usr/bin/systemctl restart cashflow.service'
```

If your distro uses a different path, include both paths in sudoers:

```sudoers
orangepi ALL=(root) NOPASSWD: /usr/bin/systemctl restart cashflow.service, /usr/bin/systemctl is-active cashflow.service, /usr/bin/systemctl daemon-reload, /bin/systemctl restart cashflow.service, /bin/systemctl is-active cashflow.service, /bin/systemctl daemon-reload
```
