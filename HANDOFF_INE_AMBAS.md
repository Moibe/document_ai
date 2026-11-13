# Handoff: `POST /procesa_ine_ambas/` de RAD, para reimplementarlo en open source

Documento para el agente que va a construir la versión de Alexis. El objetivo es que su
sistema, con el motor de lectura que prefiera (OCR clásico, modelo de visión, lo que sea),
responda **el mismo JSON** y **los mismos mensajes de error** que este endpoint de RAD.

- **Qué documenta:** el comportamiento de RAD en `document_ai` commit `10f3a06` (rama `main`).
  Su validación de INE está **sincronizada con santander_ia** (commit `af35409`), menos el sello
  y con el plegado de acentos de la sección 1.
- **Fecha:** 2026-11-13.
- **Repo (público):** https://github.com/Moibe/document_ai
- **Casos de prueba ejecutables:** [`HANDOFF_INE_AMBAS_vectores.json`](HANDOFF_INE_AMBAS_vectores.json),
  32 casos con entrada y salida exacta. Se regeneran con `python generar_vectores_ine_ambas.py`.

**Orden de autoridad si algo se contradice:** (1) los vectores, (2) este documento,
(3) el código de referencia. Si encuentras una contradicción real, avísale a Moibe en vez de
adivinar: es una diferencia que hay que resolver en RAD, no en tu sistema.

---

## 0. Lo esencial

1. **Un solo endpoint**, `POST /procesa_ine_ambas/`, que recibe el **anverso y el reverso de una
   INE en dos archivos** y devuelve los datos del anverso, la zona de lectura mecánica (MRZ) del
   reverso y un bloque `validacion` que contrasta unos contra otros.
2. **Lo que vale es la validación cruzada, no la lectura.** La INE trae información redundante
   (la CURP y la clave de elector codifican fecha de nacimiento, sexo e iniciales; la MRZ repite
   casi todo el anverso con dígitos verificadores). El motor de lectura es intercambiable; las
   reglas de la sección 8 no.
3. **El desenlace va en el cuerpo.** Toda respuesta abre con `codigo`, `estado` y `mensaje`.
   Los `2xx` responden HTTP 200; las fallas usan como HTTP el mismo número que `codigo`.
4. **Una llave sin dato se omite.** Nunca `""`, nunca un valor inventado. Excepciones: `null`
   en `frente.formato_credencial` y en `clasificacion.confianza`.
5. **No existe `sello`.** Si ya conoces el `/ine` de santander_ia, este endpoint **no** trae el
   sello de cotejo: ni la llave, ni entradas `sello.*` en `sin_verificar`, ni avisos del sello.
6. **Terminaste cuando** pasan los 32 vectores y la lista de la sección 12.

### Orden de trabajo sugerido

1. El esqueleto: la ruta, el sobre `codigo`/`estado`/`mensaje`, la tabla de desenlaces y la regla
   de HTTP (sección 5). Con eso ya pasan los casos `24` y `32`.
2. Las guardias del archivo (sección 10) y la precedencia (sección 4). Pasan `19` a `23` y `26` a
   `27`.
3. El clasificador (sección 9). Pasan `16` a `18` y `28`.
4. El parser de MRZ (8.2) y su dígito de control. Es la pieza más fácil de equivocar por un
   carácter: pruébala aparte.
5. La validación (8.3 a 8.5), bloque por bloque en el orden del documento. Pasan `01` a `13`,
   `29`, `30` y `31`.
6. Las dos reglas de la cara equivocada, `228` y `227` (8.7 y sección 4). Pasan `14`, `15` y `25`.
7. Tu motor de lectura, al final: hasta aquí trabajaste con las `entrada` de los vectores, que
   es justo lo que él tiene que producir.

---

## 1. Diferencias con el `/ine` de santander_ia

Si ya tienes `HOMOLOGACION.md` de santander_ia, esto es lo que cambia para este endpoint:

| | `/ine` de santander_ia | `/procesa_ine_ambas/` de RAD |
|---|---|---|
| Archivos | 1 (`file`), puede traer ambas caras | 2: `frente` y `atras`, **los dos obligatorios** |
| Sello de cotejo | Sí (`sello`) | **No existe** |
| `sello.*` en `sin_verificar` | Sí, baja `completo` y lleva a `212` | No aplica |
| Desenlaces nuevos | — | `227 NO_ES_REVERSO_INE`, `228 ES_REVERSO_INE` |
| Mensajes de rechazo | Sin prefijo | Prefijo `Archivo 'frente': ` o `Archivo 'atras': ` |
| Comparación de nombres contra la MRZ | Sensible a acentos | **Ignora acentos** (`GONZÁLEZ` = `GONZALEZ`) |
| `clasificacion_atras` | No | Sí |

**Todo lo demás de la validación es idéntico**, incluidas las dos reglas más recientes de
santander_ia: el reacomodo de apellidos y nombre con la MRZ (8.5) y la discrepancia por dígito en
las primeras 4 posiciones de la CURP (8.3, bloque 2).

---

## 2. Alcance

**Entra:** el contrato HTTP, el JSON de salida, los desenlaces y mensajes de error, las reglas
de validación, y el comportamiento que necesitas del clasificador.

**No entra:** el motor de lectura (Document AI en RAD; el tuyo en tu sistema), los demás
endpoints de RAD, el despliegue.

**Extensiones:** si agregas llaves propias (por ejemplo confianza por campo), tienen que ser
puramente aditivas y ningún consumidor debe depender de ellas. El contrato es lo de este
documento.

---

## 3. Entrada

```
POST /procesa_ine_ambas/
Content-Type: multipart/form-data
```

| Campo | Tipo | Obligatorio |
|---|---|---|
| `frente` | archivo: anverso de la INE | Sí |
| `atras` | archivo: reverso de la INE | Sí |

- Tipos aceptados: `application/pdf`, `image/jpeg`, `image/png`, `image/tiff`, `image/webp`.
- La ruta se declara **con** diagonal final. RAD redirige con `307` la variante sin ella; **sirve
  las dos directamente**, para no depender de que el cliente siga una redirección con un cuerpo
  multipart.
- Un PDF puede traer varias páginas, hasta el tope de tu motor (en RAD, 15; más responde `224`).
- RAD no impone un tamaño máximo propio.

```bash
curl -X POST http://HOST/procesa_ine_ambas/ \
  -F "frente=@frente.jpg" \
  -F "atras=@reverso.jpg"
```

---

## 4. Qué pasa con una petición, en orden

El orden importa porque define qué respuesta gana cuando hay más de un problema.

**Paso 1, petición.** Si falta `frente` o `atras`: HTTP `422` (ver sección 5).

**Paso 2, guardias del archivo.** Se aplican a cada archivo, primero a `frente` y luego a
`atras`:

1. El tipo declarado (`Content-Type` de la parte) tiene que estar en la lista aceptada. Si no:
   `400 ARCHIVO_NO_SOPORTADO`.
2. Se lee el contenido y se determina el **tipo real por los primeros bytes**. Si el tipo real
   es uno de los aceptados, **ese** es el que se usa, aunque el cliente haya declarado otro
   (un PNG declarado `image/jpeg` se procesa como PNG; queda un warning en el log).
3. Se revisa el archivo (sección 10). Los fallos son `222` o `223`.

**Paso 3, clasificador** (sección 9), a cada archivo. Si el `frente` no es una INE o viene en
blanco: `220` o `221`. Si no, lo mismo para `atras`.

**Paso 4, lectura.** Se lee el anverso (campos y texto) y el reverso (texto).

