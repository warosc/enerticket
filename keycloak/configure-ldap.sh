#!/bin/sh
# -----------------------------------------------------------------------------
# Energuate Service Desk — configuración post-arranque de Keycloak
#
# Lo ejecuta el contenedor one-shot "keycloak-config" (imagen de Keycloak) con
# /opt/keycloak/bin/kcadm.sh. Es idempotente: se puede ejecutar en cada
# "docker compose up" sin duplicar nada.
#
# Pasos:
#   1. Espera a que Keycloak acepte el login del administrador y a que exista
#      el realm (importado con --import-realm desde realm-energuate.json).
#   2. (opcional, KC_WEB_URLS) fija redirect URIs / web origins / post-logout
#      del cliente público servicedesk-web (PKCE S256, sin direct grants).
#   3. Crea los grupos de equipo de SEED_TEAM_GROUPS si no existen.
#   4. (opcional, KC_LAB_USERS=true) crea/actualiza usuarios locales de
#      laboratorio (los mismos nombres que los usuarios demo del backend). Con
#      KC_LAB_USERS distinto de true los deshabilita si existen.
#   5. (opcional, LDAP_URL definido) crea/actualiza la federación con el Active
#      Directory real de Energuate (vendor ad, READ_ONLY) y su mapper de grupos
#      (group-ldap-mapper, READ_ONLY); prueba la conexión y sincroniza grupos.
#   6. Asigna roles de realm a grupos según KC_GROUP_ROLE_MAP.
#
# Ninguna credencial está en el repositorio: todo llega por variables de
# entorno (ver .env.example y docs/AD_INTEGRATION.md).
# -----------------------------------------------------------------------------
set -eu

