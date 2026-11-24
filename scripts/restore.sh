#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Energuate Service Desk — restauración desde una copia de scripts/backup.sh.
#
# ATENCIÓN: REEMPLAZA los datos actuales (bases de datos y/o adjuntos).
#
# Uso:
#   scripts/restore.sh /var/backups/energuate-sd/20261123-021500
#   scripts/restore.sh <carpeta> --only app        # solo BD de la aplicación
#   scripts/restore.sh <carpeta> --only keycloak   # solo BD de Keycloak
#   scripts/restore.sh <carpeta> --only minio      # solo adjuntos
#   scripts/restore.sh <carpeta> --yes             # sin confirmación interactiva
#
# Procedimiento:
#   1. Verifica los SHA-256 del MANIFEST.
#   2. Detiene los servicios que escriben (caddy, web, api, worker, beat,
#      keycloak) dejando en marcha db, keycloak-db y minio.
#   3. Recrea cada base de datos (DROP/CREATE) y aplica pg_restore en una sola
#      transacción; sincroniza el bucket con mc mirror --remove.
#   4. Vuelve a levantar todo con "docker compose up -d". El arranque de "api"
#      aplica las migraciones pendientes (alembic upgrade head) si la copia es
#      de una versión anterior del código.
# -----------------------------------------------------------------------------
set -euo pipefail
umask 077

# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(dirname "$0")/lib.sh"

src=""
only=all
assume_yes=false
while [ $# -gt 0 ]; do
    case $1 in
        --only)
            only=${2:-}
            shift 2
            case $only in app | keycloak | minio) ;; *) die "--only admite app, keycloak o minio" ;; esac
            ;;
        --yes | -y) assume_yes=true; shift ;;
        -h | --help) sed -n '2,24p' "$0"; exit 0 ;;
        -*) die "opción desconocida: $1" ;;
        *) src=$1; shift ;;
    esac
done

[ -n "$src" ] || die "indique la carpeta de la copia (ver --help)"
[ -d "$src" ] || die "no existe la carpeta $src"
src=$(cd "$src" && pwd)
[ -f "$src/MANIFEST" ] || die "$src no contiene MANIFEST: ¿es una copia generada por backup.sh?"

check_compose_ready
require_cmd sha256sum tar mktemp

wants() { [ "$only" = all ] || [ "$only" = "$1" ]; }

# --- 1. verificación de integridad ---------------------------------------------
log "verificando integridad de $src"
sed -n '/^---$/,$p' "$src/MANIFEST" | tail -n +2 | while read -r sum _size file; do
    [ -n "$file" ] || continue
    [ -f "$src/$file" ] || die "falta $file indicado en el MANIFEST"
    actual=$(sha256sum "$src/$file" | cut -d' ' -f1)
    [ "$actual" = "$sum" ] || die "SHA-256 no coincide para $file (copia corrupta)"
    log "ok $file"
done

if wants app; then [ -f "$src/servicedesk.dump" ] || die "la copia no contiene servicedesk.dump"; fi
if wants keycloak; then [ -f "$src/keycloak.dump" ] || die "la copia no contiene keycloak.dump"; fi
if wants minio && [ ! -f "$src/attachments.tar.gz" ]; then
    if [ "$only" = minio ]; then die "la copia no contiene attachments.tar.gz"; fi
    warn "la copia no contiene adjuntos (backup con --skip-minio): se omiten"
    restore_minio=false
else
    restore_minio=true
fi
wants minio || restore_minio=false

grep -E '^(fecha_utc|git_commit|alembic_revision)=' "$src/MANIFEST" | while IFS= read -r line; do log "copia: $line"; done

# --- confirmación ------------------------------------------------------------------
if [ "$assume_yes" = false ]; then
    [ -t 0 ] || die "sin terminal interactiva: use --yes para confirmar"
    printf 'Se REEMPLAZARÁN los datos actuales (%s) con la copia %s.\nEscriba RESTAURAR para continuar: ' "$only" "$src" >&2
    read -r answer
    [ "$answer" = RESTAURAR ] || die "cancelado por el usuario"
fi

# --- 2. detener escritores ---------------------------------------------------------
writers="caddy web api worker beat"
if wants keycloak; then writers="$writers keycloak"; fi
log "deteniendo servicios: $writers"
# shellcheck disable=SC2086
compose stop $writers || true
compose up -d db keycloak-db minio
for svc in db keycloak-db minio; do
    for _ in $(seq 1 60); do
        status=$(docker inspect -f '{{.State.Health.Status}}' "$(compose ps -q "$svc")" 2>/dev/null || echo unknown)
        [ "$status" = healthy ] && break
        sleep 2
    done
    [ "$status" = healthy ] || die "el servicio $svc no está sano (estado: $status)"
done

# --- 3. restauración -----------------------------------------------------------------
restore_db() {
    local service=$1 user=$2 db=$3 dump=$4
    log "recreando la base de datos $db"
    compose exec -T "$service" psql -U "$user" -d postgres -v ON_ERROR_STOP=1 -q \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$db' AND pid <> pg_backend_pid();" >/dev/null
    compose exec -T "$service" dropdb -U "$user" --if-exists "$db"
    compose exec -T "$service" createdb -U "$user" -O "$user" "$db"
    log "pg_restore $db"
    compose exec -T "$service" pg_restore -U "$user" -d "$db" --no-owner --role="$user" \
        --single-transaction --exit-on-error <"$dump"
    log "base de datos $db restaurada"
}

if wants app; then restore_db db servicedesk servicedesk "$src/servicedesk.dump"; fi
if wants keycloak; then restore_db keycloak-db keycloak keycloak "$src/keycloak.dump"; fi

if [ "$restore_minio" = true ]; then
    staging=$(mktemp -d "${TMPDIR:-/tmp}/sd-restore.XXXXXX")
    trap 'rm -rf "$staging"' EXIT
    log "extrayendo adjuntos"
    tar -xzf - -C "$staging" <"$src/attachments.tar.gz"
    chmod -R a+rX "$staging"
    log "sincronizando el bucket (mc mirror --remove)"
    # shellcheck disable=SC2016 # las variables se expanden dentro del contenedor
    compose run --rm --no-deps -T \
        -v "$staging:/restore:ro" \
        --entrypoint /bin/sh minio-init -c '
            set -eu
            mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
            mc mb --ignore-existing "local/$S3_BUCKET" >/dev/null
            mc mirror --overwrite --remove /restore/ "local/$S3_BUCKET" >/dev/null
            echo "objetos en el bucket: $(mc ls --recursive "local/$S3_BUCKET" | wc -l)"
        ' | while IFS= read -r line; do log "minio: $line"; done
fi

# --- 4. arranque -----------------------------------------------------------------------
log "levantando el stack completo"
compose up -d
log "restauración finalizada; verifique con scripts/healthcheck.sh"