**Paso 5, la cara correcta en cada campo.**
1. Si el `frente` es en realidad un reverso: `228 ES_REVERSO_INE`.
2. Si el `atras` no trae ninguna MRZ: `227 NO_ES_REVERSO_INE`.

**Paso 6, validación y armado.** Se construye la respuesta de la sección 6 y el código sale
de la regla de la sección 5.

### Qué gana cuando hay más de un problema

1. Los errores del **archivo** (`400`, `222`, `223`) ganan a los de **clasificación**
   (`220`, `221`). Ejemplo: `frente` en blanco y `atras` dañado responde `222`.
2. Entre dos errores del mismo tipo, gana el de `frente`.
3. `228` (anverso que es reverso) gana a `227` (reverso sin MRZ). Es lo que pasa cuando los
   archivos vienen **invertidos**: responde `228` y no `227`.

Los vectores `25` a `28` cubren estos casos. RAD resuelve el punto 2 por orden de llegada, que
en la práctica es `frente` primero; implementa la regla como está escrita.

---

## 5. Desenlaces y errores

**Todo cuerpo abre con estas tres llaves**, sea éxito o falla:

```json
{ "codigo": 210, "estado": "PROCESADO_CON_DISCREPANCIAS", "mensaje": "..." }
```

| Llave | Para qué | Estabilidad |
|---|---|---|
| `codigo` | Guardar en base de datos, hacer `switch` | **Contrato.** Nunca cambia de significado |
| `estado` | Que un log se entienda sin la tabla | **Contrato** |
| `mensaje` | Mostrar a una persona | **No** es contrato: los clientes no lo comparan. Usa estos textos tal cual salvo que tu motor lo impida |

### Tabla de desenlaces de este endpoint

| codigo | estado | HTTP | mensaje |
|---:|---|---:|---|
| 200 | `PROCESADO` | 200 | El documento se procesó y todas las comprobaciones cuadraron. |
| 210 | `PROCESADO_CON_DISCREPANCIAS` | 200 | El documento se procesó, pero hay datos que se contradicen entre sí. Revisa 'validacion.discrepancias' antes de dar los datos por buenos. |
| 211 | `PROCESADO_CON_AVISOS` | 200 | El documento se procesó y nada se contradijo, pero hay algo que requiere revisión humana. Revisa 'avisos'. |
| 212 | `PROCESADO_INCOMPLETO` | 200 | El documento se procesó y nada se contradijo, pero quedaron comprobaciones sin poder realizarse. Revisa 'validacion.sin_verificar'. |
| 220 | `NO_ES_INE` | 200 | El documento no es una credencial para votar (INE). No se extrajo ningún dato. |
| 221 | `HOJA_EN_BLANCO` | 200 | El documento viene en blanco o ilegible: no se leyó ningún texto. |
| 222 | `ARCHIVO_CORRUPTO` | 200 | El archivo no se pudo abrir: está dañado, incompleto, o no es del tipo que dice ser. |
| 223 | `PDF_PROTEGIDO` | 200 | El PDF está protegido con contraseña. Quítasela y vuelve a enviarlo. |
| 224 | `LIMITE_PAGINAS` | 200 | El documento tiene más páginas de las que se pueden procesar en una sola llamada. Envía solo las páginas de la credencial. |
| 227 | `NO_ES_REVERSO_INE` | 200 | No se encontró la zona de lectura mecánica (MRZ) del reverso de la INE. Verifica que el archivo sea el reverso de la credencial y no el frente. |
| 228 | `ES_REVERSO_INE` | 200 | El archivo enviado como anverso es el reverso de la INE: trae la zona de lectura mecánica (MRZ) pero ningún dato del frente. Envía el frente, o usa el endpoint del reverso. |
| 400 | `ARCHIVO_NO_SOPORTADO` | 400 | El tipo de archivo enviado no se acepta. |
| 422 | `PETICION_INVALIDA` | 422 | La petición no es válida. Se espera multipart/form-data con un campo de archivo. |
| 429 | `CUOTA_EXCEDIDA` | 429 | Se alcanzó el límite de páginas por minuto del servicio de OCR. Espera un momento y vuelve a intentar. |
| 500 | `ERROR_INTERNO` | 500 | Error interno de la API. |
| 502 | `ERROR_DOCUMENT_AI` | 502 | El servicio de Document AI falló o respondió algo no interpretable. |
| 503 | `SIN_CREDENCIALES` | 503 | El servidor no pudo obtener las credenciales de Google Cloud. |

- `225` y `226` existen en RAD para otros documentos; **este endpoint no los emite**.
- `502` y `503` están redactados para Google Document AI. **Conserva `codigo` y `estado`**
  aunque tu motor sea otro, porque son el contrato; adapta solo el `mensaje`: `502` es "el
  motor de lectura falló o respondió algo no interpretable" y `503` es "el servicio no está
  disponible o no está configurado".

### Reglas del sobre

- **Los `2xx` responden HTTP 200; las fallas responden HTTP = `codigo`.** `220` ("no es una INE")
  es HTTP 200 a propósito: la API hizo su trabajo y el resultado fue que el documento no era del
  tipo esperado.
- **Cómo sale el código de un documento procesado**, en este orden:
  1. Hay alguna entrada en `validacion.discrepancias` → `210`
  2. Si no, hay alguna entrada en `avisos` → `211`
  3. Si no, `validacion.completo` es `false` → `212`
  4. Si no → `200`
- **Mensaje con el archivo:** todos los rechazos que salen de las guardias (`400`, `222`, `223`,
  `220`, `221`, `227`, `228`) llevan como prefijo del mensaje `Archivo 'frente': ` o
  `Archivo 'atras': `, según el archivo que originó el desenlace. Los que vienen del motor de
  lectura (`224`, `429`, `502`, `503`, y el `222` que traduces de un error del motor) **no**
  llevan prefijo: no se sabe cuál de las dos lecturas falló.
- **Llaves extra en un rechazo:** `220`, `221`, `227` y `228` traen además `clasificacion` (el
  veredicto del archivo que originó el rechazo). Los demás rechazos solo traen las tres llaves.
- **`mensaje` con detalle propio:** `224` sustituye el mensaje por
  `El documento tiene {n} páginas y el máximo por llamada es {tope}. Envía solo las páginas de la credencial.`
  cuando conoces los dos números. `502` lleva el detalle del motor en el mensaje
  (en RAD: `Error de Document AI ({status}): {texto}`). Los `222` de la sección 10 también llevan
  texto propio.
- Codificación UTF-8, sin escapes `\uXXXX`.

### Ejemplos de rechazo (salidas reales de RAD)

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

### El `422`

Es el sobre normal más la lista de errores de validación del framework en `errores`, el mismo
formato que usa santander_ia. El contenido de `errores` es del framework de cada sistema y no se
compara; lo que cuenta es `codigo`, `estado` y el HTTP 422.

```json
{ "codigo": 422, "estado": "PETICION_INVALIDA",
  "mensaje": "La petición no es válida. Se espera multipart/form-data con un campo de archivo.",
  "errores": [ { "type": "missing", "loc": ["body", "atras"], "msg": "Field required", "input": null } ] }
```

### Errores inesperados

Una excepción que nadie previó responde `500 ERROR_INTERNO` con el sobre de tres llaves
(`{ "codigo": 500, "estado": "ERROR_INTERNO", "mensaje": "Error interno de la API." }`) y se
registra en el log. Caso `32`.

