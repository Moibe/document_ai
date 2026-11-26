# RAD — Guía de la API

RAD lee documentos de identidad escaneados y **contrasta lo impreso contra la redundancia del
propio documento**: la zona de lectura mecánica (MRZ) del reverso, la CURP y la clave de elector
codifican otra vez el nombre, la fecha de nacimiento, el sexo y la vigencia. La respuesta no solo
trae los datos: dice si se sostienen entre sí.

- **Base URL**: `http://SERVIDOR_RAD`
- **Swagger interactivo** (para probar desde el navegador): `/docs`
- **Llave de acceso obligatoria**: cabecera `X-API-Key: rad_…` en cada petición. Sin ella, o con
  una llave que no existe o que fue revocada, RAD responde `401 LLAVE_INVALIDA` sin procesar el
  documento. Para probar con tu llave en `/docs`: botón **Authorize**, pegas la llave y cada
  «Try it out» la manda.
- **Llamadas desde tu backend**, no desde el navegador: RAD no envía cabeceras CORS, así que una
  página en otro origen no puede leer la respuesta.

Una llamada con la llave:

```bash
curl -X POST http://SERVIDOR_RAD/procesa_ine_ambas/ \
     -H "X-API-Key: rad_…" \
     -F "frente=@anverso.jpg" -F "atras=@reverso.jpg"
```

Esta guía cubre la **credencial para votar (INE)**. Los demás endpoints se listan abajo, pero su
cuerpo aún no se documenta aquí.

[TOC]

---

## Endpoints

| Ruta | Qué hace |
|---|---|
| `POST /procesa_ine_ambas/` | **Recomendado.** Anverso y reverso de la INE en dos archivos: extrae y contrasta uno contra el otro |
| `POST /procesa_ine/` | Solo el anverso (o un PDF que ya trae las dos caras) |
| `POST /procesa_ine_atras/` | Solo el reverso: lee y verifica la MRZ |
| `POST /procesa_pasaporte/` | Pasaporte. Mismo sobre (`codigo`, `estado`, `mensaje`); si el documento no es un pasaporte responde `225` |
| `POST /procesa_cedula_panama/` | Cédula de identidad panameña. Mismo sobre; si el documento no es una cédula panameña responde `226` |
| `POST /procesa_fm/`, `/procesa_csf/`, `/procesa_cedula/` | Endpoints anteriores, cada uno con su respuesta propia (no usan el sobre de esta guía) |
| `GET /health` | `200` con `{"status": "ok"}`. No toca el motor de lectura: un `200` aquí **no** garantiza que el procesamiento funcione |

### ¿Cuál de los tres de INE?

Si tu captura toma las dos caras por separado (lo normal en una app o un formulario), usa
**`/procesa_ine_ambas/`**: es el único que contrasta el anverso contra la MRZ del reverso, que es
la comprobación que caza los errores de lectura y las credenciales armadas con piezas de dos
personas.

`/procesa_ine/` con solo el anverso hace las comprobaciones que caben en una cara (CURP contra
fecha y sexo, clave de elector contra CURP, código postal contra estado), pero la comparación
contra la MRZ queda pendiente, así que en el mejor caso responde `212`. `/procesa_ine_atras/` solo
verifica que la MRZ sea íntegra.

---

## `POST /procesa_ine_ambas/`

### Petición

`multipart/form-data` con dos archivos:

| Campo | Contenido | Requerido |
|---|---|---|
| `frente` | Anverso de la INE | sí |
| `atras` | Reverso de la INE | sí |

- **Tipos aceptados**: `application/pdf`, `image/jpeg`, `image/png`, `image/tiff`, `image/webp`.
- **Declara el tipo de cada archivo.** Una parte sin tipo, o con `application/octet-stream`, se
  rechaza con `400`. Si declaras un tipo aceptado pero el contenido es otro tipo aceptado (un PNG
  declarado como `image/jpeg`), se procesa con el tipo real.
- Un PDF puede traer varias páginas, hasta 15; con más responde `224`. No hay un tope de tamaño
  propio de la API.
