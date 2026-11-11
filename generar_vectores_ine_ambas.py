"""Genera los vectores de prueba de /procesa_ine_ambas/ para el handoff.

    python generar_vectores_ine_ambas.py            # escribe HANDOFF_INE_AMBAS_vectores.json
    python generar_vectores_ine_ambas.py --ver      # además imprime un resumen por caso

Corre el endpoint REAL (rutas, guardias, validación, armado de la respuesta) con
dos piezas sustituidas: la llamada a Document AI y el clasificador. A cambio de
eso no necesita red ni credenciales, y el resultado es exacto: cada
`salida_esperada` es lo que RAD respondió para esa entrada, no algo escrito a mano.

Todos los datos son ficticios. Las identidades se construyen aquí mismo (CURP con
su dígito verificador, clave de elector coherente con la CURP, MRZ con sus dígitos
de control), de modo que los casos "válidos" lo son de verdad.

Si cambia la lógica de `ine.py`, `ine_atras.py`, `codigos.py` o `archivo.py`, hay
que volver a correr este script y revisar el diff del JSON: ese diff ES el cambio
de contrato.
"""
import json
import os
import re
import sys
import unicodedata
from datetime import date

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)

import logging
logging.disable(logging.CRITICAL)

from fastapi.testclient import TestClient

import clasificador
import funciones
import ine
import app as app_rad

SALIDA = os.path.join(RAIZ, "HANDOFF_INE_AMBAS_vectores.json")
FIRMA_PNG = b"\x89PNG\r\n\x1a\n"

# --------------------------------------------------------------------------
# Construcción de identidades ficticias coherentes
# --------------------------------------------------------------------------

PARTICULAS = {"DA", "DAS", "DE", "DEL", "DER", "DI", "DIE", "DD", "EL", "LA", "LOS", "LAS",
              "LE", "LES", "MAC", "MC", "VAN", "VON", "Y"}
VOCALES = set("AEIOU")


def palabras(texto):
    t = unicodedata.normalize("NFD", texto.upper())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Z ]", "", t).split()


def nucleo_apellido(apellido):
    """La CURP no cuenta las partículas: 'DE LA CRUZ' se codifica como 'CRUZ'."""
    ps = palabras(apellido)
    while len(ps) > 1 and ps[0] in PARTICULAS:
        ps.pop(0)
    return ps[0]


def nucleo_nombre(nombre):
    ps = palabras(nombre)
    if len(ps) > 1 and ps[0] in ("MARIA", "MA", "JOSE", "J"):
        return ps[1]
    return ps[0]


def consonante(p):
    return next((c for c in p[1:] if c not in VOCALES), "X")


def vocal(p):
    return next((c for c in p[1:] if c in VOCALES), "X")


def dc(cadena):
    return ine.digito_control(cadena)


def persona(paterno, materno, nombre, nac, sexo, estado_nac, estado_clave, seccion,
            cic, ident, vig_anio, emision, domicilio, anio_registro=None):
    """Todos los datos de una credencial, coherentes entre sí."""
    pat, mat, nom = nucleo_apellido(paterno), nucleo_apellido(materno), nucleo_nombre(nombre)
    yymmdd = nac.strftime("%y%m%d")
    base = (pat[0] + vocal(pat) + mat[0] + nom[0] + yymmdd + sexo + estado_nac
            + consonante(pat) + consonante(mat) + consonante(nom) + ("A" if nac.year >= 2000 else "0"))
    curp = base + str(ine.digito_verificador_curp(base))
    clave = (pat[0] + consonante(pat) + mat[0] + consonante(mat) + nom[0] + consonante(nom)
             + yymmdd + estado_clave + sexo + "123")
    assert clave[:6] == ine._iniciales_desde_curp(curp)
    return dict(paterno=paterno, materno=materno, nombre=nombre, nac=nac, sexo=sexo,
                curp=curp, clave=clave, seccion=seccion, cic=cic, ident=ident,
                vig_anio=vig_anio, emision=emision, domicilio=domicilio,
                anio_registro=anio_registro or f"{max(nac.year + 18, 2010)} {emision}")


