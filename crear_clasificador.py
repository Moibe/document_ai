"""Crea (o adopta) el Custom Document Classifier y le sube su esquema.

Correr con:  python crear_clasificador.py

Son tres llamadas repartidas en DOS versiones de la API de Document AI, y eso
no es un descuido: es la superficie real de Google. El recurso `dataset` y su
esquema solo existen en v1beta3; el `:process` de siempre vive en v1.

  1. POST  v1       .../processors                    crear (síncrono)
  2. PATCH v1beta3  .../processors/{id}/dataset        inicializar (es LRO, se pollea)
  3. PATCH v1beta3  .../dataset/datasetSchema          subir el esquema (síncrono)

No hay paso de entrenar ni de desplegar: el procesador nace con las versiones
`pretrained-classifier-*` ya utilizables en zero-shot, igual que el extractor
`santander_ine` de este mismo proyecto, que nunca se entrenó.

Es IDEMPOTENTE por displayName: `processors.create` no acepta requestId y
`processors.list` no tiene filtro, así que dos ejecuciones crearían dos
procesadores. Antes de crear se lista y se busca el nombre exacto; si ya
existe se adopta y solo se le reemplaza el esquema.

Estructura copiada de `nexus_back/servicios/procesadores.py`, donde el mismo
mecanismo ya corre en producción.
"""
import asyncio
import json
import logging
import sys

import httpx

from dotenv import load_dotenv
load_dotenv()

import clasificador as cl

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def _pedir(cliente, metodo, api, ruta, **kwargs):
    respuesta = await cliente.request(
        metodo,
        cl._url(api, ruta),
        headers=cl.cabeceras(),
        timeout=120.0,
        **kwargs,
    )
    if respuesta.status_code >= 400:
        raise RuntimeError(f"{metodo} {api}/{ruta} -> {respuesta.status_code}: {respuesta.text[:600]}")
    return respuesta.json() if respuesta.text else {}


async def _esperar_operacion(cliente, nombre_op):
    """Las operaciones de larga duración devuelven un handle que hay que pollear."""
    for intento in range(30):
        op = await _pedir(cliente, "GET", "v1beta3", nombre_op)
        if op.get("done"):
            if op.get("error"):
                raise RuntimeError(f"La operación falló: {op['error']}")
            return op
        await asyncio.sleep(2)
    raise RuntimeError(f"La operación {nombre_op} no terminó a tiempo.")


async def _buscar_por_display_name(cliente, display_name):
    """Idempotencia: buscar antes de crear. `processors.list` no filtra, así que
    se pagina a mano y se compara el displayName exacto."""
    token = None
    while True:
        params = {"pageSize": 100}
        if token:
            params["pageToken"] = token
        pagina = await _pedir(cliente, "GET", "v1", f"{cl.PADRE}/processors", params=params)
        for p in pagina.get("processors", []):
            if p.get("displayName") == display_name:
                return p
        token = pagina.get("nextPageToken")
        if not token:
            return None


async def main():
    async with httpx.AsyncClient() as cliente:
        existente = await _buscar_por_display_name(cliente, cl.DISPLAY_NAME)

        if existente:
            procesador = existente
            logger.info(f"Ya existía, se adopta: {procesador['name']}")
        else:
            procesador = await _pedir(
                cliente, "POST", "v1", f"{cl.PADRE}/processors",
                json={
                    "type": "CUSTOM_CLASSIFICATION_PROCESSOR",
                    "displayName": cl.DISPLAY_NAME,
                },
            )
            logger.info(f"Creado: {procesador['name']}")

        recurso = procesador["name"]

        # 2. Inicializar el dataset. Si el procesador ya se había sincronizado,
        # Google responde "already initialized" y eso no es un error.
        try:
            op = await _pedir(
                cliente, "PATCH", "v1beta3", f"{recurso}/dataset",
                params={"updateMask": "unmanaged_dataset_config"},
                json={"name": f"{recurso}/dataset", "unmanagedDatasetConfig": {}},
            )
            if op.get("name") and not op.get("done"):
                logger.info("Inicializando el dataset (operación de larga duración)...")
                await _esperar_operacion(cliente, op["name"])
            logger.info("Dataset listo.")
        except RuntimeError as e:
            if "already initialized" not in str(e):
                raise
            logger.info("El dataset ya estaba inicializado.")

        # 3. Subir el esquema de categorías (reemplazo total, no incremental)
        await _pedir(
            cliente, "PATCH", "v1beta3", f"{recurso}/dataset/datasetSchema",
            json={
                "name": f"{recurso}/dataset/datasetSchema",
                "documentSchema": cl.esquema(),
            },
        )
        logger.info("Esquema subido.")

        final = await _pedir(cliente, "GET", "v1", recurso)

    pid = recurso.split("/")[-1]
    version = (final.get("defaultProcessorVersion") or "").split("/")[-1]
    print()
    print("=" * 66)
    print(f"  Clasificador : {final.get('displayName')}")
    print(f"  ID           : {pid}")
    print(f"  Estado       : {final.get('state')}")
    print(f"  Versión      : {version}")
    print(f"  Categorías   : {[e['name'] for e in cl.esquema()['entityTypes']]}")
    print("=" * 66)
    print()
    print("Pon estos valores en clasificador.py:")
    print(f'  CLASIFICADOR_ID = "{pid}"')
    print(f'  VERSION = "{version}"')
    print()


if __name__ == "__main__":
    asyncio.run(main())
