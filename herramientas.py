import base64
from fastapi import UploadFile
import json
import fitz
from PIL import Image
import io
import json
from typing import Dict, Any
from typing import Dict, Any, List, Union
import time

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

    print("Retornando cadena:")
    print("Inicio de la cadena Base64:", base64_cadena[:50] + "...") 
    
    return base64_cadena


# Asumiendo que 'data_json' es el diccionario Python que recibí de response.json()
# Ejemplo: data_json = response.json()

def imprimir_entidades(data_json):
    """Extrae e imprime la sección 'entities' del JSON de Document AI."""
    
    # La información estructurada del documento se encuentra en data_json['document']['entities']
    
    try:
        # Navegar al nodo 'document' y luego a 'entities'
        entidades = data_json['document']['entities']
        
        
        print("--- Entidades Extraídas del Documento ---")
        # Usamos json.dumps(..., indent=2) para un formato limpio y legible
        print(json.dumps(entidades, indent=4))
        print("----------------------------------------")
        
        return entidades
        
    except KeyError:
        # Manejo de error si la estructura no es la esperada (por ejemplo, si 'document' no existe)
        print("Error: La respuesta de Document AI no contiene el nodo esperado ['document']['entities'].")
        return None






# --- FUNCIÓN PRINCIPAL REVISADA ---
def obtener_todas_las_entidades(data_json: Dict[str, Any]) -> Dict[str, str]:
    datos_resumidos = {}
    
    try:
        entidades_raiz = data_json['document']['entities']
        # Llamar a la función recursiva para iniciar el aplanamiento
        extraer_entidades_recursivas(entidades_raiz, datos_resumidos)
        
    except KeyError:
        # Manejar error si la estructura JSON no es la esperada
        return {}

    return datos_resumidos

def extraer_entidades_recursivamente(entidades: List[Dict[str, Any]], datos_planos: Dict[str, str] = None) -> Dict[str, str]:
    """
    Función recursiva para extraer todas las entidades y sub-entidades anidadas,
    creando un diccionario plano de tipo: valor.
    """
    if datos_planos is None:
        datos_planos = {}

    for entidad in entidades:
        nombre_campo = entidad.get('type')
        valor_campo = entidad.get('mentionText')
        
        # 1. Procesar valor principal si existe
        if nombre_campo and valor_campo:
            # Usamos el nombre del campo para la clave (ej. 'PASSPORT_NUMBER')
            datos_planos[nombre_campo] = valor_campo
        
        # 2. Búsqueda en propiedades (sub-entidades anidadas)
        # Las entidades anidadas suelen estar en la lista 'properties'
        if 'properties' in entidad:
            # Llamada recursiva a la función para procesar las sub-entidades
            extraer_entidades_recursivamente(entidad['properties'], datos_planos)
            
        # 3. Manejar el caso de las entidades que no tienen mentionText (ej. solo sub-campos)
        # A veces, la entidad padre no tiene texto, pero sus sub-entidades sí
        elif nombre_campo and not valor_campo and 'properties' in entidad:
            extraer_entidades_recursivamente(entidad['properties'], datos_planos)

    return datos_planos

def extraer_entidades_recursivas(entidades: List[Dict[str, Any]], datos_resumidos: Dict[str, str]):
    """
    Función recursiva para extraer todas las entidades, incluidas las sub-entidades (propiedades),
    y aplanarlas en un único diccionario.
    """

    if datos_planos is None:
        datos_planos = {}


    for entidad in entidades:
        nombre_campo = entidad.get('type')
        valor_campo = entidad.get('mentionText')

        if nombre_campo and valor_campo:
            # 1. Guardar la entidad (Padre o Hijo)
            datos_resumidos[nombre_campo] = valor_campo
        
        # 2. Verificar y procesar Sub-entidades
        if 'properties' in entidad and isinstance(entidad['properties'], list):
            # Llama recursivamente a esta misma función para procesar los hijos
            extraer_entidades_recursivas(entidad['properties'], datos_resumidos)

    return datos_planos

