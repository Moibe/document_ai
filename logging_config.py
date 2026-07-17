import logging
import sys


class FiltroNivelMaximo(logging.Filter):
    """Deja pasar únicamente registros con severidad MENOR al nivel indicado."""

    def __init__(self, nivel_maximo):
        super().__init__()
        self.nivel_maximo = nivel_maximo

    def filter(self, record):
        return record.levelno < self.nivel_maximo


def configurar_logging(nivel=logging.INFO):
    """
    Configura el logging de toda la app.

    INFO/DEBUG van a stdout y WARNING/ERROR/CRITICAL van a stderr, para
    aprovechar que pm2 ya separa esos streams en <app>-out.log y <app>-error.log.
    """
    formato = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler_stdout = logging.StreamHandler(sys.stdout)
    handler_stdout.setLevel(nivel)
    handler_stdout.setFormatter(formato)
    handler_stdout.addFilter(FiltroNivelMaximo(logging.WARNING))

    handler_stderr = logging.StreamHandler(sys.stderr)
    handler_stderr.setLevel(logging.WARNING)
    handler_stderr.setFormatter(formato)

    root = logging.getLogger()
    root.setLevel(nivel)
    root.handlers = [handler_stdout, handler_stderr]
