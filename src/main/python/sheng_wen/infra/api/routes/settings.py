from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from src.main.python.sheng_wen.config.settings import config
from src.main.python.sheng_wen.infra.api.routes.schemas import (
    BilibiliCookieFromBrowserResult,
    LLMProviderInfo,
    LLMSettings,
    LLMSettingsUpdate,
    LLMTestResult,
    ModelPathValidationRequest,
    ModelPathValidationResult,
    SummarizationSettings,
    SummarizationSettingsUpdate,
    TranscriptionSettings,
    TranscriptionSettingsUpdate,
    VibeVoiceServiceScanResult,
    VibeVoiceServiceStatus,
)

from src.main.python.sheng_wen.transcriber.vibevoice_model_validator import (
    perform_lightweight_load_test,
)


router = APIRouter(prefix="")


@router.get("/llm/providers", response_model=list[LLMProviderInfo])
async def list_llm_providers(request: Request):
    return request.app.state.llm_provider_manager.list_providers()


@router.get("/llm/settings", response_model=LLMSettings)
async def get_llm_settings(request: Request):
    return request.app.state.llm_provider_manager.get_settings()


@router.put("/llm/settings", response_model=LLMSettings)
async def update_llm_settings(payload: LLMSettingsUpdate, request: Request):
    try:
        settings = request.app.state.llm_provider_manager.update_settings(
            provider=payload.provider,
            base_url=payload.base_url,
            api_key=payload.api_key,
            model_id=payload.model_id,
            temperature=payload.temperature,
            context_window_size=payload.context_window_size,
            extra_headers=payload.extra_headers,
        )
        request.app.state.config_manager.save_llm_config(
            request.app.state.llm_provider_manager.export_runtime_config()
        )
        return settings
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/llm/test", response_model=LLMTestResult)
async def test_llm_connection(request: Request):
    from src.main.python.sheng_wen.llm.llm import (
        LLMConnectionError,
        LLMMessage,
        LLMResponseError,
        get_llm,
    )

    logger.info("开始测试 LLM 连接...")

    try:
        runtime_config = request.app.state.llm_provider_manager.get_runtime_config()
        llm_client = get_llm(runtime_config)

        llm_client_config = getattr(llm_client, "config", None)
        if llm_client_config:
            logger.info(
                "使用 LLM 配置: "
                f"provider={llm_client_config.provider}, "
                f"base_url={llm_client_config.base_url}, "
                f"model={llm_client_config.model_id}"
            )
        else:
            logger.warning("无法获取 LLM 配置信息")

        test_message = LLMMessage(role="user", content="Reply with exactly: OK")
        response_chunks = []
        error_occurred = None

        def callback(chunk):
            nonlocal error_occurred
            if isinstance(chunk, (LLMResponseError, LLMConnectionError)):
                error_occurred = chunk
            else:
                response_chunks.append(chunk)

        await llm_client.response(
            messages=[test_message],
            resp_callback=callback,
            stream=False,
            timeout=10,
        )

        if error_occurred:
            logger.error(f"LLM 响应错误: {error_occurred}")
            return LLMTestResult(
                status="error",
                message=f"LLM 响应错误: {str(error_occurred)}",
                response=None,
            )

        full_response = "".join(response_chunks).strip()
        if full_response == "OK":
            logger.info("LLM 测试通过")
            return LLMTestResult(
                status="success",
                message="测试通过，模型响应正确",
                response=full_response,
            )

        logger.warning(f"LLM 测试响应不符合预期: 期望 'OK'，实际 '{full_response}'")
        return LLMTestResult(
            status="warning",
            message=f"模型已响应，但内容不符合预期（期望: 'OK'，实际: '{full_response}'）",
            response=full_response,
        )
    except asyncio.TimeoutError:
        logger.error("LLM 测试超时（10秒）")
        return LLMTestResult(
            status="error",
            message="请求超时（10秒），请检查网络连接或 API 地址",
            response=None,
        )
    except LLMConnectionError as e:
        logger.error(f"LLM 连接失败: {e}", exc_info=True)
        return LLMTestResult(
            status="error",
            message=f"连接失败: {str(e)}",
            response=None,
        )
    except Exception as e:
        logger.exception("LLM 测试失败")
        return LLMTestResult(
            status="error",
            message=f"测试失败: {str(e)}",
            response=None,
        )


@router.get("/transcription/settings", response_model=TranscriptionSettings)
async def get_transcription_settings(request: Request):
    return request.app.state.transcription_settings_manager.get_settings()


