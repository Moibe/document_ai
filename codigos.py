"""Códigos de desenlace de la API.

Cada respuesta abre con tres campos que dicen qué pasó:

    {"codigo": 210, "estado": "PROCESADO_CON_DISCREPANCIAS", "mensaje": "..."}

  - `codigo`  número estable, para guardar en base de datos y para el `switch`
  - `estado`  nombre estable, para que un log se entienda sin ir a buscar la tabla
  - `mensaje` texto para humanos, para mostrar en pantalla

El que NUNCA cambia de significado es `codigo`, y en segundo lugar `estado`. El
`mensaje` se puede reescribir para que se lea mejor, así que nadie debería
comparar contra su texto.

Por qué el desenlace va en el CUERPO y no solo en el HTTP: el vocabulario de
HTTP es fijo y está pensado para la capa de transporte. No existe —ni va a
existir— un código HTTP que signifique "es una INE pero los apellidos no cuadran
con la MRZ". Un código propio, en cambio, se amplía sin romper a nadie: un
cliente que no conozca uno nuevo cae en su `default`.

SOBRE LA NUMERACIÓN, que es deliberada:

  - En la familia de fallas el `codigo` es IDÉNTICO al status HTTP (400, 422,
    502, 503). Ahí no hay nada que confundir porque significan lo mismo.
  - En la familia 2xx el HTTP siempre es 200, así que el `codigo` es el que
    distingue. Se escogieron números que HTTP NO tiene asignados (210-212,
    220-221) para que ninguno sea un falso amigo de `201 Created` o de
    `202 Accepted`. La única excepción es el 200, que se queda para "todo bien"
    porque es el número que todo mundo lee así.
  - Los `21x` son "sí se extrajo" y los `22x` "se evaluó pero no se extrajo"
    (no era una INE, venía en blanco, el archivo estaba dañado, traía
    contraseña). Todos ellos responden HTTP 200 porque la API SÍ hizo su
    trabajo: evaluar el documento y concluir que no se puede procesar es un
    desenlace legítimo, no una falla de la API.
    Queda hueco para crecer dentro de cada familia sin renumerar nada.
"""
from typing import Any, Dict, NamedTuple, Optional


class Desenlace(NamedTuple):
    codigo: int
    estado: str
    mensaje: str
    http: int = 200


# --- el documento se procesó (HTTP 200) ---

PROCESADO = Desenlace(
    200, "PROCESADO",
    "El documento se procesó y todas las comprobaciones cuadraron.")

PROCESADO_CON_DISCREPANCIAS = Desenlace(
    210, "PROCESADO_CON_DISCREPANCIAS",
    "El documento se procesó, pero hay datos que se contradicen entre sí. "
    "Revisa 'validacion.discrepancias' antes de dar los datos por buenos.")

PROCESADO_CON_AVISOS = Desenlace(
    211, "PROCESADO_CON_AVISOS",
    "El documento se procesó y nada se contradijo, pero hay algo que requiere "
    "revisión humana. Revisa 'avisos'.")

PROCESADO_INCOMPLETO = Desenlace(
    212, "PROCESADO_INCOMPLETO",
    "El documento se procesó y nada se contradijo, pero quedaron comprobaciones "
    "sin poder realizarse. Revisa 'validacion.sin_verificar'.")

# --- se evaluó pero no se procesó (HTTP 200) ---

NO_ES_INE = Desenlace(
    220, "NO_ES_INE",
    "El documento no es una credencial para votar (INE). No se extrajo ningún dato.")

HOJA_EN_BLANCO = Desenlace(
    221, "HOJA_EN_BLANCO",
    "El documento viene en blanco o ilegible: no se leyó ningún texto.")

ARCHIVO_CORRUPTO = Desenlace(
    222, "ARCHIVO_CORRUPTO",
    "El archivo no se pudo abrir: está dañado, incompleto, o no es del tipo que dice ser.")

PDF_PROTEGIDO = Desenlace(
    223, "PDF_PROTEGIDO",
    "El PDF está protegido con contraseña. Quítasela y vuelve a enviarlo.")

NO_ES_PASAPORTE = Desenlace(
    225, "NO_ES_PASAPORTE",
    "El documento no es un pasaporte. No se extrajo ningun dato.")