- **Usa la ruta con la diagonal final.** Sin ella el servidor responde una redirección `307`; no
  dependas de que tu cliente la siga reenviando el archivo.

```bash
curl -X POST http://SERVIDOR_RAD/procesa_ine_ambas/ \
  -H "X-API-Key: rad_…" \
  -F "frente=@anverso.jpg;type=image/jpeg" \
  -F "atras=@reverso.jpg;type=image/jpeg"
```

```python
import requests

with open("anverso.jpg", "rb") as frente, open("reverso.jpg", "rb") as atras:
    r = requests.post(
        "http://SERVIDOR_RAD/procesa_ine_ambas/",
        headers={"X-API-Key": "rad_…"},
        files={
            "frente": ("anverso.jpg", frente, "image/jpeg"),   # el tipo es obligatorio
            "atras": ("reverso.jpg", atras, "image/jpeg"),
        },
        timeout=120,
    )
cuerpo = r.json()
print(cuerpo["codigo"], cuerpo["estado"])
```

```javascript
// Node 18+ (desde tu backend)
import { readFile } from "node:fs/promises";

const datos = new FormData();
datos.append("frente", new Blob([await readFile("anverso.jpg")], { type: "image/jpeg" }), "anverso.jpg");
datos.append("atras", new Blob([await readFile("reverso.jpg")], { type: "image/jpeg" }), "reverso.jpg");

const r = await fetch("http://SERVIDOR_RAD/procesa_ine_ambas/", {
  method: "POST",
  headers: { "X-API-Key": "rad_…" },
  body: datos,
  signal: AbortSignal.timeout(120_000),
});
const cuerpo = await r.json();
```

### Tiempo de respuesta

Normalmente **entre 20 y 30 segundos**: clasifica y lee los dos archivos. Configura en tu cliente
un timeout de **120 segundos**; cada consulta interna al motor de lectura tiene un tope de 60.

### Qué revisa, en orden

1. **La llave.** Si falta, no existe o fue revocada: `401`. Se revisa antes que todo lo demás, así
   que una llamada sin llave válida responde `401` aunque además le falten archivos.
2. **La petición.** Si falta `frente` o `atras`: `422`.
3. **Cada archivo**, primero `frente` y luego `atras`: que el tipo declarado se acepte (si no,
   `400`), que el archivo abra (si no, `222`) y que no traiga contraseña (si la trae, `223`).
4. **Que cada archivo sea una INE.** Un clasificador lo revisa antes de leer: si no es una
   credencial para votar responde `220`; si viene en blanco o ilegible, `221`.
5. **La lectura** de las dos caras.
6. **Que cada cara esté en su campo.** Si lo que llegó en `frente` es en realidad un reverso:
   `228`. Si `atras` no trae ninguna MRZ (lo común es que hayan mandado otra vez el frente): `227`.
7. **La validación cruzada**, que decide si el documento sale `200`, `210`, `211` o `212`.

**Cuando hay más de un problema** gana, en este orden: un error del archivo (`400`, `222`, `223`)
sobre uno de clasificación (`220`, `221`); entre dos del mismo tipo, el de `frente`; y `228`
sobre `227`. Por eso dos archivos **invertidos** responden `228`.

---

## La respuesta

### `codigo`, `estado` y `mensaje` — qué pasó

**Toda respuesta abre con estas tres llaves**, sea éxito o falla, incluidos el `401`, el `422` y el `500`:

```json
{ "codigo": 210, "estado": "PROCESADO_CON_DISCREPANCIAS", "mensaje": "El documento se procesó, pero..." }
```

| Llave | Para qué | ¿Estable? |
|---|---|---|
| `codigo` | Guardar en base de datos, hacer `switch` | **Sí**: nunca cambia de significado |
| `estado` | Que un log se entienda sin consultar esta tabla | **Sí** |
| `mensaje` | Mostrar a una persona | **No**: se puede reescribir; no compares contra su texto |

