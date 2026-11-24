# Integración con el Active Directory de Energuate

El Service Desk no habla directamente con el AD: **Keycloak** federa los usuarios del AD (solo
lectura) y emite los tokens OIDC que consumen el portal y la API. Toda la configuración la aplica
de forma idempotente el contenedor one-shot `keycloak-config` (`keycloak/configure-ldap.sh`) a
partir de variables `LDAP_*` del archivo `.env`; no hay credenciales en el repositorio.

```mermaid
flowchart LR
    U[Usuario] -->|usuario + contraseña de AD| KC[Keycloak<br/>realm energuate]
    KC -->|1. bind cuenta de servicio<br/>2. búsqueda sAMAccountName<br/>3. bind del usuario| AD[(Active Directory<br/>LDAPS 636 / StartTLS 389)]
    KC -->|sincronización periódica<br/>usuarios y grupos| AD
    KC -->|JWT: realm_access.roles, groups, department| API[API Service Desk]
    API -->|groups ↔ teams.external_group| EQ[Equipos del Service Desk]
    API -->|department ↔ department_aliases| DEP[Departamento y gerencia del usuario]
    API -->|groups ∋ VIP_GROUP / gerencias.external_group| VIP[VIP y visores de gerencia]
```

## 1. Qué se necesita del equipo de AD

| Elemento | Ejemplo | Notas |
|---|---|---|
| Controlador(es) de dominio | `dc01.energuate.local` | Nombre que coincida con el certificado (SAN). Varios: `ldaps://dc01:636 ldaps://dc02:636` (separados por espacio). |
| Protocolo | LDAPS (636) o LDAP + StartTLS (389) | **Obligatorio cifrado.** En producción `LDAP_ALLOW_INSECURE` está fijado a `false`. |
| CA corporativa | `energuate-root-ca.pem` (+ intermedias) | Para validar el certificado de los DC. |
| Cuenta de servicio | `CN=svc-servicedesk-ldap,OU=Servicios,DC=energuate,DC=local` | **Solo lectura**, contraseña que no expire (o con proceso de rotación), sin derechos de inicio de sesión interactivo. |
| DN base de usuarios | `OU=Usuarios,DC=energuate,DC=local` | Se recorre con alcance `subtree` (configurable). |
| DN base de grupos | `OU=Grupos,OU=ServiceDesk,DC=energuate,DC=local` | Donde viven los grupos `GG-SD-*`. |
| Grupos de rol | `GG-SD-Admins`, `GG-SD-Agentes`, `GG-SD-Aprobadores` | Dan los roles de la aplicación. |
| Grupos de equipo | `GG-SD-Telecomunicaciones`, `GG-SD-SeguridadRedes`, `GG-SD-SOC`, `GG-SD-Riesgo`, `GG-SD-ProyectosTI`, `GG-SD-General` | Asignan al agente a su equipo/departamento. |
| Atributo de departamento | `department` (p. ej. `07 COMERCIAL`) | Departamento del usuario (Fase 3). Otro atributo: `LDAP_DEPARTMENT_ATTRIBUTE`. |
| Grupo VIP | `GG-SD-VIP` | Usuarios VIP (`VIP_GROUP`). Opcional. |
| Grupos de gerencia | p. ej. `GG-SD-Gerencia-Comercial` | Opcionales: sus miembros ven en solo lectura los tickets solicitados por la gerencia. |

### Permisos de la cuenta de servicio

Basta con un usuario de dominio estándar (los usuarios autenticados pueden leer el directorio por
defecto). Recomendaciones:

- Miembro únicamente de `Domain Users`; **no** administrador ni operador de cuentas.
- "La cuenta es confidencial y no se puede delegar"; denegar inicio de sesión local y por RDP (GPO).
- Si se usan ACL restrictivas, conceder lectura sobre `sAMAccountName`, `cn`, `givenName`, `sn`,
  `mail`, `userPrincipalName`, `objectGUID`, `memberOf`, `member`, `userAccountControl`,
  `whenCreated`, `whenChanged` y `department` (o el atributo de `LDAP_DEPARTMENT_ATTRIBUTE`) en las OU
  de usuarios y grupos.
