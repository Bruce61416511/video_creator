import os
import uuid
import aiofiles
import httpx
from pathlib import Path

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


async def save_upload(file_content: bytes, filename: str) -> dict:
    """保存文件到本地 uploads 目录"""
    ext = Path(filename).suffix or ".bin"
    save_name = f"{uuid.uuid4().hex[:12]}{ext}"
    save_path = UPLOAD_DIR / save_name

    async with aiofiles.open(save_path, "wb") as f:
        await f.write(file_content)

    return {
        "filename": save_name,
        "path": str(save_path),
        "url": f"/uploads/{save_name}",
    }


async def upload_to_dashscope(file_path: str) -> str:
    """
    上传文件到 DashScope，返回可公网访问的 OSS URL。
    
    Args:
        file_path: 本地文件路径
        
    Returns:
        str: DashScope OSS URL（有时效性，但足够视频生成使用）
        
    Raises:
        ValueError: 上传失败时抛出
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
                headers=headers
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

    return oss_url
