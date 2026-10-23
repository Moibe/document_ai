"""Crea (o adopta) un processor de Document AI y le sube su esquema.

    python crear_processor.py clasificador      # el clasificador INE / cédula panameña / otro
    python crear_processor.py cedula_panama     # el extractor de cédula panameña

Generaliza lo que antes hacía `crear_clasificador.py` (que ya no existe): el ciclo de vida es el
MISMO para un clasificador y para un extractor, solo cambia el `type` y el
esquema que se le sube. Son tres llamadas repartidas en DOS versiones de la API,
y eso no es un descuido: es la superficie real de Google. El recurso `dataset` y
su esquema solo existen en v1beta3; el `:process` de siempre vive en v1.

  1. POST  v1       .../processors                    crear (síncrono)
  2. PATCH v1beta3  .../processors/{id}/dataset        inicializar (es LRO, se pollea)
  3. PATCH v1beta3  .../dataset/datasetSchema          subir el esquema (síncrono)

No hay paso de entrenar ni de desplegar: el procesador nace con las versiones
`pretrained-foundation-model-*` ya utilizables en zero-shot. Los extractores de este
proyecto (INE, pasaporte) nunca se entrenaron y extraen con confianza >0.99.

Es IDEMPOTENTE por displayName: `processors.create` no acepta requestId y
`processors.list` no tiene filtro, así que dos ejecuciones crearían dos
procesadores. Antes de crear se lista y se busca el nombre exacto; si ya existe
se adopta y solo se le reemplaza el esquema. Eso hace que correrlo de nuevo sea
la manera normal de ACTUALIZAR el esquema, no solo de crearlo.
"""
import asyncio
import logging
import sys

import httpx

from dotenv import load_dotenv
load_dotenv()

import clasificador as cl

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _destinos():
    """Qué se puede crear. Se arma al vuelo para no importar `cedula_panama` si
    solo se va a tocar el clasificador."""
    destinos = {
        "clasificador": {
            "display_name": cl.DISPLAY_NAME,
            "tipo": "CUSTOM_CLASSIFICATION_PROCESSOR",
            "esquema": cl.esquema,
        },
    }
    try:
        import cedula_panama
        destinos["cedula_panama"] = {
            "display_name": cedula_panama.DISPLAY_NAME,
            "tipo": "CUSTOM_EXTRACTION_PROCESSOR",
            "esquema": cedula_panama.esquema,
        }
    except ImportError:
        pass
    return destinos


async def _pedir(cliente, metodo, api, ruta, **kwargs):
    respuesta = await cliente.request(
        metodo, cl._url(api, ruta), headers=cl.cabeceras(), timeout=120.0, **kwargs
    )
    if respuesta.status_code >= 400:
        raise RuntimeError(f"{metodo} {api}/{ruta} -> {respuesta.status_code}: {respuesta.text[:600]}")
    return respuesta.json() if respuesta.text else {}


async def _esperar_operacion(cliente, nombre_op):
    """Las operaciones de larga duración devuelven un handle que hay que pollear."""
    for _ in range(30):
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


async def crear(destino):
    async with httpx.AsyncClient() as cliente:
        existente = await _buscar_por_display_name(cliente, destino["display_name"])

        if existente:
            procesador = existente
            logger.info(f"Ya existía, se adopta: {procesador['name']}")
        else:
            procesador = await _pedir(
                cliente, "POST", "v1", f"{cl.PADRE}/processors",
                json={"type": destino["tipo"], "displayName": destino["display_name"]},
            )
            logger.info(f"Creado: {procesador['name']}")

        recurso = procesador["name"]

        # Inicializar el dataset. Si el procesador ya se había sincronizado,
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

        # Subir el esquema (reemplazo total: Document AI no ofrece PATCH incremental)
        esquema = destino["esquema"]()
        await _pedir(
            cliente, "PATCH", "v1beta3", f"{recurso}/dataset/datasetSchema",
            json={"name": f"{recurso}/dataset/datasetSchema", "documentSchema": esquema},
        )
        logger.info("Esquema subido.")

        final = await _pedir(cliente, "GET", "v1", recurso)

    pid = recurso.split("/")[-1]
    version = (final.get("defaultProcessorVersion") or "").split("/")[-1]
    campos = sum(len(et.get("properties", [])) for et in esquema["entityTypes"])
    print()
    print("=" * 70)
    print(f"  Processor : {final.get('displayName')}")
    print(f"  Tipo      : {final.get('type')}")
    print(f"  ID        : {pid}")
    print(f"  Estado    : {final.get('state')}")
    print(f"  Versión   : {version}")
    print(f"  Esquema   : {len(esquema['entityTypes'])} tipos, {campos} campos")
    print("=" * 70)
    print()
    print("Para usarlo, pon estos valores en su módulo:")
    print(f'  PROCESSOR_ID = "{pid}"')
    print(f'  VERSION = "{version}"')
    print()


if __name__ == "__main__":
    destinos = _destinos()
    if len(sys.argv) != 2 or sys.argv[1] not in destinos:
        print(f"Uso: python crear_processor.py [{' | '.join(destinos)}]")
        raise SystemExit(2)
    asyncio.run(crear(destinos[sys.argv[1]]))
