"""Llaves de acceso (API keys) y registro de uso de RAD.

RAD es dueño de sus llaves y de su uso: viven en su propia SQLite (`rad.db`, junto
a este archivo, o donde diga `RAD_DB`). El panel de administración (rad_view) no
toca este archivo: llega por los endpoints de `admin.py`. Así, validar una llave no
depende de que el panel esté arriba ni cuesta un salto de red por petición.

LA LLAVE ES OBLIGATORIA. Una petición a `/procesa_*` sin cabecera `X-API-Key`, con
una llave que no existe o con una revocada se rechaza con `401 LLAVE_INVALIDA` (el
mismo sobre que el resto de la API) ANTES de procesar nada: no se gasta una lectura
de Document AI en quien no tiene acceso. El rechazo también queda registrado, con
el estado de la llave (`sin_llave`, `invalida`, `revocada`), la IP y el User-Agent.

`RAD_LLAVE_OBLIGATORIA=0` en el `.env` regresa a la fase de observación (la que hubo
al principio): se registra la llave de cada petición pero no se rechaza ninguna.
Sirve para llevar esto a un ambiente cuyos clientes todavía no tienen llave; el
registro dice cuáles son (`sin_llave`, con su IP) antes de encender la exigencia.

DE CADA LLAVE se guarda solo el SHA-256 y un prefijo visible (`rad_Ab3dE9xQ`) para
reconocerla en una lista. La llave completa se entrega UNA vez, al crearla.

DE CADA PETICIÓN a un endpoint de documentos (`/procesa_*`) se guarda una fila con
la fecha (UTC), la llave, el endpoint, el status HTTP, el `codigo`/`estado` del
sobre, la duración, el tamaño, el tipo de archivo y lo que dijo el clasificador.
NUNCA datos del documento: ni nombres, ni CURP, ni la imagen.

Concurrencia: gunicorn corre varios workers, cada uno con su conexión. La base va
en modo WAL (lectores y un escritor a la vez) y cada escritura es una transacción
cortita, así que no se estorban.
"""
import asyncio
import contextvars
import hashlib
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from starlette.responses import JSONResponse

import codigos

logger = logging.getLogger(__name__)

RUTA_DB = Path(os.getenv("RAD_DB") or Path(__file__).resolve().parent / "rad.db")

CABECERA_LLAVE = "x-api-key"
PREFIJO_LLAVE = "rad_"
LARGO_PREFIJO_VISIBLE = len(PREFIJO_LLAVE) + 8

# Qué peticiones se registran: las que procesan un documento. /health, /docs, la
# guía y /admin no (el chequeo de salud de pm2 llenaría la tabla de ruido).
PREFIJO_RUTAS_REGISTRADAS = "/procesa_"

