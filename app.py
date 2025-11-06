from fastapi import FastAPI, File, UploadFile, HTTPException, status
from fastapi.responses import StreamingResponse, JSONResponse
from io import BytesIO
import funciones
import tempfile
import herramientas
import os

from dotenv import load_dotenv
load_dotenv()


app = FastAPI(
    title="RAD",
    description="Reconocimiento avanzado de documentos via computer vision.",
    version="0.0.0"
)

# Nuevo endpoint para Health Check
@app.get("/health",
         tags=["Health Check"],
         description="Verifica el estado de salud de la API.",
         summary="Health Check"
         )
async def health_check():
    """
    Este endpoint devuelve una respuesta 200 OK para indicar que la API está funcionando.
    """
    return JSONResponse(content={"status": "ok"}, status_code=200)

@app.post("/echo-image/",
          tags=["Health Check"],
          description="Test endpoint que recibe y regresa la misma imagen, para probar envío, recepción y problemas con api o red.",
          summary="Summary"
          )
async def echo_image(image: UploadFile = File(...)):
    if not image.content_type.startswith("image/"):
        return {"error": "El archivo no es una imagen"}
    contents = await image.read()
    return StreamingResponse(BytesIO(contents), media_type=image.content_type)

@app.post(
        "/procesa_pasaporte/", 
        tags=["Documentos"],       
        summary="Pasaportes")
async def procesa_documento(image: UploadFile = File(...)):
    if not image.content_type.startswith("image/"):
        return {"error": "El archivo no es una imagen"}
    return await funciones.procesa_pasaporte(image)

@app.post(
        "/procesa_fm/", 
        tags=["Documentos"],       
        summary="Formas Migratorias")
async def procesa_documento(image: UploadFile = File(...)):
    if not image.content_type.startswith("image/"):
        return {"error": "El archivo no es una imagen"}
    return await funciones.procesa_fm(image)

@app.post(
        "/procesa_csf/", 
        tags=["Documentos"],       
        summary="SAT CSF")
async def procesa_documento(pdf_file: UploadFile = File(...)):
    """
    Recibe un archivo PDF del SAT (CSF) y lo envía a la función de procesamiento.
    """
    
    # --- CAMBIO CLAVE 1: Validar el tipo MIME de PDF ---
    if pdf_file.content_type != "application/pdf":
        # Usamos HTTPException 400 Bad Request para indicar un error del cliente
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo no es un PDF. Por favor, suba un archivo con Content-Type: application/pdf."
        )
    
    # 1. Crear un archivo temporal
    # Usamos tempfile.NamedTemporaryFile para obtener una ruta de archivo única.
    # Usamos 'delete=False' para que no se borre inmediatamente al cerrarlo.
    temp_file = None
    try:
        # Crea el archivo temporal con extensión .pdf
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file_obj:
            # Lee el contenido del UploadFile de forma asíncrona
            pdf_contents = await pdf_file.read()
            
            # Escribe el contenido en el archivo temporal
            temp_file_obj.write(pdf_contents)
            
            # Obtiene la ruta del archivo temporal
            temp_file_path = temp_file_obj.name

        # 2. Llamar a tu función con la RUTA DEL ARCHIVO
        # La función espera la ruta (str), no el objeto UploadFile.
        ruta_imagen_salida = "docto.png"
        
        # Llama a tu función con la ruta del archivo temporal
        # Nota: La función unir_paginas_pdf_a_una_imagen debe ser síncrona,
        # si es pesada, deberías ejecutarla en un thread pool (run_in_threadpool).
        herramientas.unir_paginas_pdf_a_una_imagen(
            temp_file_path, 
            ruta_salida=ruta_imagen_salida
        )

        print(f"PDF unido y guardado como {ruta_imagen_salida} para su posterior procesamiento.")
    
        return await funciones.procesa_csf(ruta_imagen_salida)
    
    except Exception as e:
        print(f"Error procesando el PDF: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error interno al procesar el PDF."
        )
    finally:
        # 4. Limpieza: Asegúrate de borrar el archivo temporal
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)

@app.post(
        "/procesa_cedula/", 
        tags=["Documentos"],       
        summary="Cédula Profesional")
async def procesa_documento(pdf_file: UploadFile = File(...)):
    """
    Recibe un archivo PDF de la Cédula Profesional y lo envía a la función de procesamiento.
    """
    
    # --- CAMBIO CLAVE 1: Validar el tipo MIME de PDF ---
    if pdf_file.content_type != "application/pdf":
        # Usamos HTTPException 400 Bad Request para indicar un error del cliente
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo no es un PDF. Por favor, suba un archivo con Content-Type: application/pdf."
        )
    
    return await funciones.procesa_cedula(pdf_file)