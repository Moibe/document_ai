"""
Armado de la respuesta de /ine: agrupa las entidades planas que devuelve
Document AI en las dos caras del documento (frente, atras),
normaliza artefactos de OCR y valida el anverso contra la MRZ del reverso.
"""
import re
import logging
import unicodedata
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Qué campo del esquema va en qué sección de la respuesta
CAMPOS_FRENTE = (
    "apellido_paterno", "apellido_materno", "nombre", "domicilio",
    "clave_elector", "curp", "fecha_nacimiento", "sexo", "anio_registro",
    "seccion", "estado_clave", "municipio_clave", "localidad_clave",
    "anio_emision", "numero_emision", "vigencia", "pais",
)

# Campos que solo existen en el renglón de claves de las credenciales anteriores
# (ESTADO 09 MUNICIPIO 012 LOCALIDAD 0001 EMISIÓN 2015). Las credenciales nuevas
# no lo imprimen, así que su ausencia es normal y no una falla de lectura.
CAMPOS_SOLO_FORMATO_ANTERIOR = (
    "estado_clave", "municipio_clave", "localidad_clave", "anio_emision",
)

# El renglón de claves del formato anterior, que es lo que distingue un formato
# del otro de manera verificable en el propio texto del documento.
RENGLON_CLAVES = re.compile(
    r"ESTADO\s+\d{1,2}\b|MUNICIPIO\s+\d{1,3}\b|LOCALIDAD\s+\d{1,4}\b", re.IGNORECASE)
VOCALES = set("AEIOU")

# El OCR sustituye letras latinas por homóglifos de otros alfabetos: se ven igual
# en pantalla pero no comparan igual, así que 'ZAPATA' escrito con zeta y alfa
# griegas nunca empataría contra una base de datos.
HOMOGLIFOS = str.maketrans({
    # griego
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    "α": "a", "β": "B", "ε": "e", "ι": "i", "ο": "o", "ρ": "p", "τ": "t",
    # cirílico
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
})

# Basura que el OCR pega al inicio de un campo al arrastrar la línea anterior
BASURA_INICIAL = " -.:,;|/\\"
# Al final NO se incluye el punto: distinguir 'SON.' (sobra) de 'D.F.' (no sobra)
# es trabajo de _limpiar_punto_final, que sí mira el resto de la cadena.
BASURA_FINAL = " -:,;|/\\"

def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")


def limpiar_texto(valor: Any) -> Any:
    """
    Normaliza homóglifos, colapsa los saltos de línea que el modelo arrastra
    cuando toma un valor de varios renglones, y quita la basura que el OCR deja
    pegada a los bordes.
    """
    if not isinstance(valor, str):
        return valor
    limpio = " ".join(valor.translate(HOMOGLIFOS).split())
    return limpio.lstrip(BASURA_INICIAL).rstrip(BASURA_FINAL).strip()


def _limpiar_punto_final(valor: str) -> str:
    """
    Quita el punto final de abreviaturas simples ('SON.' -> 'SON') pero respeta
    las que llevan puntos internos ('D.F.' se queda igual), donde el punto final
    es parte de la abreviatura y no basura del renglón del domicilio.
    """
    if valor.endswith(".") and valor.count(".") == 1:
        return valor[:-1].strip()
    return valor.strip()


def _anio_completo(yy: int, es_vigencia: bool) -> int:
    """
    YY de la MRZ a año completo. Una vigencia nunca es del siglo pasado; una
    fecha de nacimiento nunca es futura (esto último evita que el mismo documento
    se interprete distinto conforme pasan los años).
    """
    if es_vigencia:
        return 2000 + yy
    return 2000 + yy if 2000 + yy <= date.today().year else 1900 + yy


def _fecha_mrz(seis: str, es_vigencia: bool = False) -> Optional[str]:
    if not re.fullmatch(r"\d{6}", seis or ""):
        return None
    yy, mm, dd = int(seis[:2]), int(seis[2:4]), int(seis[4:6])
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return None
    return f"{_anio_completo(yy, es_vigencia):04d}-{mm:02d}-{dd:02d}"


def anio_vigencia(valor: Any) -> Optional[str]:
    """
    Año en que vence la credencial. El anverso imprime un rango ('2025-2035') y
    lo que vale es el ÚLTIMO año, no el primero: tomar los primeros 4 caracteres
    devolvía el año de emisión y hacía fallar la comparación contra la MRZ.
    """
    anios = re.findall(r"(?:19|20)\d{2}", str(valor or ""))
    return anios[-1] if anios else None


# --------------------------------------------------------------------------
# MRZ (ICAO 9303, formato TD1: 3 renglones de 30 caracteres)
# --------------------------------------------------------------------------

def _valor_mrz(caracter: str) -> int:
    if caracter.isdigit():
        return int(caracter)
    if "A" <= caracter <= "Z":
        return ord(caracter) - 55
    return 0


def digito_control(cadena: str) -> int:
    """Dígito verificador ICAO: pesos 7-3-1 cíclicos, módulo 10."""
    pesos = (7, 3, 1)
    return sum(_valor_mrz(c) * pesos[i % 3] for i, c in enumerate(cadena)) % 10


def _verificar_digitos(l1: str, l2: str) -> Dict[str, bool]:
    """Los cuatro dígitos de control del TD1. Detectan corrupción del OCR."""
    compuesto = l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]
    return {
        # El dígito de la posición 15 valida el CIC (posiciones 6-14).
        "cic": digito_control(l1[5:14]) == _valor_mrz(l1[14]),
        "fecha_nacimiento": digito_control(l2[0:6]) == _valor_mrz(l2[6]),
        "vigencia": digito_control(l2[8:14]) == _valor_mrz(l2[14]),
        "compuesto": digito_control(compuesto) == _valor_mrz(l2[29]),
    }


