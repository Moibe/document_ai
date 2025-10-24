import json
import requests
import herramientas
from fastapi import UploadFile

from google.auth import default
from google.auth.transport.requests import Request

ENDPOINT_URL = "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/551531d174dec8aa:process"

async def procesa_pasaporte(image: UploadFile):
    
    # Obtener la cadena Base64
    base64_content = await herramientas.upload_a_base64(image)
    
    # Obtener el MIME type (FastAPI lo da directamente)
    mime_type = image.content_type
    
    # Estructura del JSON para Document AI
    processor_json = {
        "document": {
            "mimeType": mime_type,
            "content": base64_content
        },
        "processor": "projects/62740263137/locations/us/processors/551531d174dec8aa"
    }

    # Definir las cabeceras con el token de autenticación
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    # Enviar la solicitud POST
    response = requests.post(ENDPOINT_URL, 
                            headers=headers, 
                            data=json.dumps(processor_json))
















    
    # Aquí iría la llamada a la API de Document AI usando 'requests'
    # return llamar_document_ai(processor_json)
    
    return {"base64_length": len(base64_content), "mime_type": mime_type}