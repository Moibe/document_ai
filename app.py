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
from typing import Optional
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse, JSONResponse, PlainTextResponse
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
    """Las fallas de los endpoints con sobre se reportan con la misma forma que los éxitos."""
    return JSONResponse(
        status_code=exc.desenlace.http,
        content=codigos.cuerpo(exc.desenlace, exc.mensaje, **exc.extra),
    )

# Endpoints que responden con el sobre `codigo`/`estado`/`mensaje` en TODAS sus
# respuestas, incluidas la petición inválida y el error inesperado. Los endpoints
# anteriores (pasaporte, FM, CSF, cédula profesional) conservan sus respuestas de
# siempre: cambiarles la forma del 422 o del 500 rompería a quien ya las lee.
RUTAS_CON_SOBRE = {"/procesa_ine", "/procesa_ine_atras", "/procesa_ine_ambas", "/procesa_cedula_panama",
                   "/procesa_pasaporte"}


def _usa_sobre(request: Request) -> bool:
    return request.url.path.rstrip("/") in RUTAS_CON_SOBRE


@app.exception_handler(RequestValidationError)
async def _peticion_invalida(request: Request, exc: RequestValidationError):
    """FastAPI valida la petición antes de llegar al endpoint y responde con su propia
    forma. En los endpoints con sobre se reescribe para que TODA respuesta traiga el
    desenlace: un contrato que se cumple casi siempre obliga al cliente a programar dos
    caminos, que es justo lo que `codigo`, `estado` y `mensaje` venían a evitar."""
    if not _usa_sobre(request):
        return await request_validation_exception_handler(request, exc)
    return JSONResponse(
        status_code=codigos.PETICION_INVALIDA.http,
        content=codigos.cuerpo(codigos.PETICION_INVALIDA, errores=jsonable_encoder(exc.errors())),
    )


@app.exception_handler(Exception)
async def _error_inesperado(request: Request, exc: Exception):
    """Una excepción que nadie previó. En los endpoints con sobre responde `500
    ERROR_INTERNO` con la forma de siempre; en los demás, el texto plano que da el
    framework. La excepción se registra aquí y, además, el servidor la vuelve a
    registrar con su traza completa."""
    logger.error(f"Error inesperado en {request.url.path}: {exc!r}", exc_info=exc)
    if not _usa_sobre(request):
        return PlainTextResponse("Internal Server Error", status_code=500)
    return JSONResponse(
        status_code=codigos.ERROR_INTERNO.http,
        content=codigos.cuerpo(codigos.ERROR_INTERNO),
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
async def procesa_documento_pasaporte(image: UploadFile = File(...)):
    """
    Recibe un pasaporte en PDF o imagen y devuelve sus datos.

    Misma forma de respuesta que /procesa_ine/: el desenlace en `codigo`, `estado`
    y `mensaje`, los datos de la hoja, la MRZ (TD3, dos renglones de 44) y el
    contraste entre una y otra en `validacion`. Hoja en blanco responde 221 y un
    documento que no es pasaporte, 225.
    """
    contenido, mime, resto = await _revisar_y_clasificar(
        image, clasificador.CATEGORIA_PASAPORTE, codigos.NO_ES_PASAPORTE)
    if contenido is None:
        return resto
    respuesta = await funciones.procesa_pasaporte(contenido, mime, resto)
    return codigos.cuerpo(codigos.de_extraccion(respuesta), **respuesta)

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
        summary="INE (anverso)")
async def procesa_documento_ine(image: UploadFile = File(...)):
    """
    Recibe el anverso de una INE en PDF o imagen y devuelve sus datos.

    Sin el reverso no hay contra qué contrastar el anverso, así que esa parte de
    la validación queda en `sin_verificar` (responde 212). Para la validación
    completa, usa /procesa_ine_ambas/. Un PDF que ya trae las dos caras sí se
    contrasta aquí. Si el archivo es el reverso, responde 228.

    Toda respuesta abre con `codigo`, `estado` y `mensaje`, que dicen qué pasó.
    El status HTTP queda para lo que es de transporte: un documento que resulta
    no ser una INE responde 200, porque la API hizo su trabajo — el resultado de
    ese trabajo fue que el documento no era del tipo esperado.
    """
    contenido, mime, resto = await _revisar_y_clasificar(
        image, clasificador.CATEGORIA_INE, codigos.NO_ES_INE)
    if contenido is None:
        return resto
    respuesta = await funciones.procesa_ine(contenido, mime, resto)
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
    contrastarla contra el anverso, usa /procesa_ine_ambas/.

    Si el archivo no trae ninguna MRZ (lo más común: mandaron el frente) responde
    227 NO_ES_REVERSO_INE.
    """
    contenido, mime, resto = await _revisar_y_clasificar(
        image, clasificador.CATEGORIA_INE, codigos.NO_ES_INE)
    if contenido is None:
        return resto
    respuesta = await funciones.procesa_ine_atras(contenido, mime, resto)
    return codigos.cuerpo(codigos.de_extraccion(respuesta), **respuesta)


@app.post(
        "/procesa_ine_ambas/",
        tags=["Documentos"],
        summary="INE (anverso y reverso)")
async def procesa_documento_ine_ambas(frente: UploadFile = File(...),
                                      atras: UploadFile = File(...)):
    """
    Recibe el anverso (`frente`) y el reverso (`atras`) de una INE, cada uno en su
    archivo (PDF o imagen), y contrasta uno contra el otro: nombres, nacimiento,
    sexo, vigencia y sección contra la MRZ. Es la validación completa.

    Los dos campos son obligatorios y cada uno tiene su papel: si el anverso es en
    realidad un reverso responde 228, y si el reverso no trae MRZ responde 227. Los
    mensajes dicen cuál de los dos archivos es el problema. Hace dos lecturas de
    Document AI, así que tarda aproximadamente el doble que /procesa_ine/.
    """
    (cont_f, mime_f, resto_f), (cont_a, mime_a, resto_a) = await asyncio.gather(
        _revisar_y_clasificar(frente, clasificador.CATEGORIA_INE, codigos.NO_ES_INE, "frente"),
        _revisar_y_clasificar(atras, clasificador.CATEGORIA_INE, codigos.NO_ES_INE, "atras"),
    )
    if cont_f is None:
        return resto_f
    if cont_a is None:
        return resto_a
    respuesta = await funciones.procesa_ine_ambas(
        (cont_f, mime_f), (cont_a, mime_a), resto_f, resto_a)
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
