import base64
import json
import logging
import re
import httpx
import ine
import codigos
import herramientas
from fastapi import UploadFile, HTTPException, status
from google.auth import default
from typing import Optional, Union
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Mapeo de endpoints por tipo de documento
ENDPOINTS = {
    "pasaporte": "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/24ee194a233ec5cc:process",  # Endpoint Pasaporte
    "fm": "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/edfba1d1c9ed6145:process",  # Endpoint Formas Migratorias
    "csf": "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/339fc7810b01699b:process",  # Endpoint Sat CSF
    "cedula": "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/2da02220d55dabf1/processorVersions/pretrained-foundation-model-v1.5-pro-2025-06-20:process",  # Endpoint Cédula Profesional
    "ine": "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/bf35151f51b51521:process",  # Endpoint INE
}

# Mapeo de MIME types por tipo de documento
MIME_TYPES = {
    "pasaporte": "image/png",     # Imagen
    "fm": "image/png",             # Imagen
    "csf": "image/png",            # Imagen
    "cedula": "application/pdf",   # PDF
}

# La INE acepta PDF e imagen: se manda a Document AI el MIME real del archivo.
MIME_TYPES_PERMITIDOS_INE = ("application/pdf", "image/jpeg", "image/png", "image/tiff", "image/webp")

# Variables legadas para compatibilidad
ENDPOINT_URL = ENDPOINTS["pasaporte"]
ENDPOINT_FM = ENDPOINTS["fm"]
ENDPOINT_CSF = ENDPOINTS["csf"]
ENDPOINT_CEDULA = ENDPOINTS["cedula"]
ENDPOINT_INE = ENDPOINTS["ine"]


async def procesa_documento(
    file: Union[UploadFile, str],
    doc_type: str,
    mime_type_override: Optional[str] = None
):
    """
    Función genérica para procesar documentos con Google Document AI.
    
    Args:
        file: UploadFile (para endpoints que reciben upload) o ruta string (para archivos locales)
        doc_type: Tipo de documento ("pasaporte", "fm", "csf", "cedula")
        mime_type_override: Tipo MIME personalizado (opcional, por defecto se detecta del archivo)
    
    Returns:
        Diccionario con entidades extraídas o error
    """
    # Validar tipo de documento
    if doc_type not in ENDPOINTS:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Tipo de documento no soportado: {doc_type}. Opciones: {list(ENDPOINTS.keys())}"
        )

    endpoint_url = ENDPOINTS[doc_type]

    # Obtener credenciales y token
    try:
        credentials, _ = default(scopes=SCOPES)
        credentials.refresh(Request())
        access_token = credentials.token
        # print(f"Access token obtenido para {doc_type}")
        logger.info(f"Access token obtenido para {doc_type}")
    except Exception as e:
        # print(f"Error al obtener credenciales de Google: {e}")
        logger.exception(f"Error al obtener credenciales de Google: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudieron obtener las credenciales de Google Cloud. Verifica la configuración de GOOGLE_APPLICATION_CREDENTIALS en el servidor."
        )

    #Obtengo base64 y MIME type según el tipo de entrada
    try:
        if isinstance(file, str):
            # Es una ruta de archivo local
            base64_content = herramientas.archivo_local_a_base64(file)
            mime_type = mime_type_override or MIME_TYPES.get(doc_type, "image/png")
        else:
            # Es un UploadFile
            base64_content = await herramientas.upload_a_base64(file)
            mime_type = mime_type_override or MIME_TYPES.get(doc_type, file.content_type or "application/octet-stream")
    except Exception as e:
        # print(f"Error al procesar archivo: {e}")
        logger.exception(f"Error al procesar archivo: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error al procesar el archivo enviado: {e}"
        )

    # print(f"MIME type para {doc_type}: {mime_type}")
    logger.debug(f"MIME type para {doc_type}: {mime_type}")
    
    # Estructura del JSON para Document AI
    processor_json = {
        "rawDocument": {
            "mimeType": mime_type,
            "content": base64_content
        }
    }

    # Definir las cabeceras con el token de autenticación
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url=endpoint_url, 
                headers=headers, 
                json=processor_json
            )
            response.raise_for_status()

    except httpx.HTTPStatusError:
        # print(f"Error {response.status_code} del servidor Document AI.")
        # print("Mensaje de error:", response.text)
        logger.error(f"Error {response.status_code} del servidor Document AI. Mensaje: {response.text}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error de Document AI ({response.status_code}): {response.text}"
        )

    except Exception as e:
        # print(f"Error al ejecutar Document AI: {e}")
        logger.exception(f"Error al ejecutar Document AI: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error al comunicarse con Document AI: {e}"
        )

    # print(f"Respuesta recibida para {doc_type}")
    logger.info(f"Respuesta recibida para {doc_type}")

    try:
        data_json = response.json()
        return herramientas.obtener_datos_completos(data_json)
    except Exception as e:
        # print(f"Error al procesar respuesta: {e}")
        logger.exception(f"Error al procesar respuesta: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error al procesar la respuesta de Document AI: {e}"
        )