def obtener_datos_completos(data_json: Dict[str, Any]) -> Dict[str, str]:
    """
    Función de entrada que toma la respuesta completa de Document AI y 
    devuelve un diccionario plano con todas las entidades.
    """

    datos_resumidos = {}

    try:
        # 1. Acceder a la lista de entidades principales
        entidades_raiz = data_json['document']['entities']
        
        # 2. Llamar a la función recursiva
        datos_finales = extraer_entidades_recursivamente(entidades_raiz)
        #datos_finales = extraer_entidades_recursivas(entidades_raiz, datos_resumidos)
        
        # 3. Imprimir el resultado completo
        print("--- Datos de Pasaporte Completos (Recursivo) ---")
        print(json.dumps(datos_finales, indent=2))
        print("------------------------------------------------")
        
        return datos_finales
        
    except KeyError as e:
        print(f"Error: No se pudo encontrar el nodo 'document' o 'entities'. Error: {e}")
        return {}

def inspeccionar_respuesta_sin_base64(data_json: Dict[str, Any]):
    """
    Imprime el contenido completo del nodo 'document', excluyendo la cadena Base64
    para que sea legible en la consola.
    """
    
    if 'document' not in data_json:
        print("Error: El JSON no contiene la clave 'document'.")
        return

    # Creamos una copia del diccionario para poder modificarlo
    document_copy = data_json['document'].copy()
    
    # 1. Eliminar la cadena Base64 para que la salida sea legible
    if 'text' in document_copy:
        # El campo 'text' contiene todo el texto del OCR y puede ser muy largo
        document_copy['text_summary'] = f"Texto completo disponible (Longitud: {len(document_copy['text'])} caracteres)"
        del document_copy['text']
        
    if 'content' in document_copy:
        # Esto debería ser solo un sanity check, ya que 'content' rara vez está aquí
        document_copy['content_status'] = "Cadena Base64 omitida para legibilidad."
        del document_copy['content']

    # 2. Imprimir el JSON restante con formato
    print("\n--- Estructura del Documento (Excluyendo Base64) ---")
    print(json.dumps(document_copy, indent=2))
    print("----------------------------------------------------\n")



def imprimir_claves_raiz(data_json: Dict[str, Any]):
    """Imprime todas las claves del diccionario de respuesta principal."""
    
    print("\n--- CLAVES PRINCIPALES DEL JSON RECIBIDO ---")
    if isinstance(data_json, dict):
        for key in data_json.keys():
            # Muestra el tipo de valor para ver si es un diccionario o una lista
            value_type = type(data_json[key]).__name__
            print(f"Clave: '{key}' | Tipo: {value_type}")
    else:
        print(f"Error: La respuesta no es un diccionario JSON. Tipo recibido: {type(data_json)}")
    print("-------------------------------------------\n")

from typing import Dict, Any

def mapear_estructura_json(data: Any, nivel=0) -> Dict[str, Any]:
    """
    Genera un mapa recursivo de la estructura JSON.
    Ignora las claves con cadenas muy largas (como OCR o Base64).
    """
    indentacion = "  " * nivel
    
    # Si es un diccionario
    if isinstance(data, dict):
        esquema = {}
        for clave, valor in data.items():
            
            # Condición para omitir valores de cadena de texto largos (OCR, Base64)
            if isinstance(valor, str) and len(valor) > 100:
                esquema[clave] = f"<{type(valor).__name__} (OMITIDO, longitud: {len(valor)})>"
            
            # Si el valor es simple (str/int/bool), lo guardamos como su tipo
            elif not isinstance(valor, (dict, list)):
                esquema[clave] = f"<{type(valor).__name__}>"
            
            # Si es un diccionario o lista, se llama a la función recursivamente
            else:
                esquema[clave] = mapear_estructura_json(valor, nivel + 1)
        return esquema

    # Si es una lista
    elif isinstance(data, list) and data:
        # Solo inspeccionamos el primer elemento para representar el tipo de la lista
        tipo_elemento = type(data[0]).__name__
        
        # Si la lista contiene diccionarios o listas, llamamos recursivamente al primer elemento
        if isinstance(data[0], (dict, list)):
            return [mapear_estructura_json(data[0], nivel + 1)]
        else:
            return [f"<{tipo_elemento}>"]
            
    # Caso de listas vacías o valores simples no cubiertos arriba
    elif isinstance(data, list):
        return []
        
    else:
        return f"<{type(data).__name__}>"


