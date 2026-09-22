"""
FastAPI 入口。

部署相关的两点说明：

1. 前后端同源部署（nginx 反代）时浏览器不会触发跨域，本来就不需要 CORS。
   原配置的 allow_origins=["*"] 配上 allow_credentials=True 其实是无效组合，
   浏览器会直接拒绝。这里默认只放行本地开发口，需要别的域名再显式声明。

2. 若 frontend/dist 存在（npm run build 的产物），后端会顺带把它托管起来，
   这样不装 nginx 也能单端口跑通；生产环境仍推荐交给 nginx 托管静态资源。
"""
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routes.generation import router as generation_router
from routes.config import router as config_router
from routes.optimize import router as optimize_router
from routes.postprocess import router as postprocess_router
from services import storage

logger = logging.getLogger(__name__)

APP_VERSION = "1.0.0"

# ---------- CORS ----------
_cors_env = (os.environ.get("CORS_ALLOW_ORIGINS") or "").strip()
CORS_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()] or [
    "http://localhost:5176",
    "http://127.0.0.1:5176",
]

# ---------- 目录 ----------
BASE_DIR = Path(__file__).parent
UPLOADS_DIR = storage.UPLOAD_DIR
UPLOADS_DIR.mkdir(exist_ok=True)

FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """把生效的关键配置打到日志里，部署后一眼能确认有没有配对"""
    logger.info(
        "启动完成 | 存储后端=%s | 上传目录=%s | 前端静态资源=%s | CORS=%s",
        storage.backend_name(),
        UPLOADS_DIR,
        "已托管" if FRONTEND_DIST.is_dir() else "未构建（需 nginx 托管）",
        ",".join(CORS_ORIGINS),
    )
    if storage.use_oss():
        logger.info("OSS 模式已启用：产物将上传到云端，本机只保留处理中的临时文件")
    yield


app = FastAPI(title="万相视频生成工具", version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 本地工作区（OSS 模式下仍用于放处理中的临时文件）
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

# ---------- 业务路由 ----------
app.include_router(generation_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(optimize_router, prefix="/api")
app.include_router(postprocess_router, prefix="/api/postprocess")


@app.get("/api/health")
async def health():
    """健康检查。带上存储后端，方便部署后确认配置是否生效"""
    return {
        "status": "ok",
        "version": APP_VERSION,
        "storage_backend": storage.backend_name(),
    }


# ---------- 可选的单端口模式：直接托管前端构建产物 ----------
if FRONTEND_DIST.is_dir():
    _assets = FRONTEND_DIST / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # 能走到这里说明既不是 API 也不是已挂载的静态路径
        if full_path.startswith(("api/", "uploads/", "assets/")):
            raise HTTPException(status_code=404, detail="Not Found")

        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # 单页应用：未知路径交还 index.html，由前端路由接管
        return FileResponse(FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8002"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