# Funciones wrapper para compatibilidad con app.py (mantienen la interfaz anterior)
async def procesa_pasaporte(image: UploadFile):
    """Procesa un pasaporte."""
    return await procesa_documento(image, "pasaporte")


async def procesa_fm(image: UploadFile):
    """Procesa una forma migratoria."""
    return await procesa_documento(image, "fm")


async def procesa_csf(ruta_imagen_salida: str):
    """Procesa un archivo CSF (Constancia de Situación Fiscal)."""
    return await procesa_documento(ruta_imagen_salida, "csf", mime_type_override="image/png")


async def procesa_cedula(pdf_file: UploadFile):
    """Procesa una cédula profesional (PDF)."""
    return await procesa_documento(pdf_file, "cedula")


def _detalles_error(texto: str):
    """Saca `reason` y `metadata` del error estructurado de Document AI.

    La respuesta de error trae, además del mensaje en prosa, un bloque
    `error.details[]` con un `reason` estable y metadatos. Ejemplo real:

        {"reason": "PAGE_LIMIT_EXCEEDED",
         "metadata": {"page_limit": "15", "pages": "20"}}

    Vale mucho más que el texto: el mensaje en prosa YA CAMBIÓ una vez (en 2026
    decía "Document pages in non-imageless mode exceed the limit: 15 got 28.
    Try using imageless mode..." y hoy dice "Document pages exceed the limit:
    15 got 20"), mientras que el `reason` es parte del contrato.
    """
    try:
        error = json.loads(texto or "{}").get("error", {})
    except (ValueError, AttributeError):
        return "", {}
    for detalle in error.get("details", []):
        if detalle.get("reason"):
            return detalle["reason"], detalle.get("metadata") or {}
    return "", {}


def _desenlace_de_error(status_code: int, texto: str):
    """Traduce el error de Document AI a un desenlace accionable.

    Primero se mira el `reason` estructurado, que es estable. El texto en prosa
    queda de respaldo, buscando por SUBCADENA y no por igualdad: los mensajes
    traen números y nombres de cuota variables, y Google los reescribe. Si
    cambian de forma que ya no coincidan, el error cae en el genérico de
    siempre — se degrada, no se rompe.

    Devuelve (desenlace, mensaje) o None si no se reconoce.
    """
    reason, meta = _detalles_error(texto)
    t = (texto or "").lower()

    if reason == "PAGE_LIMIT_EXCEEDED" or ("exceed the limit" in t and "pages" in t):
        tope, trae = meta.get("page_limit"), meta.get("pages")
        if not (tope and trae):
            m = re.search(r"limit:\s*(\d+)\s*got\s*(\d+)", t)
            if m:
                tope, trae = m.group(1), m.group(2)
        if tope and trae:
            return codigos.LIMITE_PAGINAS, (
                f"El documento tiene {trae} páginas y el máximo por llamada es {tope}. "
                "Envía solo las páginas de la credencial."
            )
        return codigos.LIMITE_PAGINAS, None

    if status_code == 429 or "quota exceeded" in t or "RATE_LIMIT" in reason:
        return codigos.CUOTA_EXCEDIDA, None

    if "could not be opened or is corrupted" in t or "invalid image content" in t:
        return codigos.ARCHIVO_CORRUPTO, (
            "El archivo no se pudo abrir: está dañado, incompleto o protegido."
        )

    return None


