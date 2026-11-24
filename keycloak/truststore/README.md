# Truststore de Keycloak

Coloque aquí el certificado de la CA raíz (y las intermedias) que firmó los
certificados de los controladores de dominio de Energuate, en formato PEM
(`.pem` o `.crt`) o PKCS12 (`.p12`/`.pfx`).

Docker Compose monta esta carpeta en `/opt/keycloak/conf/truststores` y Keycloak
la usa mediante `KC_TRUSTSTORE_PATHS` para validar LDAPS/StartTLS contra el AD.

Los archivos de esta carpeta **no** se versionan (ver `.gitignore`). Tras añadir
o cambiar un certificado: `docker compose restart keycloak`.

Guía completa: `docs/AD_INTEGRATION.md`.
