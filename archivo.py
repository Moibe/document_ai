"""Revisión del archivo antes de mandarlo a Document AI.

Document AI sí detecta un PDF ilegible, pero mete TODAS las causas en un solo
mensaje —medido contra la API real en el proyecto nexus_back:

    "PDF document could not be opened or is corrupted (possible reasons: corrupt
    document structure, damaged cross-reference table, truncated file, or
    unsupported encryption)."

O sea que si solo dependiéramos de Google, a un usuario con un PDF protegido
habría que decirle "puede estar dañado, incompleto o protegido", y que él
adivine cuál de las tres. Mirando los bytes aquí sí se puede distinguir, y de
paso se ahorra la llamada, que se cobra por página.

Esto NO pretende validar un PDF a fondo: es un filtro barato para los casos que
se pueden nombrar con certeza. Lo que se le escape lo sigue cachando Document
AI, y su error se traduce al mismo código.
"""
import re
from typing import Optional

import codigos

# Primeros bytes que identifican cada formato. Un archivo que dice ser PDF pero
# no empieza con %PDF- no es un PDF, diga lo que diga su Content-Type.
FIRMAS = (
    (b"%PDF-", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)

# Un PDF cifrado declara /Encrypt en su trailer, apuntando al diccionario de
# cifrado. Se busca la forma de referencia indirecta ("/Encrypt 12 0 R") y la
# inline ("/Encrypt<<"), que son las que aparecen en un trailer real. Buscar el
# texto "/Encrypt" pelado daría falsos positivos con cualquier PDF que lo
# mencione dentro de un stream de contenido.
ENCRYPT = re.compile(rb"/Encrypt\s*(?:\d+\s+\d+\s+R|<<)")


def detectar_tipo(contenido: bytes) -> Optional[str]:
    """Tipo MIME real según los primeros bytes, o None si no se reconoce."""
    if contenido[:4] == b"RIFF" and contenido[8:12] == b"WEBP":
        return "image/webp"
    for firma, mime in FIRMAS:
        if contenido.startswith(firma):
            return mime
    return None


def pdf_protegido(contenido: bytes) -> bool:
    """Si el PDF trae cifrado declarado (lo que en la práctica es contraseña)."""
    return bool(ENCRYPT.search(contenido))


def pdf_truncado(contenido: bytes) -> bool:
    """
    Si al PDF le falta el final. Un PDF completo termina con '%%EOF' y trae un
    'startxref' que apunta a su tabla de referencias cruzadas; que falten es la
    firma de una descarga a medias o de una transferencia cortada.

    Se busca en la cola y no en todo el archivo porque ahí es donde van, y
    porque así no se confunde con un '%%EOF' de un PDF incrustado.
    """
    cola = contenido[-2048:]
    return b"%%EOF" not in cola or b"startxref" not in cola


def revisar(contenido: bytes, mime_declarado: str) -> None:
    """
    Revisa el archivo y lanza `ErrorAPI` con el desenlace que corresponda.
    No devuelve nada: si regresa, el archivo se ve bien.
    """
    if not contenido:
        raise codigos.ErrorAPI(
            codigos.ARCHIVO_CORRUPTO, "El archivo llegó vacío (0 bytes).")

    real = detectar_tipo(contenido)

    if real is None:
        raise codigos.ErrorAPI(
            codigos.ARCHIVO_CORRUPTO,
            f"El archivo no es un {mime_declarado} válido: no se reconoce su formato. "
            "Puede estar dañado o ser de otro tipo.")

    if real != mime_declarado:
        raise codigos.ErrorAPI(
            codigos.ARCHIVO_CORRUPTO,
            f"El archivo dice ser {mime_declarado} pero su contenido es {real}. "
            "Vuelve a enviarlo declarando el tipo correcto.")

    if real == "application/pdf":
        # El cifrado se revisa ANTES que el truncamiento: un PDF protegido
        # también suele fallar otras revisiones, y decir "tiene contraseña" es
        # mucho más accionable que decir "está dañado".
        if pdf_protegido(contenido):
            raise codigos.ErrorAPI(
                codigos.PDF_PROTEGIDO,
                "El PDF está protegido con contraseña. Quítasela y vuelve a enviarlo.")
        if pdf_truncado(contenido):
            raise codigos.ErrorAPI(
                codigos.ARCHIVO_CORRUPTO,
                "El PDF está incompleto: le falta el final del archivo. "
                "Probablemente se cortó al descargarlo o al subirlo.")