KCADM=${KCADM:-/opt/keycloak/bin/kcadm.sh}
KC_SERVER_URL=${KC_SERVER_URL:-http://keycloak:8080}
KC_REALM=${KC_REALM:-energuate}
KC_ADMIN=${KC_ADMIN:-}
KC_ADMIN_PASSWORD=${KC_ADMIN_PASSWORD:-}
KC_WAIT_TIMEOUT=${KC_WAIT_TIMEOUT:-300}

KC_GROUP_ROLE_MAP=${KC_GROUP_ROLE_MAP:-}
KC_LAB_USERS=${KC_LAB_USERS:-false}
KC_LAB_PASSWORD=${KC_LAB_PASSWORD:-}
KC_LAB_EMAIL_DOMAIN=${KC_LAB_EMAIL_DOMAIN:-lab.energuate.invalid}
# Grupos que reciben los usuarios de laboratorio. Los de rol deben coincidir con
# KC_GROUP_ROLE_MAP y los de equipo con teams.external_group (SEED_TEAM_GROUPS).
KC_LAB_GROUP_ADMINS=${KC_LAB_GROUP_ADMINS:-GG-SD-Admins}
KC_LAB_GROUP_AGENTS=${KC_LAB_GROUP_AGENTS:-GG-SD-Agentes}
KC_LAB_GROUP_APPROVERS=${KC_LAB_GROUP_APPROVERS:-GG-SD-Aprobadores}
KC_LAB_GROUP_TELECOM=${KC_LAB_GROUP_TELECOM:-GG-SD-Telecomunicaciones}
KC_LAB_GROUP_NETWORK=${KC_LAB_GROUP_NETWORK:-GG-SD-SeguridadRedes}
KC_LAB_GROUP_SOC=${KC_LAB_GROUP_SOC:-GG-SD-SOC}

# Cliente público del portal: URL(s) base del frontend separadas por comas.
# Se fuerzan en cada ejecución (redirect URIs, web origins y post-logout), de
# modo que cambiar de dominio no requiere reimportar el realm.
KC_WEB_CLIENT_ID=${KC_WEB_CLIENT_ID:-servicedesk-web}
KC_WEB_URLS=${KC_WEB_URLS:-}

# Grupos de equipo (SEED_TEAM_GROUPS, formato Departamento:Grupo) que se crean
# en Keycloak si no existen (con AD, el mapper de grupos los vincula por nombre).
SEED_TEAM_GROUPS=${SEED_TEAM_GROUPS:-}

LDAP_URL=${LDAP_URL:-}
LDAP_PROVIDER_NAME=${LDAP_PROVIDER_NAME:-energuate-ad}
LDAP_BIND_DN=${LDAP_BIND_DN:-}
LDAP_BIND_CREDENTIAL=${LDAP_BIND_CREDENTIAL:-}
LDAP_USERS_DN=${LDAP_USERS_DN:-}
LDAP_USER_FILTER=${LDAP_USER_FILTER:-}
LDAP_GROUPS_DN=${LDAP_GROUPS_DN:-}
LDAP_GROUP_FILTER=${LDAP_GROUP_FILTER:-}
LDAP_GROUPS_RECURSIVE=${LDAP_GROUPS_RECURSIVE:-false}
LDAP_START_TLS=${LDAP_START_TLS:-false}
LDAP_ALLOW_INSECURE=${LDAP_ALLOW_INSECURE:-false}
LDAP_CONNECTION_TIMEOUT=${LDAP_CONNECTION_TIMEOUT:-10000}
LDAP_READ_TIMEOUT=${LDAP_READ_TIMEOUT:-30000}
LDAP_SEARCH_SCOPE=${LDAP_SEARCH_SCOPE:-subtree}
LDAP_PAGINATION=${LDAP_PAGINATION:-true}
LDAP_BATCH_SIZE=${LDAP_BATCH_SIZE:-500}
LDAP_FULL_SYNC_PERIOD=${LDAP_FULL_SYNC_PERIOD:-86400}
LDAP_CHANGED_SYNC_PERIOD=${LDAP_CHANGED_SYNC_PERIOD:-3600}
LDAP_SYNC_USERS_ON_START=${LDAP_SYNC_USERS_ON_START:-false}

LDAP_STORAGE_TYPE=org.keycloak.storage.UserStorageProvider
LDAP_MAPPER_TYPE=org.keycloak.storage.ldap.mappers.LDAPStorageMapper
LDAP_GROUP_MAPPER_NAME=groups-ad
VALID_ROLES="admin agent approver requester"

# Arranque más rápido de la JVM de kcadm.sh (se invoca muchas veces).
KC_OPTS=${KC_OPTS:--XX:TieredStopAtLevel=1 -XX:+UseSerialGC -Xss512k}
export KC_OPTS

WORKDIR=$(mktemp -d)
KCADM_CONFIG="$WORKDIR/kcadm.config"
trap 'rm -rf "$WORKDIR"' EXIT
trap 'exit 130' INT TERM

NL='
'
CR=$(printf '\r')

# --- utilidades ---------------------------------------------------------------

log() { printf '%s [keycloak-config] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
warn() { log "ADVERTENCIA: $*" >&2; }
die() { log "ERROR: $*" >&2; exit 1; }

kc() { "$KCADM" "$@" --config "$KCADM_CONFIG" </dev/null; }

lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

trim() { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

is_true() {
    case $(lower "$1") in
        true | 1 | yes | si | sí | on) return 0 ;;
        *) return 1 ;;
    esac
}

bool() { if is_true "$1"; then printf 'true'; else printf 'false'; fi; }

is_uint() {
    case $1 in
        '' | *[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

# Escapa un valor para incluirlo dentro de una cadena JSON. Rechaza saltos de
# línea (ninguna opción legítima los necesita y romperían el documento).
json_escape() {
    case $1 in
        *"$NL"* | *"$CR"*) die "un valor de configuración contiene saltos de línea" ;;
    esac
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/	/\\t/g'
}

is_placeholder() {
    case $1 in
        replace-* | changeme | change-me*) return 0 ;;
        *) return 1 ;;
    esac
}

# Primera columna "id" de un listado csv de kcadm (vacío si no hay resultados).
first_id() { sed -n '1p' | tr -d '\r'; }

# --- espera y autenticación -----------------------------------------------------

login() {
    [ -n "$KC_ADMIN" ] || die "KC_ADMIN no está definido"
    [ -n "$KC_ADMIN_PASSWORD" ] || die "KC_ADMIN_PASSWORD no está definido"
    is_uint "$KC_WAIT_TIMEOUT" || die "KC_WAIT_TIMEOUT debe ser un entero (segundos)"

    log "esperando a Keycloak en $KC_SERVER_URL (máximo ${KC_WAIT_TIMEOUT}s)"
    elapsed=0
    until kc config credentials --server "$KC_SERVER_URL" --realm master \
        --user "$KC_ADMIN" --password "$KC_ADMIN_PASSWORD" >"$WORKDIR/login.log" 2>&1; do
        if [ "$elapsed" -ge "$KC_WAIT_TIMEOUT" ]; then
            cat "$WORKDIR/login.log" >&2
            die "no fue posible autenticarse en Keycloak tras ${KC_WAIT_TIMEOUT}s (revise KC_ADMIN/KC_ADMIN_PASSWORD y el estado del contenedor keycloak)"
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done
    log "sesión de administración iniciada"

    elapsed=0
    until REALM_ID=$(kc get "realms/$KC_REALM" --fields id --format csv --noquotes 2>/dev/null | first_id) && [ -n "$REALM_ID" ]; do
        if [ "$elapsed" -ge "$KC_WAIT_TIMEOUT" ]; then
            die "el realm '$KC_REALM' no existe; verifique que keycloak arrancó con --import-realm y el archivo realm-energuate.json"
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done
    log "realm '$KC_REALM' disponible (id $REALM_ID)"
}