def generar_esquema_document_ai(data_json: Dict[str, Any]) -> None:
    """
    Función de inicio que usa el mapa recursivo en el objeto 'document'.
    """
    if 'document' in data_json:
        esquema_documento = mapear_estructura_json(data_json['document'])
        
        print("\n--- MAPA ESTRUCTURAL DEL OBJETO 'document' ---")
        print(json.dumps(esquema_documento, indent=2))
        print("---------------------------------------------\n")
    else:
        print("Error: La clave 'document' no se encontró en la respuesta JSON.")

import json
from typing import Dict, Any, List

def inspeccionar_estructura_limpia(data_json: Dict[str, Any]):
    """
    Imprime el contenido completo del nodo 'document', excluyendo la cadena Base64
    de la imagen para que sea legible en la consola.
    """
    
    if 'document' not in data_json:
        print("Error: El JSON no contiene la clave 'document'.")
        return

    # 1. Crear una copia profunda del diccionario 'document' para modificarlo sin afectar el original
    # Usamos json.loads(json.dumps()) para hacer una copia simple y profunda.
    document_copy = json.loads(json.dumps(data_json['document']))
    
    # 2. Eliminar la cadena de texto larga del OCR ('text')
    if 'text' in document_copy:
        ocr_length = len(document_copy['text'])
        document_copy['text_summary'] = f"OCR Text (OMITIDO, longitud: {ocr_length})"
        del document_copy['text']
    
    # 3. Navegar a la sección 'pages' para eliminar la imagen Base64
    if 'pages' in document_copy and isinstance(document_copy['pages'], list):
        
        # Iteramos sobre todas las páginas que pueda tener el documento
        for page in document_copy['pages']:
            
            # El path que encontraste: pages[i]['image']['content']
            if 'image' in page and 'content' in page['image']:
                image_content = page['image']['content']
                image_length = len(image_content)
                
                # Reemplazamos el 'content' por un indicador de que se eliminó
                page['image']['content'] = f"<str (OMITIDO, longitud: {image_length})>"
                
                # Si quieres, también puedes eliminar completamente la clave 'image':
                # del page['image'] 
    
    # 4. Imprimir el JSON restante con formato
    print("\n--- ESTRUCTURA DEL DOCUMENTO (LIMPIA PARA INSPECCIÓN) ---")
    print(json.dumps(document_copy, indent=2))
    print("----------------------------------------------------------\n")

def obtener_estructura_limpia_para_fastapi(data_json: Dict[str, Any]) -> Dict[str, Any] | None:
    """
    Retorna el contenido completo del nodo 'document', excluyendo la cadena Base64
    de la imagen y el OCR para que sea seguro y legible al retornar en FastAPI.
    """
    
    if 'document' not in data_json:
        # En FastAPI, podríamos levantar una excepción HTTPException aquí
        return {"error": "El JSON de Document AI no contiene la clave 'document'."}

    # 1. Crear una copia profunda del diccionario 'document' para modificarlo sin afectar el original
    # Usamos json.loads(json.dumps()) para hacer una copia simple y profunda.
    document_copy = json.loads(json.dumps(data_json['document']))
    
    # 2. Eliminar la cadena de texto larga del OCR ('text')
    # if 'text' in document_copy:
    #     ocr_length = len(document_copy['text'])
    #     document_copy['text_summary'] = f"OCR Text (OMITIDO, longitud: {ocr_length})"
    #     del document_copy['text']
    
    # 3. Navegar a la sección 'pages' para eliminar la imagen Base64
    if 'pages' in document_copy and isinstance(document_copy['pages'], list):
        
        # Iteramos sobre todas las páginas que pueda tener el documento
        for page in document_copy['pages']:
            
            # El path: pages[i]['image']['content']
            if 'image' in page and 'content' in page['image']:
                image_content = page['image']['content']
                image_length = len(image_content)
                
                # Reemplazamos el 'content' por un indicador de que se eliminó
                page['image']['content'] = f"<str (OMITIDO, longitud: {image_length})>"
    
    # 4. DEVOLVER EL DICCIONARIO LIMPIO
    return document_copy

