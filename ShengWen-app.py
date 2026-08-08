import asyncio
import os
import sys
import webbrowser
import uvicorn
import socket
from multiprocessing import Process
from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from zeroconf import ServiceInfo, Zeroconf

# 将项目根目录添加到 Python 路径
path = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, path)

from loguru import logger
from src.main.python.sheng_wen.db import TaskStatus, db as task_db
from src.main.python.sheng_wen.version import APP_VERSION
import src.main.python.sheng_wen.api as api_module
from src.main.python.sheng_wen.config.settings import config
from src.main.python.sheng_wen.domain.storage.reclaimer_loop import (
    start_storage_reclaimer,
    stop_storage_reclaimer,
)
from datetime import datetime
import uuid

g_zeroconf = None
g_mdns_info = None

_PROXY_ENV_VAR_NAMES = (
    "ALL_PROXY",
    "all_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
)


def _normalize_proxy_url(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized.lower().startswith("socks://"):
        return f"socks5://{normalized[len('socks://') :]}"
    return normalized


def _is_local_port_open(port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def ensure_proxy_env_for_startup() -> None:
    """启动时自动兜底代理环境，支持 python3 ShengWen-app.py 直启。"""
    has_proxy = any(os.environ.get(name) for name in _PROXY_ENV_VAR_NAMES)

    if not has_proxy:
        for port in (7897, 7890, 1080):
            if not _is_local_port_open(port):
                continue
            http_proxy = f"http://127.0.0.1:{port}/"
            socks_proxy = f"socks5://127.0.0.1:{port}/"
            os.environ["HTTP_PROXY"] = http_proxy
            os.environ["http_proxy"] = http_proxy
            os.environ["HTTPS_PROXY"] = http_proxy
            os.environ["https_proxy"] = http_proxy
            os.environ["ALL_PROXY"] = socks_proxy
            os.environ["all_proxy"] = socks_proxy
            logger.info(f"--- [Startup] 未检测到代理环境，已自动接管本地代理端口 {port} ---")
            break

    for name in _PROXY_ENV_VAR_NAMES:
        value = os.environ.get(name)
        if value:
            os.environ[name] = _normalize_proxy_url(value)


def get_local_ip():
    """获取本机局域网 IP 地址"""
    try:
        # 创建一个 UDP socket 连接到公共 DNS 服务器
        # 这不会实际发送数据，只是用于获取本机 IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        return local_ip
    except Exception:
        # 如果获取失败，返回 0.0.0.0
        return "0.0.0.0"


def log_access_tips(port: int):
    """输出服务访问提示（本机 + 局域网）"""
    local_ip = get_local_ip()
    logger.info("╔════════════════════════════════════════════════════════════╗")
    logger.info("║  🚀 服务启动完成，可通过浏览器访问：                       ║")
    logger.info("╠════════════════════════════════════════════════════════════╣")
    logger.info(f"║  📱 本机访问:  http://localhost:{port}/")
    if local_ip != "0.0.0.0":
        logger.info(f"║  🌐 局域网访问: http://{local_ip}:{port}/")
    else:
        logger.info(f"║  🌐 局域网访问: http://<本机IP>:{port}/")
    logger.info("╚════════════════════════════════════════════════════════════╝")


def register_mdns_service(host: str, port: int):
    """注册 mDNS 服务"""
    global g_zeroconf, g_mdns_info
    try:
        g_zeroconf = Zeroconf()
        
        # 创建服务信息
        g_mdns_info = ServiceInfo(
            "_http._tcp.local.",
            "ShengWen._http._tcp.local.",
            addresses=[socket.inet_aton(host)],
            port=port,
            properties={
                b"path": b"/",
                b"description": b"ShengWen - Video Transcription Service",
            },
        )
        
        g_zeroconf.register_service(g_mdns_info)
        logger.info(f"--- [mDNS] 服务已注册: ShengWen.local -> http://{host}:{port} ---")
        logger.info(f"--- [mDNS] 局域网设备可通过 'http://ShengWen.local:{port}' 访问 ---")
    except Exception as e:
        logger.warning(f"--- [mDNS] 服务注册失败: {e} ---")
        logger.warning(f"--- [mDNS] 请确保已安装 Bonjour 服务 (Windows/macOS) 或 avahi-daemon (Linux) ---")


def unregister_mdns_service():
    """注销 mDNS 服务"""
    global g_zeroconf, g_mdns_info
    if g_zeroconf:
        try:
            g_zeroconf.unregister_service(g_mdns_info)
            g_zeroconf.close()
            logger.info("--- [mDNS] 服务已注销 ---")
        except Exception as e:
            logger.warning(f"--- [mDNS] 服务注销失败: {e} ---")
        finally:
            g_zeroconf = None
            g_mdns_info = None


async def run_sidebar_progress_test():
    """
    一个独立的测试函数，用于验证从后端到前端的进度条更新管道。
    """
    logger.info("--- [ProgressTest] 启动侧边栏进度条功能测试 ---")

    test_task_id = f"test-progress-task-{uuid.uuid4()}"
    logger.info(f"--- [ProgressTest] 创建测试任务: {test_task_id} ---")

    # 1. 在数据库中创建一个假的测试任务
    from src.main.python.sheng_wen.db import db
    from src.main.python.sheng_wen.api import notify_task_update
    
    db.save_task(test_task_id, {
        "id": test_task_id,
        "video_url": "https://www.bilibili.com/video/BV1xt411v7zS/", # 示例 URL
        "status": TaskStatus.DOWNLOADING,
        "created_at": datetime.now(),
        "progress": 0.0,
        "title": "进度条功能测试任务"
    })
    await notify_task_update(test_task_id)
    await asyncio.sleep(1)

    # 2. 模拟进度从 0 到 100
    for i in range(101):
        db.update_task(test_task_id, {"progress": float(i)})
        logger.info(f"[ProgressTest] 更新任务 '{test_task_id}' 进度为 {i}%")
        await notify_task_update(test_task_id)
        await asyncio.sleep(0.1) # 模拟工作间隔

    # 3. 模拟任务完成
    db.update_task(test_task_id, {"status": TaskStatus.COMPLETED, "progress": 100.0})
    await notify_task_update(test_task_id)
    logger.info(f"--- [ProgressTest] 测试任务 {test_task_id} 已完成 ---")
    
    # 4. (可选) 几秒后删除任务
    await asyncio.sleep(10)
    db.delete_task(test_task_id)
    await notify_task_update(test_task_id) # 通知前端删除
    logger.info(f"--- [ProgressTest] 测试任务 {test_task_id} 已删除 ---")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    管理应用的生命周期：
    - 启动时恢复中断任务、启动事件总线 Pipeline
    - Workers 在首次使用时才初始化（真正的懒加载）
    - 关闭时停止 Pipeline、Workers
    """
    # 启动恢复：把上次异常中断遗留在中间态的任务回收为 FAILED，避免前端永远卡在"处理中"。
    recovered_count = task_db.recover_interrupted_tasks()
    if recovered_count > 0:
        logger.warning(
            f"--- [Lifespan] 检测到 {recovered_count} 个中断任务，已自动标记为 FAILED（可手动重试） ---"
        )

    # 启动存储回收器：先做存量音频状态回填，再启动周期循环
    await start_storage_reclaimer()

    # 启动事件总线 Pipeline（订阅 TASK_CREATED 等事件）
    await api_module.pipeline.start()

    logger.info("--- [Lifespan] 服务启动完成，前端已可访问 ---")
    logger.info("--- [Lifespan] Workers 将在首次使用时自动初始化 ---")
    log_access_tips(config.app.port)

    # 启动进度条测试（如果已启用）
    if config.app.enable_progress_test:
        asyncio.create_task(run_sidebar_progress_test())

    yield

    # Shutdown logic
    await stop_storage_reclaimer()
    await api_module.pipeline.stop()
    await api_module.stop_all_workers()

    # 注销 mDNS 服务
    unregister_mdns_service()

# 创建一个带有生命周期管理的新 FastAPI 实例
app = FastAPI(
    lifespan=lifespan,
    title="ShengWen API",
    description="视频转录与 AI 总结服务",
    version=APP_VERSION,
)

# 添加中间件 (这部分不会被 include_router 包含)
cors_cfg = config.cors
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_cfg.origins_list,
    allow_credentials=cors_cfg.allow_credentials,
    allow_methods=cors_cfg.methods_list,
    allow_headers=cors_cfg.headers_list,
)

# 将原始 api.py 中定义的路由挂载到新实例上
app.include_router(api_module.app.router)

# 复制 api_module.app.state 上的共享状态（event_bus, pipeline, worker factories 等）
for attr, value in api_module.app.state._state.items():
    setattr(app.state, attr, value)

# 重新挂载静态文件目录 (这部分不会被 include_router 包含)
dist_dir = config.app.frontend_dist_dir
assets_dir = os.path.join(dist_dir, "assets")

if os.path.exists(dist_dir):
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
else:
    logger.warning(f"前端构建目录未找到: {dist_dir}。将无法提供前端页面。")
    logger.warning("请先在 'frontend' 目录下运行 'npm run build'。")


if __name__ == "__main__":
    ensure_proxy_env_for_startup()
    app_cfg = config.app

    host = app_cfg.host
    port = app_cfg.port

    logger.info(f"╔════════════════════════════════════════════════════════════╗")
    logger.info(f"║  声文智汇 ShengWen v{APP_VERSION}                           ║")
    logger.info(f"╚════════════════════════════════════════════════════════════╝")

    # 注册 mDNS 服务
    if app_cfg.enable_mdns:
        # 获取本机局域网 IP
        local_ip = get_local_ip()
        register_mdns_service(local_ip, port)

    # 确保 uvicorn 运行的是我们新创建的 app 实例
    uvicorn.run(app, host=host, port=port)


