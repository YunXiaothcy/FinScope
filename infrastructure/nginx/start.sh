#!/bin/sh
set -eu
umask 077
mkdir -p /tmp/nginx
envsubst '${FINSCOPE_DOMAIN} ${FINSCOPE_PUBLIC_ORIGIN} ${FINSCOPE_HTTP_LISTEN} ${FINSCOPE_HTTPS_LISTEN} ${FINSCOPE_HEALTH_LISTEN} ${FINSCOPE_UPSTREAM} ${FINSCOPE_MIME_FILE} ${FINSCOPE_WEB_ROOT} ${FINSCOPE_TLS_CERT} ${FINSCOPE_TLS_KEY} ${FINSCOPE_NGINX_PID} ${FINSCOPE_NGINX_ERROR_LOG} ${FINSCOPE_NGINX_TEMP}' < /opt/finscope/nginx.conf.template > /tmp/nginx.conf
nginx -e /dev/stderr -t -c /tmp/nginx.conf
exec nginx -e /dev/stderr -c /tmp/nginx.conf -g 'daemon off;'