def lineas_mrz(p, alterar_digito=False):
    """Los tres renglones de la MRZ (TD1) con sus dígitos de control."""
    l1 = "IDMEX" + p["cic"] + str(dc(p["cic"])) + "<<" + p["seccion"] + p["ident"]
    nac = p["nac"].strftime("%y%m%d")
    vig = f"{p['vig_anio'] % 100:02d}1231"
    cabeza = nac + str(dc(nac)) + p["sexo"] + vig + str(dc(vig)) + "MEX" + "<" + p["emision"] + "<" * 8
    compuesto = l1[5:30] + cabeza[0:7] + cabeza[8:15] + cabeza[18:29]
    l2 = cabeza + str(dc(compuesto))
    ap = "<".join(palabras(p["paterno"]))
    am = "<".join(palabras(p["materno"]))
    nombres = "<".join(palabras(p["nombre"]))
    l3 = (f"{ap}<{am}<<{nombres}").ljust(30, "<")[:30]
    if alterar_digito:
        # Se cambia el dígito verificador de la fecha de nacimiento
        l2 = l2[:6] + str((int(l2[6]) + 1) % 10) + l2[7:]
    return [l1, l2, l3]


def texto_frente(p, curp=True, formato="nuevo", claves=None, nombre_ocr=None):
    d = p["domicilio"]
    lineas = ["INSTITUTO NACIONAL ELECTORAL", "CREDENCIAL PARA VOTAR", "MEXICO", "NOMBRE",
              p["paterno"], p["materno"], nombre_ocr or p["nombre"], "DOMICILIO", d["calle_numero"],
              f"COL {d['colonia']} {d['codigo_postal']}", f"{d['municipio_delegacion']}, {d['estado']}",
              "CLAVE DE ELECTOR " + p["clave"]]
    if curp:
        lineas.append("CURP " + p["curp"])
    lineas += ["AÑO DE REGISTRO " + p["anio_registro"],
               "FECHA DE NACIMIENTO " + p["nac"].strftime("%d/%m/%Y"),
               "SEXO " + p["sexo"]]
    if formato == "anterior":
        lineas.append("ESTADO {estado} MUNICIPIO {municipio} LOCALIDAD {localidad} EMISION {emision}".format(**claves))
    lineas += ["SECCION " + p["seccion"], f"VIGENCIA {p['vig_anio']}"]
    return "\n".join(lineas)


def texto_atras(p, **kw):
    return "\n".join(["INE", "IDENTIFICADOR DE LA CREDENCIAL"] + lineas_mrz(p, **kw))


def campos_frente(p, curp=True, claves=None, **sobre):
    """Lo que un motor de lectura devuelve para el anverso, con los nombres del contrato."""
    c = {
        "apellido_paterno": p["paterno"], "apellido_materno": p["materno"], "nombre": p["nombre"],
        "domicilio": dict(p["domicilio"]), "clave_elector": p["clave"],
    }
    if curp:
        c["curp"] = p["curp"]
    c.update({"fecha_nacimiento": p["nac"].isoformat(), "sexo": p["sexo"],
              "anio_registro": p["anio_registro"], "seccion": p["seccion"]})
    if claves:
        c.update({"estado_clave": claves["estado"], "municipio_clave": claves["municipio"],
                  "localidad_clave": claves["localidad"], "anio_emision": claves["emision"]})
    else:
        c["numero_emision"] = p["emision"]
    c["vigencia"] = str(p["vig_anio"])
    c["pais"] = "MEXICO"
    c.update(sobre)
    return c


# --------------------------------------------------------------------------
# Sustitutos de Document AI y del clasificador
# --------------------------------------------------------------------------

DAI = {}      # contenido del archivo -> JSON que devolvería Document AI
CLASIF = {}   # contenido del archivo -> veredicto del clasificador