- Keycloak opera en modo `READ_ONLY`: nunca escribe en el AD, y los cambios de contraseña se hacen
  en el AD (no desde Keycloak).

## 2. Mapeo de atributos

| Keycloak | AD | Comentario |
|---|---|---|
| Username | `sAMAccountName` | Lo que escribe el usuario al iniciar sesión (sin dominio). |
| RDN | `cn` | |
| UUID (identificador estable) | `objectGUID` | No cambia aunque se renombre o mueva la cuenta. Es el `sub` del token → `users.keycloak_sub`. |
| Clases de objeto | `person, organizationalPerson, user` | |
| Nombre / Apellido / Correo | `givenName` / `sn` / `mail` | Mappers por defecto del vendor `ad`. |
| Grupos | `group-ldap-mapper` (`cn`, membresía `member`) | READ_ONLY, sin jerarquía (`groups.path=/`). |
| Atributo `department` | `department` (`LDAP_DEPARTMENT_ATTRIBUTE`) | Mapper `department-ad` (`user-attribute-ldap-mapper`), solo lectura y leído siempre del AD. Llega al token como claim `department` (mapper `department` del cliente `servicedesk-web`). |

El inicio de sesión también acepta el correo (`loginWithEmailAllowed`).

## 3. Configuración (`.env`)

```dotenv
# LDAPS (recomendado)
LDAP_URL=ldaps://dc01.energuate.local:636
LDAP_START_TLS=false
# ...o StartTLS:
# LDAP_URL=ldap://dc01.energuate.local:389
# LDAP_START_TLS=true

LDAP_BIND_DN=CN=svc-servicedesk-ldap,OU=Servicios,DC=energuate,DC=local
LDAP_BIND_CREDENTIAL=********            # solo en .env del servidor
LDAP_USERS_DN=OU=Usuarios,DC=energuate,DC=local
LDAP_GROUPS_DN=OU=Grupos,OU=ServiceDesk,DC=energuate,DC=local

# Solo cuentas habilitadas (bit ACCOUNTDISABLE de userAccountControl)
LDAP_USER_FILTER='(!(userAccountControl:1.2.840.113556.1.4.803:=2))'
# Opcional: solo miembros de algún grupo del Service Desk (incluye anidados)
# LDAP_USER_FILTER='(&(!(userAccountControl:1.2.840.113556.1.4.803:=2))(memberOf:1.2.840.113556.1.4.1941:=CN=GG-SD-Usuarios,OU=Grupos,OU=ServiceDesk,DC=energuate,DC=local))'
LDAP_GROUP_FILTER='(cn=GG-SD-*)'
LDAP_GROUPS_RECURSIVE=false              # true = resuelve grupos anidados (LDAP_MATCHING_RULE_IN_CHAIN)

LDAP_SEARCH_SCOPE=subtree
LDAP_PAGINATION=true                     # AD limita a 1000 resultados por página
LDAP_BATCH_SIZE=500
LDAP_CONNECTION_TIMEOUT=10000            # ms
LDAP_READ_TIMEOUT=30000                  # ms
LDAP_FULL_SYNC_PERIOD=86400              # s (-1 desactiva)
LDAP_CHANGED_SYNC_PERIOD=3600            # s (-1 desactiva)
LDAP_SYNC_USERS_ON_START=false           # true = importa todos al ejecutar keycloak-config

LDAP_DEPARTMENT_ATTRIBUTE=department         # vacío = no se mapea el departamento
VIP_GROUP=GG-SD-VIP                          # grupo de usuarios VIP (también lo usa la API)

KC_GROUP_ROLE_MAP=GG-SD-Admins:admin,GG-SD-Agentes:agent,GG-SD-Aprobadores:approver
SEED_TEAM_GROUPS="Telecomunicaciones:GG-SD-Telecomunicaciones,Seguridad de Redes:GG-SD-SeguridadRedes,Ciberseguridad / SOC:GG-SD-SOC,Riesgo y Cumplimiento:GG-SD-Riesgo,Proyectos TI:GG-SD-ProyectosTI,General:GG-SD-General"
```

