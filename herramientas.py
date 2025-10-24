import base64
from fastapi import UploadFile

def ruta_a_base64(ruta_archivo):
  """
  Lee un archivo binario (imagen o PDF) y lo codifica en una cadena Base64.

  Args:
    ruta_archivo (str): La ruta completa del archivo en tu sistema.

  Returns:
    str: La cadena Base64 codificada.
  """
  try:
    # Abre el archivo en modo binario ('rb')
    with open(ruta_archivo, "rb") as archivo:
      # Lee todo el contenido del archivo
      contenido_binario = archivo.read()
      # Codifica el contenido binario a Base64
      base64_bytes = base64.b64encode(contenido_binario)
      # Convierte los bytes Base64 a una cadena de texto y la devuelve
      base64_cadena = base64_bytes.decode('utf-8')
      return base64_cadena
  except FileNotFoundError:
    print(f"Error: El archivo no se encontró en la ruta: {ruta_archivo}")
    return None
  
async def upload_a_base64(archivo_subido: UploadFile) -> str:
    """
    Lee el contenido binario de un objeto UploadFile y lo codifica en Base64.
    """
    # 1. Leer el contenido binario del archivo subido (asíncrono)
    #    IMPORTANTE: .read() lee todo el archivo a la memoria.
    contenido_binario = await archivo_subido.read()
    
    # 2. Codificar el contenido binario a Base64
    base64_bytes = base64.b64encode(contenido_binario)
    
    # 3. Convertir los bytes Base64 a una cadena de texto y devolver
    base64_cadena = base64_bytes.decode('utf-8')
    
    return base64_cadena


ruta_de_mi_id = "ej_pasaporte.jpg"

base64_final = convertir_base64(ruta_de_mi_id)

if base64_final:
  print("Codificación Base64 completada. Longitud:", len(base64_final))
  # Puedes ver las primeras 50 caracteres del resultado:
  print("Inicio de la cadena Base64:", base64_final[:50] + "...") 
  
  # Este es el valor que debes insertar en el JSON del API:
  # "content": base64_final