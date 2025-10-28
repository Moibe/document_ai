import herramientas
from fastapi import UploadFile
import httpx
from dotenv import load_dotenv
from google.auth import default
from google.auth.transport.requests import Request
import json
import time

# load_dotenv()
# print("Dot env cargado...")

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

#credentials, _ = default()
credentials, _ = default(scopes=SCOPES)
credentials.refresh(Request())
access_token = credentials.token

print("Access token: ", access_token)

#ENDPOINT_URL = "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/551531d174dec8aa:process" #Endpoint preentrenado
ENDPOINT_URL = "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/24ee194a233ec5cc:process" #Endpoint entrenado

async def procesa_pasaporte(image: UploadFile):
    
    # Obtener la cadena Base64
    base64_content = await herramientas.upload_a_base64(image)
        
    # Obtener el MIME type (FastAPI lo da directamente)
    mime_type = image.content_type
    print("Mimetype es: ", mime_type)
    
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
                                    url=ENDPOINT_URL, 
                                    headers=headers, 
                                    json=processor_json
                                    )
            response.raise_for_status()

    except httpx.HTTPStatusError:
        # Si la API devuelve 400, imprime el mensaje de error de Google
        print("Error 400/4xx/5xx del servidor.")
        print("Mensaje de error de Document AI:", response.text) 
        # Devuelve el error al usuario
        return {"error": f"Error de Document AI: {response.status_code}", "details": response.json()}
    
    except Exception as e:
        return {"error": f"Error al ejecutar document ai: {e}"}
    

    print("Response:")
    print(response)

    # 1. Obtener el JSON
    data_json = response.json() 

    # print("Response JSON:")
    # print(json.dumps(data_json, indent=2)) 
    print("Hecho")
    
    #entidades = herramientas.obtener_estructura_limpia_para_fastapi(data_json)
    #entidades = herramientas.imprimir_entidades(data_json)
    entidades = herramientas.simplificar_entidades_pasaporte(data_json)
    #entidades = herramientas.obtener_datos_pasaporte_completos(data_json)
    return entidades 
