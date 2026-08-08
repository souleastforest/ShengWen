"""
FFmpeg 工具模块 - 提供统一的 ffmpeg 配置和管理

使用 imageio-ffmpeg 来自动下载和配置 ffmpeg，避免手动安装系统级的 ffmpeg。
"""

import os
import shutil
from typing import Optional

from loguru import logger


class FFmpegHelper:
    """
    FFmpeg 辅助类，提供统一的 ffmpeg 二进制文件管理和配置。
    
    这个类会自动使用 imageio-ffmpeg 提供的 ffmpeg 二进制文件，
    避免用户需要手动安装系统级的 ffmpeg。
    """
    
    _ffmpeg_path: Optional[str] = None
    _ffmpeg_dir: Optional[str] = None
    
    @classmethod
    def _ensure_ffmpeg_exe_name(cls, original_path: str) -> str:
        """
        确保 ffmpeg 可执行文件名为 ffmpeg.exe（或平台对应名称）。
        
        imageio-ffmpeg 提供的文件名可能像 ffmpeg-win64-v4.2.2.exe，
        我们需要在同一目录下创建一个名为 ffmpeg.exe 的副本/链接。
        
        参数:
            original_path: 原始 ffmpeg 可执行文件的路径。
            
        返回:
            标准名称的 ffmpeg 可执行文件路径。
        """
        # 获取目标名称
        if os.name == 'nt':  # Windows
            target_name = 'ffmpeg.exe'
        else:  # Unix-like
            target_name = 'ffmpeg'
        
        # 获取原始文件名
        original_name = os.path.basename(original_path)
        
        # 如果文件名已经是标准名称，直接返回
        if original_name == target_name:
            return original_path
        
        # 获取目标路径
        original_dir = os.path.dirname(original_path)
        target_path = os.path.join(original_dir, target_name)
        
        # 检查目标路径是否已存在
        if os.path.exists(target_path):
            # 检查是否指向同一个文件
            if os.path.samefile(original_path, target_path):
                return target_path
            # 否则删除旧的
            try:
                os.remove(target_path)
                logger.info(f"[FFmpegHelper] 删除旧的 ffmpeg.exe: {target_path}")
            except Exception as e:
                logger.warning(f"[FFmpegHelper] 无法删除旧文件 {target_path}: {e}")
        
        # 在 Windows 上，由于需要管理员权限才能创建符号链接，
        # 我们直接复制文件。对于 Linux/Mac，可以创建符号链接。
        try:
            if os.name == 'nt':  # Windows
                shutil.copy2(original_path, target_path)
                logger.info(f"[FFmpegHelper] 已复制 ffmpeg: {original_name} -> {target_name}")
            else:  # Unix-like
                os.symlink(original_path, target_path)
                logger.info(f"[FFmpegHelper] 已创建符号链接: {original_name} -> {target_name}")
            return target_path
        except Exception as e:
            logger.warning(f"[FFmpegHelper] 无法创建标准名称的 ffmpeg.exe: {e}")
            logger.warning(f"[FFmpegHelper] 将直接使用原始路径: {original_path}")
            return original_path
    
    @classmethod
    def get_ffmpeg_path(cls) -> Optional[str]:
        """
        获取 ffmpeg 可执行文件的路径。
        
        如果 imageio-ffmpeg 可用，则使用它提供的 ffmpeg；
        否则尝试查找系统安装的 ffmpeg。
        
        返回:
            ffmpeg 可执行文件的路径，如果未找到则返回 None。
        """
        if cls._ffmpeg_path is not None:
            return cls._ffmpeg_path
        
        # 优先尝试使用 imageio-ffmpeg 提供的 ffmpeg
        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            original_ffmpeg_path = get_ffmpeg_exe()
            # 确保文件名为标准名称
            cls._ffmpeg_path = cls._ensure_ffmpeg_exe_name(original_ffmpeg_path)
            cls._ffmpeg_dir = os.path.dirname(cls._ffmpeg_path)
            logger.info(f"[FFmpegHelper] 使用 imageio-ffmpeg 提供的 ffmpeg: {cls._ffmpeg_path}")
            os.environ['FFMPEG_PATH'] = cls._ffmpeg_path
            return cls._ffmpeg_path
        except ImportError:
            logger.warning("[FFmpegHelper] imageio-ffmpeg 未安装，尝试使用系统 ffmpeg")
        
        # 如果 imageio-ffmpeg 不可用，尝试查找系统 ffmpeg
        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            cls._ffmpeg_path = system_ffmpeg
            cls._ffmpeg_dir = os.path.dirname(system_ffmpeg)
            logger.info(f"[FFmpegHelper] 使用系统 ffmpeg: {cls._ffmpeg_path}")
            return cls._ffmpeg_path
        
        logger.error("[FFmpegHelper] 未找到 ffmpeg！")
        logger.error("[FFmpegHelper] 请安装 imageio-ffmpeg: pip install imageio-ffmpeg")
        logger.error("[FFmpegHelper] 或者手动安装系统 ffmpeg: Windows 用户可从 https://ffmpeg.org/download.html 下载")
        return None
    
    @classmethod
    def is_ffmpeg_available(cls) -> bool:
        """
        检查 ffmpeg 是否可用。
        
        返回:
            True 如果 ffmpeg 可用，否则返回 False。
        """
        return cls.get_ffmpeg_path() is not None
    
    @classmethod
    def get_yt_dlp_ffmpeg_location(cls) -> Optional[str]:
        """
        获取用于 yt-dlp 的 ffmpeg_location 配置。
        
        返回:
            ffmpeg 路径字符串，如果未找到则返回 None。
        """
        return cls.get_ffmpeg_path()
    
    @classmethod
    def configure_ffmpeg_python(cls) -> bool:
        """
        配置 ffmpeg-python 库使用正确的 ffmpeg 路径。
        
        ffmpeg_python 通过调用 ffmpeg 命令行工作，
        所以需要确保 ffmpeg 在 PATH 中且可执行。
        
        返回:
            True 如果配置成功，否则返回 False。
        """
        ffmpeg_path = cls.get_ffmpeg_path()
        if not ffmpeg_path:
            return False
        
        ffmpeg_dir = os.path.dirname(ffmpeg_path)
        
        # 将 ffmpeg 目录添加到 PATH
        current_path = os.environ.get('PATH', '')
        if ffmpeg_dir not in current_path:
            os.environ['PATH'] = ffmpeg_dir + os.pathsep + current_path
            logger.info(f"[FFmpegHelper] 已将 ffmpeg 目录添加到 PATH: {ffmpeg_dir}")
        
        return True
