"""Cédula de identidad personal de Panamá: esquema, armado de la respuesta y validación.

Hermano de `ine.py`, con la misma estructura de respuesta (frente / validacion /
avisos) para que quien consuma la API no tenga que aprender una forma distinta
por documento.

La diferencia de fondo es que esta credencial NO trae zona de lectura mecánica
(MRZ), así que no hay contra qué contrastar el anverso, que es lo que hace
valiosa la validación de la INE. Lo que sí se puede revisar es lo que el propio
documento garantiza: la forma del número de cédula, que las fechas existan y
sean coherentes entre sí, y que la credencial siga vigente.

Hay dos formatos impresos y el extractor los lee con el mismo esquema:

  - "Tribunal Electoral": el anterior. Foto grande a la derecha, foto chica y
    firma abajo, vigencia de 10 años.
  - "Documento de Identidad": el nuevo. Foto a la izquierda, fondo celeste y
    rosa, vigencia de 15 años.

Piezas puras (limpieza de texto, acentos) se importan de `ine.py` en vez de
copiarse, para que una corrección valga para los dos documentos.
"""
import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional

from ine import _sin_acentos, limpiar_texto

logger = logging.getLogger(__name__)

DISPLAY_NAME = "unify_cedula_panama"

# Processor creado a mano en Document AI (no por `crear_processor.py`); el script
# solo le sube el esquema. Versión fijada a propósito: sin esto Google puede
# promover otra versión por default sin aviso y cambiar el comportamiento del
# endpoint de un día a otro.
PROCESSOR_ID = "13a487d8b86e7dc2"
VERSION = "pretrained-foundation-model-v1.5-pro-2025-06-20"

CAMPOS_FRENTE = (
    "numero_cedula", "nombres", "apellidos", "nombre_usual",
    "fecha_nacimiento", "lugar_nacimiento", "sexo", "tipo_sangre",
    "fecha_expedicion", "fecha_expiracion",
)

# Lo que toda cédula trae impreso. `nombre_usual` y `tipo_sangre` NO están aquí:
# casi todas las credenciales los traen en blanco, y que falten es lo normal.
CAMPOS_ESPERADOS = (
    "numero_cedula", "nombres", "apellidos", "fecha_nacimiento",
    "lugar_nacimiento", "sexo", "fecha_expedicion", "fecha_expiracion",
)

CAMPOS_FECHA = ("fecha_nacimiento", "fecha_expedicion", "fecha_expiracion")

MESES = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "SET": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}

# Provincia-tomo-asiento: '8-847-1152'. En lugar de la provincia (1 a 13) puede
# ir un prefijo (PE, E, N, AV, PI) y algunos números llevan un segundo prefijo
# ('8-AV-123-4567'). Se acepta la forma sin atar cada prefijo a un significado:
# lo que se busca es cachar un número que el OCR leyó mal, no auditar el registro.
RE_NUMERO = re.compile(r"(?:\d{1,2}|PE|E|N|AV|PI)(?:-(?:AV|PI|NT))?-\d{1,4}-\d{1,6}")


def esquema() -> Dict[str, Any]:
    """DocumentSchema del extractor de cédula panameña.

    Reemplaza al esquema de arranque del processor (solo `fecha_nacimiento` y
    `lugar_nacimiento`, ambos REQUIRED_ONCE). Decisiones:

      - Todo es OPTIONAL_ONCE. Marcar como obligatorio algo que la credencial
        legítimamente puede no traer (`nombre_usual`, `tipo_sangre`, casi siempre
        en blanco) empuja al modelo a inventarlo.
      - Las fechas son `string` y no `datetime`: la credencial las imprime como
        '28-may-1990' o '12-JUN-1962', con mes abreviado en español, y el
        normalizador de Document AI no está pensado para eso. Se leen tal cual y
        se pasan a ISO en `cedula_panama.normalizar_fecha`, donde se puede
        comprobar que la fecha exista.
      - `nombres` y `apellidos` van separados porque así vienen impresos (nombres
        en el primer renglón, apellidos en el segundo). Paterno y materno NO se
        separan: en 'Brown Bravo de Chiari' (casada) no hay forma de saber dónde
        corta, y partirlos a ciegas sería inventar.
    """
    def p(nombre, desc):
        return {"name": nombre, "valueType": "string",
                "occurrenceType": "OPTIONAL_ONCE", "method": "EXTRACT",
                "description": desc}

    return {
        "displayName": "Cedula panameña",
        "description": "Cédula de identidad personal de Panamá, formato Tribunal Electoral y Documento de Identidad.",
        "entityTypes": [{
            "name": "custom_extraction_document_type",
            "baseTypes": ["document"],
            "properties": [
                p("numero_cedula",
                  "Número de cédula impreso bajo la fotografía, con guiones: provincia, tomo y "
                  "asiento, por ejemplo 8-847-1152. Puede empezar con PE, E, N, AV o PI."),
                p("nombres",
                  "Nombres de pila del titular: el primer renglón del nombre, sin los apellidos."),
                p("apellidos",
                  "Apellidos del titular: el segundo renglón del nombre, completo, incluido el "
                  "'de' de las mujeres casadas (por ejemplo 'Brown Bravo de Chiari')."),
                p("nombre_usual",
                  "Lo que sigue a la etiqueta NOMBRE USUAL. Casi siempre está en blanco: "
                  "si no hay nada escrito, no lo devuelvas."),
                p("fecha_nacimiento",
                  "Fecha que sigue a FECHA DE NACIMIENTO, tal como viene impresa, por ejemplo 28-may-1990."),
                p("lugar_nacimiento",
                  "Lo que sigue a LUGAR DE NACIMIENTO, tal como viene impreso: un distrito y una "
                  "provincia ('COLÓN, COLÓN') o solo uno ('BOCAS DEL TORO')."),
                p("sexo", "Letra que sigue a SEXO: M o F."),
                p("tipo_sangre",
                  "Lo que sigue a TIPO DE SANGRE, por ejemplo O+. Casi siempre está en blanco: "
                  "si no hay nada escrito, no lo devuelvas."),
                p("fecha_expedicion",
                  "Fecha que sigue a EXPEDIDA, tal como viene impresa, por ejemplo 24-abr-2024."),
                p("fecha_expiracion",
                  "Fecha que sigue a EXPIRA, tal como viene impresa, por ejemplo 24-abr-2039."),
            ],
        }],
    }


