#!/bin/bash
set -e

NGINX_CONF='/opt/FileMaker/FileMaker Server/NginxServer/conf/fms_nginx.conf'

echo '[*] Fixing variables in fms_nginx.conf...'
python3 -c '
conf_path = "/opt/FileMaker/FileMaker Server/NginxServer/conf/fms_nginx.conf"
with open(conf_path, "r") as f:
    text = f.read()

text = text.replace("\$host", "$host")
text = text.replace("\$proxy_add_x_forwarded_for", "$proxy_add_x_forwarded_for")

with open(conf_path, "w") as f:
    f.write(text)
print("Variables cleaned up successfully.")
'

NGINX_PID=$(pgrep -o -f 'nginx: master' || true)
if [ -n "$NGINX_PID" ]; then
    echo "[*] Gracefully reloading Nginx (PID: $NGINX_PID)..."
    kill -HUP "$NGINX_PID"
    echo '[+] Nginx reloaded successfully!'
else
    echo '[-] Nginx master process not found!'
    exit 1
fi

echo ''
echo '================================================================='
echo '[+] SUCCESS! https://fms-a.threegeneration.org/line-webhook is ready!'
echo '================================================================='
