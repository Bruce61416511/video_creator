import json
import os
import time
import asyncio
import logging
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

CONFIG_DIR = Path(__file__).parent.parent / "config"
UPLOAD_DIR = Path(__file__).parent.parent / "uploads"

# 内存中存储任务状态
_tasks: dict = {}


def _load_config() -> dict:
    with open(CONFIG_DIR / "models.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _is_local_url(url: str) -> bool:
    """判断是否是本地上传的文件路径"""
    return url.startswith("/uploads/") or url.startswith("uploads/")


async def _ensure_public_url(url: str) -> str:
    """
    如果是本地文件路径，上传到 DashScope 获取公网 URL。
    如果已经是公网 URL，直接返回。
    """
    print(f"[DEBUG] _ensure_public_url: {url}")
    if not _is_local_url(url):
        print(f"[DEBUG] 已是公网 URL，直接返回")
        return url  # 已经是公网 URL
    
    # 拼接完整本地路径
    filename = url.split("/")[-1]
    local_path = UPLOAD_DIR / filename
    print(f"[DEBUG] 本地文件路径: {local_path}")
    
    if not local_path.exists():
        raise ValueError(f"本地文件不存在: {local_path}")
    
    # 上传到 DashScope
    from services.uploader import upload_to_dashscope
    print(f"[DEBUG] 开始上传到 DashScope...")
    public_url = await upload_to_dashscope(str(local_path))
    print(f"[DEBUG] 上传成功，获取到公网 URL")
    return public_url


async def _submit_task(endpoint: str, payload: dict) -> str:
    from services.key_manager import get_dashscope_api_key
    api_key = get_dashscope_api_key()
    if not api_key:
        raise ValueError("请先在环境变量或配置中设置 DASHSCOPE_API_KEY")

    url = f"https://dashscope.aliyuncs.com/api/v1/services/aigc/{endpoint}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    task_id = data.get("output", {}).get("task_id", "")
    if not task_id:
        raise ValueError(f"提交任务失败: {data}")
    return task_id


async def _poll_task(task_id: str) -> dict:
    from services.key_manager import get_dashscope_api_key
    api_key = get_dashscope_api_key()

    url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
    headers = {"Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    output = data.get("output", {})
    status = output.get("task_status", "UNKNOWN")
    return {
        "task_id": task_id,
        "status": status,
        "output": output,
        "usage": data.get("usage", {}),
    }


# ========== 文生图 ==========

async def submit_t2i(prompt: str, params: dict) -> str:
    config = _load_config()
    model_cfg = config["models"]["t2i"]
    model_id = model_cfg.get("model_id", "wanx2.6-t2i")
    defaults = model_cfg.get("defaults", {})

    payload = {
        "model": model_id,
        "input": {
            "prompt": prompt,
        },
        "parameters": {
            "size": params.get("size", defaults.get("size", "1024*1024")),
            "n": params.get("n", defaults.get("n", 4)),
        },
    }
    style = params.get("style", defaults.get("style", ""))
    if style:
        payload["parameters"]["style"] = style

    seed = params.get("seed")
    if seed is not None:
        payload["parameters"]["seed"] = seed

    return await _submit_task("text2image/image-synthesis", payload)


# ========== 文生视频 ==========

async def submit_t2v(prompt: str, params: dict) -> str:
    config = _load_config()
    model_cfg = config["models"]["t2v"]
    model_id = model_cfg.get("model_id", "wan2.7-t2v")
    defaults = model_cfg.get("defaults", {})

    payload = {
        "model": model_id,
        "input": {
            "prompt": prompt,
        },
        "parameters": {
            "resolution": params.get("resolution", defaults.get("resolution", "720P")),
            "ratio": params.get("ratio", defaults.get("ratio", "16:9")),
            "duration": params.get("duration", defaults.get("duration", 5)),
        },
    }
    if params.get("prompt_extend", defaults.get("prompt_extend", True)):
        payload["parameters"]["prompt_extend"] = True

    seed = params.get("seed")
    if seed is not None:
        payload["parameters"]["seed"] = seed

    return await _submit_task("video-generation/video-synthesis", payload)


# ========== 首尾帧生视频 ==========

async def submit_i2v(prompt: str, img_url: str, last_img_url: Optional[str], params: dict) -> str:
    config = _load_config()
    model_cfg = config["models"]["i2v"]
    model_id = model_cfg.get("model_id", "wan2.7-i2v")
    defaults = model_cfg.get("defaults", {})

    # 确保图片 URL 是公网可访问的
    public_img_url = await _ensure_public_url(img_url)
    
    media = [{"type": "first_frame", "url": public_img_url}]
    if last_img_url:
        public_last_img_url = await _ensure_public_url(last_img_url)
        media.append({"type": "last_frame", "url": public_last_img_url})

    payload = {
        "model": model_id,
        "input": {
            "prompt": prompt,
            "media": media,
        },
        "parameters": {
            "resolution": params.get("resolution", defaults.get("resolution", "720P")),
            "ratio": params.get("ratio", defaults.get("ratio", "16:9")),
            "duration": params.get("duration", defaults.get("duration", 5)),
        },
    }

    return await _submit_task("video-generation/video-synthesis", payload)


# ========== 参考素材生视频 ==========

async def submit_r2v(prompt: str, media_items: list, params: dict) -> str:
    logger.info(f"[submit_r2v] media_items: {media_items}")
    config = _load_config()
    model_cfg = config["models"]["r2v"]
    model_id = model_cfg.get("model_id", "wan2.7-r2v")
    defaults = model_cfg.get("defaults", {})

    # 处理每个 media item，如果是本地文件则上传获取公网 URL
    processed_media = []
    for item in media_items:
        logger.info(f"[submit_r2v] processing item: {item}")
        item_type = item.get("type", "")
        item_url = item.get("url", "")
        logger.info(f"[submit_r2v] item_type={item_type}, item_url={item_url}, is_local={_is_local_url(item_url)}")
        
        # 只处理图片类型，音频 URL 保持原样（TTS 生成的已经是公网 URL）
        if item_type in ("reference_image", "ref_image") and _is_local_url(item_url):
            public_url = await _ensure_public_url(item_url)
            processed_media.append({"type": item_type, "url": public_url})
        else:
            processed_media.append(item)

    payload = {
        "model": model_id,
        "input": {
            "prompt": prompt,
            "media": processed_media,
        },
        "parameters": {
            "resolution": params.get("resolution", defaults.get("resolution", "720P")),
            "ratio": params.get("ratio", defaults.get("ratio", "16:9")),
            "duration": params.get("duration", defaults.get("duration", 5)),
        },
    }

    return await _submit_task("video-generation/video-synthesis", payload)


# ========== 任务状态查询 ==========

async def get_task_status(task_id: str) -> dict:
    result = await _poll_task(task_id)
    # 缓存到内存
    _tasks[task_id] = result
    return result


def get_cached_task(task_id: str) -> Optional[dict]:
    return _tasks.get(task_id)
