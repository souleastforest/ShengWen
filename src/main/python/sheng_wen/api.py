import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from src.main.python.sheng_wen.application.events.bus import AsyncioEventBus
from src.main.python.sheng_wen.application.pipeline import Pipeline
from src.main.python.sheng_wen.config.settings import config, get_config_manager
from src.main.python.sheng_wen.infra.api.error_handler import register_error_handlers
from src.main.python.sheng_wen.infra.api.routes.bilibili import (
    router as bilibili_router,
)
from src.main.python.sheng_wen.infra.api.routes.deps import (
    _build_model_load_error_detail,
)
from src.main.python.sheng_wen.infra.api.routes.health import router as health_router
from src.main.python.sheng_wen.infra.api.routes.settings import (
    router as settings_router,
)
from src.main.python.sheng_wen.infra.api.routes.tasks import router as tasks_router
from src.main.python.sheng_wen.infra.api.routes.upload import router as upload_router
from src.main.python.sheng_wen.infra.api.routes.websocket import (
    notify_progress_update,  # noqa: F401
    notify_task_update,  # noqa: F401
    router as websocket_router,
)
from src.main.python.sheng_wen.llm.llm import LLMConfig
from src.main.python.sheng_wen.llm.provider_manager import LLMProviderManager
from src.main.python.sheng_wen.logging import setup_logging
from src.main.python.sheng_wen.transcriber.settings_manager import (
    TranscriptionSettingsManager,
)
from src.main.python.sheng_wen.transcriber.transcriber import ModelLoadError
from src.main.python.sheng_wen.version import APP_VERSION

setup_logging()
downloader_worker = file_upload_worker = llm_worker = transcriber_worker = None
config_manager = get_config_manager()
llm_cfg = config.llm
initial_llm_config = LLMConfig(
    base_url=llm_cfg.base_url,
    api_key=llm_cfg.api_key,
    model_id=llm_cfg.model_id,
    temperature=llm_cfg.temperature,
    context_window_size=llm_cfg.context_window_size,
    provider=llm_cfg.provider,
    extra_headers=llm_cfg.extra_headers,
)
initial_provider_id = llm_cfg.provider.strip() if llm_cfg.provider.strip() else None
whisper_cfg = config.whisper
initial_transcription_device = str(whisper_cfg.device).lower()
if initial_transcription_device not in {"cpu", "cuda"}:
    initial_transcription_device = "cpu"
initial_enable_bilibili_subtitle_fetch = bool(
    whisper_cfg.enable_bilibili_subtitle_fetch
)
initial_bilibili_sessdata = str(whisper_cfg.bilibili_sessdata or "")
transcription_settings_manager = TranscriptionSettingsManager(
    initial_device=initial_transcription_device,
    model_source=str(whisper_cfg.model_source),
    model_size=whisper_cfg.model_size,
    model_path=whisper_cfg.configured_model_path,
    initial_enable_bilibili_subtitle_fetch=initial_enable_bilibili_subtitle_fetch,
    initial_bilibili_sessdata=initial_bilibili_sessdata,
    transcriber_type=whisper_cfg.transcriber_type,
    vibevoice_language_model=whisper_cfg.vibevoice_language_model,
    vibevoice_max_new_tokens=whisper_cfg.vibevoice_max_new_tokens,
    vibevoice_dtype=whisper_cfg.vibevoice_dtype,
    vibevoice_inference_mode=whisper_cfg.vibevoice_inference_mode,
    vibevoice_api_url=whisper_cfg.vibevoice_api_url,
)
llm_provider_manager = LLMProviderManager(
    initial_config=initial_llm_config,
    initial_provider_id=initial_provider_id,
)
event_bus = AsyncioEventBus()
pipeline = Pipeline(event_bus)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await pipeline.start()
    yield
    await pipeline.stop()
    await stop_all_workers()