@router.put("/transcription/settings", response_model=TranscriptionSettings)
async def update_transcription_settings(
    payload: TranscriptionSettingsUpdate, request: Request
):
    try:
        settings = request.app.state.transcription_settings_manager.update_settings(
            device=payload.device,
            model_source=payload.model_source,
            model_size=payload.model_size,
            model_path=payload.model_path,
            enable_bilibili_subtitle_fetch=payload.enable_bilibili_subtitle_fetch,
            bilibili_sessdata=payload.bilibili_sessdata,
            clear_bilibili_sessdata=payload.clear_bilibili_sessdata,
            transcriber_type=payload.transcriber_type,
            vibevoice_language_model=payload.vibevoice_language_model,
            vibevoice_max_new_tokens=payload.vibevoice_max_new_tokens,
            vibevoice_dtype=payload.vibevoice_dtype,
            vibevoice_inference_mode=payload.vibevoice_inference_mode,
            vibevoice_api_url=payload.vibevoice_api_url,
        )
        request.app.state.config_manager.save_transcription_config(
            request.app.state.transcription_settings_manager.get_runtime_state()
        )
        return settings
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/transcription/settings/bilibili-cookie/from-browser",
    response_model=BilibiliCookieFromBrowserResult,
)
async def read_bilibili_cookie_from_browser(request: Request):
    result = request.app.state.transcription_settings_manager.read_cookie_from_browser()
    if result["success"]:
        request.app.state.config_manager.save_transcription_config(
            request.app.state.transcription_settings_manager.get_runtime_state()
        )
    return result


@router.post(
    "/transcription/settings/validate-model-path",
    response_model=ModelPathValidationResult,
)
async def validate_model_path(payload: ModelPathValidationRequest):
    if payload.transcriber_type == "vibe_voice_asr":
        result = perform_lightweight_load_test(payload.path)
        return ModelPathValidationResult(**result.to_dict())
    else:
        path = os.path.abspath(os.path.expanduser(payload.path))
        if os.path.isdir(path):
            return ModelPathValidationResult(
                valid=True,
                message="路径有效。",
                resolved_path=path,
                missing_files=[],
                has_processor_config=False,
                details={},
            )
        return ModelPathValidationResult(
            valid=False,
            message=f"路径不存在或不是目录: {path}",
            resolved_path=path,
            missing_files=[],
            has_processor_config=False,
            details={"error": "path_not_found"},
        )


@router.post(
    "/transcription/settings/vibevoice-scan",
    response_model=list[VibeVoiceServiceScanResult],
)
async def vibevoice_scan_services(request: Request):
    """Scan localhost ports 8000-8010 for VibeVoice vLLM services."""
    mgr = request.app.state.vibevoice_service_manager
    results = await mgr.scan_local_ports()
    return [VibeVoiceServiceScanResult(**r) for r in results]


@router.post("/transcription/settings/vibevoice-service/start")
async def vibevoice_service_start(request: Request):
    """Start a local vLLM subprocess for VibeVoice inference."""
    body = await request.json()
    model_path = str(body.get("model_path", "")).strip()
    port = int(body.get("port", 8000))
    dtype = str(body.get("dtype", "bfloat16"))

    if not model_path:
        raise HTTPException(status_code=400, detail="请填写模型目录")

    mgr = request.app.state.vibevoice_service_manager
    result = mgr.start_service(model_path=model_path, port=port, dtype=dtype)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "启动失败"))
    return result


@router.post("/transcription/settings/vibevoice-service/stop")
async def vibevoice_service_stop(request: Request):
    """Stop the managed vLLM subprocess."""
    mgr = request.app.state.vibevoice_service_manager
    result = mgr.stop_service()
    return result


@router.get(
    "/transcription/settings/vibevoice-service/status",
    response_model=VibeVoiceServiceStatus,
)
async def vibevoice_service_status(request: Request):
    """Get the status of the managed vLLM subprocess and its API health."""
    mgr = request.app.state.vibevoice_service_manager
    health = await mgr.health_check()
    return VibeVoiceServiceStatus(
        running=mgr.is_running,
        pid=mgr.pid,
        api_url=mgr.api_url,
        api_healthy=health.get("healthy", False),
    )


@router.get("/summarization/settings", response_model=SummarizationSettings)
async def get_summarization_settings():
    cfg = config.summarization
    return SummarizationSettings(
        mode=str(cfg.mode),
        auto_chunk_min_audio_duration_sec=int(cfg.auto_chunk_min_audio_duration_sec),
        auto_chunk_min_transcript_lines=int(cfg.auto_chunk_min_transcript_lines),
        chunk_target_duration_sec=int(cfg.chunk_target_duration_sec),
        chunk_min_duration_sec=int(cfg.chunk_min_duration_sec),
        chunk_max_duration_sec=int(cfg.chunk_max_duration_sec),
        boundary_jump_sec=int(cfg.boundary_jump_sec),
        prev_tail_timestamp_lines_m=int(cfg.prev_tail_timestamp_lines_m),
        prev_summary_tail_chars_j=int(cfg.prev_summary_tail_chars_j),
        llm_call_retry_max=int(cfg.llm_call_retry_max),
        max_agent_value_chars=int(cfg.max_agent_value_chars),
        fallback_to_standard_on_agent_error=bool(
            cfg.fallback_to_standard_on_agent_error
        ),
    )


@router.put("/summarization/settings", response_model=SummarizationSettings)
async def update_summarization_settings(
    payload: SummarizationSettingsUpdate, request: Request
):
    try:
        patch = payload.model_dump(exclude_none=True)
    except AttributeError:
        patch = payload.dict(exclude_none=True)

    try:
        request.app.state.config_manager.save_summarization_config(patch)
        return await get_summarization_settings()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