# Estado de la llave en cada fila de uso.
LLAVE_VALIDA = "valida"
LLAVE_AUSENTE = "sin_llave"
LLAVE_INVALIDA = "invalida"
LLAVE_REVOCADA = "revocada"

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id        INTEGER PRIMARY KEY,
    prefijo   TEXT NOT NULL,
    hash      TEXT NOT NULL UNIQUE,
    cliente   TEXT NOT NULL,
    nota      TEXT,
    creada    TEXT NOT NULL,
    revocada  TEXT
);
CREATE TABLE IF NOT EXISTS uso (
    id          INTEGER PRIMARY KEY,
    fecha       TEXT NOT NULL,
    api_key_id  INTEGER REFERENCES api_keys(id),
    llave       TEXT NOT NULL,
    metodo      TEXT NOT NULL,
    endpoint    TEXT NOT NULL,
    status_http INTEGER NOT NULL,
    codigo      INTEGER,
    estado      TEXT,
    ms          INTEGER NOT NULL,
    bytes       INTEGER,
    archivos    INTEGER,
    mime        TEXT,
    categoria   TEXT,
    confianza   REAL,
    ip          TEXT,
    agente      TEXT
);
CREATE INDEX IF NOT EXISTS uso_fecha ON uso(fecha);
CREATE INDEX IF NOT EXISTS uso_llave_fecha ON uso(api_key_id, fecha);
"""


def ahora_iso() -> str:
    """UTC con milisegundos y `Z`: se ordena como texto y JavaScript lo lee sin ambigüedad."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def conectar() -> sqlite3.Connection:
    con = sqlite3.connect(RUTA_DB, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 10000")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def iniciar() -> None:
    """Crea la base y sus tablas si no existen. Idempotente: corre en cada worker."""
    RUTA_DB.parent.mkdir(parents=True, exist_ok=True)
    with conectar() as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(_ESQUEMA)
    con.close()


# --- llaves -----------------------------------------------------------------

def _hash(llave: str) -> str:
    return hashlib.sha256(llave.encode("utf-8")).hexdigest()


def crear_llave(cliente: str, nota: Optional[str] = None) -> Dict[str, Any]:
    """Genera una llave nueva. La respuesta trae la llave completa en `llave`: es la
    única vez que existe en claro; después solo queda su hash."""
    llave = PREFIJO_LLAVE + secrets.token_urlsafe(32)
    fila = {
        "prefijo": llave[:LARGO_PREFIJO_VISIBLE],
        "hash": _hash(llave),
        "cliente": cliente,
        "nota": nota,
        "creada": ahora_iso(),
    }
    con = conectar()
    try:
        with con:
            cur = con.execute(
                "INSERT INTO api_keys (prefijo, hash, cliente, nota, creada) "
                "VALUES (:prefijo, :hash, :cliente, :nota, :creada)", fila)
        return {"id": cur.lastrowid, "llave": llave, "prefijo": fila["prefijo"],
                "cliente": cliente, "nota": nota, "creada": fila["creada"], "revocada": None}
    finally:
        con.close()


def listar_llaves() -> List[Dict[str, Any]]:
    """Todas las llaves (sin hash) con cuántas peticiones lleva cada una y la última."""
    con = conectar()
    try:
        filas = con.execute("""
            SELECT k.id, k.prefijo, k.cliente, k.nota, k.creada, k.revocada,
                   COUNT(u.id) AS peticiones, MAX(u.fecha) AS ultimo_uso
            FROM api_keys k LEFT JOIN uso u ON u.api_key_id = k.id
            GROUP BY k.id ORDER BY k.id DESC""").fetchall()
        return [dict(f) for f in filas]
    finally:
        con.close()


def revocar_llave(llave_id: int) -> Optional[Dict[str, Any]]:
    """Marca la llave como revocada (no se borra: su uso pasado sigue apuntándole).
    Revocar dos veces conserva la fecha de la primera. None si no existe."""
    con = conectar()
    try:
        with con:
            con.execute("UPDATE api_keys SET revocada = ? WHERE id = ? AND revocada IS NULL",
                        (ahora_iso(), llave_id))
        fila = con.execute("SELECT id, prefijo, cliente, nota, creada, revocada FROM api_keys "
                           "WHERE id = ?", (llave_id,)).fetchone()
        return dict(fila) if fila else None
    finally:
        con.close()


def llave_obligatoria() -> bool:
    """Si se rechaza la petición que no trae una llave válida. Encendido salvo que el
    `.env` diga `RAD_LLAVE_OBLIGATORIA=0`. Se lee en cada llamada y no al importar: app.py
    importa este módulo antes de cargar el `.env`."""
    valor = os.getenv("RAD_LLAVE_OBLIGATORIA", "1").strip().lower()
    return valor not in ("0", "no", "false", "off")


def identificar(llave: Optional[str]):
    """(estado, api_key_id) de la llave que mandó el cliente."""
    if not llave:
        return LLAVE_AUSENTE, None
    con = conectar()
    try:
        fila = con.execute("SELECT id, revocada FROM api_keys WHERE hash = ?",
                           (_hash(llave),)).fetchone()
    finally:
        con.close()
    if fila is None:
        return LLAVE_INVALIDA, None
    return (LLAVE_REVOCADA if fila["revocada"] else LLAVE_VALIDA), fila["id"]


# Lo que se le dice a quien no pasa, según cómo venía su llave. El `codigo` y el
# `estado` son los mismos (401 LLAVE_INVALIDA): en los tres casos lo que toca es
# corregir la llave; el mensaje solo dice cuál fue el problema.
MENSAJES_RECHAZO = {
    LLAVE_AUSENTE: "Falta la llave de acceso: mándala en la cabecera X-API-Key.",
    LLAVE_INVALIDA: "La llave de acceso no existe. Revisa que la cabecera X-API-Key traiga "
                    "la llave completa, tal como te la entregaron.",
    LLAVE_REVOCADA: "La llave de acceso fue revocada. Pide una nueva a quien opera la API.",
}


# --- consulta del uso -------------------------------------------------------

# Desenlace de cada petición en tres cubetas, definido UNA vez (aquí) para agrupar,
# filtrar y marcar cada fila. Los endpoints viejos no traen sobre (codigo null): ahí
# solo cuenta el status HTTP.
#   procesado     21x/200: se extrajeron datos
#   no_procesado  22x: se evaluó y no se pudo extraer (no era el documento, en blanco…)
#   error         status HTTP >= 400 (el codigo coincide con el status)
RESULTADOS = ("procesado", "no_procesado", "error")
_RESULTADO_SQL = ("CASE WHEN u.status_http >= 400 THEN 'error' "
                  "WHEN u.codigo BETWEEN 220 AND 299 THEN 'no_procesado' "
                  "ELSE 'procesado' END")


def _filtros(desde, hasta, api_key_id, llave, endpoint, resultado=None):
    """WHERE común a la lista y al resumen. `desde` incluye, `hasta` excluye; ambos
    en ISO UTC (basta 'AAAA-MM-DD')."""
    condiciones, params = [], []
    if resultado:
        condiciones.append(f"({_RESULTADO_SQL}) = ?")
        params.append(resultado)
    if desde:
        condiciones.append("u.fecha >= ?")
        params.append(desde)
    if hasta:
        condiciones.append("u.fecha < ?")
        params.append(hasta)
    if api_key_id is not None:
        condiciones.append("u.api_key_id = ?")
        params.append(api_key_id)
    if llave:
        condiciones.append("u.llave = ?")
        params.append(llave)
    if endpoint:
        condiciones.append("u.endpoint = ?")
        params.append(endpoint)
    where = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
    return where, params


def listar_uso(desde=None, hasta=None, api_key_id=None, llave=None, endpoint=None,
               antes_de: Optional[int] = None, limite: int = 100,
               resultado: Optional[str] = None) -> Dict[str, Any]:
    """Filas de uso, de la más nueva a la más vieja. Se pagina con `antes_de` (el id
    de la última fila recibida); `siguiente` es el valor para la página que sigue."""
    where, params = _filtros(desde, hasta, api_key_id, llave, endpoint, resultado)
    if antes_de is not None:
        where = (where + " AND " if where else "WHERE ") + "u.id < ?"
        params.append(antes_de)
    con = conectar()
    try:
        filas = con.execute(f"""
            SELECT u.*, k.cliente, k.prefijo, {_RESULTADO_SQL} AS resultado
            FROM uso u LEFT JOIN api_keys k ON k.id = u.api_key_id
            {where} ORDER BY u.id DESC LIMIT ?""", (*params, limite + 1)).fetchall()
    finally:
        con.close()
    filas = [dict(f) for f in filas]
    siguiente = filas[limite - 1]["id"] if len(filas) > limite else None
    return {"filas": filas[:limite], "siguiente": siguiente}


# Columnas por las que se puede agrupar el resumen (lista cerrada: van al SQL).
AGRUPABLES = {
    "dia": "dia",
    "hora": "hora",
    "cliente": "u.api_key_id, k.cliente, k.prefijo",
    "llave": "u.llave",
    "endpoint": "u.endpoint",
    "codigo": "u.codigo, u.estado",
    "status": "u.status_http",
    "resultado": "resultado",
    "ip": "u.ip",
    "agente": "u.agente",
}
_COLUMNAS_SELECT = {
    "dia": "strftime('%Y-%m-%d', u.fecha, ? || ' minutes') AS dia",
    "hora": "strftime('%Y-%m-%dT%H', u.fecha, ? || ' minutes') AS hora",
    "cliente": "u.api_key_id, k.cliente, k.prefijo",
    "llave": "u.llave",
    "endpoint": "u.endpoint",
    "codigo": "u.codigo, u.estado",
    "status": "u.status_http",
    "resultado": f"{_RESULTADO_SQL} AS resultado",
    "ip": "u.ip",
    "agente": "u.agente",
}
# Las que se cortan en hora local: llevan el offset como parámetro del SELECT.
_CON_OFFSET = ("dia", "hora")


def resumir_uso(agrupar: Iterable[str], desde=None, hasta=None, api_key_id=None,
                llave=None, endpoint=None, offset_min: int = -360,
                resultado: Optional[str] = None) -> List[Dict[str, Any]]:
    """Conteos agrupados. `agrupar` es una combinación de AGRUPABLES. `dia` y `hora`
    se cortan en la hora local que diga `offset_min` (minutos respecto de UTC; -360
    es el centro de México, que ya no cambia de horario); `hora` sale como
    'AAAA-MM-DDTHH'."""
    columnas = [c for c in dict.fromkeys(agrupar) if c in AGRUPABLES]
    if not columnas:
        raise ValueError(f"agrupar debe incluir alguna de: {', '.join(AGRUPABLES)}")
    where, params = _filtros(desde, hasta, api_key_id, llave, endpoint, resultado)
    select = ", ".join(_COLUMNAS_SELECT[c] for c in columnas)
    group = ", ".join(AGRUPABLES[c] for c in columnas)
    params_select = [f"{int(offset_min):+d}" for c in columnas if c in _CON_OFFSET]
    con = conectar()
    try:
        filas = con.execute(f"""
            SELECT {select}, COUNT(*) AS peticiones, CAST(AVG(u.ms) AS INTEGER) AS ms_promedio,
                   MIN(u.fecha) AS primera, MAX(u.fecha) AS ultima
            FROM uso u LEFT JOIN api_keys k ON k.id = u.api_key_id
            {where} GROUP BY {group} ORDER BY {group}""", (*params_select, *params)).fetchall()
        return [dict(f) for f in filas]
    finally:
        con.close()


# --- registro de cada petición ----------------------------------------------

# Lo que el endpoint sabe y el middleware no: el tipo real del archivo y lo que dijo
# el clasificador. El middleware abre un diccionario por petición y los endpoints lo
# llenan con `anotar_archivo` (asyncio.gather copia el contexto pero el diccionario
# es el mismo objeto, así que /procesa_ine_ambas anota sus dos archivos).
_anotaciones: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "rad_uso_anotaciones", default=None)