Notas:
- Los filtros deben ir entre paréntesis y, en `.env`, entre comillas simples.
- `$` en la contraseña: escríbala entre comillas simples o duplique el símbolo (`$$`).
- `LDAP_USER_FILTER` también aplica al login: una cuenta deshabilitada en AD deja de poder
  entrar de inmediato, aunque siga importada en Keycloak.

## 4. Certificado de la CA corporativa (LDAPS / StartTLS)

Keycloak valida el certificado de los DC con su *truststore*. **No** desactive la validación.

1. Obtenga la CA raíz (y las intermedias) en PEM. Desde un equipo del dominio:
   ```powershell
   certutil -ca.cert energuate-root-ca.cer
   certutil -encode energuate-root-ca.cer energuate-root-ca.pem
   ```
   o desde Linux, para ver la cadena que presenta el DC:
   ```bash
   openssl s_client -connect dc01.energuate.local:636 -showcerts </dev/null
   ```
2. Copie el/los `.pem` en `keycloak/truststore/` (o en la carpeta de `KC_TRUSTSTORE_DIR`). La carpeta
   no se versiona.
3. Reinicie Keycloak y reaplique la configuración:
   ```bash
   docker compose -f docker-compose.prod.yml restart keycloak
   docker compose -f docker-compose.prod.yml up keycloak-config
   ```

Keycloak carga la carpeta mediante `KC_TRUSTSTORE_PATHS=/opt/keycloak/conf/truststores`
(archivos `.pem`, `.crt`, `.p12`, `.pfx`) y el proveedor LDAP usa `useTruststoreSpi=always`. El nombre
de `LDAP_URL` debe coincidir con el CN/SAN del certificado del DC (no use la IP).

## 5. Aplicar y probar

```bash
# Producción
docker compose -f docker-compose.prod.yml up keycloak-config
docker compose -f docker-compose.prod.yml logs keycloak-config
# Laboratorio
docker compose up keycloak-config && docker compose logs keycloak-config
```

Salida esperada:

```
[keycloak-config] federación LDAP 'energuate-ad' creada (id …)
[keycloak-config] grupo VIP 'GG-SD-VIP' presente en Keycloak
[keycloak-config] perfil de usuario: atributo 'department' declarado (edición solo administradores)
[keycloak-config] cliente 'servicedesk-web': mapper del claim 'department' creado
[keycloak-config] mapper de grupos 'groups-ad' creado (id …)
[keycloak-config] mapper de departamento 'department-ad' creado (department -> department, id …)
[keycloak-config] prueba de conexión y bind contra AD: correcta
[keycloak-config] grupos sincronizados desde AD: …
[keycloak-config] grupo 'GG-SD-Admins' -> rol 'admin'
[keycloak-config] configuración de Keycloak completada
```

El script valida antes de aplicar: esquema `ldap://`/`ldaps://`, StartTLS incompatible con
`ldaps://`, cifrado obligatorio, filtros entre paréntesis, enteros válidos y que no queden valores
`replace-...`. Si la prueba de conexión falla, deja la federación creada (Keycloak reintenta) e
informa del motivo.

### Prueba de un usuario real

1. Abra `https://<APP_DOMAIN>/` e inicie sesión con un usuario de AD (sAMAccountName).
2. Verifique en el portal (menú de usuario) sus roles y equipos, o con la API:
   ```bash
   SD_KEYCLOAK_URL=https://<APP_DOMAIN>/auth SD_WEB_URL=https://<APP_DOMAIN> \
     scripts/get-test-token.sh -u jperez --claims
   ```
   El token debe contener `aud: servicedesk-api`, `realm_access.roles` (p. ej. `agent`),
   `groups` (p. ej. `["GG-SD-Agentes","GG-SD-SOC"]`) y `department` (p. ej. `"07 COMERCIAL"`).
3. La API crea el usuario en su primer acceso (JIT), lo asigna a los equipos cuyo
   `teams.external_group` coincide con sus grupos y resuelve su departamento (ver sección 7).

### Prueba de conectividad desde el host (sin Keycloak)