# --- grupos y roles ---------------------------------------------------------------

# Devuelve el id del grupo de primer nivel con el nombre exacto dado (o vacío).
find_group_id() {
    _name=$1
    kc get groups -r "$KC_REALM" -q search="$_name" -q exact=true -q briefRepresentation=true \
        --fields id,name --format csv --noquotes >"$WORKDIR/groups.csv"
    while IFS= read -r _line || [ -n "$_line" ]; do
        _line=$(printf '%s' "$_line" | tr -d '\r')
        if [ "${_line#*,}" = "$_name" ]; then
            printf '%s' "${_line%%,*}"
            return 0
        fi
    done <"$WORKDIR/groups.csv"
    return 0
}

ensure_group() {
    _name=$1
    _gid=$(find_group_id "$_name")
    if [ -z "$_gid" ]; then
        printf '{"name":"%s"}' "$(json_escape "$_name")" >"$WORKDIR/group.json"
        _gid=$(kc create groups -r "$KC_REALM" -f "$WORKDIR/group.json" -i)
        log "grupo '$_name' creado en Keycloak (si existe en AD, la sincronización lo vinculará por nombre)" >&2
    fi
    printf '%s' "$_gid"
}

validate_role() {
    for _r in $VALID_ROLES; do
        [ "$_r" = "$1" ] && return 0
    done
    die "rol '$1' no válido; use uno de: $VALID_ROLES"
}

apply_group_role_map() {
    if [ -z "$(trim "$KC_GROUP_ROLE_MAP")" ]; then
        log "KC_GROUP_ROLE_MAP vacío: no se asignan roles a grupos"
        return 0
    fi
    log "aplicando KC_GROUP_ROLE_MAP"
    _old_ifs=$IFS
    IFS=,
    set -f
    # shellcheck disable=SC2086
    set -- $KC_GROUP_ROLE_MAP
    set +f
    IFS=$_old_ifs
    for _pair in "$@"; do
        _pair=$(trim "$_pair")
        [ -n "$_pair" ] || continue
        case $_pair in
            *:*) ;;
            *) die "entrada inválida en KC_GROUP_ROLE_MAP: '$_pair' (formato Grupo:rol)" ;;
        esac
        _group=$(trim "${_pair%:*}")
        _role=$(trim "${_pair##*:}")
        [ -n "$_group" ] || die "grupo vacío en KC_GROUP_ROLE_MAP: '$_pair'"
        validate_role "$_role"
        _gid=$(ensure_group "$_group")
        kc add-roles -r "$KC_REALM" --gid "$_gid" --rolename "$_role"
        log "grupo '$_group' -> rol '$_role'"
    done
}

# --- usuarios de laboratorio ------------------------------------------------------

find_user_id() {
    kc get users -r "$KC_REALM" -q username="$1" -q exact=true -q briefRepresentation=true \
        --fields id --format csv --noquotes | first_id
}

# Usuarios de laboratorio, coherentes con los usuarios demo del backend
# (app/seeds/90_core_demo.py). Formato: usuario|nombre|apellido|roles|grupos
lab_user_specs() {
    cat <<EOF
demo.admin|Administrador|Demo|admin,agent|$KC_LAB_GROUP_ADMINS,$KC_LAB_GROUP_AGENTS
ana.telecom|Ana|López|agent|$KC_LAB_GROUP_AGENTS,$KC_LAB_GROUP_TELECOM
luis.soc|Luis|Pérez|agent|$KC_LAB_GROUP_AGENTS,$KC_LAB_GROUP_SOC
rosa.redes|Rosa|Méndez|agent|$KC_LAB_GROUP_AGENTS,$KC_LAB_GROUP_NETWORK
marta.aprobadora|Marta|Díaz|approver|$KC_LAB_GROUP_APPROVERS
carlos.solicitante|Carlos|Ramírez|requester|
sofia.solicitante|Sofía|Hernández|requester|
EOF
}

# Nombres usados por versiones anteriores del script: siempre se deshabilitan.
legacy_lab_usernames() {
    printf '%s\n' admin.lab agente.telecom agente.soc aprobador.lab usuario.lab
}

disable_user_if_enabled() {
    _row=$(kc get users -r "$KC_REALM" -q username="$1" -q exact=true -q briefRepresentation=true \
        --fields id,enabled --format csv --noquotes | first_id)
    if [ -n "$_row" ] && [ "${_row#*,}" = "true" ]; then
        kc update "users/${_row%%,*}" -r "$KC_REALM" -s enabled=false
        log "$2: usuario '$1' deshabilitado"
    fi
}