NO_ES_CEDULA_PANAMA = Desenlace(
    226, "NO_ES_CEDULA_PANAMA",
    "El documento no es una cédula de identidad panameña. No se extrajo ningún dato.")

LIMITE_PAGINAS = Desenlace(
    224, "LIMITE_PAGINAS",
    "El documento tiene más páginas de las que se pueden procesar en una sola "
    "llamada. Envía solo las páginas de la credencial.")

# --- fallas (el codigo coincide con el status HTTP) ---

ARCHIVO_NO_SOPORTADO = Desenlace(
    400, "ARCHIVO_NO_SOPORTADO",
    "El tipo de archivo enviado no se acepta.", 400)

PETICION_INVALIDA = Desenlace(
    422, "PETICION_INVALIDA",
    "La petición no es válida. Se espera multipart/form-data con un campo de archivo.", 422)

ERROR_INTERNO = Desenlace(
    500, "ERROR_INTERNO",
    "Error interno de la API.", 500)

ERROR_DOCUMENT_AI = Desenlace(
    502, "ERROR_DOCUMENT_AI",
    "El servicio de Document AI falló o respondió algo no interpretable.", 502)

CUOTA_EXCEDIDA = Desenlace(
    429, "CUOTA_EXCEDIDA",
    "Se alcanzó el límite de páginas por minuto del servicio de OCR. "
    "Espera un momento y vuelve a intentar.", 429)

SIN_CREDENCIALES = Desenlace(
    503, "SIN_CREDENCIALES",
    "El servidor no pudo obtener las credenciales de Google Cloud.", 503)


class ErrorAPI(Exception):
    """Falla que se le reporta al cliente con su desenlace.

    Existe para que las respuestas de error tengan la misma forma que las de
    éxito: con `HTTPException` el detalle queda anidado y el cliente termina con
    dos formas distintas que parsear.

    `mensaje` sobreescribe el texto genérico del desenlace cuando hay algo
    concreto que decir (por ejemplo, QUÉ tipo de archivo se rechazó).
    """

    def __init__(self, desenlace: Desenlace, mensaje: Optional[str] = None):
        super().__init__(mensaje or desenlace.mensaje)
        self.desenlace = desenlace
        self.mensaje = mensaje or desenlace.mensaje


def de_extraccion(respuesta: Dict[str, Any]) -> Desenlace:
    """Deduce el desenlace a partir de la respuesta ya armada.

    El orden importa: se reporta lo más grave primero. Un documento con
    discrepancias Y avisos sale como PROCESADO_CON_DISCREPANCIAS, porque es lo
    que el consumidor tiene que atender antes.
    """
    validacion = respuesta.get("validacion", {})

    if validacion.get("discrepancias"):
        return PROCESADO_CON_DISCREPANCIAS
    if respuesta.get("avisos"):
        return PROCESADO_CON_AVISOS
    if not validacion.get("completo", True):
        return PROCESADO_INCOMPLETO
    return PROCESADO


def cuerpo(desenlace: Desenlace, mensaje: Optional[str] = None,
           **extra: Any) -> Dict[str, Any]:
    """Arma la respuesta con el desenlace en las primeras tres llaves.

    Van primero para que se vean de inmediato al leer la respuesta o un log, sin
    tener que bajar por todo el JSON.
    """
    return {
        "codigo": desenlace.codigo,
        "estado": desenlace.estado,
        "mensaje": mensaje or desenlace.mensaje,
        **extra,
    }


# Tabla para la documentación y para pruebas: todos los desenlaces posibles.
TODOS = (
    PROCESADO, PROCESADO_CON_DISCREPANCIAS, PROCESADO_CON_AVISOS,
    PROCESADO_INCOMPLETO, NO_ES_INE, HOJA_EN_BLANCO,
    ARCHIVO_CORRUPTO, PDF_PROTEGIDO, LIMITE_PAGINAS, NO_ES_PASAPORTE, NO_ES_CEDULA_PANAMA,
    ARCHIVO_NO_SOPORTADO, PETICION_INVALIDA, CUOTA_EXCEDIDA,
    ERROR_INTERNO, ERROR_DOCUMENT_AI, SIN_CREDENCIALES,
)
