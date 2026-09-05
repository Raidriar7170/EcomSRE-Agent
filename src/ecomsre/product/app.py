"""FastAPI Product application and process entrypoint."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
import uvicorn

from ecomsre.product.api import router
from ecomsre.product.baselines import BaselineRepositoryV1
from ecomsre.product.changes import ChangeEventRepositoryV1
from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.errors import ProductError
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.knowledge.repository import KnowledgeRepositoryV1
from ecomsre.product.pilot.baseline_audit_v021 import (
    BaselineReadinessAuditRepositoryV021,
)
from ecomsre.product.pilot.baseline_readiness_v023 import (
    ProductBaselineReadinessAuditRepositoryV023,
)
from ecomsre.product.incidents.repository import (
    DiagnosisRepositoryV1,
    IncidentRepositoryV1,
)
from ecomsre.product.remediation.runtime import configured_attempts
from ecomsre.product.remediation.api import router as remediation_router
from ecomsre.product.remediation.repository import RemediationRepositoryV1
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from ecomsre.product.remediation.metrics import ProductRemediationMetricsV1


LOGGER = logging.getLogger(__name__)


def create_app(settings: ProductSettingsV1 | None = None) -> FastAPI:
    resolved = settings or ProductSettingsV1.from_environment()
    resolved.data_root.mkdir(parents=True, exist_ok=True)
    store = SqliteStoreV1(resolved.sqlite_path)
    app = FastAPI(
        title="EcomSRE Product API",
        version="0.1.0",
        description="Self-hosted read-only incident diagnosis Product MVP.",
    )
    app.state.settings = resolved
    app.state.store = store
    app.state.object_store = ContentAddressedObjectStoreV1(
        resolved.object_store_root,
        metadata_store=store,
    )
    app.state.environments = EnvironmentRepositoryV1(store)
    app.state.services = ServiceCatalogRepositoryV1(store)
    app.state.capabilities = CapabilityMatrixRepositoryV1(store)
    app.state.baselines = BaselineRepositoryV1(store)
    app.state.baseline_readiness_audits = BaselineReadinessAuditRepositoryV021(store)
    app.state.baseline_readiness_audits_v023 = (
        ProductBaselineReadinessAuditRepositoryV023(store)
    )
    app.state.changes = ChangeEventRepositoryV1(store)
    app.state.jobs = JobRepositoryV1(store)
    app.state.incidents = IncidentRepositoryV1(
        store,
        environments=app.state.environments,
        services=app.state.services,
        capabilities=app.state.capabilities,
        baselines=app.state.baselines,
    )
    app.state.diagnoses = DiagnosisRepositoryV1(store, app.state.object_store)
    app.state.knowledge = KnowledgeRepositoryV1(store, app.state.object_store)
    app.state.metrics = ProductRemediationMetricsV1(store)
    app.state.remediation = RemediationRepositoryV1(store, app.state.object_store)
    app.state.remediation_attempts = configured_attempts(app.state.remediation)

    @app.middleware("http")
    async def record_http_request(request: Request, call_next: object):
        response = await call_next(request)  # type: ignore[operator]
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        app.state.metrics.increment(
            "ecomsre_http_requests_total",
            {
                "method": request.method,
                "route": route_path,
                "status_class": f"{response.status_code // 100}xx",
            },
        )
        return response

    @app.exception_handler(ProductError)
    async def product_error_handler(
        _request: Request,
        exc: ProductError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "The request does not satisfy the Product API contract.",
                    "details": {},
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        _request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        code = "RESOURCE_NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        message = (
            "The requested API resource does not exist."
            if exc.status_code == 404
            else "The HTTP request could not be completed."
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": message, "details": {}}},
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(
        _request: Request,
        _exc: Exception,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_CONTRACT_FAILURE",
                    "message": "The Product could not complete the request safely.",
                    "details": {},
                }
            },
        )

    app.include_router(router)
    app.include_router(remediation_router)
    if resolved.resolved_admin_token() is None:
        LOGGER.warning("LOCAL_NO_AUTH: Product mutations are unauthenticated on loopback")
    return app


def main() -> None:
    settings = ProductSettingsV1.from_environment()
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()


__all__ = ("create_app", "main")