app = FastAPI(
    title="ShengWen API",
    description="视频转录与 AI 总结服务",
    version=APP_VERSION,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _sync_worker_state() -> None:
    for name, worker in {
        "downloader_worker": downloader_worker,
        "file_upload_worker": file_upload_worker,
        "llm_worker": llm_worker,
        "transcriber_worker": transcriber_worker,
    }.items():
        setattr(app.state, name, worker)


app.state.config_manager = config_manager
app.state.llm_provider_manager = llm_provider_manager
app.state.transcription_settings_manager = transcription_settings_manager
app.state.event_bus = event_bus
app.state.pipeline = pipeline


async def get_llm_worker():
    global llm_worker
    if llm_worker is not None:
        return llm_worker
    from .llm.llm import get_llm
    from .llm.llm_worker import LLMWorker

    llm_config = llm_provider_manager.get_runtime_config()
    llm_client = get_llm(llm_config)
    llm_worker = LLMWorker(name="LLMWorker", llm_client=llm_client)
    llm_worker.load_system_prompt(config.app.prompt_file)
    llm_provider_manager.bind_llm_worker(llm_worker)
    llm_worker.start()
    _sync_worker_state()
    return llm_worker


async def get_transcriber_worker():
    global transcriber_worker
    if transcriber_worker is not None:
        return transcriber_worker
    from .transcriber.transcriber import get_transcriber
    from .transcriber.transcriber_worker import TranscriberWorker

    runtime_transcription_state = transcription_settings_manager.get_runtime_state()
    transcriber_config = transcription_settings_manager.build_transcriber_kwargs()
    transcriber_type = str(
        runtime_transcription_state.get("transcriber_type") or "fast_whisper"
    )
    model_source = str(
        runtime_transcription_state.get("model_source") or "auto_download"
    )
    if transcriber_type == "vibe_voice_asr":
        logger.info(
            f"[Transcriber] VibeVoice-ASR 模型路径: {transcriber_config.get('model_path')}"
        )
        # Add VibeVoice-specific config
        import torch
        dtype_str = runtime_transcription_state.get("vibevoice_dtype", "bfloat16")
        dtype = torch.bfloat16 if dtype_str == "bfloat16" else torch.float16
        transcriber_config["language_model_pretrained_name"] = runtime_transcription_state.get(
            "vibevoice_language_model", "Qwen/Qwen2.5-7B"
        )
        transcriber_config["max_new_tokens"] = runtime_transcription_state.get(
            "vibevoice_max_new_tokens", 8192
        )
        transcriber_config["dtype"] = dtype
    elif model_source == "manual_path":
        logger.info(
            f"[Transcriber] 使用本地模型路径: {transcriber_config.get('model_size_or_path')}"
        )
    else:
        logger.info(
            f"[Transcriber] 使用模型大小: {transcriber_config.get('model_size')}"
        )
    transcriber = get_transcriber(transcriber_type, **transcriber_config)
    llm_w = await get_llm_worker()
    transcriber_worker = TranscriberWorker(
        name="TranscriberWorker", transcriber=transcriber, next_worker=llm_w
    )
    transcription_settings_manager.bind_transcriber_worker(transcriber_worker)
    transcriber_worker.start()
    _sync_worker_state()
    return transcriber_worker


async def get_downloader_worker():
    global downloader_worker
    if downloader_worker is not None:
        return downloader_worker
    from .downloader.video_downloader_worker import VideoDownloaderWorker

    transcriber_w = await get_transcriber_worker()
    llm_w = await get_llm_worker()
    downloader_worker = VideoDownloaderWorker(
        name="VideoDownloaderWorker",
        next_worker=transcriber_w,
        summary_worker=llm_w,
        transcription_settings_manager=transcription_settings_manager,
    )
    downloader_worker.start()
    _sync_worker_state()
    return downloader_worker


async def get_file_upload_worker():
    global file_upload_worker
    if file_upload_worker is not None:
        return file_upload_worker
    from .downloader.file_upload_worker import FileUploadWorker

    transcriber_w = await get_transcriber_worker()
    file_upload_worker = FileUploadWorker(
        name="FileUploadWorker", next_worker=transcriber_w
    )
    file_upload_worker.start()
    _sync_worker_state()
    return file_upload_worker


async def stop_all_workers():
    for worker in [
        downloader_worker,
        file_upload_worker,
        transcriber_worker,
        llm_worker,
    ]:
        if worker is not None:
            try:
                await worker.stop()
                logger.info(f"[Shutdown] {worker.name} 已停止")
            except Exception as e:
                logger.warning(f"[Shutdown] 停止 {worker.name} 失败: {e}")


_sync_worker_state()
for name, factory in {
    "get_llm_worker": get_llm_worker,
    "get_transcriber_worker": get_transcriber_worker,
    "get_downloader_worker": get_downloader_worker,
    "get_file_upload_worker": get_file_upload_worker,
}.items():
    setattr(app.state, name, factory)

pipeline.register_worker_factory("get_downloader_worker", get_downloader_worker)
pipeline.register_worker_factory("get_file_upload_worker", get_file_upload_worker)


@app.exception_handler(ModelLoadError)
async def handle_model_load_error(_: Request, exc: ModelLoadError):
    return JSONResponse(
        status_code=503, content={"detail": _build_model_load_error_detail(exc)}
    )


base_dir = os.path.dirname(
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
)
dist_dir = os.path.join(base_dir, "frontend", "dist")
if os.path.exists(dist_dir):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(dist_dir, "assets")),
        name="assets",
    )

    @app.get("/")
    async def read_index():
        return FileResponse(os.path.join(dist_dir, "index.html"))


app.include_router(health_router)
app.include_router(upload_router)
app.include_router(tasks_router)
app.include_router(settings_router)
app.include_router(bilibili_router)
app.include_router(websocket_router)
register_error_handlers(app)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.app.host, port=config.app.port)