| `codigo` | `estado` | HTTP | Qué hacer |
|---:|---|---:|---|
| `200` | `PROCESADO` | 200 | Todo cuadró: seguir adelante |
| `210` | `PROCESADO_CON_DISCREPANCIAS` | 200 | Hay datos que **se contradicen entre sí**: revisar `validacion.discrepancias` antes de dar nada por bueno |
| `211` | `PROCESADO_CON_AVISOS` | 200 | Nada se contradijo, pero algo pide ojo humano: revisar `avisos` |
| `212` | `PROCESADO_INCOMPLETO` | 200 | Nada se contradijo, pero quedaron comprobaciones sin hacer: revisar `validacion.sin_verificar` |
| `220` | `NO_ES_INE` | 200 | Uno de los archivos no es una credencial para votar. No se extrajo nada |
| `221` | `HOJA_EN_BLANCO` | 200 | Sin texto legible: pedir que vuelvan a escanear |
| `222` | `ARCHIVO_CORRUPTO` | 200 | El archivo está dañado, incompleto o no es del tipo que dice: pedirlo de nuevo |
| `223` | `PDF_PROTEGIDO` | 200 | El PDF trae contraseña: pedir que la quiten |
| `224` | `LIMITE_PAGINAS` | 200 | Más páginas de las que se procesan en una llamada: enviar solo las de la credencial |
| `227` | `NO_ES_REVERSO_INE` | 200 | El archivo de `atras` no trae la MRZ: casi siempre mandaron el frente dos veces |
| `228` | `ES_REVERSO_INE` | 200 | El archivo de `frente` es un reverso: casi siempre los archivos vienen invertidos |
| `400` | `ARCHIVO_NO_SOPORTADO` | 400 | Tipo de archivo no aceptado o sin declarar: convertir a PDF o imagen y declarar su tipo |
| `401` | `LLAVE_INVALIDA` | 401 | Falta la cabecera `X-API-Key`, la llave no existe o fue revocada (el `mensaje` dice cuál): corregir la llave; reintentar igual no sirve |
| `422` | `PETICION_INVALIDA` | 422 | Corregir la llamada (falta un campo o no es `multipart/form-data`) |
| `429` | `CUOTA_EXCEDIDA` | 429 | Se agotó la cuota por minuto del motor de lectura: esperar y reintentar |
| `500` | `ERROR_INTERNO` | 500 | Reportar |
| `502` | `ERROR_DOCUMENT_AI` | 502 | El motor de lectura falló: reintentar |
| `503` | `SIN_CREDENCIALES` | 503 | Problema de configuración del servidor: avisar a quien opera la API; reintentar no sirve |

**Los `2xx` responden HTTP 200 y las fallas responden con HTTP igual a `codigo`.** "No es una
INE" es HTTP 200 a propósito: la API hizo su trabajo, y el resultado de ese trabajo fue que el
documento no era del tipo esperado. Los `21x` son "sí se extrajo" y los `22x` "se evaluó pero no
se extrajo". Los números `210`–`228` se eligieron entre los que HTTP no tiene asignados, para que
ninguno se confunda con un `201 Created` o un `202 Accepted`.

**Los rechazos dicen qué archivo fue.** En este endpoint, los mensajes de `400`, `220`, `221`,
`222`, `223`, `227` y `228` empiezan con `Archivo 'frente': ` o `Archivo 'atras': `. Los que
vienen del motor de lectura (`224`, `429`, `502`, `503`, y un `222` cuando es el motor el que no
pudo abrir el archivo) no lo llevan, porque no se sabe cuál de las dos lecturas falló.

> Si escribes un `switch`, deja un `default` que trate un código desconocido como "no
> procesado": es el lado seguro si mañana se agrega uno.

### Cómo se decide el código de un documento procesado

En este orden:

1. Hay algo en `validacion.discrepancias` → `210`
2. Si no, hay algo en `avisos` → `211`
3. Si no, quedó algo en `validacion.sin_verificar` → `212`
4. Si no → `200`

Para **aprobar sin revisión humana**, exige `200`: equivale a `validacion.ok` y
`validacion.completo` en `true` y `avisos` vacío.

### Ejemplo de una respuesta procesada

