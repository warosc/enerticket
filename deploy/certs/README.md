# Certificados TLS (TLS_MODE=files)

Coloque aquí el certificado del dominio público (`APP_DOMAIN`) cuando no se use ACME:

- `tls.crt`: certificado del servidor **seguido de las CA intermedias** (cadena completa, PEM).
- `tls.key`: clave privada sin contraseña (PEM). Permisos recomendados: `chmod 600`.

Docker Compose monta esta carpeta (o la indicada en `TLS_CERTS_DIR`) en `/etc/caddy/certs`
en modo solo lectura. Tras renovar el certificado:

```bash
docker compose -f docker-compose.prod.yml exec caddy caddy reload --config /etc/caddy/Caddyfile
```

Los archivos de esta carpeta **no** se versionan (ver `.gitignore`). Guía: `docs/DEPLOYMENT.md`.
