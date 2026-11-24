# Reportes y catálogo de servicio — guía de uso

Guía para las personas que usan el portal: cómo armar un reporte, guardarlo como plantilla,
programar su envío por correo y qué datos ve cada rol. Al final, el formato del archivo CSV con el
que el administrador carga el catálogo de categorías.

## 1. Qué se puede medir

Los reportes se calculan sobre los **tickets**, con su clasificación de la Fase 3:

- **Departamento de atención** → **categoría** → **subcategoría** (el catálogo que carga el
  administrador para cada departamento que atiende tickets).
- **Tipo**: Incidencia, Petición u Orden de trabajo.
- **Gerencia y departamento solicitante**: salen del departamento del AD de quien pidió el ticket.
- **Proveedor**, **canal** (Portal, Correo, Teléfono, Otro), **prioridad**, **VIP**, **técnico
  asignado** y **quién resolvió**.
- **Horas hábiles de resolución**: se cuentan solo dentro del horario hábil del SLA (o, si el ticket
  no tiene SLA, del calendario «Horario hábil Guatemala»: lunes a viernes de 08:00 a 17:00 sin
  feriados) y descuentan el tiempo «En espera». Se guardan al resolver el ticket. Son las mismas
  unidades que el **objetivo SLA** del ticket: en una política 24x7 (p. ej. «Crítica 24x7») cuentan
  todas las horas, igual que su objetivo. Para comparar solo horario de oficina, filtre por prioridad
  o separe las políticas 24x7 en otra sección.

Todas las fechas se agrupan en hora de Guatemala.

## 2. Pestañas de *Reportes*

| Pestaña | Para qué |
|---|---|
| **Resumen** | El tablero de la Fase 2: totales, tendencia, antigüedad y SLA. |
| **Generador** | Arma un reporte a medida, lo muestra en pantalla y lo exporta a Excel. |
| **Plantillas** | Reportes guardados: propios, compartidos contigo y los de fábrica. |

Arriba de los resultados siempre se ve el **alcance**, por ejemplo «Viendo: tickets de
Telecomunicaciones, además de los tuyos». Ese alcance no se puede ampliar con filtros (ver sección 5).

## 3. Generador de reportes

### Paso a paso

1. **Periodo**: Este mes, Mes anterior, Últimos 30 días, Este año, Año anterior o Personalizado
   (desde/hasta, máximo 366 días).
2. **Campo de fecha**: *Fecha de solicitud* (cuándo se creó) o *Fecha de solución* (cuándo se
   resolvió). Para informes de desempeño o de proveedor use *Fecha de solución*.
3. **Filtros generales** (opcionales, selección múltiple): tipo, departamento de atención, equipo,
   categoría, subcategoría, proveedor, gerencia, departamento solicitante, técnico, prioridad, canal y
   VIP. Se aplican a todas las secciones.
4. **Secciones** (de 1 a 10). Cada una es una tabla dinámica con:
   - **Filas**: una o dos dimensiones (p. ej. *Categoría*, o *Categoría* y *Técnico asignado*).
   - **Columnas**: ninguna o una dimensión (p. ej. *Mes* o *Tipo*).
   - **Métricas**: de 1 a 8 (tabla siguiente).
   - **Filtros de la sección** (además de los generales) y si se muestran **totales**.
5. **Vista previa**: la tabla aparece en pantalla con la fila y la columna de totales («Acumulado»).
   En el celular la tabla se desplaza horizontalmente dentro de su recuadro.
6. **Exportar Excel**: un libro `.xlsx` con una hoja por sección y, si se marca, la hoja **Detalle**
   (una fila por ticket, hasta 50 000 filas). Si el periodo tiene más tickets, la hoja «Detalle» y
   el encabezado de cada sección muestran el aviso «Detalle recortado…» con el total; acote el periodo
   o los filtros para obtenerlo completo (las hojas de sección siempre cuentan todos los tickets).
7. **Guardar como plantilla** para repetirlo cuando quiera (sección 4).

### Dimensiones (filas y columnas)

Tipo · Departamento de atención · Equipo · Gerencia solicitante · Departamento solicitante ·
Categoría · Subcategoría · Proveedor · Técnico asignado · Resuelto por · Prioridad · VIP · Canal ·
Estado · Día · Semana · Mes · Año.

Los tickets sin dato aparecen agrupados como «Sin categoría», «Sin proveedor», «Sin asignar», etc.

### Métricas

