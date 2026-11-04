"""Reverso de la INE: arma la respuesta de /procesa_ine_atras.

El reverso de la credencial trae la MRZ (TD1, tres renglones de 30). No hay un
extractor de campos para esta cara: la MRZ es de los pocos datos que se pueden
localizar en el texto del OCR sin depender de ningún modelo, porque son tres
renglones de forma rígida y con dígitos verificadores que la validan a sí misma.
Por eso aquí solo hace falta el texto que Document AI leyó, y el parseo y los
dígitos de control son los de `ine.py`, que no se copian para que una corrección
valga para las dos rutas.

Sola, esta cara solo puede comprobar que la MRZ sea íntegra. La comprobación que
cacha errores de lectura de verdad es contrastarla contra el anverso, y eso lo
hace `/procesa_ine/` cuando recibe también el archivo `atras`.
"""
import logging
from typing import Any, Dict, List, Optional

import ine

logger = logging.getLogger(__name__)


def armar_respuesta(texto_ocr: str) -> Optional[Dict[str, Any]]:
    """
    Devuelve la respuesta del reverso, o None si el texto no trae ninguna MRZ.

    El None es a propósito y no un resultado vacío: significa "este archivo no es
    el reverso de una INE" (lo más común es que hayan mandado el frente), y el
    endpoint lo reporta con su propio desenlace en vez de una respuesta que el
    consumidor tendría que interpretar.
    """
    crudo = ine._mrz_en_texto(texto_ocr)
    if not crudo:
        return None

    mrz = ine.parsear_mrz(crudo)
    if mrz and not mrz.get("error"):
        mrz["origen"] = "texto"

    coincidencias: List[str] = []
    discrepancias: List[str] = []
    omitidas: List[str] = []

    if mrz.get("error"):
        omitidas.append(f"MRZ completa ({mrz['error']})")
    elif not mrz.get("integra"):
        fallan = [k for k, v in mrz.get("digitos_control", {}).items() if not v]
        discrepancias.append(
            f"MRZ corrupta: fallan los dígitos de control de {', '.join(fallan)}")
    else:
        coincidencias.append("MRZ (dígitos de control)")

    return {
        "atras": {"mrz": mrz},
        "validacion": {
            "ok": not discrepancias,
            "completo": not omitidas,
            "coincidencias": coincidencias,
            "discrepancias": discrepancias,
            "sin_verificar": omitidas,
        },
        "avisos": [],
    }