# Patrones que identifican cada renglón del TD1 por su estructura, no por su
# posición. El 2 es el más reconocible: fecha, verificador, sexo, fecha,
# verificador y nacionalidad, todo en posiciones fijas.
# El encabezado exige su forma COMPLETA —tipo, país, 9 caracteres de documento y
# el verificador— y no solo las primeras letras. Con un patrón corto, en un
# documento real se enganchó con 'IDAN<MEXICO', que es como el OCR leyó el
# escudo de "ESTADOS UNIDOS MEXICANOS" impreso arriba de la credencial.
RE_MRZ_1 = re.compile(r"^I[D<][A-Z<]{3}[A-Z0-9<]{9}\d")
RE_MRZ_2 = re.compile(r"^\d{6}\d[MFH<]\d{6}\d[A-Z<]{3}")


def _normalizar_renglon(linea: str) -> str:
    """Todo lo que no sea letra o dígito se vuelve relleno.

    El OCR lee el '<' de la MRZ como espacio, guion, o glifos de otros alfabetos
    —se han visto katakana (く, へ) y comillas angulares—, así que en vez de ir
    coleccionando sustituciones se declara qué SÍ es válido.
    """
    return re.sub(r"[^A-Z0-9]", "<", linea.upper())


def _a_treinta(linea: str) -> str:
    """Ajusta un renglón a los 30 caracteres que manda el formato.

    Un renglón más largo se recorta por la DERECHA: el carácter de más es una
    mota o un borde que el OCR leyó al final del renglón (caso real: un '7'
    espurio al final del renglón 2). Uno más corto se rellena, que es inocuo
    porque el relleno es justamente '<'.
    """
    return linea[:30] if len(linea) >= 30 else linea.ljust(30, "<")


def _mrz_en_texto(texto_ocr: str) -> str:
    """Localiza los tres renglones de la MRZ dentro del texto completo del OCR.

    Se usa cuando el modelo no devolvió la entidad `mrz`. Busca el renglón 1 por
    su encabezado y se lleva los dos siguientes; la validación de que de verdad
    sea una MRZ la hace después `parsear_mrz` con los dígitos de control, así
    que aquí basta con ser generoso.
    """
    lineas = [l for l in (texto_ocr or "").splitlines() if l.strip()]
    candidatos = []

    for i, linea in enumerate(lineas):
        if not RE_MRZ_1.match(_normalizar_renglon(linea)):
            continue
        # Se acumula hasta juntar los 90 caracteres, no un número fijo de
        # renglones: el OCR parte la MRZ en pedazos con frecuencia, y llevarse
        # "los tres siguientes" dejaba fuera el renglón de los nombres.
        juntos, total = [], 0
        for siguiente in lineas[i:i + 8]:
            juntos.append(siguiente)
            total += len(_normalizar_renglon(siguiente))
            if total >= 90:
                break
        candidatos.append("\n".join(juntos))

    # Se prefiere el candidato cuyos dígitos de control cuadran. Es la manera
    # de no quedarse con un falso encabezado: una MRZ de verdad se valida a sí
    # misma, cualquier otro texto no.
    for candidato in candidatos:
        renglones, error = _renglones_mrz(candidato)
        if not error and all(_verificar_digitos(renglones[0], renglones[1]).values()):
            return candidato

    return candidatos[0] if candidatos else ""