*(datos ficticios)*

```json
{
  "codigo": 200,
  "estado": "PROCESADO",
  "mensaje": "El documento se procesó y todas las comprobaciones cuadraron.",
  "frente": {
    "apellido_paterno": "MORALES",
    "apellido_materno": "VEGA",
    "nombre": "DANIELA ITZEL",
    "domicilio": {
      "calle_numero": "CALLE ALAMEDA 120",
      "colonia": "LOMAS DE SAN JUAN",
      "codigo_postal": "44100",
      "municipio_delegacion": "GUADALAJARA",
      "estado": "JAL"
    },
    "clave_elector": "MRVGDN91072314M123",
    "curp": "MOVD910723MJCRGN05",
    "fecha_nacimiento": "1991-07-23",
    "sexo": "M",
    "anio_registro": "2014 01",
    "seccion": "0817",
    "numero_emision": "01",
    "vigencia": "2032",
    "pais": "MEXICO",
    "formato_credencial": "nuevo",
    "no_impresos": ["estado_clave", "municipio_clave", "localidad_clave", "anio_emision"]
  },
  "atras": {
    "mrz": {
      "lineas": [
        "IDMEX2034519875<<0817112233445",
        "9107234M3212312MEX<01<<<<<<<<6",
        "MORALES<VEGA<<DANIELA<ITZEL<<<"
      ],
      "tipo_documento": "ID",
      "pais_emisor": "MEX",
      "cic": "203451987",
      "seccion": "0817",
      "identificador_ciudadano": "112233445",
      "nacionalidad": "MEX",
      "folio": "203451987",
      "fecha_nacimiento": "1991-07-23",
      "sexo": "M",
      "vigencia": "2032-12-31",
      "apellido_paterno": "MORALES",
      "apellido_materno": "VEGA",
      "nombres": "DANIELA ITZEL",
      "digitos_control": { "cic": true, "fecha_nacimiento": true, "vigencia": true, "compuesto": true },
      "integra": true,
      "origen": "texto"
    }
  },
  "validacion": {
    "ok": true,
    "completo": true,
    "coincidencias": [
      "MRZ (dígitos de control)",
      "fecha_nacimiento (MRZ)",
      "sexo (MRZ)",
      "apellido_paterno (MRZ)",
      "apellido_materno (MRZ)",
      "nombre (MRZ)",
      "vigencia (MRZ)",
      "seccion (MRZ)",
      "anio_registro, sufijo (MRZ)",
      "fecha_nacimiento (CURP)",
      "sexo (CURP)",
      "curp (dígito verificador)",
      "clave_elector vs CURP (iniciales)",
      "fecha_nacimiento (clave_elector)",
      "sexo (clave_elector)",
      "domicilio (código postal vs estado)"
    ],
    "discrepancias": [],
    "sin_verificar": []
  },
  "avisos": [],
  "clasificacion": { "categoria": "ine", "confianza": 0.9999, "caracteres_leidos": 313 },
  "clasificacion_atras": { "categoria": "ine", "confianza": 0.9999, "caracteres_leidos": 127 }
}
```

**Una llave sin dato no aparece.** Nunca llega una cadena vacía ni un valor inventado para
rellenar. Las únicas excepciones son `frente.formato_credencial` y `clasificacion.confianza`, que
pueden ser `null`.

### `frente` — lo impreso en el anverso