def a_entidades(campos, repetidos=None):
    ents = []
    for k, v in campos.items():
        if k == "domicilio":
            ents.append({"type": "domicilio",
                         "properties": [{"type": kk, "mentionText": vv} for kk, vv in v.items()]})
        elif k == "fecha_nacimiento":
            y, m, d = (int(x) for x in v.split("-"))
            ents.append({"type": k, "mentionText": f"{d:02d}/{m:02d}/{y}",
                         "normalizedValue": {"dateValue": {"year": y, "month": m, "day": d}}})
        else:
            ents.append({"type": k, "mentionText": v})
    for tipo, veces in (repetidos or {}).items():
        for _ in range(veces - 1):
            ents.append({"type": tipo, "mentionText": "bloque repetido"})
    return ents


async def dai_falso(contenido, mime_type, doc_type):
    return DAI[contenido]


async def clasificar_falso(contenido, mime_type):
    return CLASIF[contenido]


funciones._llama_document_ai = dai_falso
clasificador.clasificar = clasificar_falso

cliente = TestClient(app_rad.app)


def veredicto(categoria="ine", confianza=0.9999, texto=""):
    return {"categoria": categoria, "confianza": confianza, "caracteres_leidos": len(texto.strip())}


def archivo_falso(id_archivo, campos, texto, repetidos=None, clasif=None):
    """Registra un archivo de imagen ficticio y lo que 'leería' cada servicio."""
    contenido = FIRMA_PNG + id_archivo.encode()
    DAI[contenido] = {"document": {"text": texto, "entities": a_entidades(campos, repetidos)}}
    CLASIF[contenido] = clasif or veredicto("ine", 0.9999, texto)
    return contenido


# --------------------------------------------------------------------------
# Casos
# --------------------------------------------------------------------------

DOM_JAL = {"calle_numero": "CALLE ALAMEDA 120", "colonia": "LOMAS DE SAN JUAN", "codigo_postal": "44100",
           "municipio_delegacion": "GUADALAJARA", "estado": "JAL"}
DOM_CDMX = {"calle_numero": "AV INSURGENTES 2050 INT 4", "colonia": "DEL VALLE", "codigo_postal": "06600",
            "municipio_delegacion": "CUAUHTEMOC", "estado": "CDMX"}
DOM_GTO = {"calle_numero": "PRIV DE LOS PINOS 8", "colonia": "CENTRO", "codigo_postal": "36000",
           "municipio_delegacion": "GUANAJUATO", "estado": "GTO"}

A = persona("MORALES", "VEGA", "DANIELA ITZEL", date(1991, 7, 23), "M", "JC", "14", "0817",
            "203451987", "112233445", 2032, "01", DOM_JAL, "2014 01")
B = persona("GÓMEZ", "MARTÍNEZ", "JOSÉ LUIS", date(1985, 11, 4), "H", "DF", "09", "4210",
            "318274650", "556677889", 2030, "02", DOM_CDMX, "2012 02")
C = persona("ORTEGA", "LUNA", "SERGIO", date(1970, 5, 12), "H", "GT", "11", "1530",
            "471920386", "998877665", 2024, "02", DOM_GTO, "2008 02")
D = persona("BERNARDINO", "HERNANDEZ", "JUAN GUILLERMO", date(1979, 2, 18), "H", "MC", "15", "2290",
            "507318246", "123987456", 2033, "00", DOM_CDMX, "2016 00")
E = persona("DE LA CRUZ", "HERNANDEZ", "MARIO", date(1988, 9, 30), "H", "DF", "09", "3345",
            "682045913", "445566778", 2031, "01", DOM_CDMX, "2013 01")

CASOS = []


def caso(id_caso, descripcion, frente, atras, http=200, no_comparar=None, entrada_extra=None):
    """Ejecuta una petición contra el endpoint real y guarda entrada y salida."""
    r = cliente.post("/procesa_ine_ambas/", files={
        "frente": ("frente.png", frente, "image/png"),
        "atras": ("atras.png", atras, "image/png"),
    })
    CASOS.append({"id": id_caso, "descripcion": descripcion, "http_status": r.status_code,
                  "no_comparar": no_comparar or [], "salida_esperada": r.json(), "_f": frente, "_a": atras,
                  "_extra": entrada_extra or {}})