def normalizar_fecha(valor: Any) -> Optional[str]:
    """
    Lleva a ISO ('1990-05-28') la fecha tal como la imprime la cédula: '28-may-1990',
    '12-JUN-1962'. Tolera '/', '.' o espacios en lugar de guiones y mes completo
    ('28-mayo-1990'); acepta también una fecha ya en ISO. Devuelve None si no cuadra
    o si la fecha no existe en el calendario ('31-feb-2020'), en vez de publicar una
    cadena que parece fecha y no lo es.
    """
    if not isinstance(valor, str):
        return None
    v = _sin_acentos(valor.strip().upper())

    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        anio, mes, dia = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"(\d{1,2})\s*[-/. ]\s*([A-Z]{3})[A-Z]*\.?\s*[-/. ]\s*(\d{4})", v)
        if not (m and m.group(2) in MESES):
            return None
        dia, mes, anio = int(m.group(1)), MESES[m.group(2)], int(m.group(3))

    try:
        return date(anio, mes, dia).isoformat()
    except ValueError:
        return None


def _normalizar_numero(valor: str) -> str:
    """'8 - 847 – 1152' -> '8-847-1152'. El OCR mete espacios y guiones largos."""
    return re.sub(r"\s*[-‐‑–—]\s*", "-", valor.strip()).replace(" ", "").upper()


def detectar_formato(texto_ocr: str) -> Optional[str]:
    """Cuál de los dos formatos impresos es, según el encabezado."""
    t = re.sub(r"\s+", " ", _sin_acentos((texto_ocr or "").upper()))
    if "DOCUMENTO DE IDENTIDAD" in t:
        return "documento_identidad"
    if "TRIBUNAL ELECTORAL" in t:
        return "tribunal_electoral"
    return None


def validar(frente: Dict[str, Any], hoy: date) -> Dict[str, Any]:
    """
    Revisa lo que la propia cédula garantiza. Sin MRZ no hay segunda fuente, así
    que `coincidencias` es corta: dice qué reglas cuadraron, no que el dato sea
    verdadero. Para eso está `sin_verificar`, que lista lo que no se pudo revisar.

    Dos booleanos, no uno: 'ok' dice que nada se contradijo, 'completo' dice que
    todo lo que se quería revisar se pudo revisar. Mismo criterio que en la INE.
    """
    coincidencias: List[str] = []
    discrepancias: List[str] = []
    omitidas: List[str] = []

    for campo in CAMPOS_ESPERADOS:
        if not frente.get(campo):
            omitidas.append(f"{campo} (ausente)")

    numero = frente.get("numero_cedula")
    if numero:
        if RE_NUMERO.fullmatch(numero):
            coincidencias.append("numero_cedula (formato)")
        else:
            discrepancias.append(
                f"numero_cedula: '{numero}' no tiene forma de número de cédula "
                f"(provincia-tomo-asiento, como 8-847-1152)")

    sexo = frente.get("sexo")
    if sexo:
        if sexo in ("M", "F"):
            coincidencias.append("sexo (M o F)")
        else:
            discrepancias.append(f"sexo: '{sexo}' no es M ni F")

    nacimiento = _a_fecha(frente.get("fecha_nacimiento"))
    expedicion = _a_fecha(frente.get("fecha_expedicion"))
    expiracion = _a_fecha(frente.get("fecha_expiracion"))

    if nacimiento and nacimiento > hoy:
        discrepancias.append(
            f"fecha_nacimiento: '{nacimiento}' es posterior a hoy ({hoy})")
    if expedicion and expedicion > hoy:
        discrepancias.append(
            f"fecha_expedicion: '{expedicion}' es posterior a hoy ({hoy})")

    if nacimiento and expedicion:
        if expedicion > nacimiento:
            coincidencias.append("fechas (nacimiento anterior a expedición)")
        else:
            discrepancias.append(
                f"fechas: la expedición '{expedicion}' no es posterior al nacimiento '{nacimiento}'")
    if expedicion and expiracion:
        if expiracion > expedicion:
            coincidencias.append("fechas (expedición anterior a expiración)")
        else:
            discrepancias.append(
                f"fechas: la expiración '{expiracion}' no es posterior a la expedición '{expedicion}'")

    return {
        "ok": not discrepancias,
        "completo": not omitidas,
        "coincidencias": coincidencias,
        "discrepancias": discrepancias,
        "sin_verificar": omitidas,
    }


