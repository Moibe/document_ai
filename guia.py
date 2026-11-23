"""Sirve la documentación del repo como páginas web (adaptado de santander_ia).

La documentación vive en `API.md`, que es donde se escribe y se revisa. Esto solo
la renderiza: no hay una segunda copia que mantener, así que no pueden
desincronizarse — lo que se ve en el navegador es literalmente el archivo que
viaja en el commit desplegado.

Se renderiza del lado del SERVIDOR y el CSS va embebido. Nada de traer una
librería de un CDN: esto corre en la red interna y no se puede dar por hecho que
el navegador de quien abra la página alcance internet.

Diferencia con santander_ia: aquí NO hay una ruta comodín `/{documento}`. Los
endpoints de RAD se declaran con diagonal final, y un comodín GET de un segmento
haría que `POST /procesa_ine_ambas` (sin diagonal) respondiera 405 en vez de la
redirección 307 de siempre: el router prefiere la coincidencia parcial del
comodín a la redirección. Por eso cada página tiene su ruta explícita en app.py.
"""
import html
import logging
import re
from pathlib import Path
from typing import Optional

# `markdown` se importa dentro de `renderizar`, no aquí: app.py importa este módulo al
# arrancar, y el deploy de webhook-central sigue de largo si falla el `pip install`.
# Con el import arriba, un paquete faltante tumbaría TODA la API; así solo deja de
# verse /guia.

logger = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parent

# Qué documento se publica y con qué nombre. Ni los HANDOFF_* (son para quien
# reimplementa, no para quien consume) ni nada que describa el servidor.
DOCUMENTOS = {
    "guia": ("API.md", "Guía de la API", "Cómo llamar a la API, qué devuelve y qué hacer con cada código"),
}