def par(id_caso, descripcion, p_frente, p_atras=None, campos=None, repetidos=None, texto_f=None,
        texto_a=None, claves=None, formato="nuevo", curp=True, **kw):
    """Arma el par de archivos de un caso típico: anverso de `p_frente`, reverso de `p_atras`."""
    p_atras = p_atras or p_frente
    tf = texto_f if texto_f is not None else texto_frente(p_frente, curp=curp, formato=formato, claves=claves)
    ta = texto_a if texto_a is not None else texto_atras(p_atras)
    cf = campos if campos is not None else campos_frente(p_frente, curp=curp, claves=claves)
    f = archivo_falso(f"{id_caso}-frente", cf, tf, repetidos)
    a = archivo_falso(f"{id_caso}-atras", {}, ta)
    caso(id_caso, descripcion, f, a, **kw)
    CASOS[-1]["_entrada"] = {"frente": {"campos": cf, "texto_ocr": tf, "repetidos": repetidos or {}},
                             "atras": {"texto_ocr": ta}}


# 1-5: documentos válidos, uno por variante
par("01_valido_formato_nuevo",
    "Credencial de formato nuevo, todo coherente: las 16 comprobaciones cuadran.", A)
par("02_valido_con_acentos",
    "El anverso imprime acentos (GÓMEZ, MARTÍNEZ, JOSÉ) y la MRZ no los admite: es el mismo dato, no una discrepancia.", B)
CLAVES_C = {"estado": "11", "municipio": "015", "localidad": "0001", "emision": "2008"}
par("03_valido_formato_anterior",
    "Credencial de formato anterior: trae el renglón ESTADO/MUNICIPIO/LOCALIDAD/EMISIÓN, así que no hay `no_impresos`.",
    C, claves=CLAVES_C, formato="anterior")
par("04_nombre_truncado_en_la_mrz",
    "El nombre completo no cabe en los 30 caracteres del renglón 3: la MRZ lo abrevia (`campo_truncado`) y el prefijo cuenta como coincidencia.", D)
par("05_apellido_compuesto",
    "Apellido compuesto 'DE LA CRUZ': en la MRZ el espacio y el separador entre apellidos son el mismo '<', así que se comparan las palabras.", E)

# 6-13: lo que cambia el código de desenlace
clave_mal = A["clave"][:3] + "U" + A["clave"][4:]   # una L/N leída como U, por ejemplo
pm = dict(A, clave=clave_mal)
par("06_error_de_ocr_en_clave_de_elector",
    "Una letra de la clave de elector mal leída: el modelo no lo nota, el cruce contra la CURP sí. Sale 210.",
    A, campos=campos_frente(pm), texto_f=texto_frente(pm))
pm = dict(A, paterno="MORALESS")
par("07_nombre_corregido_con_la_mrz",
    "El OCR pegó una letra de más ('MORALESS'): la MRZ es íntegra y trae 'MORALES', se corrige y se avisa. Sale 211.",
    A, campos=campos_frente(pm), texto_f=texto_frente(pm))
par("08_mrz_corrupta",
    "Un dígito de control de la MRZ no cuadra: se reporta la corrupción UNA vez y se deja de contrastar contra ella. Sale 210.",
    A, texto_a=texto_atras(A, alterar_digito=True))
par("09_reverso_de_otra_persona",
    "El reverso es de otra persona: todo lo que se contrasta contra la MRZ discrepa. Sale 210.", A, B)
par("10_sin_curp",
    "El anverso no trae CURP legible: no hay con qué hacer los cruces contra ella. Sale 212.", A, curp=False)
pm = dict(A, domicilio=dict(DOM_JAL, estado="CDMX"))
par("11_codigo_postal_no_corresponde_al_estado",
    "El código postal 44100 es de Jalisco y el domicilio dice CDMX. Sale 210.",
    A, campos=campos_frente(pm), texto_f=texto_frente(pm))
par("12_pdf_con_mas_de_un_documento",
    "El motor devolvió dos bloques 'domicilio' (un PDF con más de una identidad): se conserva el primero y se avisa. Sale 211.",
    A, repetidos={"domicilio": 2})
par("13_valor_que_no_aparece_en_el_texto",
    "El nombre extraído no aparece literal en el texto que leyó el OCR (el motor lo compuso): se avisa. Sale 211.",
    A, texto_f=texto_frente(A, nombre_ocr="DANIELA ITZEI"))

