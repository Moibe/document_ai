"""Extractor de pasaporte: esquema, armado de la respuesta y MRZ formato TD3.

Hermano de `ine.py`, con la misma estructura de respuesta (datos / mrz /
validacion / avisos) para que quien consuma la API no tenga que aprender dos
formas distintas.

La diferencia de fondo está en la MRZ. La INE usa TD1 —tres renglones de 30— y
el pasaporte usa **TD3: dos renglones de 44**. No es la misma maquinaria: las
posiciones son otras y el renglón 2 del TD3 trae MÁS redundancia que todo el
TD1 junto (número de pasaporte, nacionalidad, nacimiento, sexo, vencimiento,
número personal, cada uno con su dígito verificador, más el compuesto). Por eso
aquí se puede validar mejor que en la INE.

Lo que SÍ se reusa de `ine.py` son las piezas puras y ya probadas —el algoritmo
de los dígitos de control y la normalización de caracteres—, que no dependen del
formato. Se importan en vez de copiarse para que una corrección en el algoritmo
valga para los dos documentos.
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import ine
from ine import (
    _fecha_mrz,
    _normalizar_renglon,
    _valor_mrz,
    digito_control,
    limpiar_texto,
)

logger = logging.getLogger(__name__)

DISPLAY_NAME = "rad_pasaporte"

# Se llenan tras correr `python crear_processor.py pasaporte`.
PROCESSOR_ID = "610ebca726249369"
VERSION = "pretrained-foundation-model-v1.5-pro-2025-06-20"

# Qué campo del esquema va en qué sección de la respuesta
CAMPOS_DATOS = (
    "tipo_documento", "pais_emisor", "numero_pasaporte",
    "apellido_paterno", "apellido_materno", "nombres",
    "nacionalidad", "nacionalidad_traducida", "fecha_nacimiento", "lugar_nacimiento", "sexo",
    "curp", "fecha_expedicion", "fecha_vencimiento", "autoridad_expedidora",
)
# Campos que Document AI INFIERE en vez de extraer (method DERIVE): su valor no está
# escrito en el documento, así que no tiene sentido buscarlo literal en el texto, y se
# generan POR PÁGINA, así que en un PDF de varias páginas pueden venir repetidos sin
# que eso signifique que el PDF trae dos documentos.
#
# `nacionalidad_codigo` NO está en CAMPOS_DATOS: no se entrega, solo sirve para comparar la
# nacionalidad impresa contra la MRZ (ver `validar`).
CAMPOS_DERIVADOS = ("nacionalidad_traducida", "nacionalidad_codigo")


# La descripción ES la instrucción: en un campo derivado no hay nada que extraer, así
# que lo único que guía al modelo es este texto.
DESCRIPCION_NACIONALIDAD_TRADUCIDA = (
    "Nacionalidad del titular escrita en espanol, como gentilicio de una sola palabra en "
    "femenino singular, porque concuerda con la palabra 'nacionalidad' y NO con el sexo del "
    "titular (por ejemplo: Mexicana, China, Estadounidense, Colombiana, Brasilena), con la "
    "primera letra en mayuscula. Se infiere de la nacionalidad impresa en la hoja de datos, "
    "que puede venir en otro idioma o en otro alfabeto: no se copia tal cual, se traduce. "
    "Si la nacionalidad es AMERICAN, USA o UNITED STATES OF AMERICA, el resultado es Estadounidense. "
    "Si lo impreso es el nombre de un pais y no un gentilicio (por ejemplo NEW ZEALAND), el "
    "resultado es el gentilicio de ese pais (Neozelandesa)."
)
# La regla de Estados Unidos NO es decorativa. Medido: sin ella, AMERICAN / USA / UNITED STATES
# OF AMERICA no devolvian nada (5 de 5 intentos); con ella, Estadounidense con confianza 1.0.
# El modelo toma los ejemplos de la descripcion casi como una lista cerrada y se abstiene ante
# lo ambiguo (Americana vs Estadounidense). Si aparece otra nacionalidad que sale vacia, se
# arregla igual: con una regla explicita aqui.


DESCRIPCION_NACIONALIDAD_CODIGO = (
    "Codigo de tres letras del pais de la nacionalidad del titular, segun el estandar ICAO 9303 "
    "de los documentos de viaje, el mismo que usa la zona de lectura mecanica (por ejemplo: MEX, "
    "COL, USA, CHN, BRA, KOR). Se infiere UNICAMENTE de la nacionalidad impresa en el campo de "
    "nacionalidad de la hoja de datos, que puede venir en otro idioma, en otro alfabeto o como "
    "nombre de pais. Ignora la zona de lectura mecanica (los renglones de letras y signos < al "
    "pie de la hoja) y no uses el pais que expide el pasaporte: la nacionalidad del titular puede "
    "ser distinta. Si la nacionalidad impresa ya es un codigo de tres letras, devuelvelo igual."
)


def esquema() -> Dict[str, Any]:
    """DocumentSchema del extractor de pasaporte.

    Parte del esquema del processor `extractor` que `document_ai` usa en
    producción, pero corrigiendo los problemas que ya nos costó encontrar en el
    del INE:

      - `expiracion` (string) y `fecha_vencimiento` (datetime) eran el MISMO
        dato en dos campos. Un campo que apunta a dos conceptos nunca converge,
        porque el modelo tiene razón en ambos casos — fue exactamente lo que
        pasó con `emision` en la INE. Queda solo `fecha_vencimiento`.
      - `nombres`, `apellidos`, `nombre_completo`, `apellido_paterno` y
        `apellido_materno` eran cinco campos solapados para el nombre. Quedan
        los tres que la MRZ entrega por separado, que son los que se pueden
        contrastar.
      - `country` y `nacionalidad` se solapaban, y uno estaba en inglés. El país
        que emite y la nacionalidad del titular SON cosas distintas (un
        naturalizado), así que se conservan los dos pero bien nombrados.
      - Todo estaba en REQUIRED_ONCE, incluido `curp`, que un pasaporte
        extranjero no trae. Marcar como obligatorio algo que legítimamente no
        existe empuja al modelo a inventarlo.
      - `lugar_nacimiento` era de tipo `address`; en el pasaporte es una ciudad
        o un estado, no un domicilio.
    """
    def p(nombre, tipo="string", desc=None, metodo="EXTRACT"):
        d = {"name": nombre, "valueType": tipo,
             "occurrenceType": "OPTIONAL_ONCE", "method": metodo}
        if desc:
            d["description"] = desc
        return d

    return {
        "displayName": "Pasaporte RAD",
        "description": "Pasaporte de cualquier pais.",
        "entityTypes": [{
            "name": "custom_extraction_document_type",
            "baseTypes": ["document"],
            "properties": [
                # --- datos del pasaporte ---
                p("tipo_documento", desc="Tipo de documento impreso en el pasaporte, normalmente la letra P."),
                p("pais_emisor", desc="Pais que expide el pasaporte, tal como viene impreso."),
                p("numero_pasaporte", desc="Numero del pasaporte, el identificador del documento."),
                p("apellido_paterno"),
                p("apellido_materno"),
                p("nombres", desc="Nombres de pila del titular, sin los apellidos."),
                p("nacionalidad", desc="Nacionalidad del titular. Puede diferir del pais que expide."),
                p("nacionalidad_traducida", metodo="DERIVE", desc=DESCRIPCION_NACIONALIDAD_TRADUCIDA),
                p("nacionalidad_codigo", metodo="DERIVE", desc=DESCRIPCION_NACIONALIDAD_CODIGO),
                p("fecha_nacimiento", "datetime"),
                p("lugar_nacimiento", desc="Ciudad o estado de nacimiento tal como viene impreso."),
                p("sexo"),
                p("curp", desc="CURP del titular. Solo la traen los pasaportes mexicanos."),
                p("fecha_expedicion", "datetime"),
                p("fecha_vencimiento", "datetime"),
                p("autoridad_expedidora", desc="Autoridad que expide, por ejemplo SRE."),
                # --- reverso ---
                p("mrz", desc="Zona de lectura mecanica, los dos renglones de 44 caracteres "
                              "que empiezan con P y van al pie de la hoja de datos."),
            ],
        }],
    }


# --------------------------------------------------------------------------
# MRZ formato TD3 (ICAO 9303): 2 renglones de 44 caracteres
# --------------------------------------------------------------------------
#
#   Renglón 1:  P<MEXGARCIA<LOPEZ<<JUAN<CARLOS<<<<<<<<<<<<<<
#               │ │  └─ apellidos << nombres, relleno hasta 44
#               │ └──── país emisor (3)
#               └────── tipo de documento (1) + subtipo (1)
#
#   Renglón 2:  G12345678 4 MEX 850315 7 M 300214 9 <<<<<<<<<<<<<< 0 6
#               │         │ │   │      │ │ │      │ │              │ │
#               │         │ │   │      │ │ │      │ │              │ └ compuesto
#               │         │ │   │      │ │ │      │ │              └── verif. del personal
#               │         │ │   │      │ │ │      │ └───────────────── número personal (14)
#               │         │ │   │      │ │ │      └─────────────────── verif. vencimiento
#               │         │ │   │      │ │ └────────────────────────── vencimiento AAMMDD
#               │         │ │   │      │ └──────────────────────────── sexo
#               │         │ │   │      └────────────────────────────── verif. nacimiento
#               │         │ │   └───────────────────────────────────── nacimiento AAMMDD
#               │         │ └───────────────────────────────────────── nacionalidad (3)
#               │         └─────────────────────────────────────────── verif. del número
#               └───────────────────────────────────────────────────── número de pasaporte (9)

LARGO_TD3 = 44

# El encabezado exige además que el renglón traiga al menos un '<'. Sin eso, la
# palabra "PASAPORTE" impresa en la portada encaja con el patrón (P + letra + 3
# letras) y el parser se engancha con ella. Es el mismo tropiezo que ya se midió
# en la INE con 'IDAN<MEXICO', que es como el OCR lee el escudo de la
# credencial. Un renglón 1 de verdad siempre trae '<': como subtipo, como
# separador de nombres o como relleno hasta los 44 caracteres.
#
# En las tres posiciones del código de país se aceptan también dígitos: el OCR lee 'COL'
# como 'C0L', y si el patrón los rechazara la MRZ entera se perdería en vez de corregirse
# (ver `_letras`).
RE_TD3_1 = re.compile(r"^(?=[^<]*<)P[A-Z<][A-Z0-9<]{3}[A-Z<]")
RE_TD3_2 = re.compile(r"^[A-Z0-9<]{9}\d[A-Z0-9<]{3}\d{6}\d[MFX<]\d{6}\d")


def _a_largo(linea: str) -> str:
    """Ajusta un renglón a los 44 caracteres del formato.

    Mismo criterio que en la INE y por el mismo motivo medido: un renglón más
    largo se recorta por la DERECHA, porque el carácter de más es una mota que
    el OCR leyó al final; uno más corto se rellena, que es inocuo porque el
    relleno es justamente '<'.
    """
    return linea[:LARGO_TD3] if len(linea) >= LARGO_TD3 else linea.ljust(LARGO_TD3, "<")


# Dígito que el OCR pone en lugar de la letra a la que se parece.
_A_LETRA = str.maketrans({"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B"})


def _letras(campo: str) -> str:
    """Un campo que el estándar define SOLO con letras (los códigos de país): un dígito
    ahí es una confusión del OCR, no un dato. Medido: 'COL' llegaba como 'C0L' y 'KOR'
    como 'K0R'. Los dígitos de control no cubren esas posiciones (el compuesto salta la
    nacionalidad), así que nada más las detecta."""
    return campo.translate(_A_LETRA)


def _verificar_digitos(l2: str) -> Dict[str, bool]:
    """Los cinco dígitos de control del TD3.

    El compuesto cubre número + nacimiento + vencimiento + número personal, o
    sea casi todo el renglón 2: si cuadra, la MRZ está íntegra.
    """
    compuesto = l2[0:10] + l2[13:20] + l2[21:43]
    return {
        "numero_pasaporte": digito_control(l2[0:9]) == _valor_mrz(l2[9]),
        "fecha_nacimiento": digito_control(l2[13:19]) == _valor_mrz(l2[19]),
        "fecha_vencimiento": digito_control(l2[21:27]) == _valor_mrz(l2[27]),
        "numero_personal": digito_control(l2[28:42]) == _valor_mrz(l2[42]),
        "compuesto": digito_control(compuesto) == _valor_mrz(l2[43]),
    }


def _renglones_td3(texto: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """Reconstruye los 2 renglones de 44 caracteres.

    Misma estrategia que terminó funcionando en la INE: identificar cada renglón
    por su PATRÓN y acumular pedazos hasta completarlo, en vez de concatenar
    todo y cortar por longitud. Cortar a ciegas hace que un solo carácter de más
    —y el OCR los mete— recorra el renglón siguiente entero y devuelva campos
    corridos sin ninguna señal de error.
    """
    if not texto:
        return None, "sin texto de MRZ"

    pedazos = [_normalizar_renglon(l) for l in texto.splitlines() if l.strip()]
    arranques = [i for i, p in enumerate(pedazos) if RE_TD3_1.match(p)]
    if not arranques:
        return None, "no se encontró el encabezado de la MRZ"

    armados = [_armar_desde(pedazos[i:]) for i in arranques]
    armados = [a for a in armados if a]

    # Gana el candidato que valida sus propios dígitos de control. Es la manera
    # de descartar un falso encabezado sin tener que adivinar cuál lo es: una
    # MRZ de verdad se valida a sí misma, cualquier otro texto no.
    for renglones in armados:
        if all(_verificar_digitos(renglones[1]).values()):
            return renglones, None

    if armados:
        return armados[0], None
    return None, f"MRZ incompleta: menos de {LARGO_TD3 * 2} caracteres"


def _armar_desde(pedazos: List[str]) -> Optional[List[str]]:
    """Arma los dos renglones acumulando pedazos desde un encabezado dado."""
    i, r1 = 0, ""
    while i < len(pedazos):
        r1 += pedazos[i]
        i += 1
        if len(r1) >= LARGO_TD3 or (i < len(pedazos) and RE_TD3_2.match(pedazos[i])):
            break

    r2 = ""
    while i < len(pedazos) and len(r2) < LARGO_TD3:
        r2 += pedazos[i]
        i += 1

    return [_a_largo(r1), _a_largo(r2)] if r2 else None


def mrz_en_texto(texto_ocr: str) -> str:
    """Localiza la MRZ dentro del texto completo del OCR.

    Se usa cuando el modelo no devolvió la entidad `mrz`. En la INE se midió que
    el modelo la pierde de forma intermitente —la lee bien pero no la asigna—,
    así que no tiene sentido depender de él para un dato que se puede encontrar
    solo, por su forma rígida.

    Entre varios candidatos gana el que valida sus propios dígitos de control:
    una MRZ de verdad se valida a sí misma, cualquier otro texto no.
    """
    lineas = [l for l in (texto_ocr or "").splitlines() if l.strip()]
    candidatos = []

    for i, linea in enumerate(lineas):
        if not RE_TD3_1.match(_normalizar_renglon(linea)):
            continue
        juntos, total = [], 0
        for siguiente in lineas[i:i + 6]:
            juntos.append(siguiente)
            total += len(_normalizar_renglon(siguiente))
            if total >= LARGO_TD3 * 2:
                break
        candidatos.append("\n".join(juntos))

    # La selección del candidato bueno la hace `_renglones_td3`, que compara los
    # dígitos de control de cada arranque posible. Aquí basta con entregarle
    # todo el bloque de texto donde puede estar la MRZ.
    return "\n".join(candidatos) if candidatos else ""


def parsear_mrz(texto: str) -> Dict[str, Any]:
    """Parsea una MRZ formato TD3 y verifica sus dígitos de control.

    Si no se puede reconstruir con confianza devuelve el motivo y el texto
    crudo, sin inventar campos.
    """
    if not texto:
        return {}

    renglones, error = _renglones_td3(texto)
    if error:
        logger.warning(f"MRZ no interpretable: {error}")
        return {"error": error, "texto_crudo": texto}

    l1, l2 = renglones
    # Se corrigen en los renglones mismos, no solo en los campos, para que `lineas` y
    # `pais_emisor` / `nacionalidad` no se contradigan. No altera ningún dígito de control.
    l1 = l1[:2] + _letras(l1[2:5]) + l1[5:]
    l2 = l2[:10] + _letras(l2[10:13]) + l2[13:]
    renglones = [l1, l2]
    resultado: Dict[str, Any] = {
        "lineas": renglones,
        "tipo_documento": l1[0:2].replace("<", ""),
        "pais_emisor": l1[2:5].replace("<", ""),
        "numero_pasaporte": l2[0:9].replace("<", ""),
        "nacionalidad": l2[10:13].replace("<", ""),
        "numero_personal": l2[28:42].replace("<", ""),
    }

    if (f := _fecha_mrz(l2[13:19])):
        resultado["fecha_nacimiento"] = f
    if l2[20] in ("M", "F", "X"):
        resultado["sexo"] = l2[20]
    if (f := _fecha_mrz(l2[21:27], es_vigencia=True)):
        resultado["fecha_vencimiento"] = f

    # Renglón 1: APELLIDOS<<NOMBRES, apellidos separados por un solo '<'
    partes = l1[5:].split("<<")
    apellidos = [p for p in partes[0].split("<") if p]
    if apellidos:
        resultado["apellido_paterno"] = apellidos[0]
    if len(apellidos) > 1:
        resultado["apellido_materno"] = " ".join(apellidos[1:])
    if len(partes) > 1:
        nombres = [p for p in partes[1].split("<") if p]
        if nombres:
            resultado["nombres"] = " ".join(nombres)

    # Si el renglón no deja relleno al final, el nombre venía abreviado
    resultado["nombre_abreviado"] = not l1.endswith("<")

    digitos = _verificar_digitos(l2)
    resultado["digitos_control"] = digitos
    resultado["integra"] = all(digitos.values())

    # Una llave sin dato se omite: nunca una cadena vacía (el número personal, por
    # ejemplo, viene vacío en casi todos los pasaportes que no son mexicanos).
    return {k: v for k, v in resultado.items() if v != ""}


# --------------------------------------------------------------------------

def _apellido_completo(paterno: Any, materno: Any) -> str:
    """El apellido entero como secuencia de palabras, sin acentos y en mayúsculas.
    Es lo único que se puede comparar con certeza: en la MRZ el espacio de un
    apellido compuesto y el separador entre apellidos son el mismo '<'."""
    junto = " ".join(str(x) for x in (paterno, materno) if x)
    return ine._sin_acentos(" ".join(junto.upper().split()))


def _codigo_pais(valor: Any) -> str:
    """Un código de país en forma comparable: mayúsculas, sin '<' ni espacios, y con letras
    donde el OCR puso dígitos."""
    return _letras(re.sub(r"[\s<]", "", str(valor or "").upper()))


def _es_codigo_pais(valor: Any) -> bool:
    return bool(re.fullmatch(r"[A-Z]{3}", _codigo_pais(valor)))


def _mismo_pais(a: Any, b: Any) -> bool:
    """Si dos códigos de país son el mismo país. Casi siempre es igualdad; las dos
    excepciones son de la propia norma ICAO 9303 y por eso no son un catálogo: Alemania se
    escribe 'D' en la MRZ (y 'DEU' en cualquier otra parte), y el Reino Unido tiene varios
    códigos de nacionalidad (GBR, GBD, GBN, GBO, GBP, GBS) para el mismo pasaporte británico."""
    a, b = _codigo_pais(a), _codigo_pais(b)
    if not a or not b:
        return False
    return a == b or {a, b} == {"D", "DEU"} or (a[:2] == b[:2] == "GB")


def validar(datos: Dict[str, Any], mrz: Dict[str, Any], nacionalidad_codigo: Any = None) -> Dict[str, Any]:
    """Contrasta la hoja de datos contra la MRZ.

    El TD3 repite casi toda la hoja de datos, así que es la referencia natural.
    Mismo criterio que en la INE: `ok` dice que nada se contradijo y `completo`
    que todo se pudo revisar, separados porque un dato ilegible no es lo mismo
    que una identidad inconsistente.

    `nacionalidad_codigo` es el código ICAO que Document AI infirió de la nacionalidad
    impresa (campo derivado, no se entrega). Solo sirve para esa comparación.
    """
    coincidencias: List[str] = []
    discrepancias: List[str] = []
    omitidas: List[str] = []

    # El país decide cómo se lee la 'M' del sexo impreso (ver `_sexo`).
    pais = datos.get("pais_emisor") or mrz.get("pais_emisor")

    def comparar(etiqueta: str, a: Any, b: Any, truncable: bool = False, plegar: bool = False):
        if a in (None, "") or b in (None, ""):
            omitidas.append(etiqueta)
            return
        av, bv = str(a).upper(), str(b).upper()
        if plegar:
            # La MRZ no admite acentos ni Ñ (el estándar ICAO los quita y la Ñ
            # pasa a N), pero la hoja de datos sí los imprime: 'GARCÍA' contra
            # 'GARCIA' es el mismo apellido, no una contradicción.
            av, bv = ine._sin_acentos(av), ine._sin_acentos(bv)
        if av == bv:
            coincidencias.append(etiqueta)
        elif truncable and av.startswith(bv):
            coincidencias.append(f"{etiqueta} (abreviado en la MRZ)")
        else:
            discrepancias.append(f"{etiqueta}: hoja de datos '{a}' vs '{b}'")

    if mrz.get("error"):
        omitidas.append(f"MRZ completa ({mrz['error']})")
    elif not mrz:
        omitidas.append("MRZ completa (no se extrajo)")
    elif not mrz.get("integra"):
        fallan = [k for k, v in mrz.get("digitos_control", {}).items() if not v]
        discrepancias.append(
            f"MRZ corrupta: fallan los dígitos de control de {', '.join(fallan)}; "
            f"no se pudo contrastar la hoja de datos contra el reverso"
        )
        omitidas.append("todas las comprobaciones contra la MRZ")
    else:
        coincidencias.append("MRZ (dígitos de control)")
        abreviado = bool(mrz.get("nombre_abreviado"))
        comparar("numero_pasaporte (MRZ)", datos.get("numero_pasaporte"), mrz.get("numero_pasaporte"))
        comparar("fecha_nacimiento (MRZ)", datos.get("fecha_nacimiento"), mrz.get("fecha_nacimiento"))
        # Si el sexo impreso es ambiguo (una 'M' en un pasaporte mexicano) se calla:
        # no se compara ni se reporta como falta.
        sexo_h = _sexo(datos.get("sexo"), pais)
        if sexo_h is not None or not datos.get("sexo"):
            comparar("sexo (MRZ)", sexo_h, mrz.get("sexo"))
        comparar("fecha_vencimiento (MRZ)", datos.get("fecha_vencimiento"), mrz.get("fecha_vencimiento"))
        # Nacionalidad. La hoja imprime un gentilicio o el nombre del país ('MEXICANA',
        # '中国/CHINESE') y la MRZ un código ICAO de tres letras ('MEX'): comparar los textos
        # nunca coincide. Medido sobre 39 pasaportes reales, así marcaba discrepancia a 29.
        # Se compara el CÓDIGO: el impreso si ya lo es, y si no el que Document AI infirió.
        # Solo se usa el inferido cuando HAY nacionalidad impresa: el modelo ve también la
        # MRZ, y sin esa condición la comparación sería una tautología (la MRZ contra la MRZ).
        impreso, en_mrz = datos.get("nacionalidad"), mrz.get("nacionalidad")
        codigo = impreso if _es_codigo_pais(impreso) else (
            nacionalidad_codigo if _es_codigo_pais(nacionalidad_codigo) else None)
        if impreso and en_mrz and codigo:
            if _mismo_pais(codigo, en_mrz):
                coincidencias.append("nacionalidad (MRZ)")
            else:
                visto = impreso if codigo is impreso else f"{impreso} ({_codigo_pais(codigo)})"
                discrepancias.append(f"nacionalidad (MRZ): hoja de datos '{visto}' vs '{en_mrz}'")
        else:
            omitidas.append("nacionalidad (MRZ)")
        # Solo hay segundo apellido en algunos países. Si NINGUNA de las dos
        # fuentes lo trae no hay nada que comparar, y registrarlo como "sin
        # verificar" mandaba a 212 todo pasaporte de un solo apellido.
        hay_materno = bool(datos.get("apellido_materno") or mrz.get("apellido_materno"))
        completo_h = _apellido_completo(datos.get("apellido_paterno"), datos.get("apellido_materno"))
        completo_m = _apellido_completo(mrz.get("apellido_paterno"), mrz.get("apellido_materno"))
        if completo_h and completo_m and (
                completo_h == completo_m or (abreviado and completo_h.startswith(completo_m))):
            # Mismo apellido aunque repartido distinto entre paterno y materno
            # ('DE OLIVEIRA' / 'SIMAO' contra la MRZ 'DE' / 'OLIVEIRA SIMAO').
            sufijo = "" if completo_h == completo_m else " (abreviado en la MRZ)"
            coincidencias.append(f"apellido_paterno (MRZ){sufijo}")
            if hay_materno:
                coincidencias.append(f"apellido_materno (MRZ){sufijo}")
        else:
            comparar("apellido_paterno (MRZ)", datos.get("apellido_paterno"),
                     mrz.get("apellido_paterno"), truncable=abreviado, plegar=True)
            if hay_materno:
                comparar("apellido_materno (MRZ)", datos.get("apellido_materno"),
                         mrz.get("apellido_materno"), truncable=abreviado, plegar=True)
        comparar("nombres (MRZ)", datos.get("nombres"), mrz.get("nombres"), truncable=abreviado,
                 plegar=True)

        # El número personal del TD3 es donde México pone la CURP. No siempre
        # cabe completa (el campo mide 14 y la CURP 18), así que se compara por
        # prefijo y solo cuando el documento trae las dos cosas.
        curp = re.sub(r"[^A-Z0-9]", "", (datos.get("curp") or "").upper())
        personal = (mrz.get("numero_personal") or "").upper()
        if curp and personal:
            if curp.startswith(personal) or personal.startswith(curp):
                coincidencias.append("curp vs número personal (MRZ)")
            else:
                discrepancias.append(
                    f"curp: '{curp}' no corresponde al número personal de la MRZ '{personal}'")

    # La CURP codifica fecha de nacimiento y sexo, igual que en la INE
    curp = re.sub(r"[^A-Z0-9]", "", (datos.get("curp") or "").upper())
    if re.fullmatch(r"[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d", curp):
        if (f := _fecha_mrz(curp[4:10])):
            comparar("fecha_nacimiento (CURP)", datos.get("fecha_nacimiento"), f)
        # En la CURP 'H' es hombre y 'M' es mujer; en la MRZ son 'M' y 'F'.
        sexo_h = _sexo(datos.get("sexo"), pais)
        if sexo_h is not None or not datos.get("sexo"):
            comparar("sexo (CURP)", sexo_h, "F" if curp[10] == "M" else "M")
    elif curp:
        omitidas.append(f"CURP (formato inválido: '{curp}')")

    return {
        "ok": not discrepancias,
        "completo": not omitidas,
        "coincidencias": coincidencias,
        "discrepancias": discrepancias,
        "sin_verificar": omitidas,
    }


def _sexo(valor: Any, pais: Optional[str] = None) -> Optional[str]:
    """Normaliza el sexo impreso al código de una letra de la MRZ (M / F / X).

    Devuelve None cuando no se puede decir con certeza, y quien lo llama debe
    CALLAR en ese caso en vez de comparar.

    La trampa está en la 'M': en español significa mujer y en el estándar
    internacional (que usan casi todos los pasaportes y siempre la MRZ) significa
    male. Un pasaporte mexicano es el único caso donde la 'M' impresa es
    ambigua para esta función, porque no se sabe cuál de las dos convenciones
    imprime; en cualquier otro país es 'male'. Una versión anterior asumía que
    'M' era siempre mujer: daba una discrepancia falsa con cada pasaporte de
    hombre extranjero, y la comparación contra la CURP estaba invertida.

    Lo inequívoco ('H', 'F', 'X', y las palabras completas) se traduce siempre.
    """
    if not isinstance(valor, str) or not valor.strip():
        return None
    v = valor.strip().upper().rstrip(".")
    if v in ("H", "HOMBRE", "MALE", "MASCULINO"):
        return "M"
    if v in ("F", "FEMALE", "MUJER", "FEMENINO"):
        return "F"
    if v == "X":
        return "X"
    if v == "M":
        return None if (pais or "").upper() == "MEX" else "M"
    return None


def armar_respuesta(entidades: Dict[str, Any],
                    repetidos: Optional[Dict[str, int]] = None,
                    texto_ocr: str = "") -> Dict[str, Any]:
    """Arma la respuesta de /pasaporte, con la misma forma que la de /ine."""
    avisos: List[str] = []

    datos: Dict[str, Any] = {}
    for campo in CAMPOS_DATOS:
        if campo in entidades:
            datos[campo] = limpiar_texto(entidades[campo])

    crudo_mrz = entidades.get("mrz") or ""
    origen = "entidad"
    if not crudo_mrz and texto_ocr:
        crudo_mrz = mrz_en_texto(texto_ocr)
        origen = "texto"
        if crudo_mrz:
            logger.info("La MRZ no vino como entidad; se recuperó del texto del OCR.")

    mrz = parsear_mrz(crudo_mrz)
    if mrz and not mrz.get("error"):
        mrz["origen"] = origen

    for tipo, veces in (repetidos or {}).items():
        if tipo in CAMPOS_DERIVADOS:
            continue
        avisos.append(
            f"el PDF contiene {veces} bloques '{tipo}': se conservó el primero, "
            f"revisa que el documento no traiga más de una identidad")

    if texto_ocr:
        for campo, valor in datos.items():
            if campo in CAMPOS_DERIVADOS:
                continue
            if isinstance(valor, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
                continue
            if isinstance(valor, str) and len(valor) > 3 and valor not in texto_ocr:
                avisos.append(f"datos.{campo}='{valor}' no aparece literal en el texto leído")

    return {
        "datos": datos,
        "mrz": mrz,
        "validacion": validar(datos, mrz, limpiar_texto(entidades.get("nacionalidad_codigo"))),
        "avisos": avisos,
    }