| Métrica | Qué calcula |
|---|---|
| Cantidad de tickets | Número de tickets. |
| Horas hábiles de resolución (promedio, suma, mediana, máximo) | Sobre los tickets resueltos, con las horas hábiles guardadas al resolver. |
| Horas a primera respuesta (promedio) | Desde la creación hasta la primera respuesta de un agente. |
| Objetivo SLA de resolución (horas) | Promedio del objetivo de resolución de la política SLA aplicada. |
| Cumplimiento SLA (%) | Tickets cuya resolución cumplió el SLA ÷ tickets con SLA de resolución evaluado. |
| SLA vencidos | Tickets cuyo SLA de resolución venció. |
| Abiertos | Tickets que aún no están resueltos. |

Los totales recalculan promedios, medianas y porcentajes sobre todos los tickets de la fila o
columna (no son un promedio de promedios). Las horas se muestran con dos decimales y las celdas sin
datos quedan vacías.

### Columnas de la hoja «Detalle»

Por defecto: número, fecha de solicitud, fecha de solución, tipo, departamento, categoría,
subcategoría, proveedor, canal, prioridad, VIP, solicitante, departamento y gerencia del solicitante,
técnico, resuelto por, horas hábiles, objetivo, cumplió SLA, estado, título y solución. Se pueden
quitar columnas antes de exportar. Los textos se exportan protegidos contra fórmulas (una celda que
empieza por `=`, `+`, `-` o `@` se guarda como texto).

## 4. Plantillas

En **Plantillas** se listan:

- **Mías**: las que guardé. Puedo ejecutarlas, exportarlas, editarlas, compartirlas y eliminarlas.
- **Compartidas conmigo**: las que otra persona compartió con mi **rol** (p. ej. todos los agentes)
  o con mi **departamento**. Puedo ejecutarlas y exportarlas, o **duplicarlas** para modificarlas.
- **De fábrica**: no se pueden editar ni borrar; se **duplican** para ajustarlas.

Plantillas de fábrica:

| Plantilla | Contenido |
|---|---|
| **Informe mensual de proveedor** | Mes anterior por fecha de solución; tres secciones (Incidencias, Peticiones, Órdenes de trabajo) con filas por categoría y columnas por mes; promedio y suma de horas hábiles, cantidad, objetivo SLA y cumplimiento. Es el informe mensual que se entrega a un proveedor: duplíquela y filtre por el proveedor o el departamento que corresponda. |
| **Desempeño por técnico** | Resueltos del mes anterior por categoría y técnico: cantidad, promedio y suma de horas. |
| **Tickets por día** | Tickets creados cada día del mes en curso, por tipo. |
| **Tickets por gerencia solicitante** | Creados el mes anterior por gerencia y departamento solicitante, por tipo. |
| **Cumplimiento de SLA por categoría** | Resueltos del mes anterior por departamento y categoría: cumplimiento, vencidos y objetivo. |

Ejecutar una plantilla compartida **siempre** usa el alcance de quien la ejecuta: compartir una
plantilla comparte la *definición*, no los datos.

## 5. Alcance por rol

Todo reporte parte de los tickets que la persona puede ver en el portal; los filtros solo pueden
**reducir** ese conjunto.

| Rol | Ve en los reportes |
|---|---|
| Administrador | Todos los tickets. |
| Agente / líder de equipo | Los tickets de sus equipos y departamentos de atención, más los suyos (solicitados o asignados). |
| Solicitante | Sus propios tickets. |
| Aprobador | Sus tickets y los que le corresponde aprobar. |
| Visor de gerencia | Además de lo anterior, en solo lectura, los tickets **solicitados** por los departamentos de su gerencia (grupo AD de la gerencia o asignación manual; ver [AD_INTEGRATION.md](AD_INTEGRATION.md)). |

Ejemplo: un agente de Telecomunicaciones que filtra «Departamento de atención = Soporte Técnico» solo
obtiene los tickets de Soporte Técnico que ya podía ver (normalmente ninguno).

## 6. Envío programado por correo

Solo **administradores** y **líderes de equipo** pueden programar una plantilla:

- **Frecuencia**: mensual (día 1 a 28) o semanal (lunes a domingo), a una **hora** (hora de
  Guatemala).
- **Destinatarios**: hasta 20 correos, solo de los dominios de `REPORT_RECIPIENT_DOMAINS` (por
  defecto `energuate.com` en docker compose; solo si se define vacía se acepta cualquier dominio).
- El servicio revisa cada 15 minutos las plantillas pendientes, genera el Excel **con el alcance del
  dueño** de la plantilla y lo envía como adjunto. Use periodos relativos («Mes anterior») para que
  cada envío traiga datos nuevos.
- Si el envío falla (servidor de correo caído, tiempo de espera) se reintenta a los 15, 30 y 60
  minutos («Error al enviar (se reintentará)») sin pasar del siguiente periodo; agotados los
  reintentos queda «Error al enviar».