```bash
# Resolución y puerto
getent hosts dc01.energuate.local && nc -vz dc01.energuate.local 636
# Certificado y cadena (debe terminar en "Verify return code: 0 (ok)" con la CA corporativa)
openssl s_client -connect dc01.energuate.local:636 -CAfile keycloak/truststore/energuate-root-ca.pem </dev/null
# Búsqueda con la cuenta de servicio (paquete ldap-utils)
LDAPTLS_CACERT=keycloak/truststore/energuate-root-ca.pem ldapsearch -H ldaps://dc01.energuate.local \
  -D 'CN=svc-servicedesk-ldap,OU=Servicios,DC=energuate,DC=local' -W \
  -b 'OU=Usuarios,DC=energuate,DC=local' '(sAMAccountName=jperez)' sAMAccountName memberOf
```

## 6. Roles y equipos

- **Roles** (`KC_GROUP_ROLE_MAP`): cada grupo recibe un rol de realm. Todo usuario autenticado tiene
  además `requester` (rol por defecto del realm). Un usuario en `GG-SD-Agentes` y `GG-SD-SOC` obtiene
  `agent` y queda en el equipo SOC.
- **Equipos** (`SEED_TEAM_GROUPS` → `teams.external_group`): la API sincroniza `team_members` en cada
  petición según el claim `groups` (sin ruta completa). Si el usuario sale de todos los grupos,
  Keycloak omite el claim y la API retira sus membresías mapeadas (`OIDC_MISSING_GROUPS_AS_EMPTY`).
  Un administrador puede cambiar el grupo de un equipo desde *Administración → Equipos*.
- Los grupos se crean en Keycloak si no existen; cuando el AD sincroniza un grupo con el mismo nombre,
  Keycloak reutiliza el grupo existente (y conserva su rol).

Para añadir un área nueva: cree el grupo en AD (`GG-SD-<Área>`), cree el equipo en la aplicación con
ese `external_group`, y ejecute `keycloak-config` (o espere a la sincronización periódica).

## 7. Departamento, gerencia y VIP (Fase 3)

### Atributo `department` → departamento del usuario

1. El AD guarda el departamento como texto libre en el atributo `department` (a veces con un código
   delante: `07 COMERCIAL`, `0903 Distribución`). Keycloak lo copia al atributo de usuario
   `department` (mapper LDAP `department-ad`) y lo envía en el claim `department` del token.
2. En cada login la API **normaliza** el valor (mayúsculas, sin tildes, sin prefijo numérico ni
   espacios sobrantes: `07 Comercial` → `COMERCIAL`) y lo busca en los **alias** de departamento
   (`department_aliases`).
3. Si el alias ya está asignado, actualiza `users.department_id`. Si el valor del AD **cambió** y el
   alias está pendiente (o sin departamento), el usuario queda **sin departamento** hasta que se
   asigne: así la gerencia anterior deja de ver sus tickets nuevos. Si el valor no cambió, se respeta
   el departamento fijado a mano por un administrador. Si es un valor nuevo:
   - si coincide con el nombre de un departamento existente (p. ej. departamento «Comercial»), el alias
     se crea ya asignado;
   - si no, queda **pendiente** y aparece resaltado para los administradores en
     **Administración → Valores del AD**, donde se asigna a un departamento (también se pueden
     registrar alias por adelantado). Al asignarlo, los usuarios que ya traían ese valor se
     actualizan **al instante** y sus tickets creados mientras el valor estaba pendiente reciben el
     departamento solicitante (la gerencia los ve y los reportes los atribuyen); los demás, en su
     siguiente login. Retirar el departamento de un alias también lo retira de esos usuarios.
4. Si el token no trae `department` (atributo vacío o mapper no configurado), la API no toca el
   departamento actual; el administrador puede fijarlo a mano en **Administración → Usuarios**
   (el siguiente login con claim `department` vuelve a mandar el AD).

Varios valores del AD pueden apuntar al mismo departamento (`COMERCIAL`, `GERENCIA COMERCIAL`,
`VENTAS`…). Los departamentos **solicitantes** (los del AD) no tienen por qué atender tickets: la
casilla «Atiende tickets» (`serves_tickets`) distingue los departamentos de atención (Telecomunicaciones,
Soporte Técnico…) que aparecen al crear un ticket.

