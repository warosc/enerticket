# shellcheck shell=bash
# -----------------------------------------------------------------------------
# Funciones comunes de los scripts de operación (backup, restore, healthcheck).
# Se incluye con:  . "$(dirname "$0")/lib.sh"
#
# Variables reconocidas:
#   SD_ROOT          raíz del repositorio (por defecto, la carpeta padre de scripts/)
#   SD_COMPOSE_FILE  archivo compose (por defecto docker-compose.prod.yml)
#   SD_ENV_FILE      archivo de variables (por defecto .env)
# -----------------------------------------------------------------------------

SD_ROOT=${SD_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
SD_COMPOSE_FILE=${SD_COMPOSE_FILE:-docker-compose.prod.yml}
SD_ENV_FILE=${SD_ENV_FILE:-.env}

case $SD_COMPOSE_FILE in /*) ;; *) SD_COMPOSE_FILE="$SD_ROOT/$SD_COMPOSE_FILE" ;; esac
case $SD_ENV_FILE in /*) ;; *) SD_ENV_FILE="$SD_ROOT/$SD_ENV_FILE" ;; esac

SD_SCRIPT_NAME=$(basename "$0" .sh)

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SD_SCRIPT_NAME" "$*" >&2; }
warn() { log "ADVERTENCIA: $*"; }
die() { log "ERROR: $*"; exit 1; }

require_cmd() {
    local bin
    for bin in "$@"; do
        command -v "$bin" >/dev/null 2>&1 || die "falta el comando '$bin'"
    done
}

# Lee una variable del archivo .env sin ejecutarlo (no se hace "source" de
# secretos). Quita comillas simples o dobles envolventes.
env_get() {
    local key=$1 default=${2:-} line value
    if [ -f "$SD_ENV_FILE" ]; then
        line=$(grep -E "^[[:space:]]*${key}=" "$SD_ENV_FILE" | tail -n 1 || true)
        if [ -n "$line" ]; then
            value=${line#*=}
            value=${value%$'\r'}
            case $value in
                \"*\") value=${value#\"}; value=${value%\"} ;;
                \'*\') value=${value#\'}; value=${value%\'} ;;
            esac
            printf '%s' "$value"
            return 0
        fi
    fi
    printf '%s' "$default"
}

compose() {
    docker compose --project-directory "$SD_ROOT" -f "$SD_COMPOSE_FILE" --env-file "$SD_ENV_FILE" "$@"
}

check_compose_ready() {
    require_cmd docker
    [ -f "$SD_COMPOSE_FILE" ] || die "no existe $SD_COMPOSE_FILE (defina SD_COMPOSE_FILE)"
    [ -f "$SD_ENV_FILE" ] || die "no existe $SD_ENV_FILE (defina SD_ENV_FILE)"
    docker compose version >/dev/null 2>&1 || die "Docker Compose v2 no está disponible"
}

# true si el servicio tiene un contenedor en ejecución.
service_running() {
    [ -n "$(compose ps --status running -q "$1" 2>/dev/null)" ]
}