# La URL que traen los ejemplos escritos en API.md. El repo es público, así que ahí
# no va la dirección real: se sustituye al servir por la del servidor que atiende,
# y así quien abre la guía en desarrollo copia ejemplos que apuntan a desarrollo.
URL_EN_LOS_EJEMPLOS = "http://SERVIDOR_RAD"

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0;
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: #1a1d21; background: #f6f7f9;
}
.barra {
  background: #15181c; color: #fff; padding: 14px 24px;
  display: flex; flex-wrap: wrap; gap: 18px; align-items: baseline;
  position: sticky; top: 0; z-index: 10;
}
.barra strong { font-size: 15px; letter-spacing: .2px; }
.barra a { color: #9fb6d4; text-decoration: none; font-size: 14px; }
.barra a:hover { color: #fff; text-decoration: underline; }
.barra .activo { color: #fff; font-weight: 600; }
main {
  max-width: 920px; margin: 0 auto; padding: 36px 24px 90px;
  background: #fff; min-height: 100vh;
  border-left: 1px solid #e6e8ec; border-right: 1px solid #e6e8ec;
}
h1, h2, h3, h4 { line-height: 1.25; margin: 2em 0 .6em; }
h1 { font-size: 30px; margin-top: 0; padding-bottom: .3em; border-bottom: 2px solid #e6e8ec; }
h2 { font-size: 23px; padding-bottom: .25em; border-bottom: 1px solid #e6e8ec; }
h3 { font-size: 18px; }
h4 { font-size: 16px; color: #41464d; }
p, li { margin: .6em 0; }
a { color: #1a5fb4; }
code {
  font: 13.5px/1.5 "Cascadia Code", Consolas, "SF Mono", Monaco, monospace;
  background: #f0f1f4; padding: .15em .4em; border-radius: 4px;
  border: 1px solid #e3e5ea; white-space: nowrap;
}
pre {
  background: #15181c; color: #e6e8ec; padding: 16px 18px;
  border-radius: 8px; overflow-x: auto; font-size: 13.5px; line-height: 1.55;
}
pre code { background: none; border: 0; padding: 0; color: inherit; white-space: pre; }
table { border-collapse: collapse; width: 100%; margin: 1.2em 0; font-size: 14.5px; display: block; overflow-x: auto; }
th, td { border: 1px solid #e3e5ea; padding: 8px 11px; text-align: left; vertical-align: top; }
th { background: #f6f7f9; font-weight: 600; }
tr:nth-child(even) td { background: #fbfbfc; }
blockquote {
  margin: 1.2em 0; padding: 12px 18px;
  border-left: 4px solid #e8a33d; background: #fdf6e9; border-radius: 0 6px 6px 0;
}
blockquote p { margin: .3em 0; }
hr { border: 0; border-top: 1px solid #e6e8ec; margin: 2.4em 0; }
.toc ul { list-style: none; padding-left: 1.1em; }
.toc > ul { padding-left: 0; }
.pie { max-width: 920px; margin: 0 auto; padding: 0 24px 40px; color: #6b7280; font-size: 13px; }
@media (max-width: 760px) {
  main { padding: 24px 16px 70px; border: 0; }
  .barra { padding: 12px 16px; gap: 12px; }
}
"""


def _barra(actual: Optional[str]) -> str:
    enlaces = ['<strong>RAD</strong>']
    for clave, (_, titulo, _d) in DOCUMENTOS.items():
        clase = ' class="activo"' if clave == actual else ""
        enlaces.append(f'<a href="/{clave}"{clase}>{html.escape(titulo)}</a>')
    enlaces.append('<a href="/docs">Swagger</a>')
    enlaces.append('<a href="/health">Estado</a>')
    return '<nav class="barra">' + "".join(enlaces) + "</nav>"


def _pagina(titulo: str, cuerpo: str, actual: Optional[str] = None) -> str:
    return (
        "<!doctype html><html lang=\"es\"><head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(titulo)}</title>"
        f"<style>{_CSS}</style></head><body>"
        f"{_barra(actual)}<main>{cuerpo}</main>"
        "<p class=\"pie\">Esta página renderiza el archivo API.md del repositorio, tal como está "
        "en la versión desplegada. Para corregir algo, se edita el archivo y se despliega.</p>"
        "</body></html>"
    )


def renderizar(clave: str, base_url: str = "") -> Optional[str]:
    """Devuelve el HTML del documento, o None si no existe."""
    if clave not in DOCUMENTOS:
        return None
    nombre, titulo, _ = DOCUMENTOS[clave]
    ruta = RAIZ / nombre
    if not ruta.is_file():
        logger.warning(f"No se encontró {nombre} para publicar en /{clave}.")
        return None

    try:
        import markdown
    except ImportError:
        logger.error("Falta el paquete 'markdown' (está en requirements.txt): /guia no se puede renderizar.")
        return _pagina(titulo, "<h1>Guía no disponible</h1><p>Al servidor le falta un paquete para "
                       "mostrar esta guía. Los endpoints de la API funcionan igual.</p>", clave)

    texto = ruta.read_text(encoding="utf-8")
    if base_url:
        texto = texto.replace(URL_EN_LOS_EJEMPLOS, base_url.rstrip("/"))

    cuerpo = markdown.markdown(
        texto,
        extensions=["tables", "fenced_code", "toc", "sane_lists", "attr_list"],
        # El índice ([TOC]) sin el h1: el título ya está arriba de él.
        extension_configs={"toc": {"toc_depth": "2-3"}},
    )
    # Los enlaces entre documentos del repo apuntan a archivos; aquí se
    # traducen a las rutas que sí existen en el navegador.
    for clave_otro, (nombre_otro, _t, _d) in DOCUMENTOS.items():
        cuerpo = cuerpo.replace(f'href="{nombre_otro}"', f'href="/{clave_otro}"')
    # Lo que siga apuntando a un archivo del repo deja de ser un enlace roto y
    # se vuelve texto: desde el navegador no hay forma de abrirlo.
    cuerpo = re.sub(r'<a href="[^"]+\.(?:py|json|md)"[^>]*>(.*?)</a>', r"<code>\1</code>", cuerpo)

    return _pagina(titulo, cuerpo, clave)


def indice(base_url: str = "") -> str:
    """Portada: a dónde ir desde la raíz."""
    tarjetas = []
    for clave, (_, titulo, descripcion) in DOCUMENTOS.items():
        tarjetas.append(
            f'<li><a href="/{clave}"><strong>{html.escape(titulo)}</strong></a>'
            f" — {html.escape(descripcion)}</li>"
        )
    cuerpo = (
        "<h1>RAD</h1>"
        "<p>Reconocimiento avanzado de documentos: API que lee documentos de identidad "
        "escaneados —credencial para votar (INE), pasaporte, cédula panameña— y contrasta "
        "lo impreso contra la redundancia del propio documento (la zona de lectura mecánica, "
        "la CURP, la clave de elector) para decir si los datos se sostienen.</p>"
        "<h2>Documentación</h2><ul>" + "".join(tarjetas) + "</ul>"
        "<h2>Endpoints principales</h2>"
        "<table><thead><tr><th>Ruta</th><th>Qué hace</th></tr></thead><tbody>"
        "<tr><td><code>POST /procesa_ine_ambas/</code></td><td>INE, anverso y reverso en dos archivos: la validación completa</td></tr>"
        "<tr><td><code>POST /procesa_ine/</code></td><td>INE, solo el anverso</td></tr>"
        "<tr><td><code>POST /procesa_ine_atras/</code></td><td>INE, solo el reverso</td></tr>"
        "<tr><td><code>GET /health</code></td><td>Estado del servicio</td></tr>"
        "<tr><td><a href=\"/docs\"><code>GET /docs</code></a></td>"
        "<td>Swagger: probar los endpoints desde el navegador</td></tr>"
        "</tbody></table>"
    )
    return _pagina("RAD", cuerpo)
