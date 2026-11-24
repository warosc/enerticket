#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Energuate Service Desk — obtiene un access token de prueba de Keycloak.
#
# Ejecuta el mismo flujo que el portal (Authorization Code + PKCE S256 con el
# cliente público servicedesk-web), por lo que NO requiere habilitar "direct
# access grants". Pensado para pruebas de integración y diagnóstico.
#
# Uso:
#   SD_TEST_PASSWORD='...' scripts/get-test-token.sh -u ana.telecom            # imprime el token
#   SD_TEST_PASSWORD='...' scripts/get-test-token.sh -u ana.telecom --claims   # token decodificado
#   TOKEN=$(SD_TEST_PASSWORD='...' scripts/get-test-token.sh -u ana.telecom)
#   curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/me
#
# Opciones (o variables de entorno):
#   -u USER       usuario (SD_TEST_USER)                         [obligatorio]
#   -k URL        URL pública de Keycloak (SD_KEYCLOAK_URL)       [http://localhost:8081]
#                 producción: https://<APP_DOMAIN>/auth
#   -r REALM      realm (SD_REALM)                                [energuate]
#   -c CLIENT     client_id (SD_CLIENT_ID)                        [servicedesk-web]
#   -w URL        URL del portal registrada como redirect (SD_WEB_URL) [http://localhost:3000]
#   --claims      imprime el payload JSON del access token en lugar del token
#   --json        imprime la respuesta completa del endpoint /token
# La contraseña se toma de SD_TEST_PASSWORD o se pide por la terminal.
# -----------------------------------------------------------------------------
set -euo pipefail

user=${SD_TEST_USER:-}
kc_url=${SD_KEYCLOAK_URL:-http://localhost:8081}
realm=${SD_REALM:-energuate}
client_id=${SD_CLIENT_ID:-servicedesk-web}
web_url=${SD_WEB_URL:-http://localhost:3000}
output=token

die() { printf 'get-test-token: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case $1 in
        -u) user=${2:-}; shift 2 ;;
        -k) kc_url=${2:-}; shift 2 ;;
        -r) realm=${2:-}; shift 2 ;;
        -c) client_id=${2:-}; shift 2 ;;
        -w) web_url=${2:-}; shift 2 ;;
        --claims) output=claims; shift ;;
        --json) output=json; shift ;;
        -h | --help) sed -n '2,27p' "$0"; exit 0 ;;
        *) die "opción desconocida: $1 (use --help)" ;;
    esac
done

[ -n "$user" ] || die "indique el usuario con -u o SD_TEST_USER"
for bin in curl openssl sed tr; do
    command -v "$bin" >/dev/null 2>&1 || die "falta el comando '$bin'"
done

password=${SD_TEST_PASSWORD:-}
if [ -z "$password" ]; then
    [ -t 0 ] || die "defina SD_TEST_PASSWORD (no hay terminal para pedir la contraseña)"
    printf 'Contraseña de %s: ' "$user" >&2
    IFS= read -rs password
    printf '\n' >&2
fi

kc_url=${kc_url%/}
web_url=${web_url%/}
base="$kc_url/realms/$realm/protocol/openid-connect"
redirect_uri="$web_url/"

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
jar="$workdir/cookies.txt"

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
urlencode() {
    local s=$1 out="" c i
    for ((i = 0; i < ${#s}; i++)); do
        c=${s:i:1}
        case $c in
            [a-zA-Z0-9.~_-]) out+=$c ;;
            *) out+=$(printf '%%%02X' "'$c") ;;
        esac
    done
    printf '%s' "$out"
}

verifier=$(openssl rand 48 | b64url)
challenge=$(printf '%s' "$verifier" | openssl dgst -sha256 -binary | b64url)
state=$(openssl rand 12 | b64url)
nonce=$(openssl rand 12 | b64url)

# 1) Página de login (sesión + URL de acción del formulario).
auth_url="$base/auth?client_id=$(urlencode "$client_id")&response_type=code&scope=openid"
auth_url+="&redirect_uri=$(urlencode "$redirect_uri")&state=$state&nonce=$nonce"
auth_url+="&code_challenge=$challenge&code_challenge_method=S256"
login_page=$(curl -sS --fail -c "$jar" -b "$jar" "$auth_url") ||
    die "no se pudo abrir la página de login en $base/auth (¿Keycloak arriba? ¿URL/realm correctos?)"
action=$(printf '%s' "$login_page" | tr -d '\n' | sed -n 's/.*<form[^>]*id="kc-form-login"[^>]*action="\([^"]*\)".*/\1/p')
[ -n "$action" ] || action=$(printf '%s' "$login_page" | tr -d '\n' | sed -n 's/.*<form[^>]*action="\([^"]*\)"[^>]*id="kc-form-login".*/\1/p')
[ -n "$action" ] || die "no se encontró el formulario de login (¿redirect_uri $redirect_uri no registrado en el cliente?)"
action=$(printf '%s' "$action" | sed 's/&amp;/\&/g')

# 2) Envío de credenciales; Keycloak responde 302 al redirect_uri con ?code=.
headers=$(curl -sS -o "$workdir/login.html" -D - -c "$jar" -b "$jar" \
    --data-urlencode "username=$user" --data-urlencode "password=$password" \
    --data-urlencode "credentialId=" "$action")
location=$(printf '%s' "$headers" | tr -d '\r' | sed -n 's/^[Ll]ocation: //p' | tail -n 1)
case $location in
    *"code="*) ;;
    *)
        msg=$(tr -d '\n' <"$workdir/login.html" | sed -n 's/.*<span[^>]*id="input-error"[^>]*>\([^<]*\)<.*/\1/p' | sed 's/^ *//;s/ *$//')
        die "login rechazado para '$user'${msg:+: $msg} (credenciales, usuario deshabilitado o acción requerida pendiente)"
        ;;
esac
code=$(printf '%s' "$location" | sed -n 's/.*[?&]code=\([^&#]*\).*/\1/p')
[ -n "$code" ] || die "no se pudo extraer el código de autorización"

# 3) Canje del código por tokens con el code_verifier (PKCE).
response=$(curl -sS --fail-with-body "$base/token" \
    --data-urlencode "grant_type=authorization_code" \
    --data-urlencode "client_id=$client_id" \
    --data-urlencode "code=$code" \
    --data-urlencode "redirect_uri=$redirect_uri" \
    --data-urlencode "code_verifier=$verifier") || die "el endpoint /token rechazó el código: $response"

token=$(printf '%s' "$response" | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')
[ -n "$token" ] || die "respuesta de /token sin access_token: $response"

case $output in
    json) printf '%s\n' "$response" ;;
    claims)
        payload=$(printf '%s' "$token" | cut -d. -f2 | tr '_-' '/+')
        case $((${#payload} % 4)) in
            2) payload+="==" ;;
            3) payload+="=" ;;
        esac
        printf '%s' "$payload" | openssl base64 -d -A
        printf '\n'
        ;;
    *) printf '%s\n' "$token" ;;
esac