def unir_paginas_pdf_a_una_imagen(ruta_pdf, ruta_salida="pdf_unido.png", resolucion_dpi=150):
    """
    Convierte todas las páginas de un PDF y las une verticalmente en una sola imagen.
    """
    try:
        documento = fitz.open(ruta_pdf)
        imagenes_pil = []
        alto_total = 0
        ancho_maximo = 0

        # Calcular el factor de zoom a partir de la resolución DPI
        factor_zoom = resolucion_dpi / 72.0
        matriz = fitz.Matrix(factor_zoom, factor_zoom)
        
        # 1. Renderizar cada página y guardarla temporalmente como objeto PIL Image
        print(f"Renderizando {documento.page_count} páginas...")
        for num_pagina in range(documento.page_count):
            pagina = documento.load_page(num_pagina)
            pix = pagina.get_pixmap(matrix=matriz)
            
            # Convertir el pixmap de PyMuPDF a un objeto Image de Pillow
            img_data = pix.tobytes("ppm")
            imagen_pagina = Image.open(io.BytesIO(img_data))
            
            imagenes_pil.append(imagen_pagina)
            
            # Calcular las dimensiones totales
            alto_total += imagen_pagina.height
            ancho_maximo = max(ancho_maximo, imagen_pagina.width)

        documento.close()
        
        # 2. Crear una nueva imagen vacía (lienzo) con las dimensiones totales
        imagen_final = Image.new('RGB', (ancho_maximo, alto_total), 'white')
        
        # 3. Pegar las imágenes de las páginas en el lienzo
        posicion_y = 0
        for imagen in imagenes_pil:
            # Pegar la imagen de la página en la posición Y actual
            imagen_final.paste(imagen, (0, posicion_y))
            # Actualizar la posición Y para la siguiente página
            posicion_y += imagen.height
        
        # 4. Guardar la imagen final
        imagen_final.save(ruta_salida)
        print(f"\n✅ Documento unido y guardado en: {ruta_salida} (Resolución: {resolucion_dpi} DPI)")

    except FileNotFoundError:
        print(f"❌ Error: El archivo PDF '{ruta_pdf}' no fue encontrado.")
    except Exception as e:
        print(f"❌ Ocurrió un error inesperado: {e}")

import base64
import os

def archivo_local_a_base64(ruta_archivo: str) -> str:
    """
    Lee el contenido binario de un archivo local y lo codifica en Base64.
    """
    try:
        # 1. Leer el contenido binario del archivo local
        # Usamos 'rb' (read binary) para leer los bytes del archivo de imagen
        with open(ruta_archivo, "rb") as archivo_local:
            contenido_binario = archivo_local.read()
            
        # 2. Codificar el contenido binario a Base64
        base64_bytes = base64.b64encode(contenido_binario)
        
        # 3. Convertir los bytes Base64 a una cadena de texto y devolver
        base64_cadena = base64_bytes.decode('utf-8')
        
        print(f"Archivo {os.path.basename(ruta_archivo)} codificado con éxito.")
        return base64_cadena
        
    except FileNotFoundError:
        print(f"Error: El archivo '{ruta_archivo}' no fue encontrado.")
        return "" # O lanza una excepción, como vimos antes