def _renglones_mrz(texto: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    Reconstruye los 3 renglones de 30 caracteres.

    Se intenta primero POR PATRÓN y no por longitud, porque el OCR se equivoca
    de a un carácter y eso basta para arruinar el corte a ciegas: en un caso
    real el renglón 2 vino con 31 caracteres (un '7' de más al final), y
    concatenar y cortar cada 30 recorría ese '7' al inicio del renglón 3,
    devolviendo `apellido_paterno: "7CARRILLO"`. Peor aún, los dígitos de
    control NO lo detectaban: solo cubren los renglones 1 y 2, así que el dato
    corrupto salía marcado como íntegro.

    Identificando cada renglón por su forma, un carácter de más se recorta en el
    renglón donde sobra y no contamina a los demás.

    Si los renglones no se pueden identificar —el OCR a veces parte la MRZ en
    cinco pedazos— se cae al método anterior de concatenar y cortar, que para
    ese caso sí funciona.
    """
    if not texto:
        return None, "sin texto de MRZ"

    pedazos = [_normalizar_renglon(l) for l in texto.splitlines() if l.strip()]

    inicio = next((i for i, p in enumerate(pedazos) if RE_MRZ_1.match(p)), None)
    if inicio is None:
        return None, "no se encontró el encabezado de la MRZ"
    pedazos = pedazos[inicio:]

    # Renglón 1: se cierra al completar 30 caracteres, o antes si el siguiente
    # pedazo ya tiene forma de renglón 2 — ese es el caso donde el OCR dejó un
    # carácter de más al final del renglón 1 y cortar por longitud lo recorrería
    # todo.
    i, r1 = 0, ""
    while i < len(pedazos):
        r1 += pedazos[i]
        i += 1
        if len(r1) >= 30 or (i < len(pedazos) and RE_MRZ_2.match(pedazos[i])):
            break

    # Renglones 2 y 3: se acumulan pedazos hasta completar 30. El OCR parte un
    # renglón en varios trozos con frecuencia, y cada trozo por sí solo no mide
    # 30 pero la suma sí.
    r2 = ""
    while i < len(pedazos) and len(r2) < 30:
        r2 += pedazos[i]
        i += 1

    r3 = "".join(pedazos[i:])

    if not r2 or not r3:
        total = len(r1) + len(r2) + len(r3)
        return None, f"MRZ incompleta: {total} caracteres de 90"

    return [_a_treinta(r1), _a_treinta(r2), _a_treinta(r3)], None


def parsear_mrz(texto: str) -> Dict[str, Any]:
    """
    Parsea una MRZ formato TD1 (3 renglones de 30 caracteres), que es el que usa
    el reverso de la INE, y verifica sus dígitos de control. Si no se puede
    reconstruir con confianza devuelve el motivo y el texto crudo, sin inventar
    campos.
    """
    if not texto:
        return {}

    renglones, error = _renglones_mrz(texto)
    if error:
        logger.warning(f"MRZ no interpretable: {error}")
        return {"error": error, "texto_crudo": texto}

    l1, l2, l3 = renglones

    # El renglón 1 del TD1 reserva las posiciones 16-30 para "datos opcionales",
    # y el INE las usa con una estructura propia. Verificada contra 10 de 10
    # credenciales reales: la sección que sale de aquí coincidió con la impresa
    # en el anverso en todas.
    #
    #   IDMEX 137284837 7 << 3958 037885328
    #     │       │     │      │       └─ identificador del ciudadano (9)
    #     │       │     │      └───────── sección (4)
    #     │       │     └──────────────── dígito verificador del CIC
    #     │       └────────────────────── CIC (9)
    #     └────────────────────────────── tipo de documento + país
    resultado: Dict[str, Any] = {
        "lineas": renglones,
        "tipo_documento": l1[0:2].replace("<", ""),
        "pais_emisor": l1[2:5].replace("<", ""),
        "cic": l1[5:14].replace("<", ""),
        "seccion": l1[17:21].replace("<", ""),
        "identificador_ciudadano": l1[21:30].replace("<", ""),
        "nacionalidad": l2[15:18].replace("<", ""),
    }
    # OBSOLETO: `folio` es el mismo dato que `cic`, con el nombre que se le puso
    # antes de saber cómo se llama de verdad. Se conserva para no romper a quien
    # ya lo lea; quitar en cuanto nadie lo use.
    resultado["folio"] = resultado["cic"]

    if (f := _fecha_mrz(l2[0:6])):
        resultado["fecha_nacimiento"] = f
    if l2[7] in ("M", "F", "H"):
        resultado["sexo"] = l2[7]
    if (f := _fecha_mrz(l2[8:14], es_vigencia=True)):
        resultado["vigencia"] = f

    # Renglón 3: APELLIDOS<<NOMBRES, apellidos separados por un solo '<'
    partes = l3.split("<<")
    apellidos = [p for p in partes[0].split("<") if p]
    if apellidos:
        resultado["apellido_paterno"] = apellidos[0]
    if len(apellidos) > 1:
        resultado["apellido_materno"] = " ".join(apellidos[1:])
    if len(partes) > 1:
        nombres = [p for p in partes[1].split("<") if p]
        if nombres:
            resultado["nombres"] = " ".join(nombres)

    # El renglón 3 mide 30 caracteres fijos: cuando el nombre completo no cabe,
    # se corta. Pero solo se corta el ÚLTIMO campo que alcanzó el borde: los
    # anteriores están completos. Marcar los tres como truncables dejaba pasar
    # apellidos fusionados ('BERNARDINO HERNANDEZ' contra 'BERNARDINO').
    if not l3.endswith("<"):
        if len(partes) > 1 and resultado.get("nombres"):
            resultado["campo_truncado"] = "nombres"
        elif resultado.get("apellido_materno"):
            resultado["campo_truncado"] = "apellido_materno"
        else:
            resultado["campo_truncado"] = "apellido_paterno"

    digitos = _verificar_digitos(l1, l2)
    resultado["digitos_control"] = digitos
    resultado["integra"] = all(digitos.values())

    return resultado


def _nombre_para_clave(nombre: str) -> str:
    """
    Nombre de pila que usan la CURP y la clave de elector: cuando el primero es
    MARÍA o JOSÉ y hay otro, se usa el segundo ('MARIA VALENTINA' -> VALENTINA).
    """
    partes = re.sub(r"[^A-Z ]", "", _sin_acentos((nombre or "").upper())).split()
    if len(partes) > 1 and partes[0] in ("MARIA", "MA", "JOSE", "J"):
        return partes[1]
    return partes[0] if partes else ""


# --------------------------------------------------------------------------
# Validación cruzada
# --------------------------------------------------------------------------

# Preposiciones, contracciones y conjunciones que la CURP no cuenta cuando un
# apellido es compuesto ('DE LA CRUZ' se codifica como 'CRUZ').
PARTICULAS_APELLIDO = {
    "DA", "DAS", "DE", "DEL", "DER", "DI", "DIE", "DD", "EL", "LA", "LOS", "LAS",
    "LE", "LES", "MAC", "MC", "VAN", "VON", "Y",
}


def _iniciales_clave(palabra: str, saltar_particulas: bool = False) -> str:
    """
    Primera letra + primera consonante interna, que es como se forman los
    primeros 6 caracteres de la clave de elector a partir del nombre.

    Con `saltar_particulas`, un apellido compuesto se reduce a su primera palabra
    significativa ('DE LA CRUZ' -> 'CRUZ'), que es como lo codifica la CURP.
    """
    texto = re.sub(r"[^A-Z ]", "", _sin_acentos((palabra or "").upper()))
    if saltar_particulas:
        palabras = texto.split()
        while len(palabras) > 1 and palabras[0] in PARTICULAS_APELLIDO:
            palabras.pop(0)
        texto = palabras[0] if palabras else ""
    limpia = re.sub(r"[^A-Z]", "", texto)
    if not limpia:
        return ""
    interna = next((c for c in limpia[1:] if c not in VOCALES), "")
    return limpia[0] + interna


def _iniciales_desde_curp(curp: str) -> str:
    """
    Las mismas 6 iniciales, pero derivadas de la CURP en vez del nombre que
    extrajo el modelo. Es más confiable: la CURP viene impresa en el documento y
    el nombre puede venir mal leído o mal asignado, en cuyo caso comparar la
    clave contra él acusa al documento de un error que es del OCR.

    CURP: [0] y [13] del paterno, [2] y [14] del materno, [3] y [15] del nombre.
    """
    if len(curp) < 16:
        return ""
    return curp[0] + curp[13] + curp[2] + curp[14] + curp[3] + curp[15]


# --------------------------------------------------------------------------
# Comprobaciones que salen de reglas públicas, no del modelo
# --------------------------------------------------------------------------

# Diccionario con el que RENAPO calcula el dígito verificador de la CURP.
DICCIONARIO_CURP = "0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"


def digito_verificador_curp(curp: str) -> Optional[int]:
    """
    Dígito verificador de la CURP (posición 18), calculado desde las primeras 17.

    Cada carácter se convierte a su índice en el diccionario de RENAPO, se
    multiplica por un peso que baja de 18 a 2 y la suma se lleva a módulo 10. Es
    otra comprobación que el documento se hace a sí mismo, igual que los dígitos
    de control de la MRZ: no depende del modelo ni de ninguna fuente externa.

    Límite medido, que conviene tener presente: NO detecta todos los errores de
    un carácter. Probando todas las sustituciones posibles (37 símbolos) en las
    17 posiciones, detecta en promedio el 77%. En la posición 9 (peso 10) no
    detecta ninguna, porque multiplicar por 10 nunca cambia la suma módulo 10; y
    en las posiciones 4 y 14 (peso 15 y 5) deja pasar casi la mitad. Por eso no
    reemplaza a los cruces contra la MRZ y la clave de elector, que cubren justo
    esas posiciones (la 9 es el primer dígito del día de nacimiento).

    Devuelve None si la cadena no sirve para calcularlo.
    """
    if len(curp) < 17 or any(c not in DICCIONARIO_CURP for c in curp[:17]):
        return None
    suma = sum(DICCIONARIO_CURP.index(c) * (18 - i) for i, c in enumerate(curp[:17]))
    return (10 - suma % 10) % 10


# Primeros dos dígitos del código postal de cada entidad, según la asignación de
# SEPOMEX. Es una tabla de PREFIJOS, no de códigos completos: alcanza para cachar
# un código postal mal leído o puesto en el estado equivocado, que es lo que se
# busca, sin cargar con el catálogo entero.
PREFIJOS_CP = {
    "AGS": (20, 20), "BC": (21, 22), "BCS": (23, 23), "CAMP": (24, 24),
    "COAH": (25, 27), "COL": (28, 28), "CHIS": (29, 30), "CHIH": (31, 33),
    "CDMX": (1, 16), "DF": (1, 16), "DGO": (34, 35), "GTO": (36, 38),
    "GRO": (39, 41), "HGO": (42, 43), "JAL": (44, 49), "MEX": (50, 57),
    "MICH": (58, 61), "MOR": (62, 62), "NAY": (63, 63), "NL": (64, 67),
    "OAX": (68, 71), "PUE": (72, 75), "QRO": (76, 76), "QROO": (77, 77),
    "SLP": (78, 79), "SIN": (80, 82), "SON": (83, 85), "TAB": (86, 86),
    "TAMPS": (87, 89), "TLAX": (90, 90), "VER": (91, 96), "YUC": (97, 97),
    "ZAC": (98, 99),
}

# Cómo puede venir escrito el estado, ya reducido a solo letras sin acentos. La
# credencial imprime abreviaturas ('D.F.', 'OAX.', 'N.L.') pero el OCR o el modelo
# a veces las expanden. 'MEXICO' a secas NO está: es ambiguo entre el estado y el
# país, y ante la duda es mejor no comprobar que adivinar.
ALIAS_ESTADO = {
    "AGS": "AGS", "AGUASCALIENTES": "AGS",
    "BC": "BC", "BAJACALIFORNIA": "BC",
    "BCS": "BCS", "BAJACALIFORNIASUR": "BCS",
    "CAMP": "CAMP", "CAMPECHE": "CAMP",
    "COAH": "COAH", "COAHUILA": "COAH", "COAHUILADEZARAGOZA": "COAH",
    "COL": "COL", "COLIMA": "COL",
    "CHIS": "CHIS", "CHIAPAS": "CHIS",
    "CHIH": "CHIH", "CHIHUAHUA": "CHIH",
    "CDMX": "CDMX", "CIUDADDEMEXICO": "CDMX",
    "DF": "DF", "DISTRITOFEDERAL": "DF",
    "DGO": "DGO", "DURANGO": "DGO",
    "GTO": "GTO", "GUANAJUATO": "GTO",
    "GRO": "GRO", "GUERRERO": "GRO",
    "HGO": "HGO", "HIDALGO": "HGO",
    "JAL": "JAL", "JALISCO": "JAL",
    "MEX": "MEX", "EDOMEX": "MEX", "ESTADODEMEXICO": "MEX",
    "MICH": "MICH", "MICHOACAN": "MICH", "MICHOACANDEOCAMPO": "MICH",
    "MOR": "MOR", "MORELOS": "MOR",
    "NAY": "NAY", "NAYARIT": "NAY",
    "NL": "NL", "NUEVOLEON": "NL",
    "OAX": "OAX", "OAXACA": "OAX",
    "PUE": "PUE", "PUEBLA": "PUE",
    "QRO": "QRO", "QUERETARO": "QRO",
    "QROO": "QROO", "QR": "QROO", "QUINTANAROO": "QROO",
    "SLP": "SLP", "SANLUISPOTOSI": "SLP",
    "SIN": "SIN", "SINALOA": "SIN",
    "SON": "SON", "SONORA": "SON",
    "TAB": "TAB", "TABASCO": "TAB",
    "TAMPS": "TAMPS", "TAMAULIPAS": "TAMPS",
    "TLAX": "TLAX", "TLAXCALA": "TLAX",
    "VER": "VER", "VERACRUZ": "VER", "VERACRUZDEIGNACIODELALLAVE": "VER",
    "YUC": "YUC", "YUCATAN": "YUC",
    "ZAC": "ZAC", "ZACATECAS": "ZAC",
}


def _clave_estado(texto: Any) -> Optional[str]:
    """Lleva el estado, escrito como venga, a su clave en PREFIJOS_CP."""
    if not isinstance(texto, str):
        return None
    letras = "".join(c for c in _sin_acentos(texto.upper()) if c.isalpha())
    return ALIAS_ESTADO.get(letras)


def codigo_postal_vs_estado(cp: Any, estado: Any) -> Optional[bool]:
    """
    True si el código postal cae en el estado, False si no, None si no se puede
    decir (el código no tiene 5 dígitos o el estado no se reconoce).
    """
    cp = str(cp or "").strip()
    clave = _clave_estado(estado)
    if clave is None or not re.fullmatch(r"[0-9]{5}", cp):
        return None
    desde, hasta = PREFIJOS_CP[clave]
    return desde <= int(cp[:2]) <= hasta


def _sufijo_anio_registro(valor: Any) -> Optional[str]:
    """Los dos dígitos que siguen al año en 'AÑO DE REGISTRO' ('2008 02' -> '02').

    Acepta espacio, punto o guion como separador porque el modelo no es
    consistente ('2022 00' y '2022.00' salen del mismo tipo de credencial).
    """
    m = re.fullmatch(r"\s*(?:19|20)[0-9]{2}\s*[ .\-]\s*([0-9]{2})\s*", str(valor or ""))
    return m.group(1) if m else None


def _emision_en_mrz(mrz: Dict[str, Any]) -> Optional[str]:
    """Los dos dígitos que la MRZ trae en el renglón 2, posiciones 19 y 20.

    Valen lo mismo que el sufijo del año de registro. NO son un mes: en las
    credenciales medidas valen 00, 01, 02 y 04, y coinciden con el
    `numero_emision` que extrae el modelo en todas las que lo traen.
    """
    lineas = mrz.get("lineas") or []
    if len(lineas) < 2:
        return None
    tramo = lineas[1][19:21]
    return tramo if re.fullmatch(r"[0-9]{2}", tramo) else None


def _coincide(a: Any, b: Any, truncable: bool) -> bool:
    """La misma regla que usa `comparar` dentro de validar(), para poder
    preguntarla sin registrar nada."""
    if a in (None, "") or b in (None, ""):
        return False
    av, bv = str(a).upper(), str(b).upper()
    return av == bv or (truncable and av.startswith(bv))


def _apellidos_equivalentes(ap_f: Any, am_f: Any, ap_m: Any, am_m: Any, abreviado: bool) -> bool:
    """
    Si el frente y la MRZ traen los MISMOS apellidos aunque repartidos distinto
    entre paterno y materno.

    En la MRZ el espacio de un apellido compuesto y el separador entre los dos
    apellidos son el mismo '<', así que la frontera no se puede conocer. Con
    'DE LA CRUZ' / 'PEREZ' la MRZ trae 'DE<LA<CRUZ<PEREZ' y se lee como paterno
    'DE' y materno 'LA CRUZ PEREZ': comparar campo por campo daba tres falsas
    discrepancias. Lo que SÍ se puede comparar es la secuencia de palabras.

    Exige que el frente traiga AMBOS apellidos. Sin materno en el frente no se
    puede saber si es un apellido único compuesto o dos apellidos fusionados por
    el modelo, y ese segundo caso es un error real que se sigue reportando.

    Con la MRZ abreviada se acepta que cada palabra sea prefijo de la del frente.
    """
    if not all(isinstance(x, str) and x.strip() for x in (ap_f, am_f, ap_m, am_m)):
        return False
    pf = f"{ap_f} {am_f}".upper().split()
    pm = f"{ap_m} {am_m}".upper().split()
    if len(pf) != len(pm):
        return False
    return all(x == y or (abreviado and x.startswith(y)) for x, y in zip(pf, pm))


def validar(frente: Dict[str, Any], mrz: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compara el anverso contra la MRZ, la CURP y la clave de elector, que traen
    la fecha de nacimiento y el sexo codificados. Sirve para cachar errores de
    OCR que Document AI reporta con confianza alta.

    Reporta también qué comprobaciones NO se pudieron correr: un 'ok' sin esa
    lista no distingue "el documento está bien" de "casi nada se pudo revisar".
    """
    coincidencias: List[str] = []
    discrepancias: List[str] = []
    omitidas: List[str] = []

    def comparar(etiqueta: str, a: Any, b: Any, truncable: bool = False):
        if a in (None, "") or b in (None, ""):
            omitidas.append(etiqueta)
            return
        av, bv = str(a).upper(), str(b).upper()
        if av == bv:
            coincidencias.append(etiqueta)
        elif truncable and av.startswith(bv):
            # La MRZ corta a 30 caracteres: que sea prefijo es coincidencia
            coincidencias.append(f"{etiqueta} (truncado en la MRZ)")
        else:
            discrepancias.append(f"{etiqueta}: anverso '{a}' vs '{b}'")

    curp = re.sub(r"[^A-Z0-9]", "", (frente.get("curp") or "").upper())
    clave = re.sub(r"[^A-Z0-9]", "", (frente.get("clave_elector") or "").upper())

    # --- contra la MRZ del reverso ---
    if mrz.get("error"):
        omitidas.append(f"MRZ completa ({mrz['error']})")
    elif not mrz:
        omitidas.append("MRZ completa (no se extrajo)")
    elif not mrz.get("integra"):
        # Con los dígitos de control mal, la MRZ está corrupta y sus campos ya no
        # son referencia: se reporta la corrupción UNA vez, en vez de una
        # discrepancia por cada campo que quedó corrido.
        fallan = [k for k, v in mrz.get("digitos_control", {}).items() if not v]
        discrepancias.append(
            f"MRZ corrupta: fallan los dígitos de control de {', '.join(fallan)}; "
            f"no se pudo contrastar el anverso contra el reverso"
        )
        omitidas.append("todas las comprobaciones contra la MRZ")
    else:
        coincidencias.append("MRZ (dígitos de control)")
        # Cuando el renglón de nombres llena los 30 caracteres, el formato
        # ABREVIA, y no solo el último campo: en un caso real
        # 'BERNARDINO<HERNAN<<JUAN<GUILLE' trae cortados el apellido materno
        # (HERNANDEZ) y el nombre (GUILLERMO) a la vez. Por eso ahí se acepta el
        # prefijo en los tres campos del nombre.
        abreviado = bool(mrz.get("campo_truncado"))
        comparar("fecha_nacimiento (MRZ)", frente.get("fecha_nacimiento"), mrz.get("fecha_nacimiento"))
        comparar("sexo (MRZ)", frente.get("sexo"), mrz.get("sexo"))
        ap_f, am_f = frente.get("apellido_paterno"), frente.get("apellido_materno")
        ap_m, am_m = mrz.get("apellido_paterno"), mrz.get("apellido_materno")

        # Apellido compuesto ('DE LA CRUZ'): la MRZ no distingue el espacio del
        # separador entre apellidos, así que el reparto paterno/materno puede
        # salir distinto aunque las palabras sean las mismas. Solo se usa cuando
        # la comparación campo por campo no cuadra; si cuadra, todo sigue igual.
        compuesto_ok = (_apellidos_equivalentes(ap_f, am_f, ap_m, am_m, abreviado)
                        and not (_coincide(ap_f, ap_m, abreviado) and _coincide(am_f, am_m, abreviado)))
        if compuesto_ok:
            coincidencias.append("apellido_paterno (MRZ)")
            coincidencias.append("apellido_materno (MRZ)")
        else:
            comparar("apellido_paterno (MRZ)", ap_f, ap_m, truncable=abreviado)
            comparar("apellido_materno (MRZ)", am_f, am_m, truncable=abreviado)
        comparar("nombre (MRZ)", frente.get("nombre"), mrz.get("nombres"),
                 truncable=abreviado)

        # Aceptar prefijos abre un hueco: un anverso con los dos apellidos
        # fusionados en `apellido_paterno` ('BERNARDINO HERNANDEZ' contra un
        # 'BERNARDINO' de la MRZ) pasaría como coincidencia. Se revisa aparte,
        # comparando contra lo que la MRZ sí trae separado. No aplica cuando ya
        # se determinó que es un apellido compuesto legítimo: ahí el frente trae
        # ambos apellidos y las palabras coinciden una por una.
        ap_frente = (ap_f or "").upper()
        ap_mrz = (ap_m or "").upper()
        am_mrz = (am_m or "").upper()
        if not compuesto_ok and ap_frente and ap_mrz and am_mrz and ap_frente != ap_mrz:
            resto = ap_frente[len(ap_mrz):].strip()
            if ap_frente.startswith(ap_mrz) and resto and am_mrz.startswith(resto[:len(am_mrz)]):
                discrepancias.append(
                    f"apellido_paterno: '{frente.get('apellido_paterno')}' parece traer los dos "
                    f"apellidos juntos; la MRZ los separa en '{ap_mrz}' y '{am_mrz}'"
                )
        comparar("vigencia (MRZ)", anio_vigencia(frente.get("vigencia")),
                 (mrz.get("vigencia") or "")[:4] or None)
        comparar("seccion (MRZ)", frente.get("seccion"), mrz.get("seccion"))

        # Sufijo del año de registro contra la MRZ. Ver el comentario de abajo:
        # esta comprobación calla cuando no puede correr.
        sufijo = _sufijo_anio_registro(frente.get("anio_registro"))
        emision = _emision_en_mrz(mrz)
        if sufijo and emision:
            if sufijo == emision:
                coincidencias.append("anio_registro, sufijo (MRZ)")
            else:
                discrepancias.append(
                    f"anio_registro: el sufijo '{sufijo}' no cuadra con el de la MRZ '{emision}'"
                )

    # --- contra la CURP: codifica fecha de nacimiento y sexo ---
    if re.fullmatch(r"[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d", curp):
        if (f := _fecha_mrz(curp[4:10])):
            comparar("fecha_nacimiento (CURP)", frente.get("fecha_nacimiento"), f)
        comparar("sexo (CURP)", frente.get("sexo"), curp[10])

        # Y el dígito verificador que la propia CURP trae al final.
        esperado = digito_verificador_curp(curp)
        if esperado is not None:
            if str(esperado) == curp[17]:
                coincidencias.append("curp (dígito verificador)")
            else:
                discrepancias.append(
                    f"curp: el dígito verificador no cuadra, la CURP trae '{curp[17]}' "
                    f"y el cálculo da '{esperado}'"
                )
    else:
        omitidas.append(f"CURP (formato inválido: '{curp or 'ausente'}')")

    # --- contra la clave de elector ---
    if len(clave) == 18:
        esperadas_curp = _iniciales_desde_curp(curp)
        def derivadas(saltar: bool) -> str:
            return (_iniciales_clave(frente.get("apellido_paterno"), saltar)
                    + _iniciales_clave(frente.get("apellido_materno"), saltar)
                    + _iniciales_clave(_nombre_para_clave(frente.get("nombre")), saltar))

        esperadas_nombre = derivadas(False)
        # Un apellido compuesto ('DE LA CRUZ') se codifica con su primera palabra
        # significativa, no con la cadena entera. Se aceptan las dos derivaciones
        # para no depender de recordar la regla exacta de cada institución: una
        # coincidencia con cualquiera basta, y para los apellidos de una palabra
        # las dos son idénticas, así que el resto no cambia.
        alternativas = {d for d in (esperadas_nombre, derivadas(True)) if len(d) == 6}

        if len(esperadas_curp) == 6:
            comparar("clave_elector vs CURP (iniciales)", clave[:6], esperadas_curp)
            # Si clave y CURP concuerdan entre sí pero el nombre no, el dato malo
            # es el NOMBRE, no la clave: hay que decirlo, no callarlo.
            if (clave[:6] == esperadas_curp and alternativas
                    and esperadas_curp not in alternativas):
                discrepancias.append(
                    f"nombre: no concuerda con CURP ni clave de elector "
                    f"(ambas codifican '{esperadas_curp}', el nombre leído da '{esperadas_nombre}')"
                )
        elif len(esperadas_nombre) == 6:
            comparar("clave_elector vs nombre (iniciales)", clave[:6],
                     clave[:6] if clave[:6] in alternativas else esperadas_nombre)
        else:
            omitidas.append("clave_elector (iniciales)")

        if (f := _fecha_mrz(clave[6:12])):
            comparar("fecha_nacimiento (clave_elector)", frente.get("fecha_nacimiento"), f)
        comparar("sexo (clave_elector)", frente.get("sexo"), clave[14])
    else:
        omitidas.append(f"clave_elector (mide {len(clave)} caracteres, se esperaban 18)")

    # --- domicilio: el código postal tiene que caer en el estado ---
    domicilio = frente.get("domicilio")
    if isinstance(domicilio, dict):
        cuadra = codigo_postal_vs_estado(domicilio.get("codigo_postal"), domicilio.get("estado"))
        if cuadra is True:
            coincidencias.append("domicilio (código postal vs estado)")
        elif cuadra is False:
            discrepancias.append(
                f"domicilio: el código postal '{domicilio.get('codigo_postal')}' "
                f"no corresponde al estado '{domicilio.get('estado')}'"
            )

    # Las tres comprobaciones de arriba que salen de reglas públicas (dígito de la
    # CURP, sufijo del año de registro y código postal contra estado) NO agregan
    # a `sin_verificar` cuando no pueden correr. Es deliberado, por dos razones:
    #
    #   - Los datos de los que dependen no son estables: el modelo devuelve el año
    #     de registro a veces con su sufijo y a veces sin él, y el estado a veces
    #     abreviado y a veces no. Si su ausencia bajara `completo`, el MISMO
    #     documento oscilaría entre 200 y 212 de una llamada a otra por pura
    #     variabilidad del extractor, que es justo lo que `completo` no debe medir.
    #   - Cuando falta la MRZ o la CURP ya hay una entrada que lo dice; repetirlo
    #     por cada comprobación derivada solo hace ruido.
    #
    # Aportan evidencia cuando pueden correr (coincidencia o discrepancia) y callan
    # cuando no: no cambian el código de ningún documento que antes salía bien.

    # Dos booleanos, no uno: 'ok' dice que nada se contradijo, 'completo' dice que
    # todo lo que se quería revisar se pudo revisar. Juntarlos en un solo 'ok'
    # hacía que una revisión incompleta se viera igual que una identidad inconsistente,
    # y que 'ok: true' no significara lo que un lector supone que significa.
    return {
        "ok": not discrepancias,
        "completo": not omitidas,
        "coincidencias": coincidencias,
        "discrepancias": discrepancias,
        "sin_verificar": omitidas,
    }


# --------------------------------------------------------------------------

def detectar_formato(texto_ocr: str, frente: Dict[str, Any]) -> Optional[str]:
    """
    Distingue los dos formatos de credencial. El anterior imprime un renglón de
    claves (ESTADO / MUNICIPIO / LOCALIDAD con sus números) que el nuevo ya no
    trae. Sin esto, el consumidor no puede distinguir "el papel no lo dice" de
    "no se pudo leer", que para un banco son cosas muy distintas.
    """
    if RENGLON_CLAVES.search(texto_ocr or ""):
        return "anterior"
    if any(frente.get(c) for c in CAMPOS_SOLO_FORMATO_ANTERIOR):
        return "anterior"
    if frente.get("numero_emision"):
        return "nuevo"
    # No hay renglón de claves. Solo se concluye "nuevo" si el anverso sí se leyó
    # (hay CURP o clave de elector): así no se confunde "la credencial no lo trae"
    # con "el escaneo salió tan mal que no se leyó nada de esa zona".
    if frente.get("curp") or frente.get("clave_elector"):
        return "nuevo"
    return None


# Campo del frente -> (campo equivalente en la MRZ, etiqueta que usa validar())
CAMPOS_NOMBRE_MRZ = {
    "apellido_paterno": ("apellido_paterno", "apellido_paterno (MRZ)"),
    "apellido_materno": ("apellido_materno", "apellido_materno (MRZ)"),
    "nombre": ("nombres", "nombre (MRZ)"),
}


def corregir_nombre_con_mrz(frente: Dict[str, Any], mrz: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Corrige en `frente` un apellido o nombre al que el OCR le pegó letras de más,
    usando la MRZ, y devuelve qué corrigió.

    Caso real que lo motivó: sobre la 'A' de 'LARA' cae el punteado del fondo de
    seguridad de la credencial y el OCR lo lee como una 'S', así que el frente
    sale 'LARAS' mientras la MRZ trae 'LARA'. Se vio el mismo patrón en otros dos
    documentos del corpus ('SAENZA' contra 'SAENZ', 'PATRICIAA' contra
    'PATRICIA'): el frente trae letras de más y la MRZ, que se imprime en una
    tipografía hecha para OCR, trae el nombre bien.

    Solo se corrige cuando se cumplen TODAS estas condiciones, y cada una cierra un
    caso en el que corregir sería inventar:

      - La MRZ es íntegra (sus dígitos de control cuadran).
      - El renglón de nombres de la MRZ deja relleno al final. Cuando no lo deja,
        el formato ABREVIA los nombres para que quepan y el frente completo
        ('HERNANDEZ') es el correcto contra el recorte de la MRZ ('HERNAN').
      - El frente es exactamente la MRZ más 1 o 2 letras: el valor de la MRZ es el
        prefijo y lo que sobra son solo letras, sin espacios. Con espacios podría
        ser un apellido compuesto ('DE LA CRUZ' contra 'DE', porque en la MRZ el
        espacio y el separador de apellidos son el mismo '<').

    Si no se cumplen, no se toca nada: la discrepancia sigue reportándose como
    hasta ahora. Nunca se reemplaza un valor por otro que no sea el mismo con
    letras de más.
    """
    if not mrz or mrz.get("error") or not mrz.get("integra") or mrz.get("campo_truncado"):
        return []

    hechas = []
    for campo, (campo_mrz, etiqueta) in CAMPOS_NOMBRE_MRZ.items():
        leido = frente.get(campo)
        en_mrz = mrz.get(campo_mrz)
        if not (isinstance(leido, str) and isinstance(en_mrz, str)) or len(en_mrz) < 3:
            continue
        if leido.startswith(en_mrz) and re.fullmatch(r"[A-Z]{1,2}", leido[len(en_mrz):]):
            frente[campo] = en_mrz
            hechas.append({"campo": campo, "leido": leido, "mrz": en_mrz, "etiqueta": etiqueta})
    return hechas


def armar_respuesta(entidades: Dict[str, Any],
                    repetidos: Optional[Dict[str, int]] = None,
                    texto_ocr: str = "") -> Dict[str, Any]:
    """
    Convierte el diccionario plano de entidades de Document AI en la respuesta
    de dos secciones (frente, atras) más el bloque de validación.
    """
    avisos: List[str] = []

    frente: Dict[str, Any] = {}
    for campo in CAMPOS_FRENTE:
        if campo in entidades:
            frente[campo] = limpiar_texto(entidades[campo]) if campo != "domicilio" else entidades[campo]

    domicilio = frente.get("domicilio")
    if isinstance(domicilio, dict):
        for campo, valor in list(domicilio.items()):
            if isinstance(valor, str):
                domicilio[campo] = limpiar_texto(valor).rstrip(",").strip()
        if isinstance(domicilio.get("estado"), str):
            domicilio["estado"] = _limpiar_punto_final(domicilio["estado"])

    formato = detectar_formato(texto_ocr, frente)
    frente["formato_credencial"] = formato
    if formato == "nuevo":
        # Ausencia esperada, no hueco: se dice cuáles campos no vienen impresos
        # en este formato para que nadie los lea como datos perdidos.
        frente["no_impresos"] = [c for c in CAMPOS_SOLO_FORMATO_ANTERIOR
                                 if not frente.get(c)]

    # La MRZ se busca en el TEXTO del OCR cuando el modelo no la entregó como
    # entidad. Pasa: hay un caso real donde el OCR la leyó completa y correcta y
    # aun así el extractor no la asignó al campo, dejando la sección `atras`
    # vacía. La MRZ es de los pocos datos que se pueden localizar sin depender
    # del modelo —tres renglones con una forma rígida e inconfundible—, así que
    # no tiene sentido perderla por una falla de asignación.
    crudo_mrz = entidades.get("mrz") or ""
    origen = "entidad"
    if not crudo_mrz and texto_ocr:
        crudo_mrz = _mrz_en_texto(texto_ocr)
        origen = "texto"
        if crudo_mrz:
            logger.info("La MRZ no vino como entidad; se recuperó del texto del OCR.")

    mrz = parsear_mrz(crudo_mrz)
    if mrz and not mrz.get("error"):
        mrz["origen"] = origen

    # Apellidos o nombre con letras de más que la MRZ desmiente: se corrigen y se
    # avisa, para que un humano lo vea aunque el dato ya salga bien.
    correcciones = corregir_nombre_con_mrz(frente, mrz)
    for c in correcciones:
        avisos.append(
            f"frente.{c['campo']} corregido con la MRZ: se leyó '{c['leido']}' "
            f"y la MRZ trae '{c['mrz']}'"
        )

    # Un PDF con varios documentos produce entidades repetidas: se conserva la
    # primera (la INE) y se avisa, en vez de mezclar dos identidades en silencio.
    for tipo, veces in (repetidos or {}).items():
        avisos.append(
            f"el PDF contiene {veces} bloques '{tipo}': se conservó el primero, "
            f"revisa que el documento no traiga más de una identidad"
        )

    # Un valor que no aparece literal en el texto leído es un valor que el
    # modelo compuso, no que extrajo.
    if texto_ocr:
        for campo, valor in frente.items():
            # Campos que calcula esta función, no que se leyeron del documento
            if campo in ("formato_credencial", "no_impresos"):
                continue
            # Las fechas vienen normalizadas a ISO por Document AI, así que nunca
            # aparecen literales: el papel dice '20/03/1976'.
            if isinstance(valor, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
                continue
            if isinstance(valor, str) and len(valor) > 3 and valor not in texto_ocr:
                avisos.append(f"frente.{campo}='{valor}' no aparece literal en el texto leído")

    validacion = validar(frente, mrz)

    # Un campo corregido con la MRZ ya no se puede contrastar contra la MRZ: su
    # valor salió de ahí, así que la coincidencia sería circular y no prueba
    # nada. Se quita de la lista para que `coincidencias` solo cuente evidencia
    # independiente; el aviso es lo que deja constancia de la corrección.
    circulares = {c["etiqueta"] for c in correcciones}
    if circulares:
        validacion["coincidencias"] = [e for e in validacion["coincidencias"] if e not in circulares]

    return {
        "frente": frente,
        "atras": {"mrz": mrz} if mrz else {},
        "validacion": validacion,
        "avisos": avisos,
    }