| Llave | Notas |
|---|---|
| `apellido_paterno`, `apellido_materno`, `nombre` | Como vienen impresos: mayúsculas, con acentos si los llevan. `nombre` puede traer varios nombres de pila |
| `domicilio` | Objeto con `calle_numero`, `colonia`, `codigo_postal`, `municipio_delegacion` y `estado` (todas opcionales). `estado` es la abreviatura impresa en el domicilio (`JAL`, `CDMX`, `D.F.`) |
| `clave_elector`, `curp` | 18 caracteres cada una |
| `fecha_nacimiento` | **ISO `AAAA-MM-DD`**, no como viene impresa |
| `sexo` | Como viene impreso: normalmente `H` o `M`. El modelo de credencial 2026 puede imprimir `NB`; la CURP y la clave de elector solo codifican `H` o `M`, así que en ese caso la comparación del sexo sale como discrepancia |
| `anio_registro` | Año, un espacio y el número (`2014 01`), aunque la lectura los traiga juntos (`201401`) o con punto. Los dos últimos dígitos **no son un mes** |
| `seccion` | Conserva los ceros a la izquierda (`0817`) |
| `numero_emision` | Solo en el formato nuevo |
| `estado_clave`, `municipio_clave`, `localidad_clave`, `anio_emision` | Solo en el formato anterior. Claves numéricas, no nombres |
| `vigencia` | Año final, 4 dígitos. Si el anverso imprime un rango (`2026-2036`), llega solo el último año (`2036`) |
| `pais` | `MEXICO` |
| `formato_credencial` | `"nuevo"`, `"anterior"` o `null` si el anverso se leyó tan mal que no se puede saber. **Siempre presente** |
| `no_impresos` | Solo en el formato nuevo: los datos del formato anterior que **el papel no trae**, que es distinto de "no se pudo leer" |

### `atras.mrz` — la zona de lectura mecánica

Los tres renglones de 30 caracteres del reverso, ya interpretados. Los campos que no se pudieron
leer no aparecen.

| Campo | Notas |
|---|---|
| `lineas` | Los tres renglones reconstruidos |
| `tipo_documento`, `pais_emisor`, `nacionalidad` | `ID`, `MEX`, `MEX` |
| `cic` | Identifica el **plástico**; cambia con cada reposición |
| `identificador_ciudadano` | Identifica a la **persona**; sobrevive a las reposiciones |
| `seccion` | Sección electoral |
| `folio` | **Obsoleto**: es igual a `cic`. Se conserva por compatibilidad |
| `fecha_nacimiento`, `vigencia` | ISO. `vigencia` es fecha completa (`2032-12-31`) |
| `sexo` | Como lo trae la MRZ |
| `apellido_paterno`, `apellido_materno`, `nombres` | Del tercer renglón, sin acentos (la MRZ no los admite) |
| `campo_truncado` | Solo si el nombre llegó al borde de los 30 caracteres: qué campo quedó cortado |
| `digitos_control` | Cuatro booleanos: `cic`, `fecha_nacimiento`, `vigencia`, `compuesto` |
| `integra` | `true` si los cuatro dígitos de control cuadran. **Con `false`, los demás campos de `atras.mrz` no son referencia**: se entregan tal como se leyeron, para diagnóstico, y pueden venir corridos o mal leídos |
| `origen` | De dónde salió: `"texto"` (localizada en el texto leído) o `"entidad"` |

**Usa los campos de `atras.mrz` como dato solo cuando `integra` es `true`.** Con `false` llegan
igual, pero `validacion` ya lo reporta como `MRZ corrupta` y no los contrasta contra el anverso.

Si la MRZ está pero ni siquiera se logran armar sus tres renglones, en lugar de esos campos llegan
solo el motivo y el texto crudo:

```json
"mrz": { "error": "MRZ incompleta: 60 caracteres de 90", "texto_crudo": "IDMEX2034519875<<0817112233445\n9107234M3212312MEX<01<<<<<<<<6" }
```

### `validacion` — el contraste

| Llave | Significado |
|---|---|
| `ok` | `true` si `discrepancias` está vacía: nada se contradijo |
| `completo` | `true` si `sin_verificar` está vacía: todo se pudo revisar |
| `coincidencias` | Las comprobaciones que pasaron |
| `discrepancias` | Conflictos reales, con el detalle de qué contra qué |
| `sin_verificar` | Lo que no se pudo comprobar, y por qué |

Son **dos booleanos y no uno a propósito**: "nada se contradijo" y "todo se pudo revisar" son
riesgos distintos.