upsert_lab_user() {
    _user=$1 _first=$2 _last=$3 _roles=$4 _groups=$5
    _email="$_user@$KC_LAB_EMAIL_DOMAIN"
    cat >"$WORKDIR/user.json" <<EOF
{"username":"$(json_escape "$_user")","enabled":true,"emailVerified":true,"firstName":"$(json_escape "$_first")","lastName":"$(json_escape "$_last")","email":"$(json_escape "$_email")","requiredActions":[]}
EOF
    _uid=$(find_user_id "$_user")
    if [ -z "$_uid" ]; then
        _uid=$(kc create users -r "$KC_REALM" -f "$WORKDIR/user.json" -i)
        log "usuario de laboratorio '$_user' creado"
    else
        kc update "users/$_uid" -r "$KC_REALM" -f "$WORKDIR/user.json"
        log "usuario de laboratorio '$_user' actualizado"
    fi

    printf '{"type":"password","temporary":false,"value":"%s"}' "$(json_escape "$KC_LAB_PASSWORD")" >"$WORKDIR/password.json"
    if ! kc update "users/$_uid/reset-password" -r "$KC_REALM" -f "$WORKDIR/password.json" 2>"$WORKDIR/password.err"; then
        rm -f "$WORKDIR/password.json"
        # passwordHistory(5): en una re-ejecución la contraseña ya está en el historial
        # (normalmente es la vigente); no es un error y se mantiene la idempotencia.
        if grep -q 'invalidPasswordHistoryMessage' "$WORKDIR/password.err"; then
            log "usuario de laboratorio '$_user': KC_LAB_PASSWORD ya está en el historial; no se cambia"
        else
            cat "$WORKDIR/password.err" >&2
            die "Keycloak rechazó KC_LAB_PASSWORD para '$_user' (política: mínimo 12 caracteres, distinta del usuario y del correo)"
        fi
    fi
    rm -f "$WORKDIR/password.json"

    _old_ifs=$IFS
    IFS=,
    for _role in $_roles; do
        IFS=$_old_ifs
        [ -n "$_role" ] && kc add-roles -r "$KC_REALM" --uid "$_uid" --rolename "$_role"
        IFS=,
    done
    for _group in $_groups; do
        IFS=$_old_ifs
        if [ -n "$_group" ]; then
            _gid=$(ensure_group "$_group")
            kc update "users/$_uid/groups/$_gid" -r "$KC_REALM" -s realm="$KC_REALM" -s userId="$_uid" -s groupId="$_gid" -n
        fi
        IFS=,
    done
    IFS=$_old_ifs
}

configure_lab_users() {
    lab_user_specs >"$WORKDIR/lab-users.txt"
    if is_true "$KC_LAB_USERS"; then
        [ -n "$KC_LAB_PASSWORD" ] || die "KC_LAB_USERS=true requiere KC_LAB_PASSWORD"
        is_placeholder "$KC_LAB_PASSWORD" && die "KC_LAB_PASSWORD conserva el valor de ejemplo; defina una contraseña propia"
        log "KC_LAB_USERS=true: creando/actualizando usuarios de laboratorio (solo para entornos aislados)"
        while IFS='|' read -r _u _f _l _r _g || [ -n "$_u" ]; do
            [ -n "$_u" ] || continue
            upsert_lab_user "$_u" "$_f" "$_l" "$_r" "$_g"
        done <"$WORKDIR/lab-users.txt"
    else
        while IFS='|' read -r _u _f _l _r _g || [ -n "$_u" ]; do
            [ -n "$_u" ] || continue
            disable_user_if_enabled "$_u" "KC_LAB_USERS desactivado"
        done <"$WORKDIR/lab-users.txt"
    fi
    legacy_lab_usernames >"$WORKDIR/legacy-users.txt"
    while IFS= read -r _u || [ -n "$_u" ]; do
        [ -n "$_u" ] && disable_user_if_enabled "$_u" "usuario de laboratorio obsoleto"
    done <"$WORKDIR/legacy-users.txt"
}

# --- grupos de equipos ------------------------------------------------------------

