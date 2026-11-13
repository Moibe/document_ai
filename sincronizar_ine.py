"""Mantiene `ine.py` de RAD igualado al de santander_ia.

El `ine.py` de RAD NO se edita a mano: se DERIVA del de santander_ia con dos cambios fijos,

  1. se quita todo lo del sello de cotejo (RAD no lo entrega), y
  2. se compara el anverso contra la MRZ ignorando acentos (santander_ia no lo hace, y
     'GONZÁLEZ' contra 'GONZALEZ' daba una discrepancia falsa).

    python sincronizar_ine.py --verificar          # ¿ine.py sigue siendo la derivación de ULTIMA_SINCRONIZACION?
    python sincronizar_ine.py --aplicar            # deriva del HEAD de santander_ia y reescribe ine.py
    python sincronizar_ine.py --aplicar <commit>   # igual, desde un commit concreto

Después de `--aplicar`: correr `python generar_vectores_ine_ambas.py --verificar`. Si dice que el
contrato cambió es porque santander_ia cambió su validación: revisa el diff del JSON, vuelve a
generarlo, actualiza HANDOFF_INE_AMBAS.md y avisa a quien implementa contra él.

Dónde está santander_ia: variable de entorno SANTANDER_IA, o `../santander_ia`.

`--verificar` es la protección contra el error de siempre: arreglar algo directo en `ine.py`
y que el siguiente `--aplicar` lo borre. Si falla, o el arreglo se hace también en santander_ia, o
se agrega aquí como una transformación más de `derivar`.
"""
import difflib
import os
import re
import subprocess
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
INE_RAD = os.path.join(RAIZ, "ine.py")
SANTANDER = os.environ.get("SANTANDER_IA") or os.path.normpath(os.path.join(RAIZ, "..", "santander_ia"))

# Commit de santander_ia con el que se derivó el `ine.py` actual. `--aplicar` lo actualiza solo.
ULTIMA_SINCRONIZACION = "af35409"