> En RAD el `422` con sobre y el `500` con sobre valen para los endpoints nuevos (`/procesa_ine`,
> `/procesa_ine_atras`, `/procesa_ine_ambas` y `/procesa_cedula_panama`). Los endpoints anteriores
> de RAD conservan el cuerpo por defecto de FastAPI y el texto plano `Internal Server Error`, para
> no cambiarle la forma a quien ya los lee. Eso no afecta a tu implementación: en este endpoint,
> toda respuesta lleva el sobre.

---

## 6. El cuerpo de una respuesta procesada

Llaves de primer nivel, en este orden:

```
codigo, estado, mensaje, frente, atras, validacion, avisos, clasificacion, clasificacion_atras
```

### 6.1 Ejemplo completo (datos ficticios, caso `01` de los vectores)

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

### 6.2 `frente`

Se emiten solo las llaves que se leyeron, en este orden:

| Llave | Tipo | Notas |
|---|---|---|
| `apellido_paterno`, `apellido_materno`, `nombre` | texto | Como vienen impresos (mayúsculas, con acentos si los lleva). `nombre` puede traer varios nombres de pila |
| `domicilio` | objeto | Ver abajo |
| `clave_elector` | texto | 18 caracteres |
| `curp` | texto | 18 caracteres |
| `fecha_nacimiento` | texto | **ISO `YYYY-MM-DD`**, no como está impresa |
| `sexo` | texto | `H` o `M`, como viene impreso |
| `anio_registro` | texto | Año y número de emisión **tal como vienen impresos**: `2014 01`. Los dos últimos dígitos **no son un mes** |
| `seccion` | texto | Conserva ceros a la izquierda (`0817`) |
| `estado_clave`, `municipio_clave`, `localidad_clave` | texto | Solo formato anterior. Claves numéricas, **no** nombres. Conservan ceros |
| `anio_emision` | texto | Solo formato anterior. 4 dígitos |
| `numero_emision` | texto | Solo formato nuevo. 1 o 2 dígitos |
| `vigencia` | texto | Año final de 4 dígitos. Si el anverso imprime un rango (`2021-2031`) lo que vale es el último año |
| `pais` | texto | `MEXICO` |
| `formato_credencial` | texto o `null` | `"nuevo"`, `"anterior"` o `null`. **Siempre presente** |
| `no_impresos` | lista | Solo si el formato es `"nuevo"` |

`frente.domicilio`, todas las llaves son texto y opcionales: `calle_numero`, `colonia`,
`codigo_postal`, `municipio_delegacion`, `estado`. `estado` es el nombre abreviado del estado
tal como va en el domicilio (`CDMX`, `JAL`, `D.F.`), distinto de `estado_clave` (numérico).

**Reglas de valores:**

- Una llave sin dato **se omite**. Nunca `""` ni `null` (salvo `formato_credencial`).
- Texto en alfabeto latino: sin homóglifos griegos ni cirílicos, sin saltos de línea, sin basura
  pegada a los bordes (ver 8.1).
- Domicilio: en cada valor se quita la coma final, y el `estado` pierde un punto final si es el
  único punto (`SON.` pasa a `SON`; `D.F.` se queda igual).

**`formato_credencial`** (en este orden de decisión):

1. El texto del OCR (de **los dos archivos juntos**) trae un renglón de claves, es decir una
   coincidencia, sin importar mayúsculas, con
   `ESTADO\s+\d{1,2}\b|MUNICIPIO\s+\d{1,3}\b|LOCALIDAD\s+\d{1,4}\b` → `"anterior"`.
2. Si no, hay alguno de `estado_clave`, `municipio_clave`, `localidad_clave`, `anio_emision` →
   `"anterior"`.
3. Si no, hay `numero_emision` → `"nuevo"`.
4. Si no, hay `curp` o `clave_elector` → `"nuevo"`.
5. Si no → `null` (el anverso salió tan mal que no se puede concluir nada).

**`no_impresos`** (solo con formato `"nuevo"`): de `estado_clave`, `municipio_clave`,
`localidad_clave`, `anio_emision`, los que **no** están en `frente`, en ese orden. Dice "el papel
no trae este dato", que es distinto de "no se pudo leer".

### 6.3 `atras.mrz`

`atras` es siempre `{ "mrz": { ... } }` en una respuesta procesada. Campos de `mrz`, en este
orden; los marcados `*` solo aparecen si se pudieron leer:

| Campo | Ejemplo | Notas |
|---|---|---|
| `lineas` | 3 cadenas de 30 | Los renglones reconstruidos |
| `tipo_documento` | `ID` | |
| `pais_emisor` | `MEX` | |
| `cic` | `203451987` | Los 9 dígitos, sin el verificador. Identifica el **plástico** |
| `seccion` | `0817` | Sección electoral |
| `identificador_ciudadano` | `112233445` | Identifica a la **persona**; sobrevive a reposiciones |
| `nacionalidad` | `MEX` | |
| `folio` | `203451987` | **Obsoleto**: igual a `cic`. Se emite por compatibilidad |
| `fecha_nacimiento` * | `1991-07-23` | ISO |
| `sexo` * | `M` | `M`, `F` o `H` según lo que traiga |
| `vigencia` * | `2032-12-31` | ISO, fecha completa |
| `apellido_paterno` *, `apellido_materno` *, `nombres` * | | Del tercer renglón |
| `campo_truncado` * | `nombres` | Solo si el renglón 3 llegó al borde de los 30 caracteres |
| `digitos_control` | objeto | Cuatro booleanos: `cic`, `fecha_nacimiento`, `vigencia`, `compuesto` |
| `integra` | `true` | `true` si los cuatro cuadran |
| `origen` | `"texto"` | `"texto"` si se recuperó del texto del OCR, `"entidad"` si un lector dedicado la entregó ya aislada |

Si la MRZ está pero **no se puede reconstruir**, en lugar de esos campos llega solo:

```json
"mrz": { "error": "MRZ incompleta: 60 caracteres de 90", "texto_crudo": "IDMEX2034519875<<0817112233445\n9107234M3212312MEX<01<<<<<<<<6" }
```

Nunca se emiten campos derivados de una MRZ que no se pudo leer. El caso `29` lo muestra
completo.

### 6.4 `validacion`

```json
{ "ok": true, "completo": true, "coincidencias": [], "discrepancias": [], "sin_verificar": [] }
```

| Llave | Significado |
|---|---|
| `ok` | `true` si `discrepancias` está vacía |
| `completo` | `true` si `sin_verificar` está vacía |
| `coincidencias` | Etiquetas de las comprobaciones que pasaron |
| `discrepancias` | Conflictos reales, con el detalle de qué contra qué |
| `sin_verificar` | Comprobaciones que no se pudieron hacer, y por qué |

Son **dos booleanos y no uno a propósito**: "nada se contradijo" y "todo se pudo revisar" son
riesgos distintos. Para aprobar automáticamente un consumidor debe exigir los dos.

Las etiquetas y su orden están en la sección 8.3. **El orden de las listas es parte de la
salida**: sigue el orden en que se evalúan las comprobaciones.

### 6.5 `avisos`

Lista de cadenas, vacía en el caso normal. Son cosas que la API no puede decidir sola. Hay
exactamente cuatro formas (sección 8.8), y salen en este orden: corrección de letras de más con la
MRZ, reacomodo con la MRZ, bloques repetidos, valores que no aparecen en el texto.

### 6.6 `clasificacion` y `clasificacion_atras`

El veredicto del clasificador para el `frente` y para el `atras`:

```json
{ "categoria": "ine", "confianza": 0.9999, "caracteres_leidos": 313 }
```

- `categoria`: `"ine"`, `"pasaporte"`, `"cedula_panama"`, `"otro"`, `"hoja_en_blanco"` o `null`
  (el clasificador no pudo decidir). **Esta sí se compara.**
- `confianza`: número de 0 a 1, o `null`. Si tu clasificador es por reglas y no tiene un número
  honesto, `null` es aceptable. No se compara.
- `caracteres_leidos`: cuántos caracteres leyó el OCR. Puede faltar si el clasificador falló. No se
  compara.

> En un rechazo, `clasificacion` lleva el veredicto del archivo que lo originó: el del `atras` en
> un `227`, el del `frente` en un `228`, y el del archivo rechazado en un `220` o `221`.

---

## 7. Lo que tu motor de lectura tiene que entregar

La capa de validación (secciones 8 y 9) recibe **datos ya leídos**. Tu motor produce:

```
leer_anverso(bytes, mime) -> {
    campos:    { ... },        # ver abajo
    texto_ocr: "<texto plano del OCR, renglones separados por \n>",
    repetidos: { "<tipo>": n } # opcional, ver 8.8
}
leer_reverso(bytes, mime) -> { texto_ocr: "..." }
clasificar(bytes, mime)   -> { categoria, confianza, caracteres_leidos }
```

`campos` es un diccionario con los nombres de `frente` (sección 6.2). **Todos son opcionales**:
si no leíste algo, no lo pongas. Lo mínimo para que la validación sirva: `curp`,
`clave_elector`, `apellido_paterno`, `apellido_materno`, `nombre`, `fecha_nacimiento` (ya en ISO),
`sexo`, `vigencia`, `seccion`, `anio_registro` y `domicilio.codigo_postal` / `domicilio.estado`.

- **No hay un extractor para el reverso.** La MRZ se localiza en el `texto_ocr` del reverso (8.2).
  RAD busca en el texto de los dos archivos concatenado; buscar solo en el del reverso es
  equivalente salvo en casos patológicos, y es lo recomendable.
- Si tu motor tiene un lector de MRZ dedicado y entrega los renglones ya aislados, pásalos igual
  por el mismo parser y marca `origen: "entidad"`.
- Los vectores traen, por caso, exactamente `campos` y `texto_ocr` de cada archivo.

---

## 8. Reglas de validación

Todo es Python en el repo de referencia (`ine.py`, `ine_atras.py`). Aquí está el
comportamiento; ante la duda, el código manda sobre la prosa, y los vectores sobre los dos.

### 8.1 Normalización de texto (`limpiar_texto`)

Se aplica a cada valor de texto de `frente`, en este orden:

1. Sustituir homóglifos de otros alfabetos por su letra latina (tabla abajo).
2. Colapsar cualquier secuencia de espacios y saltos de línea a un solo espacio.
3. Quitar del **inicio** los caracteres ` -.:,;|/\` y del **final** ` -:,;|/\`. El punto **no**
   se quita del final aquí (lo decide la regla del estado: `SON.` pierde el punto, `D.F.` no).
4. `strip()`.

```
griego mayúsculas:   Α→A Β→B Ε→E Ζ→Z Η→H Ι→I Κ→K Μ→M Ν→N Ο→O Ρ→P Τ→T Υ→Y Χ→X
griego minúsculas:   α→a β→B ε→e ι→i ο→o ρ→p τ→t
cirílico mayúsculas: А→A В→B Е→E К→K М→M Н→H О→O Р→P С→C Т→T У→Y Х→X
cirílico minúsculas: а→a е→e о→o р→p с→c у→y х→x
```

> La tabla exacta está en `HOMOGLIFOS` de `ine.py`; **cópiala de ahí** en vez de transcribirla
> de este bloque. (Ojo con `β→B`: en la referencia la beta minúscula se convierte en `B`
> mayúscula. Es como está, no una errata de este documento.)

**Plegado para comparar** (`_plegar`): pasar a mayúsculas y quitar acentos y diéresis
(descomposición NFD y quitar las marcas); la `Ñ` queda `N`. Es lo que usan **todas** las
comparaciones contra la MRZ, porque el estándar ICAO no admite acentos. En los mensajes se
imprimen los valores **originales**, sin plegar.

### 8.2 MRZ, formato TD1

Tres renglones de **30** caracteres. Layout:

```
renglón 1:  I D M E X 2034519875 << 0817 112233445
            tipo+país  CIC(9)+dv   sec.  identificador
            [0:2] tipo  [2:5] país  [5:14] CIC  [14] dígito verificador del CIC
            [15:17] "<<"  [17:21] sección  [21:30] identificador del ciudadano
renglón 2:  [0:6] nacimiento YYMMDD  [6] dv  [7] sexo  [8:14] vigencia YYMMDD  [14] dv
            [15:18] nacionalidad  [18:30] resto; [19:21] = número de emisión; [29] dv compuesto