ensure_team_groups() {
    if [ -z "$(trim "$SEED_TEAM_GROUPS")" ]; then
        log "SEED_TEAM_GROUPS vacío: no se crean grupos de equipo"
        return 0
    fi
    _old_ifs=$IFS
    IFS=,
    set -f
    # shellcheck disable=SC2086
    set -- $SEED_TEAM_GROUPS
    set +f
    IFS=$_old_ifs
    for _pair in "$@"; do
        _pair=$(trim "$_pair")
        [ -n "$_pair" ] || continue
        case $_pair in
            *:*) _group=$(trim "${_pair##*:}") ;;
            *) warn "entrada sin ':' en SEED_TEAM_GROUPS ignorada: '$_pair'"; continue ;;
        esac
        [ -n "$_group" ] || continue
        ensure_group "$_group" >/dev/null
    done
    log "grupos de equipo presentes en Keycloak (SEED_TEAM_GROUPS)"
}

# --- cliente público del portal ----------------------------------------------------

# Fuerza redirect URIs, web origins y post-logout del cliente servicedesk-web
# a partir de KC_WEB_URLS (p. ej. "https://servicedesk.energuate.com").
configure_web_client() {
    if [ -z "$(trim "$KC_WEB_URLS")" ]; then
        log "KC_WEB_URLS vacío: se conservan las URLs del cliente '$KC_WEB_CLIENT_ID' del realm importado"
        return 0
    fi
    _cid=$(kc get clients -r "$KC_REALM" -q clientId="$KC_WEB_CLIENT_ID" --fields id --format csv --noquotes | first_id)
    [ -n "$_cid" ] || die "no existe el cliente '$KC_WEB_CLIENT_ID' en el realm '$KC_REALM'"

    _redirects="" _origins="" _logout="" _root=""
    _old_ifs=$IFS
    IFS=,
    set -f
    # shellcheck disable=SC2086
    set -- $KC_WEB_URLS
    set +f
    IFS=$_old_ifs
    for _url in "$@"; do
        _url=$(trim "$_url")
        _url=${_url%/}
        [ -n "$_url" ] || continue
        case $_url in
            https://*) ;;
            http://localhost | http://localhost:* | http://127.0.0.1 | http://127.0.0.1:*) ;;
            http://*) warn "KC_WEB_URLS contiene una URL sin TLS ($_url); úsela solo en laboratorio" ;;
            *) die "URL inválida en KC_WEB_URLS: '$_url' (debe empezar por https:// o http://)" ;;
        esac
        case $_url in
            *'*'* | *' '*) die "KC_WEB_URLS no admite comodines ni espacios: '$_url'" ;;
        esac
        _e=$(json_escape "$_url")
        [ -n "$_root" ] || _root=$_e
        _redirects="$_redirects${_redirects:+,}\"$_e/*\""
        _origins="$_origins${_origins:+,}\"$_e\""
        _logout="$_logout${_logout:+##}$_e/*"
    done
    [ -n "$_root" ] || die "KC_WEB_URLS no contiene ninguna URL válida"

    cat >"$WORKDIR/web-client.json" <<EOF
{
  "rootUrl": "$_root",
  "baseUrl": "/",
  "redirectUris": [$_redirects],
  "webOrigins": [$_origins],
  "publicClient": true,
  "standardFlowEnabled": true,
  "implicitFlowEnabled": false,
  "directAccessGrantsEnabled": false,
  "serviceAccountsEnabled": false,
  "attributes": {
    "pkce.code.challenge.method": "S256",
    "post.logout.redirect.uris": "$_logout"
  }
}
EOF
    kc update "clients/$_cid" -r "$KC_REALM" --merge -f "$WORKDIR/web-client.json"
    log "cliente '$KC_WEB_CLIENT_ID': URLs fijadas a $KC_WEB_URLS (PKCE S256, sin direct access grants)"
}

# --- tokens offline ----------------------------------------------------------------

# El portal no usa tokens offline. Un refresh token offline sobrevive al cierre de la sesión
# SSO, así que (en cada arranque, también en realms importados antes de este cambio):
#   - se quita el rol offline_access de los roles por defecto del realm,
#   - se desasigna el client scope opcional "offline_access" del cliente del portal,
#   - se acortan las sesiones offline (1 día de inactividad, 7 días máximo).
harden_offline_access() {
    if kc remove-roles -r "$KC_REALM" --rname "default-roles-$KC_REALM" --rolename offline_access \
        >/dev/null 2>&1; then
        log "rol offline_access retirado de default-roles-$KC_REALM (si lo tenía)"
    fi
    _cid=$(kc get clients -r "$KC_REALM" -q clientId="$KC_WEB_CLIENT_ID" --fields id --format csv --noquotes | first_id)
    if [ -n "$_cid" ]; then
        _scope_id=$(kc get "clients/$_cid/optional-client-scopes" -r "$KC_REALM" --fields id,name --format csv --noquotes \
            | tr -d '\r' | sed -n 's/^\([^,]*\),offline_access$/\1/p' | first_id)
        if [ -n "$_scope_id" ]; then
            kc delete "clients/$_cid/optional-client-scopes/$_scope_id" -r "$KC_REALM" \
                && log "scope offline_access desasignado del cliente '$KC_WEB_CLIENT_ID'"
        fi
    fi
    kc update "realms/$KC_REALM" \
        -s offlineSessionIdleTimeout=86400 \
        -s offlineSessionMaxLifespanEnabled=true \
        -s offlineSessionMaxLifespan=604800
    log "sesiones offline limitadas (inactividad 1 día, máximo 7 días)"
}