Keycloak 26 tiene el **perfil de usuario declarativo** siempre activo y descarta los atributos que no
están declarados. `keycloak-config` declara `department` en *Realm settings → User profile* (lectura
para el usuario y administradores, edición solo administradores); los realms nuevos ya lo traen en
`realm-energuate.json`. Si se ve el atributo vacío en un usuario de AD, revise que el mapper
`department-ad` exista en *User federation → energuate-ad → Mappers* y que el usuario tenga el
atributo en el AD.

### Gerencias

Cada departamento pertenece (opcionalmente) a una **gerencia** (*Administración → Gerencias* y
*Departamentos*). La gerencia del solicitante se guarda en cada ticket a través de su departamento y
permite reportar «tickets por gerencia solicitante».

Visores de gerencia (solo lectura de los tickets **solicitados** por los departamentos de la gerencia,
sin poder editar, transicionar ni ver comentarios internos):
- **Por grupo AD**: el campo «Grupo AD» de la gerencia (`gerencias.external_group`), p. ej.
  `GG-SD-Gerencia-Comercial`. Los miembros del grupo (claim `groups`) son visores mientras la gerencia
  esté activa. Cree el grupo en AD bajo `LDAP_GROUPS_DN` para que la sincronización lo traiga.
- **Manual**: *Administración → Gerencias → Visores* (`gerencia_viewers`).

Los visores generan reportes dentro de ese mismo alcance (ver [REPORTS.md](REPORTS.md)).

### Usuarios VIP

`VIP_GROUP` (por defecto `GG-SD-VIP`): en cada login, si el token trae `groups`, `users.vip` refleja la
pertenencia al grupo (sin distinguir mayúsculas). Los tickets que crea un VIP quedan marcados (`vip`)
y una política SLA puede aplicarse **solo a VIP** (*SLA → Políticas → Solo VIP*). `keycloak-config`
crea el grupo en Keycloak si no existe (con AD, se vincula por nombre al sincronizar). El
administrador también puede marcar/desmarcar VIP a mano en *Administración → Usuarios*; el siguiente
login con grupos lo vuelve a calcular.

## 8. Sincronización

| Tipo | Periodo | Variable |
|---|---|---|
| Completa (usuarios) | 24 h | `LDAP_FULL_SYNC_PERIOD` |
| Cambios (`whenChanged`) | 1 h | `LDAP_CHANGED_SYNC_PERIOD` |
| Grupos | al ejecutar `keycloak-config` y con cada sincronización | — |
| Al iniciar sesión | siempre | Keycloak consulta el AD en cada login (contraseña y estado). |

El atributo `department` se lee del AD en cada login (`always.read.value.from.ldap`), así que un
cambio de departamento llega al portal en el siguiente inicio de sesión sin esperar la sincronización.

Sincronización manual: consola de Keycloak → realm `energuate` → *User federation* →
`energuate-ad` → *Synchronize all users*; grupos: *Mappers* → `groups-ad` → *Sync LDAP groups to
Keycloak*. (La consola solo es accesible desde `ADMIN_ALLOWED_CIDRS`.)

Bajas: al deshabilitar la cuenta en AD, el usuario no puede iniciar sesión (filtro + bind). Las
sesiones activas expiran como máximo en `ssoSessionIdleTimeout` (30 min); para cortar
inmediatamente, cierre sus sesiones en Keycloak (*Users → Sessions → Sign out*).

## 9. Solución de problemas