def de_santander(rev):
    r = subprocess.run(["git", "-C", SANTANDER, "show", f"{rev}:ine.py"], capture_output=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(f"No pude leer ine.py de santander_ia en '{rev}' ({SANTANDER}):\n{r.stderr.strip()}")
    return r.stdout.replace("\r\n", "\n")


def commit_de(rev):
    r = subprocess.run(["git", "-C", SANTANDER, "rev-parse", "--short", rev], capture_output=True, encoding="utf-8")
    return r.stdout.strip() or rev


def una_vez(s, texto):
    if s.count(texto) != 1:
        sys.exit(f"El ine.py de santander_ia cambió y esta derivación ya no lo entiende: el fragmento\n"
                 f"    {texto[:90]!r}\n"
                 f"aparece {s.count(texto)} veces y debía aparecer 1. Hay que ajustar `derivar` en {os.path.basename(__file__)}.")


def corta(s, inicio, fin):
    """Quita desde `inicio` hasta justo antes de `fin`."""
    una_vez(s, inicio)
    una_vez(s, fin)
    a, b = s.index(inicio), s.index(fin)
    if a > b:
        sys.exit(f"Los fragmentos {inicio[:40]!r} y {fin[:40]!r} aparecen en otro orden: ajusta `derivar`.")
    return s[:a] + s[b:]


def rep(s, viejo, nuevo):
    una_vez(s, viejo)
    return s.replace(viejo, nuevo)


def derivar(s):
    # --- 1) el sello de cotejo ---
    s = corta(s, "# En el sello el prefijo", "VOCALES = set")
    s = corta(s, "def _normalizar_clave_revisor", "def _anio_completo")
    s = corta(s, "def normalizar_fecha", "def anio_vigencia")
    s = corta(s, "MESES = {", "def _sin_acentos")
    s = corta(s, "    # --- sello de cotejo ---", "    # Las tres comprobaciones de arriba")
    s = corta(s, "    sello: Dict[str, Any] = {}\n    for campo in CAMPOS_SELLO", "    formato = detectar_formato")
    s = rep(s, "def validar(frente: Dict[str, Any], mrz: Dict[str, Any],\n            sello: Dict[str, Any]) -> Dict[str, Any]:",
            "def validar(frente: Dict[str, Any], mrz: Dict[str, Any]) -> Dict[str, Any]:")
    s = rep(s, "validar(frente, mrz, sello)", "validar(frente, mrz)")
    s = rep(s, '        "sello": sello,\n', "")
    s = rep(s, "agrupa las entidades planas que devuelve\nDocument AI en las tres secciones del documento (frente, sello, atras),",
            "agrupa las entidades planas que devuelve\nDocument AI en las dos caras del documento (frente, atras),")
    s = rep(s, "de tres secciones (frente, sello, atras) más el bloque de validación.",
            "de dos secciones (frente, atras) más el bloque de validación.")
    s = rep(s, "hacía que un sello incompleto se viera igual que una identidad inconsistente,\n    # y que 'ok: true'",
            "hacía que una revisión incompleta se viera igual que una identidad inconsistente,\n    # y que 'ok: true'")

    # --- 2) plegado de acentos al comparar contra la MRZ ---
    s = rep(s, '''    if a in (None, "") or b in (None, ""):
        return False
    av, bv = str(a).upper(), str(b).upper()
    return av == bv or (truncable and av.startswith(bv))''', '''    if a in (None, "") or b in (None, ""):
        return False
    av, bv = _plegar(a), _plegar(b)
    return av == bv or (truncable and av.startswith(bv))''')
    s = rep(s, '''    pf = f"{ap_f} {am_f}".upper().split()
    pm = f"{ap_m} {am_m}".upper().split()''', '''    pf = _plegar(f"{ap_f} {am_f}").split()
    pm = _plegar(f"{ap_m} {am_m}").split()''')
    s = rep(s, '''            omitidas.append(etiqueta)
            return
        av, bv = str(a).upper(), str(b).upper()
        if av == bv:''', '''            omitidas.append(etiqueta)
            return
        av, bv = _plegar(a), _plegar(b)
        if av == bv:''')
    s = rep(s, "def _coincide(a: Any, b: Any, truncable: bool) -> bool:", '''def _plegar(valor: Any) -> str:
    """
    Mayúsculas y sin acentos, para comparar el anverso contra la MRZ. El estándar
    ICAO no admite acentos en la MRZ (y la Ñ pasa a N), pero el anverso sí los
    imprime: 'GONZÁLEZ' contra 'GONZALEZ' es el mismo apellido, no una
    contradicción. Mismo criterio que `plegar` en la validación del pasaporte.
    """
    return _sin_acentos(str(valor).upper())


def _coincide(a: Any, b: Any, truncable: bool) -> bool:''')
    return s


def verificar():
    actual = open(INE_RAD, encoding="utf-8").read().replace("\r\n", "\n")
    esperado = derivar(de_santander(ULTIMA_SINCRONIZACION))
    if actual == esperado:
        print(f"OK: ine.py es exactamente la derivación del ine.py de santander_ia en {ULTIMA_SINCRONIZACION}")
        return 0
    print(f"ine.py NO coincide con la derivación de santander_ia en {ULTIMA_SINCRONIZACION}. Diferencias:")
    for linea in difflib.unified_diff(esperado.splitlines(), actual.splitlines(),
                                      "derivación", "ine.py", lineterm="", n=1):
        print(linea)
    print("\nAlguien editó ine.py a mano. Si el cambio es correcto, hay que llevarlo también a santander_ia "
          "o agregarlo a `derivar`; si no, `--aplicar` lo deshace.")
    return 1


def aplicar(rev):
    global ULTIMA_SINCRONIZACION
    nuevo = derivar(de_santander(rev))
    corto = commit_de(rev)
    with open(INE_RAD, "w", encoding="utf-8", newline="") as f:
        f.write(nuevo)
    # Se anota con qué commit quedó, en este mismo archivo
    fuente = open(__file__, encoding="utf-8").read()
    fuente = re.sub(r'^ULTIMA_SINCRONIZACION = "[^"]*"', f'ULTIMA_SINCRONIZACION = "{corto}"', fuente, count=1, flags=re.M)
    with open(__file__, "w", encoding="utf-8", newline="") as f:
        f.write(fuente)
    print(f"ine.py reescrito desde santander_ia en {corto}.")
    print("Siguiente paso: python generar_vectores_ine_ambas.py --verificar")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--verificar"]:
        sys.exit(verificar())
    if args[:1] == ["--aplicar"]:
        aplicar(args[1] if len(args) > 1 else "HEAD")
    else:
        print(__doc__)
        sys.exit(2)
