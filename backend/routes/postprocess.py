"""
视频后处理 API - 调色、合并等
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import logging

from services.video_processor import process_single_video, merge_videos

logger = logging.getLogger(__name__)

router = APIRouter()


class ProcessRequest(BaseModel):
    """单个视频处理请求"""
    video_url: str
    color_preset: str = "warm"  # warm/cool/cinema/natural/bright/dark/none
    fade_in: float = 0.3
    fade_out: float = 0.3


class MergeRequest(BaseModel):
    """合并视频请求"""
    video_urls: List[str]  # 可以是 http:// URL 或 /uploads/xxx.mp4 本地路径
    color_preset: str = "warm"
    transition: str = "none"  # none / blank / fade
    transition_duration: float = 0.5


@router.post("/process")
async def api_process_video(req: ProcessRequest):
    """处理单个视频（调色、淡入淡出等）"""
    try:
        logger.info(f"处理视频: {req.video_url[:50]}..., preset={req.color_preset}")
        result_url = await process_single_video(
            video_url=req.video_url,
            color_preset=req.color_preset,
            fade_in=req.fade_in,
            fade_out=req.fade_out,
        )
        return {"url": result_url, "status": "success"}
    except Exception as e:
        logger.error(f"处理视频失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/merge")
async def api_merge_videos(req: MergeRequest):
    """合并多个视频"""
    try:
        logger.info(f"合并 {len(req.video_urls)} 个视频, preset={req.color_preset}, transition={req.transition}")
        result_url = await merge_videos(
            video_urls=req.video_urls,
            color_preset=req.color_preset,
            transition=req.transition,
            transition_duration=req.transition_duration,
        )
        return {"url": result_url, "status": "success"}
    except Exception as e:
        logger.error(f"合并视频失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/presets")
async def api_get_presets():
    """获取可用的色调预设"""
    return {
        "color_presets": [
            {"value": "none", "label": "无", "description": "不调整色调"},
            {"value": "warm", "label": "暖色调", "description": "增强红黄色，适合室内场景"},
            {"value": "cool", "label": "冷色调", "description": "增强蓝色，适合科技/清凉感"},
            {"value": "cinema", "label": "电影色", "description": "电影质感，压暗高光"},
            {"value": "natural", "label": "自然", "description": "保持原始色调"},
            {"value": "bright", "label": "明亮", "description": "整体提亮"},
            {"value": "dark", "label": "暗调", "description": "整体压暗，营造氛围"},
        ],
        "transitions": [
            {"value": "none", "label": "无转场", "description": "直接拼接"},
            {"value": "blank", "label": "黑场间隔", "description": "分镜间黑屏"},
            {"value": "fade", "label": "淡入淡出", "description": "渐显渐隐"},
        ]
    }