| Síntoma (logs de `keycloak-config` / `keycloak`) | Causa probable | Acción |
|---|---|---|
| `PKIX path building failed` / `unable to find valid certification path` | Falta la CA en el truststore | Copie la CA raíz e intermedias (PEM) en `keycloak/truststore/` y reinicie Keycloak. |
| `No subject alternative DNS name matching` | `LDAP_URL` usa IP o un alias que no está en el certificado | Use el FQDN del DC que aparece en el SAN. |
| `Connection refused` / `timeout` | Firewall o DNS | Abra 636/389 desde el servidor hacia los DC; pruebe con `nc -vz`. El contenedor sale por la red `egress`. |
| `LDAP: error code 49 - 80090308 … data 52e` | Credenciales de la cuenta de servicio incorrectas | Revise `LDAP_BIND_DN` (DN completo o `usuario@dominio`) y la contraseña. |
| `data 532` / `data 773` / `data 775` | Contraseña expirada / debe cambiarse / cuenta bloqueada | Corrija la cuenta de servicio en AD. |
| `Sizelimit exceeded` | Paginación desactivada | `LDAP_PAGINATION=true`. |
| Usuario entra pero sin rol `agent` | No está en el grupo o el grupo no está bajo `LDAP_GROUPS_DN` / no cumple `LDAP_GROUP_FILTER` | Verifique `memberOf`; sincronice grupos; revise `KC_GROUP_ROLE_MAP`. |
| Grupos anidados no aplican | Estrategia sin recursión | `LDAP_GROUPS_RECURSIVE=true`. |
| Usuario no aparece en su equipo | `groups` del token no coincide con `teams.external_group` | Compare con `get-test-token.sh --claims`; ajuste el equipo en Administración. |
| `una variable LDAP_* conserva el valor de ejemplo` | `.env` con `replace-...` | Complete los valores o deje `LDAP_URL=` vacío para omitir AD. |
| `ldap:// sin StartTLS envía la contraseña … en claro` | Falta cifrado | Use `ldaps://` o `LDAP_START_TLS=true`. |
| El token no trae `department` | Atributo vacío en AD, mapper `department-ad` ausente o atributo no declarado en el perfil de usuario | Revise el usuario en Keycloak (pestaña *Attributes*/*Details*), el mapper y *Realm settings → User profile*; ejecute `keycloak-config`. |
| Usuario sin departamento en el portal | Valor del AD pendiente de asignar | *Administración → Valores del AD*: asigne el valor resaltado a un departamento. |
| VIP no se actualiza | El token no trae `groups` o el grupo no coincide con `VIP_GROUP` | Compare con `get-test-token.sh --claims`. |

Logs detallados de LDAP (temporalmente): `KC_LOG_LEVEL=info,org.keycloak.storage.ldap:debug` y
`docker compose … up -d keycloak`. Vuelva a `info` al terminar: el nivel debug puede registrar DN y
atributos de usuarios.

## 10. Laboratorio sin AD

Con `LDAP_URL` vacío no se crea federación. Para pruebas, `KC_LAB_USERS=true` (solo en
`docker-compose.yml`) crea usuarios locales con los grupos anteriores: `demo.admin`, `ana.telecom`,
`luis.soc`, `rosa.redes`, `marta.aprobadora`, `carlos.solicitante` y `sofia.solicitante` (contraseña
`KC_LAB_PASSWORD`), con su atributo `department` como llegaría del AD:

| Usuario | `department` | Departamento (con `SEED_DEMO=true`) | Grupos extra |
|---|---|---|---|
| `demo.admin` | `Sistemas y Telecomunicaciones` | Sistemas y Telecomunicaciones (Gerencia de Tecnología) | |
| `ana.telecom` | `Telecomunicaciones` | Telecomunicaciones | |
| `luis.soc`, `rosa.redes` | `SISTEMAS Y TELECOMUNICACIONES` | Sistemas y Telecomunicaciones | |
| `marta.aprobadora` | `SERVICIOS CORPORATIVOS` | Servicios Corporativos | visor manual de la Gerencia Comercial |
| `carlos.solicitante` | `07 COMERCIAL` | Comercial (Gerencia Comercial) | |
| `sofia.solicitante` | `DISTRIBUCION` | Distribución (Gerencia de Distribución) | `GG-SD-VIP` |

En modo `AUTH_MODE=dev` el departamento se simula con la cabecera `X-Dev-Department` (y el VIP con
`X-Dev-Groups`). En producción `KC_LAB_USERS` está fijado a `false` y esos usuarios se
deshabilitan si existieran.
