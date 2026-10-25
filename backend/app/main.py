import logging
import os
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1.analysis import router as analysis_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.auth import router as auth_router
from app.api.v1.business import router as business_router
from app.api.v1.finance import router as finance_router
from app.api.v1.health import router as health_router
from app.api.v1.imports import router as imports_router
from app.api.v1.receipts import router as receipts_router
from app.api.v1.workspaces import router as workspace_router
from app.core.config import security_configuration
from app.security.request_limits import RequestLimitsMiddleware

settings = security_configuration()
app = FastAPI(
    title=os.getenv("FINSCOPE_APP_NAME", "FinScope API"),
    docs_url=None if settings["production"] else "/docs",
    redoc_url=None if settings["production"] else "/redoc",
    openapi_url=None if settings["production"] else "/openapi.json",
)
app.add_middleware(RequestLimitsMiddleware)
if settings["production"]:
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=[*settings["hosts"], "127.0.0.1"]
    )


@app.middleware("http")
async def response_privacy(request: Request, call_next):
    request.state.request_id = str(uuid4())
    if (
        settings["production"]
        and request.url.scheme != "https"
        and request.url.path not in ("/api/v1/health", "/api/v1/ready")
    ):
        response = JSONResponse(
            status_code=400,
            content={
                "error": {"code": "https_required", "message": "请通过 HTTPS 访问"}
            },
        )
    else:
        try:
            response = await call_next(request)
        except Exception as exc:
            if not settings["production"]:
                raise
            response = await unexpected_error(request, exc)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", str(uuid4()))
    # 不记录异常正文/SQL 参数/请求体，防止财务内容或凭据进入日志。
    logging.getLogger("finscope").error(
        "request_failed type=%s request_id=%s", type(exc).__name__, request_id
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "服务器暂时无法处理请求，请稍后重试",
                "request_id": request_id,
            }
        },
        headers={"Cache-Control": "no-store", "X-Request-ID": request_id},
    )


app.include_router(health_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(workspace_router, prefix="/api/v1")
app.include_router(finance_router, prefix="/api/v1")
app.include_router(business_router, prefix="/api/v1")
app.include_router(analytics_router, prefix="/api/v1")
app.include_router(imports_router, prefix="/api/v1")
app.include_router(receipts_router, prefix="/api/v1")
app.include_router(analysis_router, prefix="/api/v1")


@app.exception_handler(StaleDataError)
async def concurrent_edit(_: Request, __: StaleDataError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "error": {
                "code": "concurrent_edit",
                "message": "记录已被其他操作修改，请刷新后重试",
            }
        },
    )


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": f"http_{exc.status_code}", "message": exc.detail}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
    # 校验异常可能包含密码原文，因此绝不把原始异常或输入值返回给客户端。
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "输入数据无效，请检查字段格式",
            }
        },
    )
