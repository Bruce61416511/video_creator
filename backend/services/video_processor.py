"""
视频后处理服务 - 使用 MoviePy 进行调色、拼接等操作
"""
import os
import uuid
import asyncio
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

import httpx
from moviepy import (
    VideoFileClip,
    concatenate_videoclips,
    ColorClip,
    CompositeVideoClip,
    vfx,
)

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


async def download_video(url: str, filename: str) -> str:
    """下载视频到本地，或返回本地路径"""
    # 如果是本地路径（/uploads/xxx），直接返回完整路径
    if url.startswith("/uploads/") or url.startswith("uploads/"):
        local_filename = url.split("/")[-1]
        local_path = UPLOAD_DIR / local_filename
        if local_path.exists():
            return str(local_path)
        raise ValueError(f"本地文件不存在: {local_path}")
    
    # 否则下载
    save_path = UPLOAD_DIR / filename
    
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        
        with open(save_path, "wb") as f:
            f.write(resp.content)
    
    return str(save_path)


def apply_color_grading(clip: VideoFileClip, preset: str = "warm") -> VideoFileClip:
    """
    应用色调预设
    
    Args:
        clip: 视频片段
        preset: 色调预设 (warm/cool/cinema/natural/bright/dark)
    """
    if preset == "warm":
        # 暖色调 - 增加亮度，轻微 gamma 校正
        clip = clip.with_effects([vfx.MultiplyColor(1.1), vfx.GammaCorrection(0.9)])
    elif preset == "cool":
        # 冷色调 - 降低亮度，增加对比度
        clip = clip.with_effects([vfx.MultiplyColor(0.95), vfx.LumContrast(0, 10, 0)])
    elif preset == "cinema":
        # 电影色调 - 降低亮度，增加对比度
        clip = clip.with_effects([vfx.MultiplyColor(0.9), vfx.LumContrast(0, 15, 0)])
    elif preset == "bright":
        # 明亮
        clip = clip.with_effects([vfx.MultiplyColor(1.2)])
    elif preset == "dark":
        # 暗调
        clip = clip.with_effects([vfx.MultiplyColor(0.8)])
    # natural 和 none 不做处理
    
    return clip


def apply_fade(clip: VideoFileClip, fade_in: float = 0.3, fade_out: float = 0.3) -> VideoFileClip:
    """添加淡入淡出"""
    effects = []
    if fade_in > 0:
        effects.append(vfx.FadeIn(fade_in))
    if fade_out > 0:
        effects.append(vfx.FadeOut(fade_out))
    if effects:
        clip = clip.with_effects(effects)
    return clip


async def process_single_video(
    video_url: str,
    color_preset: str = "warm",
    fade_in: float = 0.3,
    fade_out: float = 0.3,
) -> str:
    """
    处理单个视频
    
    Returns:
        处理后的视频本地路径
    """
    # 下载视频
    filename = f"processed_{uuid.uuid4().hex[:8]}.mp4"
    local_path = await download_video(video_url, filename)
    logger.info(f"下载视频完成: {local_path}")
    
    # 处理视频
    clip = VideoFileClip(local_path)
    
    # 应用色调
    if color_preset != "none":
        clip = apply_color_grading(clip, color_preset)
    
    # 应用淡入淡出
    if fade_in > 0 or fade_out > 0:
        clip = apply_fade(clip, fade_in, fade_out)
    
    # 导出
    output_filename = f"output_{uuid.uuid4().hex[:8]}.mp4"
    output_path = UPLOAD_DIR / output_filename
    clip.write_videofile(
        str(output_path),
        codec="libx264",
        audio_codec="aac",
        logger=None,  # 禁用 moviepy 的日志
        # moviepy 默认把临时音轨写到进程工作目录（后端从 backend/ 启动 → 会掉在仓库根目录，
        # 形如 merged_xxxxTEMP_MPY_wvf_snd.mp4，进程被杀就残留）。显式指到 uploads/ 里。
        temp_audiofile_path=str(UPLOAD_DIR),
    )
    clip.close()
    
    return f"/uploads/{output_filename}"


async def merge_videos(
    video_urls: List[str],
    color_preset: str = "warm",
    transition: str = "none",  # none / blank / fade
    transition_duration: float = 0.5,
) -> str:
    """
    合并多个视频
    
    Args:
        video_urls: 视频 URL 列表
        color_preset: 色调预设
        transition: 转场类型
        transition_duration: 转场时长
    
    Returns:
        合并后的视频本地路径
    """
    clips = []
    temp_files = []
    
    try:
        # 下载并处理每个视频
        for i, url in enumerate(video_urls):
            logger.info(f"处理视频 {i+1}/{len(video_urls)}")
            filename = f"temp_{uuid.uuid4().hex[:8]}.mp4"
            local_path = await download_video(url, filename)
            temp_files.append(local_path)
            
            clip = VideoFileClip(local_path)
            
            # 应用色调
            if color_preset != "none":
                clip = apply_color_grading(clip, color_preset)
            
            # 应用淡入淡出
            if transition == "fade":
                clip = apply_fade(clip, transition_duration, transition_duration)
            
            clips.append(clip)
        
        # 处理转场
        if transition == "blank":
            # 添加黑场间隔
            size = clips[0].size if clips else (1920, 1080)
            blank = ColorClip(size=size, color=(0, 0, 0), duration=transition_duration)
            
            final_clips = []
            for i, clip in enumerate(clips):
                final_clips.append(clip)
                if i < len(clips) - 1:
                    final_clips.append(blank)
            
            final = concatenate_videoclips(final_clips)
        else:
            # 直接拼接或淡入淡出
            final = concatenate_videoclips(clips, method="compose")
        
        # 导出
        output_filename = f"merged_{uuid.uuid4().hex[:8]}.mp4"
        output_path = UPLOAD_DIR / output_filename
        final.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac",
            logger=None,
            # 同 process_video：临时音轨别落在进程工作目录（即 backend/），统一放 uploads/
            temp_audiofile_path=str(UPLOAD_DIR),
        )
        
        return f"/uploads/{output_filename}"
        
    finally:
        # 清理临时文件
        for clip in clips:
            try:
                clip.close()
            except:
                pass
        
        for temp_file in temp_files:
            try:
                os.remove(temp_file)
            except:
                pass
