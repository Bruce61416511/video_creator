"""
video_processor.py —— 视频后处理（调色、拼接）。

两个部署相关的要点：

1. moviepy / ffmpeg 全是同步阻塞调用。直接在 async 路由里跑会把 FastAPI 的
   事件循环堵死（两个人同时点「合并」→ 全站假死）。所以这里把所有重活都丢进
   asyncio.to_thread，并用信号量限制并发数，超出的请求排队而不是把机器打爆。

2. 输入输出统一走 services.storage：输入用 localize 变成本地文件（ffmpeg 只能
   读本地），输出交给 upload_file 决定最终去处（local 留盘 / OSS 上传后清本地）。
"""
import os
import asyncio
import logging
from pathlib import Path
from typing import List

from moviepy import (
    VideoFileClip,
    concatenate_videoclips,
    ColorClip,
    CompositeVideoClip,
    vfx,
)

from services import storage

logger = logging.getLogger(__name__)

UPLOAD_DIR = storage.UPLOAD_DIR

# 一次合并要把 N 个片段同时读进内存，很吃 CPU 和内存。
# 默认最多 2 个任务并行，多出来的在信号量上排队，避免把服务拖垮。
_MAX_CONCURRENT = max(1, int(os.environ.get("MAX_CONCURRENT_VIDEO_JOBS", "2")))
_JOB_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT)


# ========== 输入本地化 ==========

async def _localize_inputs(urls: List[str]):
    """
    把一组文件引用变成本地路径。

    Returns:
        (paths, temp_files)
        paths      —— 与入参等长的本地文件路径
        temp_files —— 本次为处理而临时下载的文件，办完事应当清理
    """
    paths: List[str] = []
    temps: List[str] = []

    for url in urls:
        # 用户上传的素材本来就躺在 uploads/ 里，永远不删
        if storage.is_local_ref(url):
            paths.append(await storage.localize(url))
            continue

        # 远端地址（DashScope 临时链接 / OSS 对象）：localize 会落到 uploads/。
        # 只有「本次新下载的」才记入待清理列表，避免误删同名的既有文件。
        name = url.split("?")[0].rstrip("/").split("/")[-1]
        existed_before = bool(name) and (UPLOAD_DIR / name).exists()

        path = await storage.localize(url)
        paths.append(path)
        if not existed_before:
            temps.append(path)

    return paths, temps


# ========== 特效 ==========

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


def _write_video(clip, output_path: Path) -> None:
    """统一出口：codec / 临时音轨位置都在这里定，避免两处各写一遍"""
    clip.write_videofile(
        str(output_path),
        codec="libx264",
        audio_codec="aac",
        logger=None,
        # moviepy 默认把临时音轨写到进程工作目录（后端从 backend/ 启动 → 会掉在仓库根目录，
        # 形如 merged_xxxxTEMP_MPY_wvf_snd.mp4，进程被杀就残留）。显式指到 uploads/ 里。
        temp_audiofile_path=str(UPLOAD_DIR),
    )


# ========== 同步渲染（只在线程池里调用）==========

def _render_single_sync(
    local_path: str,
    output_path: Path,
    color_preset: str,
    fade_in: float,
    fade_out: float,
) -> None:
    """单个视频的调色 + 淡入淡出，同步阻塞"""
    clip = VideoFileClip(local_path)
    try:
        if color_preset and color_preset != "none":
            clip = apply_color_grading(clip, color_preset)
        if fade_in > 0 or fade_out > 0:
            clip = apply_fade(clip, fade_in, fade_out)
        _write_video(clip, output_path)
    finally:
        clip.close()


def _render_merge_sync(
    local_paths: List[str],
    output_path: Path,
    color_preset: str,
    transition: str,
    transition_duration: float,
) -> None:
    """多片段拼接 + 调色 + 转场，同步阻塞"""
    clips = []
    try:
        for path in local_paths:
            clip = VideoFileClip(path)
            if color_preset and color_preset != "none":
                clip = apply_color_grading(clip, color_preset)
            if transition == "fade":
                clip = apply_fade(clip, transition_duration, transition_duration)
            clips.append(clip)

        if not clips:
            raise ValueError("没有可合并的视频片段")

        if transition == "blank":
            # 分镜之间插黑场
            size = clips[0].size
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

        try:
            _write_video(final, output_path)
        finally:
            final.close()
    finally:
        for clip in clips:
            try:
                clip.close()
            except Exception:
                pass


# ========== 对外接口 ==========

async def process_single_video(
    video_url: str,
    color_preset: str = "warm",
    fade_in: float = 0.3,
    fade_out: float = 0.3,
) -> str:
    """
    处理单个视频（调色 + 淡入淡出）

    Returns:
        处理后视频的可访问地址（local 为 /uploads/xxx，OSS 为公网 URL）
    """
    async with _JOB_SEMAPHORE:
        local_path = await storage.localize(video_url)

        output_name = storage.new_name(prefix="output_", ext=".mp4", length=8)
        output_path = UPLOAD_DIR / output_name

        logger.info("处理单个视频: preset=%s", color_preset)
        await asyncio.to_thread(
            _render_single_sync, local_path, output_path, color_preset, fade_in, fade_out
        )

        url = await storage.upload_file(output_path, output_name)
        logger.info("单个视频处理完成: %s", output_name)
        return url


async def merge_videos(
    video_urls: List[str],
    color_preset: str = "warm",
    transition: str = "none",  # none / blank / fade
    transition_duration: float = 0.5,
) -> str:
    """
    合并多个视频

    Args:
        video_urls: 视频地址列表（http URL 或 /uploads/xxx 本地引用）
        color_preset: 色调预设
        transition: 转场类型
        transition_duration: 转场时长

    Returns:
        合并后视频的可访问地址（local 为 /uploads/xxx，OSS 为公网 URL）
    """
    async with _JOB_SEMAPHORE:
        # 网络下载是异步的，先把素材备齐再进线程池
        local_paths, temp_files = await _localize_inputs(video_urls)

        output_name = storage.new_name(prefix="merged_", ext=".mp4", length=8)
        output_path = UPLOAD_DIR / output_name

        logger.info(
            "开始合并 %d 个片段: preset=%s, transition=%s",
            len(local_paths), color_preset, transition,
        )
        try:
            await asyncio.to_thread(
                _render_merge_sync,
                local_paths, output_path, color_preset, transition, transition_duration,
            )
            url = await storage.upload_file(output_path, output_name)
            logger.info("合并完成: %s", output_name)
            return url
        finally:
            # 为合并而下载回来的片段用完即删（用户自己上传的素材不在这个列表里）
            for temp_file in temp_files:
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
