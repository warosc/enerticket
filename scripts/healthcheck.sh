#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Energuate Service Desk — comprobación de salud del despliegue.
#
# Revisa:
#   1. Contenedores de larga duración: en ejecución y "healthy" (si tienen healthcheck).
#   2. One-shots (keycloak-config, minio-init): última ejecución con código 0.
#   3. Endpoints públicos a través del proxy (TLS verificado):
#        /            portal web
#        /api/health  API + base de datos
#        /auth/realms/<realm>/.well-known/openid-configuration  Keycloak (issuer correcto)
#   4. Antigüedad de la última copia de seguridad y espacio libre en disco.
#
# Uso:
#   scripts/healthcheck.sh                     # producción (APP_DOMAIN desde .env)
#   scripts/healthcheck.sh --url https://servicedesk.energuate.com
#   scripts/healthcheck.sh --cacert /ruta/ca-corporativa.pem   # CA interna
#   SD_COMPOSE_FILE=docker-compose.yml scripts/healthcheck.sh --lab
#
# Salida: una línea por comprobación (OK/WARN/FAIL). Código 0 = todo OK,
# 1 = algún FAIL, 2 = solo advertencias. Apto para cron/monitorización.
# -----------------------------------------------------------------------------
set -uo pipefail

# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(dirname "$0")/lib.sh"

base_url=""
cacert=""
lab=false
max_backup_age_hours=${MAX_BACKUP_AGE_HOURS:-26}
min_free_pct=${MIN_FREE_DISK_PCT:-15}

while [ $# -gt 0 ]; do
    case $1 in
        --url) base_url=${2:-}; shift 2 ;;
        --cacert) cacert=${2:-}; shift 2 ;;
        --lab) lab=true; shift ;;
        -h | --help) sed -n '2,22p' "$0"; exit 0 ;;
        *) die "opción desconocida: $1" ;;
    esac
done

check_compose_ready
require_cmd curl

fails=0
warns=0
ok() { printf 'OK    %s\n' "$*"; }
warn_check() { printf 'WARN  %s\n' "$*"; warns=$((warns + 1)); }
fail() { printf 'FAIL  %s\n' "$*"; fails=$((fails + 1)); }

realm=$(env_get KC_REALM energuate)

# --- 1 y 2. contenedores ------------------------------------------------------------
oneshots=" keycloak-config minio-init "
services=$(compose config --services 2>/dev/null) || { fail "docker compose config falló"; services=""; }
for svc in $services; do
    cid=$(compose ps -a -q "$svc" 2>/dev/null | head -n 1)
    if [ -z "$cid" ]; then
        fail "$svc: sin contenedor (¿docker compose up -d?)"
        continue
    fi
    state=$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null || echo desconocido)
    if [[ $oneshots == *" $svc "* ]]; then
        code=$(docker inspect -f '{{.State.ExitCode}}' "$cid" 2>/dev/null || echo "?")
        if [ "$state" = running ]; then
            ok "$svc: en ejecución (one-shot)"
        elif [ "$state" = exited ] && [ "$code" = 0 ]; then
            ok "$svc: completado"
        else
            fail "$svc: estado $state, código $code (docker compose logs $svc)"
        fi
        continue
    fi
    health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}sin-healthcheck{{end}}' "$cid" 2>/dev/null)
    restarts=$(docker inspect -f '{{.RestartCount}}' "$cid" 2>/dev/null || echo 0)
    if [ "$state" != running ]; then
        fail "$svc: estado $state"
    elif [ "$health" = healthy ] || [ "$health" = sin-healthcheck ]; then
        if [ "${restarts:-0}" -gt 3 ]; then
            warn_check "$svc: en ejecución pero con $restarts reinicios"
        else
            ok "$svc: $state ($health)"
        fi
    elif [ "$health" = starting ]; then
        warn_check "$svc: arrancando"
    else
        fail "$svc: $health (docker compose logs $svc)"
    fi
done

# --- 3. endpoints -----------------------------------------------------------------
curl_opts=(-sS -o /dev/null -w '%{http_code}' --max-time 10)
[ -n "$cacert" ] && curl_opts+=(--cacert "$cacert")

http_check() {
    local name=$1 url=$2 expected=${3:-200} code
    code=$(curl "${curl_opts[@]}" "$url" 2>/dev/null) || code=000
    if [ "$code" = "$expected" ]; then
        ok "$name: HTTP $code ($url)"
    else
        fail "$name: HTTP $code, se esperaba $expected ($url)"
    fi
}

