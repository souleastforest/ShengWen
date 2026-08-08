"""VibeVoice vLLM service manager for local subprocess lifecycle management."""

from __future__ import annotations

import asyncio
import subprocess
import urllib.error
import urllib.request
from typing import Any

from loguru import logger


class VibeVoiceServiceManager:
    """Manages a local vLLM subprocess for VibeVoice ASR inference."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._port: int = 0

    @property
    def is_running(self) -> bool:
        """Check if the subprocess is still alive."""
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> int | None:
        """Get the subprocess PID, or None if not running."""
        if self.is_running:
            return self._process.pid
        return None

    @property
    def api_url(self) -> str:
        """Get the API URL for the running service."""
        if self._port:
            return f"http://localhost:{self._port}"
        return ""

    def start_service(
        self,
        model_path: str,
        port: int = 8000,
        dtype: str = "bfloat16",
    ) -> dict[str, Any]:
        """Start a vLLM subprocess serving the VibeVoice model."""
        if self.is_running:
            return {
                "success": False,
                "message": f"服务已在运行中 (PID: {self.pid}, port: {self._port})",
            }

        cmd = [
            "vllm",
            "serve",
            model_path,
            "--served-model-name",
            "vibevoice",
            "--trust-remote-code",
            "--dtype",
            dtype,
            "--max-num-seqs",
            "8",
            "--max-model-len",
            "65536",
            "--port",
            str(port),
        ]

        logger.info(f"[VibeVoiceServiceManager] Starting vLLM: {' '.join(cmd)}")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._port = port
            logger.info(
                f"[VibeVoiceServiceManager] vLLM subprocess started (PID: {self._process.pid})"
            )
            return {
                "success": True,
                "message": f"vLLM 服务已启动 (PID: {self._process.pid}, port: {port})",
                "pid": self._process.pid,
            }
        except FileNotFoundError:
            return {
                "success": False,
                "message": "未找到 vllm 命令，请先安装 vLLM: pip install vllm",
            }
        except Exception as e:
            logger.error(f"[VibeVoiceServiceManager] Failed to start vLLM: {e}")
            return {"success": False, "message": f"启动失败: {e}"}

    def stop_service(self) -> dict[str, Any]:
        """Stop the vLLM subprocess."""
        if not self.is_running:
            if self._process is not None:
                self._process = None
                self._port = 0
            return {"success": True, "message": "服务未在运行"}

        pid = self._process.pid
        logger.info(f"[VibeVoiceServiceManager] Stopping vLLM (PID: {pid})")

        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning(
                    f"[VibeVoiceServiceManager] Process {pid} did not terminate, killing..."
                )
                self._process.kill()
                self._process.wait(timeout=5)

            self._process = None
            self._port = 0
            logger.info(f"[VibeVoiceServiceManager] vLLM process {pid} stopped")
            return {"success": True, "message": f"服务已停止 (PID: {pid})"}
        except Exception as e:
            logger.error(f"[VibeVoiceServiceManager] Error stopping process: {e}")
            self._process = None
            self._port = 0
            return {"success": False, "message": f"停止失败: {e}"}

    async def health_check(self, url: str | None = None) -> dict[str, Any]:
        """Check if a VibeVoice vLLM service is healthy."""
        check_url = url or self.api_url
        if not check_url:
            return {"healthy": False, "message": "未指定服务地址"}

        try:
            req = urllib.request.Request(f"{check_url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return {"healthy": True, "message": "服务正常运行"}
                return {"healthy": False, "message": f"服务返回状态码: {resp.status}"}
        except urllib.error.URLError:
            return {"healthy": False, "message": "服务不可达"}
        except Exception as e:
            return {"healthy": False, "message": f"健康检查失败: {e}"}

    async def scan_local_ports(self) -> list[dict[str, str]]:
        """Scan localhost ports 8000-8010 for vLLM services."""

        async def _check_port(port: int) -> dict[str, str]:
            url = f"http://localhost:{port}"
            try:
                req = urllib.request.Request(f"{url}/v1/models", method="GET")
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        data = resp.read().decode("utf-8")
                        if "vibevoice" in data.lower():
                            return {"url": url, "status": "available"}
                        return {"url": url, "status": "available"}
                    return {"url": url, "status": "unreachable"}
            except Exception:
                return {"url": url, "status": "unreachable"}

        tasks = [_check_port(port) for port in range(8000, 8011)]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r["status"] == "available"] or results

    async def shutdown(self) -> None:
        """Called during application shutdown to clean up."""
        if self.is_running:
            logger.info("[VibeVoiceServiceManager] Shutdown: stopping vLLM service")
            self.stop_service()
