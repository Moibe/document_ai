"""Clasificador previo a la extracción: decide si el documento es una INE.

Existe porque el extractor NO sabe decir que no. Es un modelo de extracción:
si le das un recibo de luz, le busca CURP y domicilio de todos modos y te
devuelve algo con confianza alta. Medido en este proyecto: un PDF con una INE
y una credencial universitaria terminó devolviendo el domicilio de la
universidad con confianza 1.0000.

Un Custom Document Classifier es un procesador DISTINTO al extractor: no saca
campos, solo etiqueta el documento entero con una categoría. Se consulta antes
de extraer, y si la respuesta no es `ine` la petición se corta ahí — sin gastar
la llamada de extracción, que se cobra por página.

Referencia: `nexus_back/servicios/procesadores.py` y `servicios/esquema.py`,
donde este mismo mecanismo ya está en producción. Las decisiones no obvias de
aquí (la categoría de escape, `fromStart`, no usar `imagelessMode`) vienen de
fallos reales medidos en ese proyecto; los comentarios dicen cuáles.
"""
import logging
from typing import Any, Dict, Optional

import httpx
from google.auth import default
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

PROJECT_ID = "62740263137"
LOCATION = "us"
PADRE = f"projects/{PROJECT_ID}/locations/{LOCATION}"

# displayName fijo: es el guardia de idempotencia. `processors.create` no acepta
# requestId y `processors.list` no filtra, así que dos ejecuciones de la
# sincronización crearían dos procesadores; buscar por este nombre exacto antes
# de crear hace que la segunda adopte el de la primera.
DISPLAY_NAME = "santander_ine_clasificador"

# Se llena tras correr `python crear_clasificador.py`.
CLASIFICADOR_ID = "d00e2f8bb0876d4a"

# Versión fijada a propósito: sin esto Google puede promover otra versión por
# default sin aviso y cambiar el comportamiento del endpoint de un día a otro.
VERSION = "pretrained-classifier-v1.5-2025-08-05"

CATEGORIA_INE = "ine"
CATEGORIA_PASAPORTE = "pasaporte"
CATEGORIA_OTRO = "otro"

# Categoría DERIVADA EN CÓDIGO, no del modelo. Se intentó agregarla al esquema
# del clasificador y no funciona: ante una página sin contenido Document AI no
# devuelve ninguna entidad, así que no hay dónde colgar la etiqueta — medido
# con cuatro variantes (página vacía, solo espacios, escaneo sucio con motas,
# hoja con franja del escáner) y las cuatro siguen saliendo sin entidades con
# la categoría en el esquema y sin ella. De pilón, agregarla al esquema bajó la
# confianza de una INE real de 0.9989 a 0.8950, o sea que costaba sin dar nada.
#
# La señal buena ya viene gratis en la misma respuesta: el texto que leyó el
# OCR. Medido sobre los mismos documentos: las INEs reales traen entre 468 y
# 1199 caracteres, los documentos ajenos entre 217 y 360, y las cuatro hojas en
# blanco exactamente 0.
CATEGORIA_BLANCO = "hoja_en_blanco"

# Margen amplio a propósito: lo medido son 0 caracteres contra un mínimo de 217
# en documentos con contenido, así que cualquier umbral chico separa igual de
# bien y este tolera que una mota del escáner se lea como un carácter suelto.
MAX_CARACTERES_BLANCO = 10

# Cuántas páginas ve el clasificador. No es optimización prematura: el
# procesamiento en línea topa en 15 páginas (un PDF más largo se rechaza con
# 400) y la facturación es POR PÁGINA. Para saber QUÉ es un documento basta el
# principio; el documento completo hace falta para extraerle datos, que es otra
# llamada. Riesgo aceptado: un documento que solo se distinga después de la
# página 2 se clasificaría mal.
PAGINAS_A_CLASIFICAR = 2

# Debajo de esto no se confía en la etiqueta y se deja pasar a extracción, para
# no rechazar una INE legítima por una clasificación dudosa. Rechazar de más es
# peor que extraer de más: lo segundo el bloque `validacion` todavía lo puede
# cachar, lo primero deja al usuario sin trámite.
CONFIANZA_MINIMA = 0.60


