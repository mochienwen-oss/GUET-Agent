"""FastAPI 应用装配、HTTP 异常处理及前端静态托管。"""
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.errors import ApiError
from core.paths import PROJECT_ROOT
from application.lifespan import lifespan
from routers import admin as admin_router, auth as auth_router, chat as chat_router, ppt as ppt_router


app = FastAPI(title="校园知识库助手 API", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.message})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": "请求参数不合法"})


@app.get("/api/health")
def health():
    return {"status": "ok"}


# 路由挂载
app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
app.include_router(chat_router.router, prefix="/api", tags=["chat"])
app.include_router(ppt_router.router, prefix="/api", tags=["ppt"])
app.include_router(admin_router.router, prefix="/api/admin", tags=["admin"])


# 生产模式：若前端已构建（frontend/dist 存在），由后端同端口托管静态页面
_DIST = str(PROJECT_ROOT / "frontend" / "dist")
if os.path.isdir(_DIST):
    class SPAStaticFiles(StaticFiles):
        # SPA history 路由：找不到的路径回退到 index.html，避免刷新子页面 404
        async def get_response(self, path, scope):
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code == 404 and not path.startswith("api"):
                    return await super().get_response("index.html", scope)
                raise

    app.mount("/", SPAStaticFiles(directory=_DIST, html=True), name="static")


