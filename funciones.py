import httpx
import herramientas
from fastapi import UploadFile
from google.auth import default
from google.auth.transport.requests import Request
from typing import Optional, Union


SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Mapeo de endpoints por tipo de documento
ENDPOINTS = {
    "pasaporte": "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/24ee194a233ec5cc:process",  # Endpoint entrenado
    "fm": "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/edfba1d1c9ed6145:process",  # Endpoint Formas Migratorias
    "csf": "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/339fc7810b01699b:process",  # Endpoint Sat CSF
    "cedula": "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/2da02220d55dabf1/processorVersions/pretrained-foundation-model-v1.5-pro-2025-06-20:process",  # Nuevo
}

# Mantener variables legadas para compatibilidad
ENDPOINT_URL = ENDPOINTS["pasaporte"]
ENDPOINT_FM = ENDPOINTS["fm"]
ENDPOINT_CSF = ENDPOINTS["csf"]
ENDPOINT_CEDULA = ENDPOINTS["cedula"]


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
        return {"error": f"Tipo de documento no soportado: {doc_type}. Opciones: {list(ENDPOINTS.keys())}"}
    
    endpoint_url = ENDPOINTS[doc_type]
    
    # Obtener credenciales y token
    credentials, _ = default(scopes=SCOPES)
    credentials.refresh(Request())
    access_token = credentials.token
    print(f"Access token obtenido para {doc_type}")
    
    # Obtener base64 y MIME type según el tipo de entrada
    try:
        if isinstance(file, str):
            # Es una ruta de archivo local
            base64_content = herramientas.archivo_local_a_base64(file)
            mime_type = mime_type_override or "image/png"
        else:
            # Es un UploadFile
            base64_content = await herramientas.upload_a_base64(file)
            mime_type = mime_type_override or (file.content_type or "application/octet-stream")
    except Exception as e:
        return {"error": f"Error al procesar archivo: {e}"}
    
    print(f"MIME type para {doc_type}: {mime_type}")
    
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
        print(f"Error {response.status_code} del servidor Document AI.")
        print("Mensaje de error:", response.text) 
        return {"error": f"Error de Document AI: {response.status_code}", "details": response.json()}
    
    except Exception as e:
        return {"error": f"Error al ejecutar Document AI: {e}"}
    
    print(f"Respuesta recibida para {doc_type}")

    # Obtener el JSON
    try:
        data_json = response.json()
        entidades = herramientas.obtener_datos_completos(data_json)
        return entidades
    except Exception as e:
        return {"error": f"Error al procesar respuesta: {e}"}


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