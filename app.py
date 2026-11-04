import os
import asyncio
import logging
import tempfile
import archivo
import clasificador
import codigos
import funciones
import herramientas
from io import BytesIO
from pathlib import Path
from typing import Optional, Union
from starlette.datastructures import UploadFile as ArchivoSubido
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

async def _revisar_y_clasificar(file: UploadFile, categoria: str, no_es, campo: Optional[str] = None):
    """Guardias previas a la extracción de un documento: `categoria` es la que debe
    decir el clasificador y `no_es` el desenlace con que se corta si dice otra cosa.
    `campo` es el nombre del archivo en la petición cuando llegan dos, para que los
    mensajes digan de cuál hablan.

    Devuelve (contenido, tipo_mime, veredicto) si el documento debe procesarse, o
    (None, None, respuesta) con el desenlace que corta la petición.
    """
    def decir(mensaje: str) -> str:
        return f"Archivo '{campo}': {mensaje}" if campo else mensaje

    permitidos = funciones.MIME_TYPES_PERMITIDOS
    if not file.content_type or file.content_type not in permitidos:
        raise codigos.ErrorAPI(
            codigos.ARCHIVO_NO_SOPORTADO,
            decir(f"Tipo de archivo no soportado: {file.content_type or 'desconocido'}. "
                  f"Permitidos: {', '.join(permitidos)}."),
        )

    # Se lee una sola vez: el UploadFile es un stream y el clasificador y el
    # extractor necesitan los mismos bytes.
    contenido = await file.read()

    # Se revisa el archivo ANTES de mandarlo: distinguir "tiene contraseña" de
    # "está dañado" solo se puede aquí (Document AI mete ambos en el mismo
    # mensaje), y de paso se ahorra una llamada que se cobra por página.
    #
    # El tipo que cuenta es el que dicen los bytes, no el que declara el cliente:
    # hay clientes que suben un PNG como image/jpeg (la versión anterior de este
    # endpoint mandaba siempre image/jpeg a Document AI y nadie lo notaba). Si el
    # contenido es de un tipo aceptado se usa ese; la discrepancia solo se
    # rechaza cuando el contenido no se reconoce (ver archivo.revisar).
    real = archivo.detectar_tipo(contenido)
    mime = real if real in permitidos else file.content_type
    if mime != file.content_type:
        logger.warning(f"El cliente declaró {file.content_type} pero el contenido es {mime}; se usa {mime}.")
    try:
        archivo.revisar(contenido, mime)
    except codigos.ErrorAPI as e:
        raise codigos.ErrorAPI(e.desenlace, decir(e.mensaje))

    veredicto = await clasificador.clasificar(contenido, mime)
    if clasificador.es_categoria(veredicto, categoria):
        return contenido, mime, veredicto

    if veredicto.get("categoria") == clasificador.CATEGORIA_BLANCO:
        desenlace = codigos.HOJA_EN_BLANCO
    else:
        desenlace = no_es
    logger.info(f"Documento no procesado: {desenlace.estado}")
    return None, None, codigos.cuerpo(desenlace, decir(desenlace.mensaje), clasificacion=veredicto)

@app.post(
        "/procesa_ine/", 
        tags=["Documentos"],       
        summary="INE")
async def procesa_documento_ine(image: UploadFile = File(...),
                                atras: Union[UploadFile, str, None] = File(None)):
    """
    Recibe una INE en PDF o imagen y devuelve sus datos.

    El reverso se puede mandar en el mismo archivo o aparte en el campo opcional
    `atras`. Con `atras`, la MRZ del reverso se contrasta contra el anverso, que
    es la comprobación que caza errores de lectura; sin ella, esa parte de la
    validación queda en `sin_verificar`.

    Toda respuesta abre con `codigo`, `estado` y `mensaje`, que dicen qué pasó.
    El status HTTP queda para lo que es de transporte: un documento que resulta
    no ser una INE responde 200, porque la API hizo su trabajo — el resultado de
    ese trabajo fue que el documento no era del tipo esperado.
    """
    # Un cliente que no tiene reverso a veces manda el campo vacío (como texto, o
    # como archivo sin nombre); es lo mismo que no mandarlo. Ojo: el archivo que
    # llega es un UploadFile de starlette, no el de fastapi (que solo lo hereda),
    # así que `isinstance` con el de fastapi lo descartaba SIEMPRE.
    if not isinstance(atras, ArchivoSubido) or not atras.filename:
        atras = None

    if atras is None:
        contenido, mime, resto = await _revisar_y_clasificar(
            image, clasificador.CATEGORIA_INE, codigos.NO_ES_INE)
        if contenido is None:
            return resto
        respuesta = await funciones.procesa_ine(contenido, mime, resto)
    else:
        (contenido, mime, resto), (cont_atras, mime_atras, resto_atras) = await asyncio.gather(
            _revisar_y_clasificar(image, clasificador.CATEGORIA_INE, codigos.NO_ES_INE, "image"),
            _revisar_y_clasificar(atras, clasificador.CATEGORIA_INE, codigos.NO_ES_INE, "atras"),
        )
        if contenido is None:
            return resto
        if cont_atras is None:
            return resto_atras
        respuesta = await funciones.procesa_ine(
            contenido, mime, resto, atras=(cont_atras, mime_atras))
        respuesta["clasificacion_atras"] = resto_atras
    return codigos.cuerpo(codigos.de_extraccion(respuesta), **respuesta)


@app.post(
        "/procesa_ine_atras/",
        tags=["Documentos"],
        summary="INE (reverso)")
async def procesa_documento_ine_atras(image: UploadFile = File(...)):
    """
    Recibe el reverso de una INE en PDF o imagen y devuelve su MRZ interpretada.

    Misma forma de respuesta que /procesa_ine/, con la sección `atras`. Sola, esta
    cara solo puede comprobar que la MRZ sea íntegra (dígitos de control). Para
    contrastarla contra el anverso hay que mandar los dos archivos a
    /procesa_ine/, con el reverso en el campo `atras`.

    Si el archivo no trae ninguna MRZ (lo más común: mandaron el frente) responde
    227 NO_ES_REVERSO_INE.
    """
    contenido, mime, resto = await _revisar_y_clasificar(
        image, clasificador.CATEGORIA_INE, codigos.NO_ES_INE)
    if contenido is None:
        return resto
    respuesta = await funciones.procesa_ine_atras(contenido, mime, resto)
    if respuesta is None:
        return codigos.cuerpo(codigos.NO_ES_REVERSO_INE, clasificacion=resto)
    return codigos.cuerpo(codigos.de_extraccion(respuesta), **respuesta)


@app.post(
        "/procesa_cedula_panama/",
        tags=["Documentos"],
        summary="Cédula de Panamá")
async def procesa_documento_cedula_panama(image: UploadFile = File(...)):
    """
    Recibe una cédula de identidad panameña en PDF o imagen y devuelve sus datos.

    Misma forma de respuesta que /procesa_ine/: el desenlace en `codigo`, `estado`
    y `mensaje`, los datos del anverso en `frente`, el bloque `validacion` y los
    `avisos`. La cédula no trae MRZ, así que la validación revisa la forma del
    número, la coherencia de las fechas y la vigencia.
    """
    contenido, mime, resto = await _revisar_y_clasificar(
        image, clasificador.CATEGORIA_CEDULA_PANAMA, codigos.NO_ES_CEDULA_PANAMA)
    if contenido is None:
        return resto
    respuesta = await funciones.procesa_cedula_panama(contenido, mime, resto)
    return codigos.cuerpo(codigos.de_extraccion(respuesta), **respuesta)
