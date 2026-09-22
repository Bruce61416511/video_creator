"""
uploader.py —— 文件上传服务。

落盘位置交给 services.storage 统一决定：
  STORAGE_BACKEND=local → 存 backend/uploads/，返回 /uploads/xxx
  STORAGE_BACKEND=oss   → 直传 OSS，返回公网 URL

注意：OSS 模式下返回的就是公网 URL，dashscope_client 里那一跳
「本地文件 → 传 DashScope files 接口 → 换临时 URL」会被自动跳过，
少一次中转、也少一处可能失败的环节。
"""
import logging
from pathlib import Path

import httpx

from services import storage

logger = logging.getLogger(__name__)

# 兼容历史引用（main.py 等地方曾直接 import 这个常量）
UPLOAD_DIR = storage.UPLOAD_DIR


async def save_upload(file_content: bytes, filename: str) -> dict:
    """保存上传的文件，返回 {filename, path, url}"""
    ext = Path(filename).suffix or ".bin"
    save_name = storage.new_name(ext=ext)

    url = await storage.save_bytes(file_content, save_name)

    return {
        "filename": save_name,
        # 本地模式下是磁盘绝对路径；OSS 模式下文件不在本机，回落成 url
        "path": url if storage.use_oss() else str(storage.UPLOAD_DIR / save_name),
        "url": url,
    }


async def upload_to_dashscope(file_path: str) -> str:
    """
    把本地文件上传到 DashScope，换取可公网访问的 OSS URL。

    仅在 local 存储模式下需要：DashScope 要求参考素材是公网可访问的地址，
    而我们本地只有 /uploads/xxx 这种相对路径，必须先过一道它的文件接口。

    若已启用 OSS 存储，参考素材本身就是公网地址，不必走这里。
    """
    from services.key_manager import get_dashscope_api_key

    api_key = get_dashscope_api_key()
    if not api_key:
        raise ValueError("请先配置 DASHSCOPE_API_KEY")

    # Step 1: 上传文件获取 file_id
    upload_url = "https://dashscope.aliyuncs.com/api/v1/files"
    headers = {"Authorization": f"Bearer {api_key}"}

    with open(file_path, "rb") as f:
        files = {"file": (Path(file_path).name, f)}
        data = {"purpose": "inference"}

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                upload_url,
                files=files,
                data=data,
                headers=headers,
            )
            resp.raise_for_status()
            upload_result = resp.json()

    # 解析上传结果（API 返回 data 字段，不是 output）
    uploaded_files = upload_result.get("data", {}).get("uploaded_files", [])
    if not uploaded_files:
        raise ValueError(f"文件上传失败: {upload_result}")

    file_id = uploaded_files[0].get("file_id")
    if not file_id:
        raise ValueError(f"未获取到 file_id: {upload_result}")

    # Step 2: 获取文件详情（包含 URL）
    get_url = f"https://dashscope.aliyuncs.com/api/v1/files/{file_id}"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(get_url, headers=headers)
        resp.raise_for_status()
        file_info = resp.json()

    oss_url = file_info.get("data", {}).get("url")
    if not oss_url:
        raise ValueError(f"未获取到文件 URL: {file_info}")

    logger.info("已把本地文件上传到 DashScope 换取公网地址: %s", Path(file_path).name)
    return oss_url
