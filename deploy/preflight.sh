#!/bin/sh
# Energuate Service Desk — comprobación previa al arranque de PRODUCCIÓN.
#
# La ejecuta el servicio one-shot "preflight" de docker-compose.prod.yml antes que
# cualquier otro servicio (todos dependen de él con service_completed_successfully).
# Aborta (exit 1) si alguna credencial o valor obligatorio:
#   - está vacío,
#   - conserva el valor de la plantilla (.env.production.example: "replace-..."),
#   - es un valor trivial conocido (changeme, password, admin, minioadmin, ...),
#   - es más corto que el mínimo exigido.
# No imprime nunca el valor de un secreto, solo el nombre de la variable.
set -u

MIN_SECRET_LENGTH="${PREFLIGHT_MIN_SECRET_LENGTH:-16}"
errors=0

fail() {
    echo "preflight: ERROR: $*" >&2
    errors=$((errors + 1))
}

value_of() {
    # Lee la variable cuyo nombre se pasa como argumento (sh POSIX, sin bash).
    eval "printf '%s' \"\${$1:-}\""
}

is_placeholder() {
    lowered=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
    case "$lowered" in
        replace-*|replace_*|*replace-with*|*replace_with*|changeme|change-me|change_me|\
        password|passw0rd|secret|admin|administrator|minioadmin|keycloak|servicedesk|\
        postgres|redis|example|test|default|123456*|qwerty*)
            return 0 ;;
    esac
    return 1
}

check_secret() {
    # check_secret NOMBRE [longitud_minima] [solo_alfanumerico]
    name="$1"
    min="${2:-$MIN_SECRET_LENGTH}"
    alnum="${3:-no}"
    value=$(value_of "$name")
    if [ -z "$value" ]; then
        fail "$name está vacío"
        return
    fi
    if is_placeholder "$value"; then
        fail "$name conserva un valor de ejemplo o trivial; genere uno aleatorio"
        return
    fi
    if [ "${#value}" -lt "$min" ]; then
        fail "$name debe tener al menos $min caracteres (tiene ${#value})"
    fi
    if [ "$alnum" = "alnum" ]; then
        case "$value" in
            *[!A-Za-z0-9]*) fail "$name solo admite letras y números (va dentro de una URL)" ;;
        esac
    fi
}

check_value() {
    # check_value NOMBRE: obligatorio y sin valor de plantilla (no secreto).
    name="$1"
    value=$(value_of "$name")
    if [ -z "$value" ]; then
        fail "$name está vacío"
    elif is_placeholder "$value"; then
        fail "$name conserva el valor de ejemplo de la plantilla"
    fi
}

check_optional() {
    # check_optional NOMBRE: puede estar vacío, pero no conservar el valor de plantilla.
    name="$1"
    value=$(value_of "$name")
    if [ -n "$value" ] && is_placeholder "$value"; then
        fail "$name conserva el valor de ejemplo de la plantilla (déjelo vacío o defínalo)"
    fi
}

# --- Secretos ---------------------------------------------------------------
check_secret DB_PASSWORD "$MIN_SECRET_LENGTH" alnum
check_secret REDIS_PASSWORD "$MIN_SECRET_LENGTH" alnum
check_secret KC_DB_PASSWORD
check_secret KC_ADMIN_PASSWORD
check_secret MINIO_ROOT_PASSWORD
check_secret S3_SECRET_KEY
# Usuarios/claves de acceso (MinIO exige al menos 3 caracteres).
check_secret MINIO_ROOT_USER 3
check_secret S3_ACCESS_KEY 3
if [ -n "${MINIO_ROOT_USER:-}" ] && [ "${MINIO_ROOT_USER:-}" = "${S3_ACCESS_KEY:-}" ]; then
    fail "S3_ACCESS_KEY debe ser distinta de MINIO_ROOT_USER (cuenta de servicio limitada al bucket)"
fi
if [ -n "${MINIO_ROOT_PASSWORD:-}" ] && [ "${MINIO_ROOT_PASSWORD:-}" = "${S3_SECRET_KEY:-}" ]; then
    fail "S3_SECRET_KEY no puede ser igual a MINIO_ROOT_PASSWORD"
fi

# --- Dominio y correo ----------------------------------------------------------
check_value APP_DOMAIN
case "${APP_DOMAIN:-}" in
    *://*|*/*|*" "*) fail "APP_DOMAIN debe ser solo el nombre de dominio (sin https:// ni rutas)" ;;
    *.*) ;;
    "") ;;
    *) fail "APP_DOMAIN no parece un nombre de dominio completo" ;;
esac
check_value SMTP_FROM
case "${SMTP_FROM:-}" in
    *@*.*) ;;
    "") ;;
    *) fail "SMTP_FROM debe ser una dirección de correo válida" ;;
esac
check_optional SMTP_HOST
check_optional SMTP_USER
check_optional SMTP_PASSWORD
if [ "${TLS_MODE:-acme}" = "acme" ]; then
    check_value ACME_EMAIL
fi
case "${TLS_MODE:-acme}" in
    acme|files|internal) ;;
    *) fail "TLS_MODE debe ser acme, files o internal" ;;
esac

# --- Directorio activo (si está configurado) -----------------------------------
check_optional LDAP_URL
if [ -n "${LDAP_URL:-}" ]; then
    check_value LDAP_BIND_DN
    check_value LDAP_BIND_CREDENTIAL
    check_value LDAP_USERS_DN
    check_optional LDAP_GROUPS_DN
fi

if [ "$errors" -gt 0 ]; then
    echo "preflight: $errors problema(s) en .env. Corríjalos antes de arrancar (ver docs/DEPLOYMENT.md)." >&2
    exit 1
fi
echo "preflight: configuración de producción verificada"