def esquema() -> Dict[str, Any]:
    """DocumentSchema del clasificador.

    Distinto al del extractor: varios entityTypes hoja, uno por categoría, cada
    uno con baseTypes ['document'] (aplica al documento ENTERO) y SIN
    properties (no extrae nada). `documentSplitter: false` le confirma a Google
    que son categorías y no cortes de partición de un archivo compuesto.

    La categoría `otro` NO es opcional. Un clasificador que solo conoce
    categorías reales no tiene dónde poner un documento desconocido y FUERZA
    todo a alguna de las que sí tiene — en nexus_back está medido dos veces:
    una captura de Figma salió clasificada como INE con 99.99% de confianza.

    Ojo con la descripción de `otro`: tiene que existir como salida, sin volverse
    más atractiva que la categoría real. En nexus_back la primera versión decía
    "...incluso si se parece un poco a alguno" y eso la convertía en la mejor
    descrita del esquema, o sea la más fácil de elegir. Se quitó al día
    siguiente. Aquí se describe de forma neutra, por eso.
    """
    return {
        "displayName": "Clasificador de documentos Santander",
        "description": "Distingue que tipo de documento de identidad es el que entra.",
        "metadata": {"documentSplitter": False},
        "entityTypes": [
            {
                "name": CATEGORIA_INE,
                "displayName": "Credencial para votar (INE/IFE)",
                "baseTypes": ["document"],
                "description": (
                    "Credencial para votar expedida por el Instituto Nacional Electoral "
                    "(INE) o el antiguo Instituto Federal Electoral (IFE) de México. "
                    "Trae la leyenda INSTITUTO NACIONAL ELECTORAL o CREDENCIAL PARA "
                    "VOTAR, la fotografía del titular, su nombre, domicilio, CURP y "
                    "clave de elector, y en el reverso una zona de lectura mecánica de "
                    "tres renglones que empieza con IDMEX. Cuenta como esta categoría "
                    "aunque el documento sea un escaneo o fotocopia, aunque traiga "
                    "sellos o firmas encima, y aunque solo venga una de las dos caras."
                ),
            },
            {
                "name": CATEGORIA_PASAPORTE,
                "displayName": "Pasaporte",
                "baseTypes": ["document"],
                "description": (
                    "Pasaporte de cualquier pais. Trae la leyenda PASAPORTE o PASSPORT, "
                    "la fotografia del titular, numero de pasaporte, nacionalidad, fechas "
                    "de expedicion y vencimiento, y al pie de la hoja de datos dos "
                    "renglones de lectura mecanica de 44 caracteres que empiezan con la "
                    "letra P."
                ),
            },
            {
                "name": CATEGORIA_OTRO,
                "displayName": "Otro documento",
                "baseTypes": ["document"],
                "description": (
                    "Documento que no es una credencial para votar: pasaporte, licencia "
                    "de conducir, cédula profesional, comprobante de domicilio, estado "
                    "de cuenta, acta, formato bancario, o cualquier otra cosa."
                ),
            },
        ],
    }


def _token() -> str:
    credenciales, _ = default(scopes=SCOPES)
    credenciales.refresh(Request())
    return credenciales.token


def _url(api: str, ruta: str) -> str:
    return f"https://{LOCATION}-documentai.googleapis.com/{api}/{ruta}"


def cabeceras() -> Dict[str, str]:
    """
    `x-goog-user-project` es necesario cuando se autentica con credenciales de
    usuario (las de `gcloud`, o sea desarrollo local): Document AI exige un
    proyecto al cual cobrar la cuota y las credenciales de usuario no lo
    traen. Con el service account del servidor es redundante pero inofensivo,
    así que va siempre y el mismo código sirve en los dos lados.
    """
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json; charset=utf-8",
        "x-goog-user-project": PROJECT_ID,
    }