# 14-15: el archivo no es la cara que dice ser
ta = texto_atras(A)
f = archivo_falso("14-frente", campos_frente(A), texto_frente(A))
a = archivo_falso("14-atras", {}, texto_frente(A))
caso("14_atras_sin_mrz",
     "En `atras` mandaron otra copia del frente: no hay MRZ. Responde 227 (`atras` es el campo con problema).",
     f, a, no_comparar=["clasificacion"],
     entrada_extra={"frente": {"campos": campos_frente(A), "texto_ocr": texto_frente(A)},
                    "atras": {"texto_ocr": texto_frente(A)}})
CASOS[-1]["_entrada"] = CASOS[-1].pop("_extra")
sin_nada = {"apellido_paterno": "MORALES", "fecha_nacimiento": "1991-07-23", "sexo": "M"}
f = archivo_falso("15-frente", sin_nada, ta)
a = archivo_falso("15-atras", {}, ta)
caso("15_frente_es_un_reverso",
     "En `frente` mandaron el reverso: trae MRZ pero ningún dato del anverso. Responde 228.",
     f, a)
CASOS[-1]["_entrada"] = {"frente": {"campos": sin_nada, "texto_ocr": ta}, "atras": {"texto_ocr": ta}}

# 16-18: el clasificador rechaza
otro = "FACTURA 0001  Total a pagar $1,250.00  Cliente ACME SA de CV"
f = archivo_falso("16-frente", {}, otro, clasif=veredicto("otro", 0.9999, otro))
a = archivo_falso("16-atras", {}, ta)
caso("16_frente_no_es_ine", "El `frente` es una factura: el clasificador lo rechaza antes de extraer. Responde 220.", f, a)
CASOS[-1]["_entrada"] = {"frente": {"texto_ocr": otro, "clasificacion": veredicto("otro", 0.9999, otro)},
                         "atras": {"texto_ocr": ta}}
f = archivo_falso("17-frente", {}, "", clasif={"categoria": "hoja_en_blanco", "confianza": None, "caracteres_leidos": 0})
a = archivo_falso("17-atras", {}, ta)
caso("17_frente_en_blanco", "El `frente` viene en blanco (0 caracteres leídos). Responde 221.", f, a)
CASOS[-1]["_entrada"] = {"frente": {"texto_ocr": "", "clasificacion": {"categoria": "hoja_en_blanco", "confianza": None, "caracteres_leidos": 0}},
                         "atras": {"texto_ocr": ta}}
f = archivo_falso("18-frente", campos_frente(A), texto_frente(A))
a = archivo_falso("18-atras", {}, otro, clasif=veredicto("otro", 0.9999, otro))
caso("18_atras_no_es_ine", "El `atras` es una factura. Responde 220 con el mensaje prefijado por el campo.", f, a)
CASOS[-1]["_entrada"] = {"frente": {"campos": campos_frente(A), "texto_ocr": texto_frente(A)},
                         "atras": {"texto_ocr": otro, "clasificacion": veredicto("otro", 0.9999, otro)}}

# 19-24: guardias del archivo y de la petición (no dependen del motor de lectura)
PNG_OK = archivo_falso("ok-frente", campos_frente(A), texto_frente(A))
PNG_ATRAS = archivo_falso("ok-atras", {}, texto_atras(A))
PDF_CIFRADO = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<< /Encrypt 5 0 R /Root 1 0 R >>\nstartxref\n0\n%%EOF"
PDF_TRUNCADO = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<< /Root 1 0 R >>\n"


def caso_http(id_caso, descripcion, files, entrada, no_comparar=None):
    r = cliente.post("/procesa_ine_ambas/", files=files)
    CASOS.append({"id": id_caso, "descripcion": descripcion, "http_status": r.status_code,
                  "no_comparar": no_comparar or [], "salida_esperada": r.json(), "_entrada": entrada})