# --- federación LDAP / Active Directory -----------------------------------------

validate_ldap_settings() {
    [ -n "$LDAP_BIND_DN" ] || die "LDAP_URL está definido pero falta LDAP_BIND_DN"
    [ -n "$LDAP_BIND_CREDENTIAL" ] || die "LDAP_URL está definido pero falta LDAP_BIND_CREDENTIAL"
    [ -n "$LDAP_USERS_DN" ] || die "LDAP_URL está definido pero falta LDAP_USERS_DN"
    for _v in "$LDAP_URL" "$LDAP_BIND_DN" "$LDAP_BIND_CREDENTIAL" "$LDAP_USERS_DN" "$LDAP_GROUPS_DN"; do
        is_placeholder "$_v" && die "una variable LDAP_* conserva el valor de ejemplo 'replace-...'"
    done

    _scheme=$(lower "${LDAP_URL%%://*}")
    case $_scheme in
        ldaps)
            is_true "$LDAP_START_TLS" && die "LDAP_START_TLS=true no es compatible con ldaps://; use ldap:// con StartTLS o ldaps:// sin StartTLS"
            ;;
        ldap)
            if ! is_true "$LDAP_START_TLS"; then
                is_true "$LDAP_ALLOW_INSECURE" ||
                    die "ldap:// sin StartTLS envía la contraseña de la cuenta de servicio en claro; use ldaps://, LDAP_START_TLS=true o (solo laboratorio) LDAP_ALLOW_INSECURE=true"
                warn "conexión LDAP sin cifrar permitida por LDAP_ALLOW_INSECURE=true (no usar en producción)"
            fi
            ;;
        *) die "LDAP_URL debe empezar por ldap:// o ldaps:// (valor actual: '$LDAP_URL')" ;;
    esac

    if [ -n "$LDAP_USER_FILTER" ]; then
        case $LDAP_USER_FILTER in
            \(*\)) ;;
            *) die "LDAP_USER_FILTER debe estar entre paréntesis, p. ej. (memberOf=CN=...)" ;;
        esac
    fi
    if [ -n "$LDAP_GROUP_FILTER" ]; then
        case $LDAP_GROUP_FILTER in
            \(*\)) ;;
            *) die "LDAP_GROUP_FILTER debe estar entre paréntesis, p. ej. (cn=GG-SD-*)" ;;
        esac
    fi

    is_uint "$LDAP_CONNECTION_TIMEOUT" || die "LDAP_CONNECTION_TIMEOUT debe ser un entero positivo (ms)"
    is_uint "$LDAP_READ_TIMEOUT" || die "LDAP_READ_TIMEOUT debe ser un entero positivo (ms)"
    is_uint "$LDAP_BATCH_SIZE" || die "LDAP_BATCH_SIZE debe ser un entero positivo"
    [ "$LDAP_FULL_SYNC_PERIOD" = "-1" ] || is_uint "$LDAP_FULL_SYNC_PERIOD" ||
        die "LDAP_FULL_SYNC_PERIOD debe ser un entero en segundos (-1 desactiva)"
    [ "$LDAP_CHANGED_SYNC_PERIOD" = "-1" ] || is_uint "$LDAP_CHANGED_SYNC_PERIOD" ||
        die "LDAP_CHANGED_SYNC_PERIOD debe ser un entero en segundos (-1 desactiva)"

    case $(lower "$LDAP_SEARCH_SCOPE") in
        one | 1) LDAP_SEARCH_SCOPE_VALUE=1 ;;
        subtree | 2) LDAP_SEARCH_SCOPE_VALUE=2 ;;
        *) die "LDAP_SEARCH_SCOPE debe ser 'one' o 'subtree'" ;;
    esac
}