def anotar_archivo(mime: Optional[str], veredicto: Optional[Dict[str, Any]] = None) -> None:
    datos = _anotaciones.get()
    if datos is None:
        return
    datos.setdefault("archivos", []).append({
        "mime": mime,
        "categoria": (veredicto or {}).get("categoria"),
        "confianza": (veredicto or {}).get("confianza"),
    })


# El sobre abre siempre con codigo y estado (codigos.cuerpo los pone primero), así que
# basta con mirar el principio del cuerpo en vez de parsear un JSON que puede ser grande.
_SOBRE = re.compile(rb'^\s*\{\s*"codigo"\s*:\s*(\d+)\s*,\s*"estado"\s*:\s*"([A-Z_]+)"')


def _registrar(fila: Dict[str, Any]) -> None:
    con = conectar()
    try:
        with con:
            con.execute("""
                INSERT INTO uso (fecha, api_key_id, llave, metodo, endpoint, status_http,
                                 codigo, estado, ms, bytes, archivos, mime, categoria,
                                 confianza, ip, agente)
                VALUES (:fecha, :api_key_id, :llave, :metodo, :endpoint, :status_http,
                        :codigo, :estado, :ms, :bytes, :archivos, :mime, :categoria,
                        :confianza, :ip, :agente)""", fila)
    finally:
        con.close()