async def clasificar(contenido: bytes, mime_type: str) -> Dict[str, Any]:
    """Pregunta al clasificador qué es el documento.

    Devuelve {"categoria": str|None, "confianza": float|None}. `categoria` es
    None solo si Document AI respondió sin ninguna entidad — raro teniendo la
    categoría de escape, pero se contempla en vez de asumir.

    Nunca lanza por un fallo del clasificador: si la llamada falla, devuelve
    categoría None. Un clasificador caído no debe tumbar la extracción, que es
    el servicio principal; el endpoint decide qué hacer con la duda.
    """
    import base64

    if CLASIFICADOR_ID.startswith("REEMPLAZAR"):
        logger.warning("El clasificador no está configurado; se omite la clasificación.")
        return {"categoria": None, "confianza": None}

    recurso = f"{PADRE}/processors/{CLASIFICADOR_ID}"
    if VERSION:
        recurso = f"{recurso}/processorVersions/{VERSION}"

    cuerpo = {
        "rawDocument": {"mimeType": mime_type, "content": base64.b64encode(contenido).decode()},
        # Solo las primeras páginas: ver PAGINAS_A_CLASIFICAR.
        # Nada de imagelessMode: una credencial se reconoce por su layout y su
        # fotografía, y ese flag es ambiguo sobre si el modelo deja de verlas
        # (en nexus_back se quitó justo investigando por qué el clasificador no
        # reconocía una INE).
        "processOptions": {"fromStart": PAGINAS_A_CLASIFICAR},
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as cliente:
            respuesta = await cliente.post(
                _url("v1", f"{recurso}:process"),
                headers=cabeceras(),
                json=cuerpo,
            )
            respuesta.raise_for_status()
    except Exception as e:
        logger.exception(f"Falló la clasificación, se continúa sin ella: {e}")
        return {"categoria": None, "confianza": None}

    documento = respuesta.json().get("document", {})
    entidades = documento.get("entities") or []
    caracteres = len((documento.get("text") or "").strip())

    if not entidades:
        if caracteres <= MAX_CARACTERES_BLANCO:
            logger.info(f"Documento sin contenido: {caracteres} caracteres leídos.")
            return {
                "categoria": CATEGORIA_BLANCO,
                "confianza": None,
                "caracteres_leidos": caracteres,
            }
        logger.warning(
            f"El clasificador respondió sin entidades pese a leer {caracteres} caracteres."
        )
        return {"categoria": None, "confianza": None, "caracteres_leidos": caracteres}

    mejor = max(entidades, key=lambda e: e.get("confidence") or 0)
    return {
        "categoria": mejor.get("type"),
        "confianza": mejor.get("confidence"),
        "caracteres_leidos": caracteres,
    }


def es_categoria(resultado: Dict[str, Any], esperada: str) -> bool:
    """
    Si el documento debe pasar a extracción como `esperada`.

    Ante la duda se deja pasar: categoría desconocida (clasificador caído o sin
    configurar) o confianza por debajo del umbral cuentan como "sí". Se rechaza
    cuando el clasificador afirma activamente otra cosa —`otro`, o un tipo
    distinto del esperado— con confianza suficiente, y cuando el documento no
    trae contenido legible, donde no hay nada que extraer de todos modos.

    Desde que el clasificador conoce más de un tipo de documento, NO basta con
    preguntar "¿no es otro?": un pasaporte mandado a /ine se clasifica como
    `pasaporte`, que no es `otro`, y pasaría a un extractor que no le toca.
    """
    categoria = resultado.get("categoria")

    # Sin contenido legible no hay nada que extraer: rechazar es más útil que
    # devolver una respuesta vacía que el consumidor tiene que interpretar.
    if categoria == CATEGORIA_BLANCO:
        return False

    # Clasificador caído o sin veredicto: se deja pasar (ver docstring).
    if categoria is None:
        return True
    if categoria == esperada:
        return True

    confianza = resultado.get("confianza")
    return confianza is not None and confianza < CONFIANZA_MINIMA


def es_ine(resultado: Dict[str, Any]) -> bool:
    """Atajo para el endpoint de INE."""
    return es_categoria(resultado, CATEGORIA_INE)