write_ldap_component_json() {
    # Keycloak no permite pool de conexiones junto con StartTLS.
    if is_true "$LDAP_START_TLS"; then _pooling=false; else _pooling=true; fi
    _id_field=""
    [ -n "${1:-}" ] && _id_field="\"id\":\"$1\","
    cat >"$WORKDIR/ldap.json" <<EOF
{
  $_id_field
  "name": "$(json_escape "$LDAP_PROVIDER_NAME")",
  "providerId": "ldap",
  "providerType": "$LDAP_STORAGE_TYPE",
  "parentId": "$REALM_ID",
  "config": {
    "enabled": ["true"],
    "priority": ["0"],
    "vendor": ["ad"],
    "editMode": ["READ_ONLY"],
    "importEnabled": ["true"],
    "syncRegistrations": ["false"],
    "trustEmail": ["true"],
    "usernameLDAPAttribute": ["sAMAccountName"],
    "rdnLDAPAttribute": ["cn"],
    "uuidLDAPAttribute": ["objectGUID"],
    "userObjectClasses": ["person, organizationalPerson, user"],
    "krbPrincipalAttribute": ["userPrincipalName"],
    "connectionUrl": ["$(json_escape "$LDAP_URL")"],
    "usersDn": ["$(json_escape "$LDAP_USERS_DN")"],
    "customUserSearchFilter": ["$(json_escape "$LDAP_USER_FILTER")"],
    "searchScope": ["$LDAP_SEARCH_SCOPE_VALUE"],
    "authType": ["simple"],
    "bindDn": ["$(json_escape "$LDAP_BIND_DN")"],
    "bindCredential": ["$(json_escape "$LDAP_BIND_CREDENTIAL")"],
    "startTls": ["$(bool "$LDAP_START_TLS")"],
    "useTruststoreSpi": ["always"],
    "connectionPooling": ["$_pooling"],
    "connectionTimeout": ["$LDAP_CONNECTION_TIMEOUT"],
    "readTimeout": ["$LDAP_READ_TIMEOUT"],
    "pagination": ["$(bool "$LDAP_PAGINATION")"],
    "batchSizeForSync": ["$LDAP_BATCH_SIZE"],
    "fullSyncPeriod": ["$LDAP_FULL_SYNC_PERIOD"],
    "changedSyncPeriod": ["$LDAP_CHANGED_SYNC_PERIOD"],
    "allowKerberosAuthentication": ["false"],
    "useKerberosForPasswordAuthentication": ["false"],
    "validatePasswordPolicy": ["false"],
    "cachePolicy": ["DEFAULT"]
  }
}
EOF
}

write_group_mapper_json() {
    if is_true "$LDAP_GROUPS_RECURSIVE"; then
        _strategy=LOAD_GROUPS_BY_MEMBER_ATTRIBUTE_RECURSIVELY
    else
        _strategy=LOAD_GROUPS_BY_MEMBER_ATTRIBUTE
    fi
    _id_field=""
    [ -n "${2:-}" ] && _id_field="\"id\":\"$2\","
    cat >"$WORKDIR/group-mapper.json" <<EOF
{
  $_id_field
  "name": "$LDAP_GROUP_MAPPER_NAME",
  "providerId": "group-ldap-mapper",
  "providerType": "$LDAP_MAPPER_TYPE",
  "parentId": "$1",
  "config": {
    "mode": ["READ_ONLY"],
    "groups.dn": ["$(json_escape "$LDAP_GROUPS_DN")"],
    "groups.ldap.filter": ["$(json_escape "$LDAP_GROUP_FILTER")"],
    "group.name.ldap.attribute": ["cn"],
    "group.object.classes": ["group"],
    "groups.path": ["/"],
    "preserve.group.inheritance": ["false"],
    "ignore.missing.groups": ["true"],
    "membership.ldap.attribute": ["member"],
    "membership.attribute.type": ["DN"],
    "membership.user.ldap.attribute": ["sAMAccountName"],
    "memberof.ldap.attribute": ["memberOf"],
    "user.roles.retrieve.strategy": ["$_strategy"],
    "mapped.group.attributes": [""],
    "drop.non.existing.groups.during.sync": ["false"]
  }
}
EOF
}

test_ldap_connection() {
    cat >"$WORKDIR/ldap-test.json" <<EOF
{"action":"testAuthentication","componentId":"$1","connectionUrl":"$(json_escape "$LDAP_URL")","authType":"simple","bindDn":"$(json_escape "$LDAP_BIND_DN")","bindCredential":"$(json_escape "$LDAP_BIND_CREDENTIAL")","useTruststoreSpi":"always","connectionTimeout":"$LDAP_CONNECTION_TIMEOUT","startTls":"$(bool "$LDAP_START_TLS")"}
EOF
    if kc create testLDAPConnection -r "$KC_REALM" -f "$WORKDIR/ldap-test.json" >"$WORKDIR/ldap-test.log" 2>&1; then
        rm -f "$WORKDIR/ldap-test.json"
        log "prueba de conexión y bind contra AD: correcta"
        return 0
    fi
    rm -f "$WORKDIR/ldap-test.json"
    warn "falló la prueba de conexión/bind contra $LDAP_URL: $(tr '\n' ' ' <"$WORKDIR/ldap-test.log")"
    warn "revise red/DNS, LDAP_BIND_DN/LDAP_BIND_CREDENTIAL y, con LDAPS/StartTLS, la CA corporativa en el truststore (docs/AD_INTEGRATION.md)"
    return 1
}