Se compara el anverso contra la MRZ (nacimiento, sexo, apellidos, nombre, vigencia, sección y el
sufijo del año de registro), la CURP contra la fecha y el sexo y su dígito verificador, la clave de
elector contra la CURP (iniciales, fecha y sexo), y el código postal contra el estado del
domicilio. La comparación de nombres contra la MRZ ignora acentos: `GONZÁLEZ` contra `GONZALEZ` no
es discrepancia.

Ejemplos reales de `discrepancias`:

```text
fecha_nacimiento (MRZ): anverso '1991-07-23' vs '1985-11-04'
clave_elector vs CURP (iniciales): anverso 'MRVUDN' vs 'MRVGDN'
domicilio: el código postal '44100' no corresponde al estado 'CDMX'
MRZ corrupta: fallan los dígitos de control de fecha_nacimiento, compuesto; no se pudo contrastar el anverso contra el reverso
```

Cuando todo lo que se contrasta contra la MRZ discrepa (nacimiento, sexo, nombre, vigencia,
sección), lo más probable es que **el reverso sea de otra persona**.

Ejemplos de `sin_verificar`:

```text
CURP (formato inválido: 'ausente')
MRZ completa (MRZ incompleta: 60 caracteres de 90)
```

### `avisos` — revisión humana

Lista de textos, vacía en el caso normal. Son cosas que la API no puede decidir sola y dejan el
documento en `211`. Hay cuatro formas:

| Aviso | Qué significa |
|---|---|
| `frente.apellido_paterno corregido con la MRZ: se leyó 'MORALESS' y la MRZ trae 'MORALES'` | La lectura pegó una letra de más; la MRZ íntegra la corrigió. El dato de `frente` ya viene corregido |
| `frente: apellidos y nombre reacomodados con la MRZ: se leyó paterno '...', materno '...', nombre '...' y la MRZ trae ...` | Las palabras estaban bien pero en el campo equivocado; se reacomodaron según la MRZ |
| `el PDF contiene 2 bloques 'domicilio': se conservó el primero, revisa que el documento no traiga más de una identidad` | El archivo parece traer más de una credencial. **Trátalo como bloqueante** si aceptas PDFs de varias páginas |
| `frente.nombre='DANIELA ITZEL' no aparece literal en el texto leído` | El valor no está tal cual en lo que se leyó: el lector lo compuso. Hay que mirarlo |

### `clasificacion` y `clasificacion_atras`

El veredicto del clasificador para cada archivo:

```json
{ "categoria": "ine", "confianza": 0.9999, "caracteres_leidos": 313 }
```

- `categoria`: `"ine"`, `"pasaporte"`, `"cedula_panama"`, `"otro"`, `"hoja_en_blanco"`, o `null`
  si el clasificador no pudo decidir (en ese caso el documento se procesa igual).
- `confianza`: de 0 a 1, o `null`.
- `caracteres_leidos`: cuánto texto se leyó; puede faltar.

En un rechazo `220`, `221`, `227` o `228`, `clasificacion` trae el veredicto **del archivo que lo
originó**.

---

## Rechazos y errores

Todos con el mismo sobre. Salidas reales:

```json
{ "codigo": 228, "estado": "ES_REVERSO_INE",
  "mensaje": "Archivo 'frente': El archivo enviado como anverso es el reverso de la INE: trae la zona de lectura mecánica (MRZ) pero ningún dato del frente. Envía el frente, o usa el endpoint del reverso.",
  "clasificacion": { "categoria": "ine", "confianza": 0.9999, "caracteres_leidos": 127 } }
```

```json
{ "codigo": 220, "estado": "NO_ES_INE",
  "mensaje": "Archivo 'atras': El documento no es una credencial para votar (INE). No se extrajo ningún dato.",
  "clasificacion": { "categoria": "otro", "confianza": 0.9999, "caracteres_leidos": 60 } }
```

```json
{ "codigo": 223, "estado": "PDF_PROTEGIDO",
  "mensaje": "Archivo 'frente': El PDF está protegido con contraseña. Quítasela y vuelve a enviarlo." }
```

```json
{ "codigo": 400, "estado": "ARCHIVO_NO_SOPORTADO",
  "mensaje": "Archivo 'frente': Tipo de archivo no soportado: text/plain. Permitidos: application/pdf, image/jpeg, image/png, image/tiff, image/webp." }
```

