#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Energuate Service Desk — copia de seguridad completa.
#
# Genera en BACKUP_DIR/<AAAAMMDD-HHMMSS>/:
#   servicedesk.dump   pg_dump (formato custom, comprimido) de la BD de la aplicación
#   keycloak.dump      pg_dump (formato custom, comprimido) de la BD de Keycloak
#   attachments.tar.gz copia puntual del bucket de adjuntos (mc mirror + tar)
#   MANIFEST           versión, fecha, tamaños y SHA-256 de cada archivo
# y elimina las copias con más de BACKUP_RETENTION_DAYS días.
#
# Uso (en el servidor, desde cualquier carpeta):
#   scripts/backup.sh                    # usa docker-compose.prod.yml y .env
#   SD_COMPOSE_FILE=docker-compose.yml scripts/backup.sh   # laboratorio
#   scripts/backup.sh --skip-minio       # solo bases de datos
#
# Variables (en .env o en el entorno; el entorno tiene prioridad):
#   BACKUP_DIR             destino (por defecto /var/backups/energuate-sd)
#   BACKUP_RETENTION_DAYS  días de retención (por defecto 14; 0 = no borrar)
#
# Programación sugerida (cron del usuario que administra Docker):
#   15 2 * * *  /opt/energuate-sd/scripts/backup.sh >>/var/log/energuate-sd-backup.log 2>&1
#
# Códigos de salida: 0 correcto, distinto de 0 error (la carpeta parcial se
# renombra a *.failed para su análisis y no cuenta como copia válida).
# -----------------------------------------------------------------------------
set -euo pipefail
umask 077

# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(dirname "$0")/lib.sh"

skip_minio=false
while [ $# -gt 0 ]; do
    case $1 in
        --skip-minio) skip_minio=true; shift ;;
        -h | --help) sed -n '2,29p' "$0"; exit 0 ;;
        *) die "opción desconocida: $1" ;;
    esac
done

check_compose_ready
require_cmd tar sha256sum find date

BACKUP_DIR=${BACKUP_DIR:-$(env_get BACKUP_DIR /var/backups/energuate-sd)}
BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-$(env_get BACKUP_RETENTION_DAYS 14)}
case $BACKUP_RETENTION_DAYS in '' | *[!0-9]*) die "BACKUP_RETENTION_DAYS debe ser un entero" ;; esac

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# Evita dos copias simultáneas.
exec 9>"$BACKUP_DIR/.backup.lock"
if command -v flock >/dev/null 2>&1; then
    flock -n 9 || die "ya hay una copia de seguridad en curso"
fi

stamp=$(date +%Y%m%d-%H%M%S)
work="$BACKUP_DIR/.$stamp.inprogress"
final="$BACKUP_DIR/$stamp"
mkdir -p "$work"

# Cualquier salida antes de completar (error, die, señal) marca la copia como fallida.
on_exit() {
    local rc=$?
    if [ -d "$work" ]; then
        mv "$work" "$BACKUP_DIR/$stamp.failed" 2>/dev/null || true
        log "copia fallida; restos en $BACKUP_DIR/$stamp.failed"
        [ "$rc" -ne 0 ] || rc=1
    fi
    exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT TERM

dump_db() {
    local service=$1 user=$2 db=$3 out=$4
    service_running "$service" || die "el servicio '$service' no está en ejecución"
    log "pg_dump $db ($service)"
    compose exec -T "$service" pg_dump -U "$user" -d "$db" --format=custom --compress=6 --no-owner >"$out.tmp"
    [ -s "$out.tmp" ] || die "pg_dump de $db generó un archivo vacío"
    # Comprobación de integridad: pg_restore debe poder leer el índice del volcado.
    compose exec -T "$service" pg_restore --list <"$out.tmp" >/dev/null ||
        die "el volcado de $db no es legible por pg_restore"
    mv "$out.tmp" "$out"
}

dump_db db servicedesk servicedesk "$work/servicedesk.dump"
dump_db keycloak-db keycloak keycloak "$work/keycloak.dump"

if [ "$skip_minio" = false ]; then
    service_running minio || die "el servicio 'minio' no está en ejecución (use --skip-minio para omitirlo)"
    bucket=$(env_get S3_BUCKET servicedesk-attachments)
    cache="$BACKUP_DIR/.minio-mirror"
    mkdir -p "$cache"
    log "mc mirror del bucket '$bucket' (incremental en $cache)"
    # Contenedor efímero con la imagen de minio-init (credenciales root solo en
    # su entorno), escribiendo como el usuario actual en la carpeta de caché.
    # shellcheck disable=SC2016 # las variables se expanden dentro del contenedor
    compose run --rm --no-deps -T \
        --user "$(id -u):$(id -g)" \
        -v "$cache:/backup" \
        --entrypoint /bin/sh minio-init -c '
            set -eu
            mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
            mc mirror --overwrite --remove --preserve "local/$S3_BUCKET" /backup/ >/dev/null
            echo "objetos: $(mc ls --recursive "local/$S3_BUCKET" | wc -l)"
        ' | while IFS= read -r line; do log "minio: $line"; done
    log "empaquetando adjuntos"
    tar -czf - -C "$cache" . >"$work/attachments.tar.gz"
fi

{
    echo "fecha_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "host=$(hostname)"
    echo "compose_file=$(basename "$SD_COMPOSE_FILE")"
    if command -v git >/dev/null 2>&1 && git -C "$SD_ROOT" rev-parse HEAD >/dev/null 2>&1; then
        echo "git_commit=$(git -C "$SD_ROOT" rev-parse HEAD)"
    fi
    echo "alembic_revision=$(compose exec -T db psql -U servicedesk -d servicedesk -tAc 'SELECT version_num FROM alembic_version' 2>/dev/null | tr -d '\r' || echo desconocida)"
    echo "---"
    (cd "$work" && for f in *.dump *.tar.gz; do
        [ -f "$f" ] || continue
        printf '%s  %s  %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$(wc -c <"$f" | tr -d ' ')" "$f"
    done)
} >"$work/MANIFEST"

mv "$work" "$final"
log "copia completada: $final ($(du -sh "$final" | cut -f1))"

if [ "$BACKUP_RETENTION_DAYS" -gt 0 ]; then
    find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d \
        \( -name '20[0-9][0-9][0-1][0-9][0-3][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]' -o -name '*.failed' \) \
        -mtime +"$BACKUP_RETENTION_DAYS" -print -exec rm -rf {} + |
        while IFS= read -r old; do log "retención: eliminada $old"; done
fi
