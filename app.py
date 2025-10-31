from fastapi import FastAPI, Form, Request
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from io import BytesIO
import funciones

app = FastAPI()

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
        tags=["documentos"],       
        summary="Pasaportes")
async def procesa_documento(image: UploadFile = File(...)):
    if not image.content_type.startswith("image/"):
        return {"error": "El archivo no es una imagen"}
    return await funciones.procesa_pasaporte(image)

@app.post(
        "/procesa_fm/", 
        tags=["documentos"],       
        summary="Formas Migratorias")
async def procesa_documento(image: UploadFile = File(...)):
    if not image.content_type.startswith("image/"):
        return {"error": "El archivo no es una imagen"}
    return await funciones.procesa_fm(image)