async def _responder(desenlace: "codigos.Desenlace", mensaje: Optional[str], scope, receive, send) -> None:
    """Contesta desde el middleware con el sobre de siempre, sin llegar al endpoint."""
    await JSONResponse(status_code=desenlace.http,
                       content=codigos.cuerpo(desenlace, mensaje))(scope, receive, send)


def _cabecera(scope, nombre: bytes) -> Optional[str]:
    for k, v in scope.get("headers") or []:
        if k == nombre:
            return v.decode("latin-1")
    return None


class RegistroDeUso:
    """Middleware ASGI de los endpoints de documentos: valida la llave ANTES de que la
    petición llegue al endpoint (y la rechaza con 401 si no sirve), y ya enviada la
    respuesta anota su fila de uso. Si anotar falla, se registra en el log y la
    respuesta del cliente no se entera."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(PREFIJO_RUTAS_REGISTRADAS):
            await self.app(scope, receive, send)
            return

        inicio = time.perf_counter()
        fecha = ahora_iso()
        anotaciones: dict = {}
        token = _anotaciones.set(anotaciones)
        respuesta = {"status": 500, "inicio_cuerpo": b""}
        estado_llave, api_key_id = None, None

        async def enviar(mensaje):
            if mensaje["type"] == "http.response.start":
                respuesta["status"] = mensaje["status"]
            elif mensaje["type"] == "http.response.body" and len(respuesta["inicio_cuerpo"]) < 256:
                respuesta["inicio_cuerpo"] += mensaje.get("body", b"")[:256]
            await send(mensaje)

        try:
            try:
                estado_llave, api_key_id = await asyncio.to_thread(
                    identificar, _cabecera(scope, CABECERA_LLAVE.encode()))
            except Exception:
                # Sin poder consultar las llaves no se sabe quién llama: se cierra el paso
                # en vez de dejar pasar a todos.
                logger.exception("No se pudo validar la llave de %s", scope.get("path"))
                await _responder(codigos.ERROR_INTERNO, None, scope, receive, enviar)
                return

            if estado_llave != LLAVE_VALIDA and llave_obligatoria():
                await _responder(codigos.LLAVE_INVALIDA, MENSAJES_RECHAZO[estado_llave],
                                 scope, receive, enviar)
                return

            await self.app(scope, receive, enviar)
        finally:
            _anotaciones.reset(token)
            # Sin estado de la llave es que la base no respondió: tampoco hay dónde anotar.
            if estado_llave is not None:
                fila = self._fila(scope, fecha, inicio, respuesta, anotaciones)
                fila["llave"], fila["api_key_id"] = estado_llave, api_key_id
                try:
                    await asyncio.to_thread(_registrar, fila)
                except Exception:
                    logger.exception("No se pudo registrar el uso de %s", scope.get("path"))

    @staticmethod
    def _fila(scope, fecha, inicio, respuesta, anotaciones) -> Dict[str, Any]:
        sobre = _SOBRE.match(respuesta["inicio_cuerpo"])
        archivos = anotaciones.get("archivos") or []
        confianzas = [a["confianza"] for a in archivos if a["confianza"] is not None]
        largo = _cabecera(scope, b"content-length")
        cliente = scope.get("client")
        agente = _cabecera(scope, b"user-agent")

        def unir(campo):
            valores = [a[campo] or "?" for a in archivos]
            return ",".join(valores) if any(a[campo] for a in archivos) else None

        return {
            "fecha": fecha,
            "metodo": scope.get("method", ""),
            # Sin la diagonal final: /procesa_ine/ y /procesa_ine son el mismo endpoint.
            "endpoint": scope["path"].rstrip("/") or "/",
            "status_http": respuesta["status"],
            "codigo": int(sobre.group(1)) if sobre else None,
            "estado": sobre.group(2).decode() if sobre else None,
            "ms": round((time.perf_counter() - inicio) * 1000),
            "bytes": int(largo) if largo and largo.isdigit() else None,
            "archivos": len(archivos) or None,
            "mime": unir("mime"),
            "categoria": unir("categoria"),
            "confianza": min(confianzas) if confianzas else None,
            "ip": cliente[0] if cliente else None,
            "agente": agente[:200] if agente else None,
        }
