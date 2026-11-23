"""Endpoints de administración de RAD: llaves de acceso y uso.

Los consume el panel rad_view DESDE SU SERVIDOR (nunca desde el navegador), con
`Authorization: Bearer <RAD_ADMIN_TOKEN>`. El token vive en el `.env` de RAD y en el
de rad_view; si RAD no lo tiene configurado, todo /admin responde 503 (apagado) en
vez de quedar abierto.

No salen en Swagger (`include_in_schema=False`): la documentación pública es para
los clientes de RAD, y a ellos esto no les toca.

    GET  /admin/keys                  llaves (sin hash) con peticiones y último uso
    POST /admin/keys                  {"cliente": "...", "nota": "..."} → 201 con la
                                      llave completa en `llave` (única vez que se ve)
    POST /admin/keys/{id}/revocar     la marca revocada (no la borra)
    GET  /admin/uso                   filas de uso, nuevas primero, paginadas con
                                      `antes_de` = `siguiente` de la página anterior
    GET  /admin/uso/resumen           conteos; `agrupar=` combinación de dia, hora,
                                      cliente, llave, endpoint, codigo, status,
                                      resultado, ip, agente

Filtros comunes de /admin/uso y /admin/uso/resumen: `desde` (incluye) y `hasta`
(excluye) en ISO UTC (basta 'AAAA-MM-DD'), `api_key_id`, `llave` (valida, sin_llave,
invalida, revocada), `endpoint` (sin diagonal final, p. ej. /procesa_ine_ambas) y
`resultado` (procesado, no_procesado, error: ver uso.RESULTADOS). Cada fila de
/admin/uso trae ya su `resultado`.
"""
import hmac
import os
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

import uso


def _exigir_token(authorization: Optional[str] = Header(default=None)) -> None:
    esperado = os.getenv("RAD_ADMIN_TOKEN", "").strip()
    if not esperado:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Administración apagada: RAD no tiene RAD_ADMIN_TOKEN en su .env.")
    dado = (authorization or "").strip()
    if dado.lower().startswith("bearer "):
        dado = dado[len("bearer "):].strip()
    if not dado or not hmac.compare_digest(dado.encode(), esperado.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de administración inválido.",
                            headers={"WWW-Authenticate": "Bearer"})


# Valores de `resultado` (uso.RESULTADOS); Literal para que FastAPI responda 422 con otro.
Resultado = Literal["procesado", "no_procesado", "error"]

router = APIRouter(prefix="/admin", include_in_schema=False, dependencies=[Depends(_exigir_token)])


class NuevaLlave(BaseModel):
    cliente: str = Field(min_length=1, max_length=120)
    nota: Optional[str] = Field(default=None, max_length=500)


@router.get("/keys")
def listar_llaves():
    return {"llaves": uso.listar_llaves()}


@router.post("/keys", status_code=status.HTTP_201_CREATED)
def crear_llave(datos: NuevaLlave):
    cliente = datos.cliente.strip()
    if not cliente:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "El cliente no puede ir vacío.")
    nota = (datos.nota or "").strip() or None
    return uso.crear_llave(cliente, nota)


@router.post("/keys/{llave_id}/revocar")
def revocar_llave(llave_id: int):
    llave = uso.revocar_llave(llave_id)
    if llave is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No existe esa llave.")
    return llave


@router.get("/uso")
def listar_uso(desde: Optional[str] = None, hasta: Optional[str] = None,
               api_key_id: Optional[int] = None, llave: Optional[str] = None,
               endpoint: Optional[str] = None, resultado: Optional[Resultado] = None,
               antes_de: Optional[int] = None,
               limite: int = Query(default=100, ge=1, le=1000)):
    return uso.listar_uso(desde, hasta, api_key_id, llave, endpoint, antes_de, limite, resultado)


@router.get("/uso/resumen")
def resumir_uso(agrupar: str = "dia", desde: Optional[str] = None, hasta: Optional[str] = None,
                api_key_id: Optional[int] = None, llave: Optional[str] = None,
                endpoint: Optional[str] = None, resultado: Optional[Resultado] = None,
                offset_min: int = Query(default=-360, ge=-840, le=840)):
    try:
        filas = uso.resumir_uso([c.strip() for c in agrupar.split(",")], desde, hasta,
                                api_key_id, llave, endpoint, offset_min, resultado)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return {"filas": filas}