- Si el dueño de la plantilla no inicia sesión en `ACCOUNT_STALE_DAYS` días (45 por defecto) los
  envíos se suspenden («Dueño sin sesión reciente»): sus roles y equipos podrían haber cambiado en el
  AD. Se reanudan cuando vuelve a iniciar sesión.
- En la lista de plantillas se ven la próxima ejecución, la última y su resultado. Si el servidor no
  tiene correo configurado (`SMTP_HOST` vacío) el resultado indica que el correo está deshabilitado y
  no se envía nada.

## 7. Catálogo de categorías (administradores)

El catálogo de cada departamento de atención se mantiene en **Administración**:

- **Categorías**: por departamento, con los tipos de ticket que admite (vacío = todos) y sus
  subcategorías en línea. Desactivar en lugar de borrar conserva los reportes históricos.
- **Proveedores**: con los departamentos donde aplican.
- **Gerencias**, **Departamentos** (gerencia, «Atiende tickets», activo) y **Valores del AD**.
- **SLA → Políticas**: además de prioridad, tipo y departamento, cada política puede limitarse a
  categorías, subcategorías o a solicitantes VIP. Gana siempre la **más específica**
  (subcategoría, luego categoría, luego las generales como las de prioridad); dentro del mismo nivel
  decide la **precedencia** (menor primero).

### Importar catálogo desde CSV

*Administración → Importar catálogo*: suba el archivo, revise la **vista previa** (no cambia nada) y
luego **aplique**. La importación es idempotente: crea o reactiva lo que falte y no borra nada.

Formato:

- CSV en **UTF-8** (en Excel: *Guardar como → CSV UTF-8*), separado por comas o por punto y coma.
- Encabezado obligatorio (sin importar mayúsculas ni tildes):
  `departamento,categoria,subcategoria,tipos,ans_horas`.
- `departamento`: nombre de un departamento de atención que ya exista.
- `categoria`: obligatoria. `subcategoria`: opcional (vacía = solo la categoría).
- `tipos`: claves separadas por `|`: `incident` (Incidencia), `service_request` (Petición),
  `work_order` (Orden de trabajo). Vacío = todos los tipos. Si la categoría ya existía, se suman los
  tipos.
- `ans_horas`: opcional; horas **hábiles** de resolución (admite decimales: `1.5`, o `1,5` en archivos
  separados por punto y coma). Crea o
  actualiza la política SLA «ANS <departamento> / <categoría>[ / <subcategoría>]» con el calendario
  hábil por defecto.
- Máximo 2 MB y 5000 filas. Las filas con error se informan con su número de línea y se omiten; el
  resto se aplica.

Ejemplo (datos ficticios):

```csv
departamento,categoria,subcategoria,tipos,ans_horas
Soporte Técnico,Equipo de cómputo,Falla de hardware,incident,16
Soporte Técnico,Equipo de cómputo,Instalación de software,service_request,8
Soporte Técnico,Impresión,,incident|service_request,
Telecomunicaciones,Enlaces de datos,Caída de enlace,incident|work_order,4
Telecomunicaciones,Telefonía,Extensiones,service_request,24
Seguridad Informática,Accesos remotos,VPN,service_request,16
```

> **Importante — precedencia de las políticas importadas.** Las políticas creadas por la importación
> tienen precedencia **50**, y las políticas por prioridad que trae el sistema («Crítica 24x7», «Alta»,
> «Media», «Baja», precedencias 10 a 40) se evalúan antes y cubren todas las prioridades. Para que
> rijan los ANS del catálogo, en *SLA → Políticas* desactive las políticas por prioridad o asígneles
> una precedencia mayor que 50 (o baje la de las importadas). Los datos de demostración
> (`SEED_DEMO=true`) usan precedencia 4-5 en sus políticas por categoría para que se vean aplicadas.

Las celdas se sanean (caracteres de control y prefijos de fórmula de Excel `=`, `+`, `-`, `@` se quitan)
y los nombres se limitan a 150 caracteres.

## 8. Datos de demostración

Con `SEED_DEMO=true` (solo laboratorio) el seed `95_catalog_demo` crea una sola vez: cuatro gerencias,
departamentos solicitantes con sus valores del AD, el proveedor «Proveedor de Telecomunicaciones», un
catálogo **genérico** de TI para los cinco departamentos de atención con políticas SLA por categoría
(y una «solo VIP» de Soporte Técnico), clasifica los tickets demo y agrega unos 45 tickets resueltos
repartidos en los últimos seis meses para que los reportes tengan datos. Son datos inventados; el
catálogo real lo carga el administrador.
