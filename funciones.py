import herramientas
from fastapi import UploadFile
import httpx
from google.auth import default
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


#ENDPOINT_URL = "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/551531d174dec8aa:process" #Endpoint preentrenado
ENDPOINT_URL = "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/24ee194a233ec5cc:process" #Endpoint entrenado
ENDPOINT_FM = "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/edfba1d1c9ed6145:process" #Endpoint Formas Migratorias
ENDPOINT_CSF = "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/339fc7810b01699b:process" #Endpoint Sat CSF
ENDPOINT_CEDULA = "https://us-documentai.googleapis.com/v1/projects/62740263137/locations/us/processors/339fc7810b01699b:process" #Sustituir con el de Cédula Profesional

async def procesa_pasaporte(image: UploadFile):

    credentials, _ = default(scopes=SCOPES)
    credentials.refresh(Request())
    access_token = credentials.token
    print("Access token: ", access_token)
    
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

    return entidades 

async def procesa_fm(image: UploadFile):
    
    credentials, _ = default(scopes=SCOPES)
    credentials.refresh(Request())
    access_token = credentials.token
    print("Access token: ", access_token)

    base64_content = await herramientas.upload_a_base64(image)
        
    mime_type = image.content_type
    print("Mimetype es: ", mime_type)
    
    processor_json = {
        "rawDocument": {
            "mimeType": mime_type,
            "content": base64_content
        }
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                                    url=ENDPOINT_FM, 
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
    
    #entidades = herramientas.imprimir_entidades(data_json)
    #entidades = herramientas.simplificar_entidades_pasaporte(data_json)
    entidades = herramientas.obtener_datos_completos(data_json)
    return entidades 

async def procesa_csf(pdf_file: UploadFile):
    """
    Procesa un archivo PDF (Constancia de Situación Fiscal) usando Google Document AI.
    """

    credentials, _ = default(scopes=SCOPES)
    credentials.refresh(Request())
    access_token = credentials.token
    print("Access token: ", access_token)
    
    # --- CAMBIO APLICADO: La función ahora espera 'pdf_file' ---
    
    # 1. Obtener el contenido del PDF y codificarlo en Base64.
    # Asumimos que 'herramientas.upload_a_base64' lee el UploadFile y hace la codificación.
    base64_content = await herramientas.upload_a_base64(pdf_file)
    
    # 2. Obtener el tipo MIME para la API (debe ser 'application/pdf')
    mime_type = pdf_file.content_type
    
    # Validación extra: Asegurar que el tipo MIME es el correcto para el servicio
    if mime_type != "application/pdf":
        # Nota: Idealmente, esta validación ya se hizo en el endpoint, pero es una buena práctica.
        print(f"ERROR INTERNO: Tipo MIME inesperado: {mime_type}")
        raise ValueError("La función espera un Content-Type de application/pdf.")

    print("Mimetype es: ", mime_type)
    
    # 3. Estructura de la petición a Document AI
    processor_json = {
        "rawDocument": {
            # Se envía el tipo MIME correcto ('application/pdf')
            "mimeType": mime_type, 
            # El contenido (el PDF completo) codificado en Base64
            "content": base64_content
        }
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    try: 
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                                            url=ENDPOINT_CSF, 
                                            headers=headers, 
                                            json=processor_json
                                            )
            response.raise_for_status()

    except httpx.HTTPStatusError:
        print("Error 400/4xx/5xx del servidor.")
        print("Mensaje de error de Document AI:", response.text) 
        return {"error": f"Error de Document AI: {response.status_code}", "details": response.json()}
    
    except Exception as e:
        return {"error": f"Error al ejecutar document ai: {e}"}
    
    print("Response:")
    print(response)

    # 4. Procesar el JSON de respuesta
    data_json = response.json() 
    entidades = herramientas.obtener_datos_completos(data_json)
    
    return entidades

async def procesa_cedula(pdf_file: UploadFile):
    """
    Procesa un archivo PDF con la Cédula Profesional usando Google Document AI.
    """

    credentials, _ = default(scopes=SCOPES)
    credentials.refresh(Request())
    access_token = credentials.token
    print("Access token: ", access_token)
    
    
    base64_content = await herramientas.upload_a_base64(pdf_file)
    
    # 2. Obtener el tipo MIME para la API (debe ser 'application/pdf')
    mime_type = pdf_file.content_type
    
    # Validación extra: Asegurar que el tipo MIME es el correcto para el servicio
    if mime_type != "application/pdf":
        # Nota: Idealmente, esta validación ya se hizo en el endpoint, pero es una buena práctica.
        print(f"ERROR INTERNO: Tipo MIME inesperado: {mime_type}")
        raise ValueError("La función espera un Content-Type de application/pdf.")

    print("Mimetype es: ", mime_type)
    
    # 3. Estructura de la petición a Document AI
    processor_json = {
        "rawDocument": {
            # Se envía el tipo MIME correcto ('application/pdf')
            "mimeType": mime_type, 
            # El contenido (el PDF completo) codificado en Base64
            "content": base64_content
        }
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    try: 
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                                            url=ENDPOINT_CEDULA, 
                                            headers=headers, 
                                            json=processor_json
                                            )
            response.raise_for_status()

    except httpx.HTTPStatusError:
        print("Error 400/4xx/5xx del servidor.")
        print("Mensaje de error de Document AI:", response.text) 
        return {"error": f"Error de Document AI: {response.status_code}", "details": response.json()}
    
    except Exception as e:
        return {"error": f"Error al ejecutar document ai: {e}"}
    
    print("Response:")
    print(response)

    # 4. Procesar el JSON de respuesta
    data_json = response.json() 
    entidades = herramientas.obtener_datos_completos(data_json)
    
    return entidades