configure_ldap() {
    if [ -z "$(trim "$LDAP_URL")" ]; then
        log "LDAP_URL vacío: se omite la federación con Active Directory"
        return 0
    fi
    validate_ldap_settings

    LDAP_ID=$(kc get components -r "$KC_REALM" -q parent="$REALM_ID" -q type="$LDAP_STORAGE_TYPE" \
        -q name="$LDAP_PROVIDER_NAME" --fields id --format csv --noquotes | first_id)
    if [ -z "$LDAP_ID" ]; then
        write_ldap_component_json ""
        LDAP_ID=$(kc create components -r "$KC_REALM" -f "$WORKDIR/ldap.json" -i)
        log "federación LDAP '$LDAP_PROVIDER_NAME' creada (id $LDAP_ID)"
    else
        write_ldap_component_json "$LDAP_ID"
        kc update "components/$LDAP_ID" -r "$KC_REALM" -f "$WORKDIR/ldap.json"
        log "federación LDAP '$LDAP_PROVIDER_NAME' actualizada (id $LDAP_ID)"
    fi
    rm -f "$WORKDIR/ldap.json"

    GROUP_MAPPER_ID=""
    if [ -n "$LDAP_GROUPS_DN" ]; then
        GROUP_MAPPER_ID=$(kc get components -r "$KC_REALM" -q parent="$LDAP_ID" -q type="$LDAP_MAPPER_TYPE" \
            -q name="$LDAP_GROUP_MAPPER_NAME" --fields id --format csv --noquotes | first_id)
        if [ -z "$GROUP_MAPPER_ID" ]; then
            write_group_mapper_json "$LDAP_ID" ""
            GROUP_MAPPER_ID=$(kc create components -r "$KC_REALM" -f "$WORKDIR/group-mapper.json" -i)
            log "mapper de grupos '$LDAP_GROUP_MAPPER_NAME' creado (id $GROUP_MAPPER_ID)"
        else
            write_group_mapper_json "$LDAP_ID" "$GROUP_MAPPER_ID"
            kc update "components/$GROUP_MAPPER_ID" -r "$KC_REALM" -f "$WORKDIR/group-mapper.json"
            log "mapper de grupos '$LDAP_GROUP_MAPPER_NAME' actualizado (id $GROUP_MAPPER_ID)"
        fi
    else
        warn "LDAP_GROUPS_DN vacío: no se crea el mapper de grupos; los usuarios de AD solo tendrán el rol requester"
    fi

    if ! test_ldap_connection "$LDAP_ID"; then
        warn "se omite la sincronización inicial; Keycloak reintentará en la próxima sincronización periódica o al iniciar sesión"
        return 0
    fi

    if [ -n "$GROUP_MAPPER_ID" ]; then
        if kc create "user-storage/$LDAP_ID/mappers/$GROUP_MAPPER_ID/sync?direction=fedToKeycloak" \
            -r "$KC_REALM" -b '{}' -o >"$WORKDIR/sync.log" 2>&1; then
            log "grupos sincronizados desde AD: $(tr -s '\n ' '  ' <"$WORKDIR/sync.log")"
        else
            warn "falló la sincronización de grupos: $(tr -s '\n ' '  ' <"$WORKDIR/sync.log")"
        fi
    fi

    if is_true "$LDAP_SYNC_USERS_ON_START"; then
        if kc create "user-storage/$LDAP_ID/sync?action=triggerFullSync" \
            -r "$KC_REALM" -b '{}' -o >"$WORKDIR/sync.log" 2>&1; then
            log "usuarios sincronizados desde AD: $(tr -s '\n ' '  ' <"$WORKDIR/sync.log")"
        else
            warn "falló la sincronización completa de usuarios: $(tr -s '\n ' '  ' <"$WORKDIR/sync.log")"
        fi
    else
        log "LDAP_SYNC_USERS_ON_START=false: los usuarios se importan al iniciar sesión y en la sincronización periódica"
    fi
}

# --- principal ------------------------------------------------------------------

main() {
    [ -x "$KCADM" ] || die "no se encontró kcadm.sh en $KCADM"
    login
    configure_web_client
    harden_offline_access
    ensure_team_groups
    configure_lab_users
    configure_ldap
    apply_group_role_map
    log "configuración de Keycloak completada"
}

main "$@"
