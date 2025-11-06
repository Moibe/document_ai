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
async def procesa_documento_csf(pdf_file: UploadFile = File(...)): # Renombre para evitar conflicto
    """
    Recibe un archivo PDF del SAT (CSF), lo convierte a una imagen unificada 
    y lo envía a la función de procesamiento.
    """
    
    # --- Validar el tipo MIME de PDF ---
    if pdf_file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo no es un PDF. Por favor, suba un archivo con Content-Type: application/pdf."
        )
    
    # 1. Declarar las variables de ruta antes del try para el bloque finally
    temp_file_path = None
    temp_image_path = None
    
    try:
        # Crea el archivo temporal para el PDF de entrada
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file_obj:
            pdf_contents = await pdf_file.read()
            temp_file_obj.write(pdf_contents)
            temp_file_path = temp_file_obj.name # Ruta única del PDF
        
        # Crea el archivo temporal para la imagen de salida (PNG)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_image_obj:
            temp_image_path = temp_image_obj.name # Ruta única de la imagen

        # 2. Llamar a tu función con las RUTAS TEMPORALES ÚNICAS
        # Ya no usamos la ruta estática "docto.png"
        herramientas.unir_paginas_pdf_a_una_imagen(
            temp_file_path, 
            ruta_salida=temp_image_path
        )

        if not os.path.exists(temp_image_path) or os.path.getsize(temp_image_path) == 0:
            print("FATAL: La imagen de salida no existe o está vacía después de la unión.")
            # Lanza una excepción interna, no de HTTP, para un mejor debug
            raise RuntimeError("Fallo al generar la imagen unida del PDF.")

        print(f"PDF unido y guardado temporalmente como {os.path.basename(temp_image_path)}.")
        
        # 3. Retornar: Ahora procesa la IMAGEN temporal
        return await funciones.procesa_csf(temp_image_path)
    
    except Exception as e:
        print(f"Error procesando el PDF: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error interno al procesar el PDF."
        )
        
    finally:
        # 4. Limpieza COMPLETA: Asegúrate de borrar AMBOS archivos temporales
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        if temp_image_path and os.path.exists(temp_image_path):
            os.remove(temp_image_path)

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