if [ "$lab" = true ]; then
    http_check "web" "http://127.0.0.1:3000/healthz"
    http_check "api" "http://127.0.0.1:8000/health"
    http_check "keycloak" "http://127.0.0.1:8081/realms/$realm/.well-known/openid-configuration"
else
    if [ -z "$base_url" ]; then
        domain=$(env_get APP_DOMAIN "")
        [ -n "$domain" ] || die "APP_DOMAIN no está definido en $SD_ENV_FILE (o use --url)"
        base_url="https://$domain"
    fi
    base_url=${base_url%/}
    http_check "web" "$base_url/"
    http_check "api" "$base_url/api/health"
    discovery_url="$base_url/auth/realms/$realm/.well-known/openid-configuration"
    discovery=$(curl -sS --max-time 10 ${cacert:+--cacert "$cacert"} "$discovery_url" 2>/dev/null || true)
    expected_issuer="$base_url/auth/realms/$realm"
    if printf '%s' "$discovery" | grep -q "\"issuer\":\"$expected_issuer\""; then
        ok "keycloak: issuer $expected_issuer"
    else
        fail "keycloak: discovery no disponible o issuer distinto de $expected_issuer ($discovery_url)"
    fi
    code=$(curl "${curl_opts[@]}" "$base_url/auth/admin/" 2>/dev/null) || code=000
    if [ "$code" = 403 ]; then
        ok "keycloak: consola de administración bloqueada desde esta IP (403)"
    else
        warn_check "keycloak: /auth/admin/ respondió $code desde esta IP (revise ADMIN_ALLOWED_CIDRS)"
    fi
    # Certificado: días hasta el vencimiento.
    if command -v openssl >/dev/null 2>&1; then
        host=${base_url#https://}
        host=${host%%/*}
        end=$(echo | openssl s_client -servername "${host%%:*}" -connect "$host$([[ $host == *:* ]] || echo :443)" 2>/dev/null |
            openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
        if [ -n "$end" ]; then
            end_s=$(date -d "$end" +%s 2>/dev/null || echo 0)
            days=$(((end_s - $(date +%s)) / 86400))
            if [ "$end_s" = 0 ]; then
                warn_check "tls: no se pudo interpretar la fecha de vencimiento ($end)"
            elif [ "$days" -lt 14 ]; then
                fail "tls: el certificado vence en $days días ($end)"
            elif [ "$days" -lt 30 ]; then
                warn_check "tls: el certificado vence en $days días ($end)"
            else
                ok "tls: certificado válido $days días más"
            fi
        else
            warn_check "tls: no se pudo leer el certificado de $host"
        fi
    fi
fi

# --- 4. copias y disco -------------------------------------------------------------
backup_dir=${BACKUP_DIR:-$(env_get BACKUP_DIR /var/backups/energuate-sd)}
if [ -d "$backup_dir" ]; then
    latest=$(find "$backup_dir" -mindepth 1 -maxdepth 1 -type d -name '20*' ! -name '*.failed' -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -n 1)
    if [ -z "$latest" ]; then
        warn_check "backup: no hay copias en $backup_dir"
    else
        age_h=$((($(date +%s) - ${latest%%.*}) / 3600))
        if [ "$age_h" -gt "$max_backup_age_hours" ]; then
            fail "backup: la última copia tiene ${age_h} h (${latest#* })"
        else
            ok "backup: última copia hace ${age_h} h (${latest#* })"
        fi
    fi
    used=$(df -P "$backup_dir" | awk 'NR==2 {gsub("%","",$5); print $5}')
    if [ -n "$used" ]; then
        if [ $((100 - used)) -lt "$min_free_pct" ]; then fail "disco: ${used}% usado en $backup_dir"; else ok "disco: ${used}% usado en $backup_dir"; fi
    fi
else
    warn_check "backup: no existe $backup_dir (¿se programó scripts/backup.sh?)"
fi
docker_root=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
if [ -d "$docker_root" ]; then
    used=$(df -P "$docker_root" | awk 'NR==2 {gsub("%","",$5); print $5}')
    if [ -n "$used" ]; then
        if [ $((100 - used)) -lt "$min_free_pct" ]; then fail "disco: ${used}% usado en $docker_root"; else ok "disco: ${used}% usado en $docker_root"; fi
    fi
fi

if [ "$fails" -gt 0 ]; then
    printf 'RESULTADO: %d fallo(s), %d advertencia(s)\n' "$fails" "$warns"
    exit 1
elif [ "$warns" -gt 0 ]; then
    printf 'RESULTADO: OK con %d advertencia(s)\n' "$warns"
    exit 2
fi
printf 'RESULTADO: OK\n'
