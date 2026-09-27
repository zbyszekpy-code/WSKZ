"""FastAPI application entrypoint for ai-router-poc."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import get_routing_service, router
from app.core.errors import RoutingError
from app.core.routing import RoutingService


def create_app(routing_service: RoutingService | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title="ai-router-poc",
        version="0.1.0",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        redoc_url=None,
    )
    if routing_service is not None:
        application.dependency_overrides[get_routing_service] = lambda: routing_service
    application.include_router(router, prefix="/api/v1")

    @application.exception_handler(RoutingError)
    async def routing_error_handler(request: Request, exc: RoutingError) -> JSONResponse:
        """Map downstream routing failures to HTTP 503."""
        return JSONResponse(status_code=503, content={"detail": exc.detail})

    @application.get("/health")
    def health() -> dict[str, str]:
        """Liveness probe used by the Compose healthcheck."""
        return {"status": "ok"}

    return application


app = create_app()
