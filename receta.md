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
```

At this point, this should work:

```bash
gunicorn CashFlow.asgi:application --bind 0.0.0.0:8000 --certfile ~/certs/server.crt --keyfile ~/certs/server.key
```

## 7. Create systemd Service

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

## 8. Enable and Operate the Service

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
