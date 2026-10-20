import os
import logging
import tempfile
import archivo
import clasificador
import codigos
import funciones
import herramientas
from io import BytesIO
from pathlib import Path
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi import FastAPI, File, Request, UploadFile, HTTPException, status

from dotenv import load_dotenv
load_dotenv()

from logging_config import configurar_logging
configurar_logging()
logger = logging.getLogger(__name__)

TEMP_DIR_ROOT = Path("/home/mbriseno/code/document_ai/temp_files")

app = FastAPI(
    title="RAD",
    description="Reconocimiento avanzado de documentos via computer vision.",
    version="0.0.0"
)

@app.exception_handler(codigos.ErrorAPI)
async def _error_api(request: Request, exc: codigos.ErrorAPI):
    """Las fallas de /procesa_ine/ se reportan con la misma forma que los éxitos."""
    return JSONResponse(
        status_code=exc.desenlace.http,
        content=codigos.cuerpo(exc.desenlace, exc.mensaje),
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
    summary="Cédula Fiscal SAT")
async def procesa_documento_csf(pdf_file: UploadFile = File(...)): 
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
    
    temp_file_path = None
    temp_image_path = None
    
    try:
        TEMP_DIR_ROOT.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=TEMP_DIR_ROOT) as temp_file_obj:
            pdf_contents = await pdf_file.read()
            temp_file_obj.write(pdf_contents)
            temp_file_path = temp_file_obj.name # Ruta única del PDF
        
        # Crea el archivo temporal para la imagen de salida (PNG)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_image_obj:
            temp_image_path = temp_image_obj.name # Ruta única de la imagen

        # Ya no usamos la ruta estática "docto.png"
        herramientas.unir_paginas_pdf_a_una_imagen(
            temp_file_path, 
            ruta_salida=temp_image_path
        )

        if not os.path.exists(temp_image_path) or os.path.getsize(temp_image_path) == 0:
            # print("FATAL: La imagen de salida no existe o está vacía después de la unión.")
            logger.error("FATAL: La imagen de salida no existe o está vacía después de la unión.")
            # Lanza una excepción interna, no de HTTP, para un mejor debug
            raise RuntimeError("Falló al generar la imagen unida del PDF.")

        # print(f"PDF unido y guardado temporalmente como {os.path.basename(temp_image_path)}.")
        logger.info(f"PDF unido y guardado temporalmente como {os.path.basename(temp_image_path)}.")

        return await funciones.procesa_csf(temp_image_path)

    except HTTPException:
        # Dejar pasar los errores con código HTTP explícito (ej. credenciales, Document AI)
        raise

    except Exception as e:
        # print(f"Error procesando el PDF: {e}")
        logger.exception(f"Error procesando el PDF: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error interno al procesar el PDF."
        )
        
    finally:
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
    
    if pdf_file.content_type != "application/pdf":
        # Uso HTTPException 400 Bad Request para indicar un error del cliente
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo no es un PDF. Por favor, suba un archivo con Content-Type: application/pdf."
        )
    
    return await funciones.procesa_cedula(pdf_file)

async def _revisar_y_clasificar_ine(file: UploadFile):
    """Guardias previas a la extracción de la INE.

    Devuelve (contenido, veredicto) si el documento debe procesarse, o
    (None, respuesta) con el desenlace que corta la petición.
    """
    permitidos = funciones.MIME_TYPES_PERMITIDOS_INE
    if not file.content_type or file.content_type not in permitidos:
        raise codigos.ErrorAPI(
            codigos.ARCHIVO_NO_SOPORTADO,
            f"Tipo de archivo no soportado: {file.content_type or 'desconocido'}. "
            f"Permitidos: {', '.join(permitidos)}.",
        )

    # Se lee una sola vez: el UploadFile es un stream y el clasificador y el
    # extractor necesitan los mismos bytes.
    contenido = await file.read()

    # Se revisa el archivo ANTES de mandarlo: distinguir "tiene contraseña" de
    # "está dañado" solo se puede aquí (Document AI mete ambos en el mismo
    # mensaje), y de paso se ahorra una llamada que se cobra por página.
    archivo.revisar(contenido, file.content_type)

    veredicto = await clasificador.clasificar(contenido, file.content_type)
    if clasificador.es_categoria(veredicto, clasificador.CATEGORIA_INE):
        return contenido, veredicto

    if veredicto.get("categoria") == clasificador.CATEGORIA_BLANCO:
        desenlace = codigos.HOJA_EN_BLANCO
    else:
        desenlace = codigos.NO_ES_INE
    logger.info(f"Documento no procesado: {desenlace.estado}")
    return None, codigos.cuerpo(desenlace, clasificacion=veredicto)

@app.post(
        "/procesa_ine/", 
        tags=["Documentos"],       
        summary="INE")
async def procesa_documento_ine(image: UploadFile = File(...)):
    """
    Recibe una INE en PDF o imagen y devuelve sus datos.

    Toda respuesta abre con `codigo`, `estado` y `mensaje`, que dicen qué pasó.
    El status HTTP queda para lo que es de transporte: un documento que resulta
    no ser una INE responde 200, porque la API hizo su trabajo — el resultado de
    ese trabajo fue que el documento no era del tipo esperado.
    """
    contenido, resto = await _revisar_y_clasificar_ine(image)
    if contenido is None:
        return resto
    respuesta = await funciones.procesa_ine(contenido, image.content_type, resto)
    return codigos.cuerpo(codigos.de_extraccion(respuesta), **respuesta)