def _a_fecha(iso: Any) -> Optional[date]:
    try:
        return date.fromisoformat(iso) if isinstance(iso, str) else None
    except ValueError:
        return None


def armar_respuesta(entidades: Dict[str, Any],
                    repetidos: Optional[Dict[str, int]] = None,
                    texto_ocr: str = "",
                    hoy: Optional[date] = None) -> Dict[str, Any]:
    """
    Convierte el diccionario plano de entidades de Document AI en la respuesta de
    /procesa_cedula_panama: la sección `frente`, el bloque de validación y los
    avisos. `hoy` solo existe para poder probar la vigencia con una fecha fija.
    """
    hoy = hoy or date.today()
    avisos: List[str] = []

    frente: Dict[str, Any] = {}
    for campo in CAMPOS_FRENTE:
        if campo in entidades:
            frente[campo] = limpiar_texto(entidades[campo])

    for campo in CAMPOS_FECHA:
        if campo in frente:
            cruda = frente[campo]
            frente[campo] = normalizar_fecha(cruda)
            if frente[campo] is None:
                avisos.append(f"frente.{campo} ilegible: se leyó '{cruda}'")
                frente.pop(campo)

    if isinstance(frente.get("numero_cedula"), str):
        frente["numero_cedula"] = _normalizar_numero(frente["numero_cedula"])
    if isinstance(frente.get("sexo"), str):
        frente["sexo"] = frente["sexo"].strip().upper()
    if isinstance(frente.get("tipo_sangre"), str):
        # Medido con las muestras: en una credencial con el campo en blanco el
        # modelo devolvió 'A', que no es un tipo de sangre (falta el Rh). Un valor
        # sin la forma de un tipo de sangre se descarta y se avisa, en vez de
        # publicar como dato algo que el modelo se sacó de otro lado.
        sangre = re.sub(r"\s+", "", frente["tipo_sangre"]).upper()
        if re.fullmatch(r"(?:A|B|AB|O)[+-]", sangre):
            frente["tipo_sangre"] = sangre
        else:
            avisos.append(
                f"frente.tipo_sangre='{frente['tipo_sangre']}' no tiene forma de tipo de sangre: se descartó")
            frente.pop("tipo_sangre")

    # Sin esto el consumidor no distingue "el papel no trae esto" de "no se leyó".
    frente["formato_credencial"] = detectar_formato(texto_ocr)

    # Un PDF con varios documentos produce entidades repetidas: se conserva la
    # primera y se avisa, en vez de mezclar dos identidades en silencio.
    for tipo, veces in (repetidos or {}).items():
        avisos.append(
            f"el PDF contiene {veces} bloques '{tipo}': se conservó el primero, "
            f"revisa que el documento no traiga más de una identidad"
        )

    # Un valor que no aparece literal en el texto leído es un valor que el modelo
    # compuso, no que extrajo. Se compara sin espacios ni mayúsculas: el OCR parte
    # los renglones donde quiere.
    if texto_ocr:
        plano = re.sub(r"\s+", "", texto_ocr).upper()
        for campo, valor in frente.items():
            # Campos que calcula esta función, no que se leyeron del documento
            if campo == "formato_credencial" or campo in CAMPOS_FECHA:
                continue
            if isinstance(valor, str) and len(valor) > 3 \
                    and re.sub(r"\s+", "", valor).upper() not in plano:
                avisos.append(f"frente.{campo}='{valor}' no aparece literal en el texto leído")

    # Una cédula vencida es un hecho del documento, no una falla de lectura, pero
    # quien la reciba tiene que enterarse: se reporta como aviso, no como discrepancia.
    vence = _a_fecha(frente.get("fecha_expiracion"))
    if vence and vence < hoy:
        avisos.append(f"la cédula venció el {vence.isoformat()}")

    return {
        "frente": frente,
        "validacion": validar(frente, hoy),
        "avisos": avisos,
    }