caso_http("19_tipo_de_archivo_no_soportado", "El `frente` se declara como text/plain. HTTP 400.",
          {"frente": ("f.txt", b"hola", "text/plain"), "atras": ("a.png", PNG_ATRAS, "image/png")},
          {"frente": {"tipo_declarado": "text/plain", "contenido": "texto"}, "atras": {"tipo_declarado": "image/png", "contenido": "imagen válida"}})
caso_http("20_archivo_vacio", "El `atras` llega vacío (0 bytes). Responde 222.",
          {"frente": ("f.png", PNG_OK, "image/png"), "atras": ("a.png", b"", "image/png")},
          {"frente": {"tipo_declarado": "image/png", "contenido": "imagen válida"}, "atras": {"tipo_declarado": "image/png", "contenido": "0 bytes"}})
caso_http("21_formato_no_reconocido", "El `frente` dice ser PDF pero sus bytes no lo son. Responde 222.",
          {"frente": ("f.pdf", b"no soy un pdf", "application/pdf"), "atras": ("a.png", PNG_ATRAS, "image/png")},
          {"frente": {"tipo_declarado": "application/pdf", "contenido": "los bytes 'no soy un pdf'"}, "atras": {"tipo_declarado": "image/png", "contenido": "imagen válida"}})
caso_http("22_pdf_con_contrasena", "El `frente` es un PDF cifrado (trae /Encrypt). Responde 223.",
          {"frente": ("f.pdf", PDF_CIFRADO, "application/pdf"), "atras": ("a.png", PNG_ATRAS, "image/png")},
          {"frente": {"tipo_declarado": "application/pdf", "contenido": "PDF con /Encrypt en el trailer"}, "atras": {"tipo_declarado": "image/png", "contenido": "imagen válida"}})
caso_http("23_pdf_truncado", "El `atras` es un PDF al que le falta el final (%%EOF). Responde 222.",
          {"frente": ("f.png", PNG_OK, "image/png"), "atras": ("a.pdf", PDF_TRUNCADO, "application/pdf")},
          {"frente": {"tipo_declarado": "image/png", "contenido": "imagen válida"}, "atras": {"tipo_declarado": "application/pdf", "contenido": "PDF sin %%EOF ni startxref"}})
caso_http("24_falta_un_campo", "Falta el campo `atras`. HTTP 422. OJO: RAD hoy responde el cuerpo por defecto de FastAPI; el estándar objetivo es el sobre con `codigo` 422 (ver el handoff).",
          {"frente": ("f.png", PNG_OK, "image/png")},
          {"frente": {"tipo_declarado": "image/png", "contenido": "imagen válida"}, "atras": "(no se envía)"},
          no_comparar=["salida_esperada"])

# 29: la MRZ está pero no se puede reconstruir (el OCR perdió el renglón de los nombres)
l1, l2, _ = lineas_mrz(A)
par("29_mrz_ilegible",
    "El reverso trae el encabezado de la MRZ pero solo 2 de los 3 renglones: no se puede interpretar. "
    "`atras.mrz` lleva `error` y `texto_crudo` en lugar de campos, y los cruces contra la MRZ quedan sin verificar. Sale 212.",
    A, texto_a="\n".join(["INE", l1, l2]))

# 25-28: precedencia cuando hay más de un problema
f = archivo_falso("25-frente", sin_nada, ta)                      # el reverso, puesto como frente
a = archivo_falso("25-atras", campos_frente(A), texto_frente(A))  # el frente, puesto como reverso
caso("25_archivos_invertidos",
     "Los dos archivos están cambiados de lugar. Se revisa primero `frente`: responde 228, no 227.", f, a)
CASOS[-1]["_entrada"] = {"frente": {"campos": sin_nada, "texto_ocr": ta},
                         "atras": {"texto_ocr": texto_frente(A)}}

f = archivo_falso("26-frente", {}, "", clasif={"categoria": "hoja_en_blanco", "confianza": None, "caracteres_leidos": 0})
caso_http("26_integridad_gana_a_clasificacion",
          "El `frente` viene en blanco (221) y el `atras` está dañado (222). Los errores del archivo se revisan antes que "
          "la clasificación, así que responde 222 del `atras`.",
          {"frente": ("f.png", f, "image/png"), "atras": ("a.png", b"", "image/png")},
          {"frente": {"tipo_declarado": "image/png", "contenido": "imagen válida que el clasificador da por hoja en blanco"},
           "atras": {"tipo_declarado": "image/png", "contenido": "0 bytes"}})