# El processor de document_ai nombra distinto varios campos que ine.py espera con
# los nombres del esquema de santander_ia. Se traducen aquí, a la entrada, para
# que ine.py quede idéntico al de santander_ia y se pueda comparar tal cual.
CAMPOS_PROCESSOR_INE = {
    "estado": "estado_clave",
    "municipio": "municipio_clave",
    "localidad": "localidad_clave",
    "emision": "anio_emision",
    "fecha_registro": "anio_registro",
}


def _a_esquema_ine(entidades: dict) -> dict:
    """Renombra los campos del processor de document_ai a los que usa ine.py.

    Ojo con `domicilio`: ahí `localidad` es el municipio (ej. 'HUATABAMPO,'),
    que en el esquema de ine.py se llama `municipio_delegacion`.
    """
    traducido = {CAMPOS_PROCESSOR_INE.get(campo, campo): valor
                 for campo, valor in entidades.items()}
    domicilio = traducido.get("domicilio")
    if isinstance(domicilio, dict) and "localidad" in domicilio:
        domicilio = dict(domicilio)
        domicilio.setdefault("municipio_delegacion", domicilio.pop("localidad"))
        traducido["domicilio"] = domicilio
    return traducido


async def procesa_ine(contenido: bytes, mime_type: str, clasificacion: Optional[dict] = None):
    """
    Procesa una INE (PDF o imagen) con Document AI y arma la respuesta con
    validación cruzada (ver ine.py).

    Recibe los bytes ya leídos y no el UploadFile: el endpoint tiene que leer el
    stream antes para pasárselo también al clasificador, y solo se puede leer
    una vez. Las fallas se lanzan como `codigos.ErrorAPI`.
    """
    endpoint_url = ENDPOINTS["ine"]

    try:
        credentials, _ = default(scopes=SCOPES)
        credentials.refresh(Request())
        access_token = credentials.token
        logger.info("Access token obtenido para ine")
    except Exception as e:
        logger.exception(f"Error al obtener credenciales de Google: {e}")
        raise codigos.ErrorAPI(
            codigos.SIN_CREDENCIALES,
            "No se pudieron obtener las credenciales de Google Cloud. Verifica la "
            "configuración de GOOGLE_APPLICATION_CREDENTIALS en el servidor.",
        )

    processor_json = {
        "rawDocument": {
            "mimeType": mime_type,
            "content": base64.b64encode(contenido).decode("utf-8"),
        }
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url=endpoint_url, headers=headers, json=processor_json)
            response.raise_for_status()

    except httpx.HTTPStatusError:
        logger.error(f"Error {response.status_code} del servidor Document AI. Mensaje: {response.text}")
        # Varias fallas de Document AI son accionables por el usuario y no
        # merecen reportarse como "el servicio falló": documento demasiado
        # largo, cuota agotada, archivo ilegible.
        traducido = _desenlace_de_error(response.status_code, response.text)
        if traducido:
            desenlace, mensaje = traducido
            raise codigos.ErrorAPI(desenlace, mensaje)
        raise codigos.ErrorAPI(
            codigos.ERROR_DOCUMENT_AI,
            f"Error de Document AI ({response.status_code}): {response.text}",
        )

    except Exception as e:
        logger.exception(f"Error al ejecutar Document AI: {e}")
        raise codigos.ErrorAPI(
            codigos.ERROR_DOCUMENT_AI,
            f"Error al comunicarse con Document AI: {e}",
        )

    logger.info("Respuesta recibida para ine")

    try:
        data_json = response.json()
        entidades = _a_esquema_ine(
            herramientas.obtener_datos_completos(data_json, conservar_primero=True))
        respuesta = ine.armar_respuesta(
            entidades,
            repetidos=herramientas.contar_repetidos(data_json),
            texto_ocr=data_json.get("document", {}).get("text", ""),
        )
        if clasificacion:
            respuesta["clasificacion"] = clasificacion
        return respuesta
    except Exception as e:
        logger.exception(f"Error al procesar respuesta: {e}")
        raise codigos.ErrorAPI(
            codigos.ERROR_DOCUMENT_AI,
            f"Error al procesar la respuesta de Document AI: {e}",
        )