renglón 3:  APELLIDO_PATERNO<APELLIDO_MATERNO<<NOMBRE<NOMBRE<<<<  (relleno con "<")
```

**Dígito de control** (ICAO 9303): pesos `7,3,1` cíclicos sobre el valor de cada carácter
(dígito = su valor, `A`=10 … `Z`=35, cualquier otro carácter = 0), suma, módulo 10.

**Los cuatro dígitos verificados** (`digitos_control`):

| Clave | Se calcula sobre | Se compara con |
|---|---|---|
| `cic` | `renglón1[5:14]` | `renglón1[14]` |
| `fecha_nacimiento` | `renglón2[0:6]` | `renglón2[6]` |
| `vigencia` | `renglón2[8:14]` | `renglón2[14]` |
| `compuesto` | `renglón1[5:30] + renglón2[0:7] + renglón2[8:15] + renglón2[18:29]` | `renglón2[29]` |

`integra` es `true` si los cuatro son `true`.

**Localizar la MRZ en el texto** (`_mrz_en_texto`):

1. Normalizar cada renglón del texto: a mayúsculas y **todo lo que no sea letra o dígito se
   vuelve `<`** (el OCR lee el `<` como espacio, guion, katakana, comillas…).
2. Un renglón es **encabezado** si cumple `^I[D<][A-Z<]{3}[A-Z0-9<]{9}\d`. El patrón exige la
   forma completa a propósito: uno corto se enganchó con `IDAN<MEXICO`, que es como el OCR lee
   el escudo impreso arriba.
3. Por cada encabezado, juntar ese renglón y los siguientes (máximo 8) hasta acumular ≥ 90
   caracteres normalizados. Cada grupo es un candidato.
4. Se elige el **primer candidato cuyos dígitos de control cuadran**; si ninguno, el primero. Si
   no hay candidatos, no hay MRZ.

**Reconstruir los 3 renglones** (`_renglones_mrz`), por **patrón y no por longitud**:

- Se parte el texto candidato en renglones, se normaliza cada uno como arriba y se empieza en el
  primer encabezado.
- Renglón 1: se junta desde el encabezado hasta completar 30 caracteres, o antes si el
  siguiente pedazo ya tiene forma de renglón 2 (`^\d{6}\d[MFH<]\d{6}\d[A-Z<]{3}`). Ese patrón
  solo sirve para **cortar el renglón 1 a tiempo**.
- Renglón 2: se juntan pedazos hasta completar 30.
- Renglón 3: todo lo que sobra.
- Cada renglón se **ajusta a 30**: si es más largo se recorta por la derecha (un carácter de
  más es una mota del OCR), si es más corto se rellena con `<`.
- Si no hay encabezado: `error` = `no se encontró el encabezado de la MRZ`. Si falta el renglón
  2 o el 3: `error` = `MRZ incompleta: {n} caracteres de 90`, con `n` la suma de lo que se
  juntó. En ambos casos la salida es `{ "error": ..., "texto_crudo": <texto candidato> }`.

**Campos** (ver la tabla de 6.3):

- `fecha_nacimiento`: `YYMMDD` del renglón 2 a ISO, solo si el mes está en 1..12 y el día en
  1..31. Año: `2000+YY` si no es posterior al año actual; si lo es, `1900+YY`.
- `vigencia`: igual pero el año **siempre** es `2000+YY`.
- `sexo`: `renglón2[7]` si es `M`, `F` o `H`.
- Renglón 3, se separa en `<<`: la parte izquierda son los apellidos (separados por un `<`
  simple) y la derecha los nombres. `apellido_paterno` es **la primera palabra**,
  `apellido_materno` **el resto unido con espacios**, `nombres` todas las palabras de la derecha
  unidas con espacios. *(Consecuencia: en `DE<LA<CRUZ<HERNANDEZ<<MARIO` el paterno sale `DE` y
  el materno `LA CRUZ HERNANDEZ`. Es esperado; lo resuelve 8.3.)*
- `campo_truncado`: si el renglón 3 **no** termina en `<`, el último campo que llegó al borde
  se marca: `nombres` si hay nombres, si no `apellido_materno` si hay materno, si no
  `apellido_paterno`.

### 8.3 Comparaciones y su orden

`validacion` se arma recorriendo estos bloques en orden. Aquí `comparar(etiqueta, a, b)`:

- Si `a` o `b` es `None` o cadena vacía → `sin_verificar` += `etiqueta` (solo la etiqueta).
- Si `plegar(a) == plegar(b)` → `coincidencias` += `etiqueta`.
- Si `truncable` y `plegar(a)` empieza con `plegar(b)` → `coincidencias` +=
  `etiqueta + " (truncado en la MRZ)"`.
- Si no → `discrepancias` += `"{etiqueta}: anverso '{a}' vs '{b}'"`.

**Bloque 1, la MRZ.**

| Situación | Efecto |
|---|---|
| `mrz.error` | `sin_verificar` += `MRZ completa ({error})` |
| `mrz` no es íntegra | `discrepancias` += `MRZ corrupta: fallan los dígitos de control de {lista}; no se pudo contrastar el anverso contra el reverso` (la lista, separada por `, `, en el orden `cic`, `fecha_nacimiento`, `vigencia`, `compuesto`) y `sin_verificar` += `todas las comprobaciones contra la MRZ`. **No se hace ninguna otra comparación contra la MRZ** |
| `mrz` íntegra | `coincidencias` += `MRZ (dígitos de control)` y se hace lo siguiente, en orden |

Con la MRZ íntegra, `abreviado` es `true` si `campo_truncado` está presente:

1. `comparar("fecha_nacimiento (MRZ)", frente.fecha_nacimiento, mrz.fecha_nacimiento)`
2. `comparar("sexo (MRZ)", frente.sexo, mrz.sexo)`
3. **Apellidos.** Con `ap_f`, `am_f` los del frente y `ap_m`, `am_m` los de la MRZ:
   - `coincide(a, b)` es verdadero si ambos tienen valor y `plegar(a) == plegar(b)`, o (con
     `abreviado`) `plegar(a)` empieza con `plegar(b)`.
   - `equivalentes` es verdadero si los cuatro valores son texto no vacío, y la lista de palabras
     de `plegar(ap_f + " " + am_f)` y la de `plegar(ap_m + " " + am_m)` tienen la misma
     longitud y cada par de palabras es igual (o, con `abreviado`, la del frente empieza con la
     de la MRZ).
   - `compuesto_ok = equivalentes y no (coincide(ap_f, ap_m) y coincide(am_f, am_m))`.
   - Si `compuesto_ok`: `coincidencias` += `apellido_paterno (MRZ)` y `apellido_materno (MRZ)`,
     sin más. Es el caso de `DE LA CRUZ`: en la MRZ el espacio de un apellido compuesto y el
     separador entre apellidos son el mismo `<`, así que la frontera entre paterno y materno
     **no se puede conocer**; se comparan las palabras.
   - Si no: `comparar("apellido_paterno (MRZ)", ap_f, ap_m, truncable=abreviado)` y lo mismo con
     `apellido_materno`.
4. `comparar("nombre (MRZ)", frente.nombre, mrz.nombres, truncable=abreviado)`
5. **Apellidos fusionados.** Si **no** es `compuesto_ok` y hay `ap_f`, `ap_m` y `am_m`, y
   `mayúsculas(ap_f) != mayúsculas(ap_m)` (aquí sin plegar acentos): sea
   `resto = mayúsculas(ap_f)[len(ap_m):].strip()`. Si `mayúsculas(ap_f)` empieza con
   `mayúsculas(ap_m)`, `resto` no está vacío y `mayúsculas(am_m)` empieza con
   `resto[:len(am_m)]`, entonces `discrepancias` +=
   `apellido_paterno: '{ap_f}' parece traer los dos apellidos juntos; la MRZ los separa en '{ap_m en mayúsculas}' y '{am_m en mayúsculas}'`.
   Cierra el hueco que abre aceptar prefijos: `BERNARDINO HERNANDEZ` contra `BERNARDINO`.
6. `comparar("vigencia (MRZ)", anio_vigencia(frente.vigencia), mrz.vigencia[:4])`, donde
   `anio_vigencia` es **el último** año de 4 dígitos (`19xx` o `20xx`) que aparezca en el valor, o
   `None`.
7. `comparar("seccion (MRZ)", frente.seccion, mrz.seccion)`
8. **Número de emisión.** `sufijo` = los dos dígitos que siguen al año en `frente.anio_registro`
   (patrón completo `\s*(?:19|20)[0-9]{2}\s*[ .\-]\s*([0-9]{2})\s*`: `2014 01`, `2014.01`,
   `2014-01`). `emision` = los caracteres `[19:21]` del renglón 2 de la MRZ, si son dos dígitos.
   Si hay los dos: igual → `coincidencias` += `anio_registro, sufijo (MRZ)`; distinto →
   `discrepancias` += `anio_registro: el sufijo '{sufijo}' no cuadra con el de la MRZ '{emision}'`.
   Si falta alguno: **no escribe en ninguna lista.**

**Bloque 2, la CURP.** `curp` = el valor en mayúsculas, solo `[A-Z0-9]`.

- Si cumple `^[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d$`:
  - Si `curp[4:10]` es una fecha válida (misma regla de 8.2 para el año):
    `comparar("fecha_nacimiento (CURP)", frente.fecha_nacimiento, <esa fecha ISO>)`.
  - `comparar("sexo (CURP)", frente.sexo, curp[10])`.
  - Dígito verificador: ver abajo. Si se puede calcular: igual → `coincidencias` +=
    `curp (dígito verificador)`; distinto → `discrepancias` +=
    `curp: el dígito verificador no cuadra, la CURP trae '{curp[17]}' y el cálculo da '{esperado}'`.
- Si no cumple: `sin_verificar` += `CURP (formato inválido: '{curp}')`, con `ausente` en lugar de
  `{curp}` cuando está vacía. **Y además**, si `curp` mide 18 caracteres y alguna de las **4
  primeras** no es una letra: `discrepancias` += `curp: los primeros 4 caracteres deben ser letras y trae '{curp[:4]}'`.
  Las 4 primeras posiciones son siempre letras (las iniciales del nombre); un dígito ahí es una
  lectura errónea del OCR (`MAMI` leído `MAM1`, una `O` leída `0`). **No se corrige el valor**, solo
  se señala. Va después de la entrada de `sin_verificar`. Caso `31`.

**Dígito verificador de la CURP.** Diccionario `0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ`. Para las
primeras 17 posiciones, `suma = Σ índice_en_el_diccionario(carácter_i) × (18 − i)` con `i` desde 0;
el dígito es `(10 − suma mod 10) mod 10`. Se puede calcular solo si hay 17 caracteres y todos están
en el diccionario.

**Bloque 3, la clave de elector.** `clave` = el valor en mayúsculas, solo `[A-Z0-9]`.

- Si **no** mide 18: `sin_verificar` += `clave_elector (mide {n} caracteres, se esperaban 18)`.
- Si mide 18:
  1. `esperadas_curp` = `curp[0] + curp[13] + curp[2] + curp[14] + curp[3] + curp[15]` si la CURP
     tiene al menos 16 caracteres; si no, cadena vacía.
  2. `derivadas(saltar)` = `iniciales(ap_f, saltar) + iniciales(am_f, saltar) + iniciales(nombre_clave(nombre), saltar)`.
     `esperadas_nombre = derivadas(falso)`. `alternativas` = el conjunto de `derivadas(falso)` y
     `derivadas(verdadero)` que midan 6.
  3. Si `esperadas_curp` mide 6: `comparar("clave_elector vs CURP (iniciales)", clave[:6], esperadas_curp)`.
     Además, si `clave[:6] == esperadas_curp` y hay `alternativas` y `esperadas_curp` **no está**
     en ellas: `discrepancias` += `nombre: no concuerda con CURP ni clave de elector (ambas codifican '{esperadas_curp}', el nombre leído da '{esperadas_nombre}')`.
     *(CURP y clave concuerdan entre sí pero el nombre no: el dato malo es el nombre.)*
  4. Si no, y `esperadas_nombre` mide 6:
     `comparar("clave_elector vs nombre (iniciales)", clave[:6], X)` con `X = clave[:6]` si
     `clave[:6]` está en `alternativas`, y `esperadas_nombre` si no.
  5. Si no: `sin_verificar` += `clave_elector (iniciales)`.
  6. Si `clave[6:12]` es una fecha válida: `comparar("fecha_nacimiento (clave_elector)", frente.fecha_nacimiento, <fecha ISO>)`.
  7. `comparar("sexo (clave_elector)", frente.sexo, clave[14])`.

**`iniciales(palabra, saltar)`**: convertir a mayúsculas sin acentos y dejar solo `A-Z` y espacio;
si `saltar`, partir en palabras, **quitar del inicio las partículas** mientras quede más de una
palabra y quedarse con la primera; dejar solo `A-Z`. Si queda vacío, cadena vacía. Si no, la
**primera letra** más la **primera consonante** de las siguientes (vacío si no hay).
Partículas: `DA DAS DE DEL DER DI DIE DD EL LA LOS LAS LE LES MAC MC VAN VON Y`.

**`nombre_clave(nombre)`**: mayúsculas sin acentos, solo `A-Z` y espacio, partir en palabras; si
hay más de una y la primera es `MARIA`, `MA`, `JOSE` o `J`, la segunda; si no, la primera; vacío
si no hay palabras. (`MARIA VALENTINA` da `VALENTINA`.)

**Bloque 4, el domicilio.** Si `frente.domicilio` existe: se comprueba que el código postal caiga
en el estado, comparando sus **dos primeros dígitos** contra el rango de prefijos de SEPOMEX del
estado (tablas `PREFIJOS_CP` y `ALIAS_ESTADO` de `ine.py`: **cópialas literalmente**). El estado
se reduce a sus letras sin acentos para buscarlo (`D.F.` y `DISTRITO FEDERAL` son el mismo;
`MEXICO` a secas **no está en la tabla** porque es ambiguo entre estado y país).
Verdadero → `coincidencias` += `domicilio (código postal vs estado)`. Falso →
`discrepancias` += `domicilio: el código postal '{cp}' no corresponde al estado '{estado}'`.
Si el código no tiene 5 dígitos o el estado no se reconoce: **no escribe en ninguna lista.**

**Las tres comprobaciones que callan.** El dígito verificador de la CURP, el número de emisión y
el código postal contra estado aportan una coincidencia o una discrepancia cuando pueden correr,
y **nunca escriben en `sin_verificar`**. Es deliberado: los datos de los que dependen no son
estables entre llamadas, y si su ausencia bajara `completo`, el mismo documento oscilaría entre
`200` y `212` por pura variabilidad del extractor.

**Resultado:**

```
ok = (discrepancias está vacía)    completo = (sin_verificar está vacía)
```

### 8.4 Orden de las etiquetas

En `coincidencias`, `discrepancias` y `sin_verificar` el orden es el de aparición según 8.3:
MRZ → CURP → clave de elector → domicilio. Es el mismo orden del ejemplo de 6.1.

### 8.5 Correcciones con la MRZ

Hay dos, y en las dos la MRZ es la fuente buena (va en una tipografía hecha para OCR). Se aplican
en este orden: primero la A y luego la B. Cada una, **si actúa**:

- deja un aviso (8.8);
- quita de `coincidencias` las etiquetas de los campos que tocó, porque comparar un valor contra
  la fuente de la que salió es circular y no prueba nada. Eso se hace **después** de validar;
- deja el código en `211`, no en `210`.

#### A. Una letra de más

Cuando el OCR pega una letra de más a un apellido o nombre (`LARA` leído `LARAS`), se corrige
`frente` con el valor de la MRZ **solo si se cumplen todas**:

- La MRZ es íntegra (`integra`).
- No hay `campo_truncado` (si el renglón de nombres llegó al borde, el formato abrevia y el valor
  del frente, completo, es el correcto).
- El valor del frente **empieza con** el de la MRZ, el valor de la MRZ mide al menos 3
  caracteres, y lo que sobra son **1 o 2 letras `A-Z`**, sin espacios (con espacios podría ser
  un apellido compuesto).

Se revisan, en este orden, `apellido_paterno` (contra `mrz.apellido_paterno`), `apellido_materno`
(contra `mrz.apellido_materno`) y `nombre` (contra `mrz.nombres`). Cada campo corregido produce un
aviso y pierde su etiqueta `apellido_paterno (MRZ)`, `apellido_materno (MRZ)` o `nombre (MRZ)` en
`coincidencias`. Caso `07`.

#### B. Las palabras correctas en el campo equivocado

El OCR leyó bien las palabras pero el motor las asignó al campo que no era. Caso real: la
credencial imprime `VENTURA`, `GARCIA` y `URBANO` bajo NOMBRE y el motor devolvió paterno
`GARCIA`, materno `URBANO` y nombre `VENTURA`; la MRZ trae `VENTURA<GARCIA<<URBANO`, donde el `<<`
separa los apellidos del nombre. Se reasignan los tres campos con el reparto de la MRZ **solo si
se cumplen todas**:

- La MRZ es íntegra y no tiene `campo_truncado`.
- Los tres campos del frente y los tres de la MRZ tienen valor.
- El nombre del frente **no** coincide con el de la MRZ, comparando las palabras plegadas. (Si
  coincide y solo cambia el reparto entre los apellidos, es un apellido compuesto y eso ya lo
  resuelve 8.3.)
- Las palabras de los tres campos del frente, juntas, son **las mismas** que las de los tres campos
  de la MRZ (plegadas). Se recorre la MRZ en su orden (paterno, materno, nombre) y cada palabra se
  busca entre las del frente que aún no se usaron; si falta alguna, o sobra alguna, no se hace nada.
  **Las palabras que se asignan son las del frente**, con su `Ñ` y sus acentos, no las de la MRZ.
- La CURP o la clave de elector, que son lectura independiente de la MRZ, codifican las iniciales
  del orden de la MRZ y **no** las del orden del frente. Los *testigos* son `esperadas_curp` (8.3,
  bloque 3) y `clave[:6]` si la clave mide 18, los que midan 6. `derivadas(p, m, n)` es el conjunto
  de `iniciales(p) + iniciales(m) + iniciales(nombre_clave(n))`, con y sin saltar partículas, que
  midan 6. La condición es que **algún testigo esté en `derivadas(<los nuevos>)`** y **ningún
  testigo esté en `derivadas(<los leídos>)`**. Sin testigos no se hace nada: sin esa segunda fuente
  no hay forma de saber cuál de los dos repartos es el bueno.

Si actúa, **los tres** campos pierden su etiqueta `... (MRZ)` en `coincidencias`. Aviso:

```
frente: apellidos y nombre reacomodados con la MRZ: se leyó paterno '{p}', materno '{m}', nombre '{n}' y la MRZ trae paterno '{p2}', materno '{m2}', nombre '{n2}'
```

donde los tres últimos son los valores nuevos, es decir, las palabras del frente ya reasignadas.
Caso `30`.

### 8.6 El orden completo de armado de la respuesta

1. `frente`: para cada llave de 6.2 que exista, normalizar (8.1); limpiar el domicilio.
2. `formato_credencial` y `no_impresos`.
3. Localizar y parsear la MRZ (8.2); `origen`.
4. Corrección de letras de más con la MRZ (8.5 A) → avisos.
5. Reacomodo con la MRZ (8.5 B) → avisos.
6. Bloques repetidos → avisos.
7. Valores que no aparecen en el texto → avisos.
8. `validar` (8.3) y quitar las etiquetas circulares (8.5).
9. Revisar la cara correcta de cada archivo (sección 4, paso 5); si falla, es `228` o `227` y
   no se devuelve nada de lo anterior.
10. Calcular el código (sección 5) y agregar `clasificacion` y `clasificacion_atras`.

### 8.7 Cuándo el anverso es en realidad un reverso (`228`)

Las **tres** condiciones, juntas (la conjunción evita rechazar un anverso mal leído):

1. `frente` no tiene `curp` ni `clave_elector`.
2. El texto del OCR **del anverso** no contiene `CURP` ni `CLAVE DE ELECTOR`, buscados en
   mayúsculas con cualquier cantidad de espacios entre las palabras.
3. En ese mismo texto se localiza una MRZ (8.2).

Un PDF con las dos caras en un solo archivo **no** cae aquí, porque su anverso sí trae CURP.

### 8.8 Avisos: las cuatro únicas formas

| Aviso | Cuándo |
|---|---|
| `frente.{campo} corregido con la MRZ: se leyó '{leído}' y la MRZ trae '{mrz}'` | Corrección A de 8.5. `{campo}` es `apellido_paterno`, `apellido_materno` o `nombre` |
| `frente: apellidos y nombre reacomodados con la MRZ: se leyó paterno '{p}', materno '{m}', nombre '{n}' y la MRZ trae paterno '{p2}', materno '{m2}', nombre '{n2}'` | Reacomodo B de 8.5 |
| `el PDF contiene {n} bloques '{tipo}': se conservó el primero, revisa que el documento no traiga más de una identidad` | El motor devolvió más de un bloque del mismo `{tipo}` (por ejemplo `domicilio`) en el anverso. Se conserva el **primero**. Opcional si tu motor no puede detectarlo. **Si tu flujo acepta PDFs de varias páginas, trátalo como bloqueante** |
| `frente.{campo}='{valor}' no aparece literal en el texto leído` | Ver abajo |

**Valor que no aparece literal.** Para cada llave de `frente` cuyo valor es **texto de más de 3
caracteres**, y que no es una fecha ISO ni `formato_credencial` ni `no_impresos`, si el valor
**no es subcadena** del `texto_ocr` (el de los dos archivos juntos, comparación exacta, con
mayúsculas) se avisa. Un valor así es uno que el motor compuso en vez de leer. Se evalúa **después**
de las dos correcciones con la MRZ (8.5). `domicilio` es un objeto y no se evalúa; `sexo`, `numero_emision`
y las claves de tres caracteres o menos tampoco, por su longitud.

---

## 9. El clasificador

El extractor **no sabe decir que no**: si le das un recibo de luz, le busca CURP y domicilio de
todos modos. Por eso cada archivo pasa antes por un clasificador. Lo que necesitas replicar es
el **comportamiento**, no el modelo:

**Entrada:** el archivo (RAD solo mira las primeras 2 páginas). **Salida:**
`{ "categoria", "confianza", "caracteres_leidos" }`.

- Si el OCR no leyó casi nada (**10 caracteres o menos**) y no hay categoría:
  `{ "categoria": "hoja_en_blanco", "confianza": null, "caracteres_leidos": n }`.
- Si no hay categoría pero el OCR sí leyó texto: `categoria: null`.
- Si hay categoría: la de mayor confianza.
- Si el clasificador **falla**: `{ "categoria": null, "confianza": null }`. **Nunca debe tumbar la
  petición.**

**Decisión** (`es_categoria`, esperada = `ine`): el archivo **pasa** a lectura si:

1. `categoria` no es `hoja_en_blanco`, **y** además
2. `categoria` es `null`, **o** es `ine`, **o** la confianza es menor a **0.60**.

Si no pasa: `221` cuando es `hoja_en_blanco`, `220` en cualquier otro caso.

**Por qué falla hacia adelante:** rechazar de más es peor que extraer de más, porque un dato malo
todavía lo puede cachar `validacion`, y un rechazo injusto deja al usuario sin trámite. Solo se
rechaza cuando el clasificador afirma activamente y con confianza que el documento es otra cosa.

Un clasificador por reglas es válido mientras cumpla lo anterior; entonces `confianza` va `null`.
Las categorías que tiene que poder distinguir son, como mínimo, `ine`, `otro` y `hoja_en_blanco`.

---

## 10. Guardias del archivo

El archivo se revisa **antes** de mandarlo al motor. Distinguir "tiene contraseña" de "está
dañado" solo se puede mirando los bytes: Document AI mete las dos causas en un mismo mensaje.
Es un filtro barato, no una validación exhaustiva.

**Tipo real por los primeros bytes:**

| Tipo | Firma |
|---|---|
| `application/pdf` | empieza con `%PDF-` |
| `image/jpeg` | `FF D8 FF` |
| `image/png` | `89 50 4E 47 0D 0A 1A 0A` |
| `image/tiff` | `II*\0` o `MM\0*` |
| `image/webp` | `RIFF` en 0..4 y `WEBP` en 8..12 |

Otros formatos (HEIC de un iPhone, GIF, BMP) **no se reconocen**: responden `222` aunque el
cliente los declare como `image/jpeg`.

**Revisión**, en este orden. Todos los mensajes llevan el prefijo `Archivo '{campo}': `:

| Condición | codigo | mensaje |
|---|---:|---|
| 0 bytes | 222 | `El archivo llegó vacío (0 bytes).` |
| Los bytes no son de ningún tipo reconocido | 222 | `El archivo no es un {tipo declarado} válido: no se reconoce su formato. Puede estar dañado o ser de otro tipo.` |
| PDF con `/Encrypt` | 223 | `El PDF está protegido con contraseña. Quítasela y vuelve a enviarlo.` |
| PDF sin `%%EOF` **o** sin `startxref` en los últimos 2048 bytes | 222 | `El PDF está incompleto: le falta el final del archivo. Probablemente se cortó al descargarlo o al subirlo.` |

- **Cifrado:** el trailer declara `/Encrypt` seguido de una referencia indirecta
  (`/Encrypt 12 0 R`) o de `<<`. Buscar el texto `/Encrypt` pelado daría falsos positivos.
- **El cifrado se revisa antes que el truncamiento**: un PDF protegido también falla otras
  revisiones, y decir "tiene contraseña" es más accionable que "está dañado".
- Lo que se le escape a este filtro lo debe cachar tu motor, y su error se traduce **al mismo**
  `222`: `El archivo no se pudo abrir: está dañado, incompleto o protegido.`

**Errores del motor** (los traduces desde el tuyo; sin prefijo de archivo):

| Causa | codigo |
|---|---:|
| Más páginas de las que el motor procesa en una llamada (RAD: 15) | 224 |
| Cuota o límite de tasa agotado | 429 |
| Archivo ilegible para el motor | 222 |
| El motor falló o respondió algo no interpretable | 502 |
| Servicio no disponible o no configurado | 503 |

---

## 11. Decisiones pendientes

RAD está sincronizado con santander_ia (`af35409`) y cumple el estándar de errores de la sección
5, así que en este endpoint ya no quedan diferencias de comportamiento con el resto. Quedan **dos
decisiones abiertas** de Moibe. **Mientras no las tome, implementa lo escrito en las secciones
anteriores** y no inventes una solución propia en estos dos puntos.

1. **La `Ñ` en las iniciales de la clave de elector.** La CURP sustituye la `Ñ` por `X`, pero
   `iniciales()` la vuelve `N` al quitar acentos. Un anverso con `PEÑA`, `MUÑOZ` o `NUÑEZ` puede
   dar un `210` falso (`nombre: no concuerda con CURP ni clave de elector`). Pasa igual en
   santander_ia. Se comprobó con datos sintéticos; falta confirmarlo con una credencial real y
   decidir si se corrige en los dos sistemas a la vez. Los vectores evitan a propósito apellidos
   con `Ñ`.
2. **Numeración compartida.** santander_ia usa `226` y `227` para `SIN_DOMICILIO` y
   `NO_ES_ESTADO_CUENTA`; RAD usa `227` para `NO_ES_REVERSO_INE`. Si un mismo consumidor integra
   los dos sistemas, el `codigo` solo no basta: desambigua con `estado`.

**Mantenerlo al día.** Si santander_ia cambia su `ine.py`, hay que volver a sincronizar el de RAD
(menos el sello, y con el plegado de acentos) y regenerar los vectores. El comando
`python generar_vectores_ine_ambas.py --verificar` dice si el contrato cambió.

---

## 12. Lista de conformidad

Antes de dar por terminado:

- [ ] Los **32 vectores** pasan. Los casos `19` a `24`, `26` a `28` y `32` solo dependen de las
      guardias y del sobre (su `entrada` describe el archivo: tipo declarado y contenido, para que
      lo fabriques); los `01` a `18`, `25` y `29` a `31` de la capa de validación (su `entrada` trae
      `campos` y `texto_ocr` de cada archivo).
- [ ] En cada caso se compara `http_status` y `salida_esperada`, salvo lo que liste `no_comparar`
      (llaves de `salida_esperada`; si dice `salida_esperada` completa, solo se compara el HTTP).
- [ ] `confianza` y `caracteres_leidos` no se comparan; `categoria` sí.
- [ ] Ninguna respuesta trae `sello`.
- [ ] Todas las respuestas `2xx` salen con HTTP 200; las demás, con HTTP = `codigo`.
- [ ] Toda respuesta abre con `codigo`, `estado`, `mensaje`, incluido el `422` y el `500`.
- [ ] Una llave sin dato **no está**. Nada de `""` ni `null`, salvo `formato_credencial` y
      `clasificacion.confianza`.
- [ ] Un PNG declarado `image/jpeg` se procesa (como PNG).
- [ ] Los archivos **invertidos** responden `228`, no un `210` con datos mezclados.
- [ ] `GONZÁLEZ` en el anverso contra `GONZALEZ` en la MRZ **no** es discrepancia.
- [ ] La ruta responde directamente con y sin diagonal final (sin redirección).
- [ ] Tu `mensaje` en los rechazos lleva el prefijo `Archivo '...': `.

**Comportamiento observado en RAD con escaneos reales** (para que sepas qué esperar de la
referencia), con el mismo par de archivos de una misma credencial:

| Se manda | Responde |
|---|---|
| `frente` y `atras` en orden | `200`, 16 coincidencias, sin discrepancias |
| `frente` y `atras` invertidos | `228` |
| El mismo frente en los dos campos | `227` (el campo `atras` no trae MRZ) |
| El mismo reverso en los dos campos | `228` |
| Frente de una persona con el reverso de otra | `210`, con discrepancias en nacimiento, nombre, sección y vigencia |
| Falta un campo | HTTP `422`, con el sobre |

**Latencia:** en las pruebas de RAD este endpoint tardó entre 22 y 29 segundos, porque hace dos
lecturas y dos clasificaciones. Los consumidores configuran un timeout holgado; si tu motor es
más rápido, mejor, pero eso no cambia el contrato.

---

## 13. Referencia de código

Fijado al commit `10f3a06` de https://github.com/Moibe/document_ai :

| Archivo | Qué tiene |
|---|---|
| `ine.py` | Normalización, MRZ, validación cruzada, corrección de nombres, armado de `frente`/`atras`/`validacion`/`avisos`. **Las tablas `HOMOGLIFOS`, `PREFIJOS_CP` y `ALIAS_ESTADO`** |
| `ine_atras.py` | Localiza la MRZ en el texto del reverso; devuelve `None` si no hay (→ `227`) |
| `codigos.py` | Desenlaces, `de_extraccion` (cómo sale el código) y `cuerpo` (el sobre) |
| `archivo.py` | Guardias del archivo (sección 10) |
| `clasificador.py` | `es_categoria` y la lógica de `hoja_en_blanco` (sección 9) |
| `funciones.py` | `_procesa`: el flujo de 4 a 6, `_parece_reverso` (8.7) y la traducción de errores del motor |
| `app.py` | `procesa_documento_ine_ambas` y `_revisar_y_clasificar`: el flujo de 1 a 3 y la precedencia; los manejadores del `422` y del `500` con sobre |
| `generar_vectores_ine_ambas.py` | Genera los vectores corriendo el endpoint real con datos ficticios; con `--verificar` comprueba que el contrato no haya cambiado |

El repo **no declara licencia**. Implementar desde este contrato no tiene ese problema; si vas a
copiar código literal (por ejemplo las tablas), confírmalo antes con Moibe.

Si cambia la lógica de RAD, hay que volver a correr `generar_vectores_ine_ambas.py` y revisar el
diff del JSON: **ese diff es el cambio de contrato**. Con `--verificar` no escribe nada y avisa
qué casos cambiaron.