caso_http("27_dos_archivos_danados",
          "Los dos archivos están dañados. Responde el del `frente`.",
          {"frente": ("f.pdf", b"no soy un pdf", "application/pdf"), "atras": ("a.png", b"", "image/png")},
          {"frente": {"tipo_declarado": "application/pdf", "contenido": "los bytes 'no soy un pdf'"},
           "atras": {"tipo_declarado": "image/png", "contenido": "0 bytes"}})

f = archivo_falso("28-frente", {}, otro, clasif=veredicto("otro", 0.9999, otro))
a = archivo_falso("28-atras", {}, otro, clasif=veredicto("otro", 0.9999, otro))
caso_http("28_los_dos_rechazados_por_el_clasificador",
          "Los dos archivos son una factura. Responde 220 del `frente`.",
          {"frente": ("f.png", f, "image/png"), "atras": ("a.png", a, "image/png")},
          {"frente": {"texto_ocr": otro, "clasificacion": veredicto("otro", 0.9999, otro)},
           "atras": {"texto_ocr": otro, "clasificacion": veredicto("otro", 0.9999, otro)}})

# --------------------------------------------------------------------------
# Salida
# --------------------------------------------------------------------------

for c in CASOS:
    c.pop("_f", None)
    c.pop("_a", None)
    c.pop("_extra", None)
    c["entrada"] = c.pop("_entrada")
    # Se ordenan las llaves para que el diff del JSON se lea
    orden = ["id", "descripcion", "entrada", "http_status", "no_comparar", "salida_esperada"]
    for k in list(c):
        if k not in orden:
            orden.append(k)
    c_ordenado = {k: c[k] for k in orden if k in c}
    c.clear()
    c.update(c_ordenado)

documento = {
    "que_es": "Vectores de prueba de POST /procesa_ine_ambas/ de RAD. Cada caso trae lo que el motor de lectura entrega "
              "(`entrada`: campos extraídos y texto del OCR de cada archivo, más el veredicto del clasificador cuando "
              "aplica) y la respuesta que RAD dio (`salida_esperada`). Datos ficticios.",
    "como_usarlos": "Alimenta tu capa de validación con `entrada` y compara la respuesta con `salida_esperada` "
                    "(igualdad de JSON, sin importar el orden de las llaves). Los casos 19-24 y 26-28 no dependen del motor de "
                    "lectura: son las guardias del archivo y de la petición. `no_comparar` lista lo que el caso no "
                    "exige igualar (ver el handoff).",
    "nunca_comparar": ["clasificacion.confianza", "clasificacion.caracteres_leidos",
                       "clasificacion_atras.confianza", "clasificacion_atras.caracteres_leidos"],
    "clasificador_por_omision": "Salvo que el caso diga otra cosa, el clasificador respondió categoria 'ine' (confianza 0.9999) "
                                "para los dos archivos. `categoria` sí se compara; `confianza` y `caracteres_leidos` no, porque "
                                "dependen del clasificador y del OCR de cada sistema.",
    "generado_con": "generar_vectores_ine_ambas.py",
    "casos": CASOS,
}

with open(SALIDA, "w", encoding="utf-8") as f:
    json.dump(documento, f, ensure_ascii=False, indent=2)
    f.write("\n")

print(f"{len(CASOS)} casos escritos en {os.path.basename(SALIDA)}")
if "--ver" in sys.argv:
    for c in CASOS:
        s = c["salida_esperada"]
        v = s.get("validacion", {}) if isinstance(s, dict) else {}
        print(f"{c['id']:<42} HTTP {c['http_status']} codigo={s.get('codigo')} {s.get('estado', '')}"
              f"  coinc={len(v.get('coincidencias', []))} disc={len(v.get('discrepancias', []))} "
              f"sin={len(v.get('sin_verificar', []))} avisos={len(s.get('avisos', []))}")