Sin llave, o con una que no existe o fue revocada (solo cambia el `mensaje`):

```json
{ "codigo": 401, "estado": "LLAVE_INVALIDA",
  "mensaje": "La llave de acceso no existe. Revisa que la cabecera X-API-Key traiga la llave completa, tal como te la entregaron." }
```

El `422` trae además la lista de errores de validación en `errores` (su contenido es informativo):

```json
{ "codigo": 422, "estado": "PETICION_INVALIDA",
  "mensaje": "La petición no es válida. Se espera multipart/form-data con un campo de archivo.",
  "errores": [ { "type": "missing", "loc": ["body", "atras"], "msg": "Field required", "input": null } ] }
```

Un error que nadie previó responde `500` con las tres llaves:

```json
{ "codigo": 500, "estado": "ERROR_INTERNO", "mensaje": "Error interno de la API." }
```

---

## `POST /procesa_ine/` — solo el anverso

- Un archivo, en el campo **`image`**. Mismos tipos y mismas reglas que arriba.
- Mismo cuerpo que `/procesa_ine_ambas/`, sin `clasificacion_atras`.
- **Sin el reverso**, `atras` llega vacío (`{}`) y la comparación contra la MRZ queda en
  `sin_verificar` como `MRZ completa (no se extrajo)`, así que el mejor resultado posible es `212`.
  Si el archivo es un PDF que ya trae las dos caras, se contrasta completo y puede salir `200`.
- Si el archivo es en realidad el reverso, responde `228`.
- Los mensajes de rechazo no llevan el prefijo `Archivo '...': `, porque hay un solo archivo.

```bash
curl -X POST http://SERVIDOR_RAD/procesa_ine/ -F "image=@anverso.jpg;type=image/jpeg"
```

## `POST /procesa_ine_atras/` — solo el reverso

- Un archivo, en el campo **`image`**.
- Devuelve `atras.mrz`, `validacion`, `avisos` (siempre vacío) y `clasificacion`.
- Sola, esta cara solo comprueba que la MRZ sea íntegra: `200` si los dígitos de control cuadran,
  `210` si no (`MRZ corrupta: fallan los dígitos de control de ...`), `212` si la MRZ no se pudo
  reconstruir.
- Si el archivo no trae ninguna MRZ (lo común: mandaron el frente), responde `227`.

```bash
curl -X POST http://SERVIDOR_RAD/procesa_ine_atras/ -F "image=@reverso.jpg;type=image/jpeg"
```

---

## Límites que conviene conocer

**La lectura no es determinista.** El mismo archivo enviado dos veces puede devolver valores
ligeramente distintos en algunos campos. Si necesitas reproducibilidad, guarda la primera
respuesta.

**A veces se omite un dato que sí está impreso.** Se ha visto con la clave de elector: la respuesta
sale `212` con esa comprobación en `sin_verificar`. Reenviar el mismo archivo a veces lo completa.

**La confianza del modelo no se expone por campo, y es intencional.** Valores incorrectos llegan a
veces con confianza altísima. El bloque `validacion` es el sustituto correcto: no mide qué tan
seguro está el modelo, mide si los datos se contradicen entre sí.

**Un error de un carácter en un campo sin redundancia puede pasar.** Una letra de más en un
apellido se caza contra la MRZ; un carácter cambiado en el domicilio, no. Para un trámite
irreversible, `200` debería ser condición necesaria, no suficiente.

**Apellidos con Ñ.** La CURP escribe la Ñ de las iniciales como X (`PEÑA` da `PX`). La
validación lo toma en cuenta, y en la clave de elector acepta X o N en esa posición. Si el OCR
pierde la tilde (`PENA`), el nombre ya no coincide con la CURP y sale como discrepancia.

**El escaneo manda.** La diferencia entre un documento que sale limpio y uno que sale a medias es
casi siempre la calidad de la imagen: resolución, contraste y que la cara entre completa y derecha.
