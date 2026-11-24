"""Aplicación FastAPI: CORS, carga de modelos/suscriptores e inclusión automática de routers."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.discovery import import_submodules, load_plugins

logger = logging.getLogger("app")


def configure_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=settings.log_level.upper(),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    else:
        root.setLevel(settings.log_level.upper())


def include_routers(app: FastAPI) -> list[str]:
    """Incluye el ``router`` de cada módulo de ``app.routers`` (orden alfabético)."""
    included: list[str] = []
    for module in import_submodules("app.routers"):
        router = getattr(module, "router", None)
        if router is None:
            logger.warning("El módulo %s no expone 'router'; se omite", module.__name__)
            continue
        app.include_router(router)
        included.append(module.__name__)
    return included


def create_app() -> FastAPI:
    configure_logging()
    load_plugins()
    docs = settings.docs_enabled
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="API del Service Desk corporativo de Energuate.",
        # El proxy publica la API bajo API_ROOT_PATH (p.ej. /api) y quita el prefijo.
        root_path=settings.api_root_path,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Necesarias para que el navegador lea el nombre del adjunto descargado y su checksum.
        expose_headers=["Content-Disposition", "X-Checksum-SHA256"],
    )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(_: Request, exc: IntegrityError) -> JSONResponse:
        logger.warning("Conflicto de integridad: %s", exc.orig)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "Conflicto de integridad de datos"},
        )

    routers = include_routers(app)
    logger.info("Routers cargados: %s", ", ".join(routers))
    logger.info(
        "Entorno=%s auth=%s docs=%s root_path=%r",
        settings.environment, settings.auth_mode, docs, settings.api_root_path,
    )
    if settings.auth_mode == "dev":
        logger.warning("AUTH_MODE=dev: autenticación de laboratorio por headers. No usar en producción.")
    return app


app = create_app()
