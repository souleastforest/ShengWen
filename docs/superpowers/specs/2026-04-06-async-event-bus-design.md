# Async Event Bus Architecture Design

**Branch**: `feature/async-event-bus`
**Date**: 2026-04-06
**Status**: Draft

---

## 1. Motivation

| # | Goal | Why |
|---|------|-----|
| 1 | **Performance** | Model loading blocks the event loop; need non-blocking async init |
| 2 | **Scalability** | Future parallel pipelines, batch processing, multi-worker |
| 3 | **Maintainability** | `api.py` is ~1600 lines with mixed concerns; clean separation needed |
| 4 | **Logging** | Replace stdlib logging with loguru across the codebase |
| 5 | **Observability** | OTEL-ready trace/metrics hooks at every layer boundary |

## 2. Architecture: DDD + Onion Model

Dependencies point strictly inward. Infrastructure implements domain Protocols.

```
                    +-----------------------------+
                    |    Presentation (infra/api)  |  FastAPI routes, WebSocket
                    |  +-----------------------+  |
                    |  |   Infrastructure      |  |  DB, LLM, transcriber, storage
                    |  |  +-----------------+  |  |
                    |  |  |   Application   |  |  |  Event bus, workers, pipeline
                    |  |  |  +-----------+  |  |  |
                    |  |  |  |   Domain  |  |  |  |  Pure business logic
                    |  |  |  +-----------+  |  |  |
                    |  |  +-----------------+  |  |
                    |  +-----------------------+  |
                    +-----------------------------+

         Observability (cross-cutting): OTEL + loguru
```

## 3. Directory Structure

```
src/main/python/sheng_wen/
|
+-- domain/                          # Domain layer -- pure business, zero external deps
|   +-- __init__.py
|   +-- task/
|   |   +-- type.py                  # TaskState, TaskStatus (dataclass/Enum)
|   |   +-- service.py               # TaskService -- task lifecycle orchestration
|   |   +-- repository.py            # TaskRepository Protocol (interface)
|   +-- transcription/
|   |   +-- type.py                  # TranscriptionResult, TranscriptionRequest
|   |   +-- service.py               # TranscriptionService
|   +-- summarization/
|   |   +-- type.py                  # SummaryMode, SummaryResult
|   |   +-- service.py               # SummarizationService
|   +-- model/
|   |   +-- type.py                  # ModelSpec, ModelState, LoaderType(Enum)
|   |   +-- service.py               # ModelService -- model lifecycle management
|   |   +-- repository.py            # ModelRepository Protocol
|   +-- storage/
|       +-- type.py                  # StoragePolicy, FileInfo, FileType(Enum)
|       +-- service.py               # StorageService -- cleanup/quota/expiry
|       +-- repository.py            # StorageRepository Protocol
|
+-- application/                     # Application layer -- orchestration, events, use cases
|   +-- __init__.py
|   +-- events/
|   |   +-- bus.py                   # EventBus Protocol + AsyncioEventBus
|   |   +-- topics.py                # Event types: TASK_CREATED, MODEL_LOADED, etc.
|   +-- workers/
|   |   +-- base.py                  # Worker base class (from existing worker.py)
|   |   +-- manager.py               # WorkerManager + graceful shutdown
|   +-- pipeline.py                  # Task pipeline: download -> transcribe -> summarize
|
+-- infra/                           # Infrastructure layer -- external implementations
|   +-- __init__.py
|   +-- api/                         # Presentation
|   |   +-- app.py                   # FastAPI app + lifespan
|   |   +-- error_handler.py         # Unified exception middleware
|   |   +-- routes/
|   |   |   +-- tasks.py
|   |   |   +-- upload.py
|   |   |   +-- settings.py
|   |   |   +-- bilibili.py
|   |   +-- schemas.py               # Pydantic models (HTTP boundary)
|   |   +-- websocket.py             # ConnectionManager
|   +-- db/
|   |   +-- task_repository.py       # Implements domain.task.repository Protocol
|   +-- llm/                         # LLM client implementations (existing)
|   +-- transcriber/                 # Transcriber implementations (existing)
|   +-- downloader/                  # Downloader implementations (existing)
|   +-- storage/
|   |   +-- local_storage.py         # Implements domain.storage.repository Protocol
|   +-- model/
|       +-- local_loader.py          # LocalModelLoader implements ModelLoader Protocol
|
+-- observability/                   # Cross-cutting observability
|   +-- __init__.py
|   +-- telemetry.py                 # OTEL tracer/meter provider init
|   +-- metrics.py                   # Business metrics: queue depth, task duration, model load
|
+-- logging/                         # loguru configuration
|   +-- __init__.py                  # Unified logger + OTEL trace context injection
|
+-- shared/                          # Cross-domain shared (pure data structures)
|   +-- __init__.py
|   +-- types/
|       +-- __init__.py
|       +-- exceptions.py            # Exception hierarchy
|
+-- config/                          # Existing config module
+-- utils/                           # Existing utilities
+-- version.py                       # Existing
```

Dependency direction:
```
infra/api -> application -> domain <- infra/db, infra/llm, infra/storage (implement domain Protocols)
                |
           observability (cross-cutting, all layers may call)
           logging (cross-cutting)
```

## 4. Event Bus

### 4.1 Interface

```python
# application/events/bus.py
from typing import Protocol, Any, Callable, Awaitable

class EventBus(Protocol):
    """Event bus interface -- asyncio implementation first, swappable to Redis later."""
    async def publish(self, topic: str, payload: Any) -> None: ...
    def subscribe(self, topic: str, handler: Callable[[Any], Awaitable[None]]) -> str: ...
    def unsubscribe(self, topic: str, subscription_id: str) -> None: ...
```

### 4.2 Event Topics

```python
# application/events/topics.py
# Task lifecycle
TASK_CREATED = "task.created"
TASK_STATUS_CHANGED = "task.status_changed"
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"
TASK_CANCELLED = "task.cancelled"
TASK_DELETED = "task.deleted"

# Model lifecycle
MODEL_LOAD_REQUESTED = "model.load_requested"
MODEL_LOAD_STARTED = "model.load_started"
MODEL_LOADED = "model.loaded"
MODEL_LOAD_FAILED = "model.load_failed"
MODEL_UNLOADED = "model.unloaded"

# Storage
STORAGE_CLEANUP_REQUESTED = "storage.cleanup_requested"
STORAGE_CLEANUP_COMPLETED = "storage.cleanup_completed"
STORAGE_QUOTA_WARNING = "storage.quota_warning"
```

### 4.3 AsyncioEventBus Implementation

- Topic-based dispatch via `dict[str, dict[str, Callable]]`
- Dead letter queue for failed handlers
- OTEL span auto-instrumentation on publish/handle

### 4.4 Future: RedisEventBus

When remote-service model loader is introduced, implement `RedisEventBus` using Redis pub/sub. Interface stays the same.

## 5. Model Loader Abstraction

### 5.1 Domain Types

```python
# domain/model/type.py
class LoaderType(str, Enum):
    LOCAL = "local"
    REMOTE_SERVICE = "remote"

@dataclass
class ModelSpec:
    model_id: str
    loader_type: LoaderType
    device: str               # "cpu" | "cuda"
    compute_type: str         # "int8" | "int8_float16"

@dataclass
class ModelState:
    model_id: str
    loader_type: LoaderType
    status: str               # "unloaded" | "loading" | "loaded" | "failed"
    load_time_sec: float = 0.0
    error_message: str | None = None
    # to_dict() / from_dict() per CLAUDE.md contract
```

### 5.2 Loader Protocol

```python
# domain/model/repository.py
class ModelLoader(Protocol):
    async def load(self, spec: ModelSpec) -> ModelState: ...
    async def unload(self, model_id: str) -> None: ...
    async def is_loaded(self, model_id: str) -> bool: ...
```

### 5.3 Implementations

- `infra/model/local_loader.py` -- `LocalModelLoader`: uses `run_in_executor` for non-blocking WhisperModel init
- Future: `infra/model/remote_loader.py` -- `RemoteModelLoader`: calls remote transcription service

### 5.4 ModelService

- `ensure_loaded(spec)`: checks state, loads if needed, publishes MODEL_LOADED/FAILED events
- `unload(model_id)`: releases resources, publishes MODEL_UNLOADED
- OTEL span on every load/unload with model_id + loader_type attributes

## 6. Exception Architecture

### 6.1 Exception Hierarchy

```
ShengWenError                          # Base
+-- DomainError                        # Business (expected, user-facing, no alert)
|   +-- TaskError
|   |   +-- TaskNotFoundError
|   |   +-- TaskInvalidStateError
|   |   +-- TaskCancelledError
|   |   +-- TaskConflictError
|   +-- ModelError
|   |   +-- ModelLoadError
|   |   +-- ModelNotReadyError
|   |   +-- ModelUnavailableError
|   +-- TranscriptionError
|   +-- StorageError
|   |   +-- StorageQuotaExceededError
|   |   +-- FileNotFoundError
|   +-- LLMError
|       +-- LLMResponseError
|       +-- LLMConnectionError
|
+-- InfrastructureError                # Technical (unexpected, needs alert)
    +-- DatabaseError
    +-- ExternalServiceError
    +-- ConfigurationError
```

### 6.2 Cross-cutting Aspects

**HTTP Middleware** (`infra/api/error_handler.py`):
- `DomainError` -> 4xx with business code, info-level log, no alert
- `InfrastructureError` -> 500, error-level log, OTEL alert
- Unknown -> 500, critical-level log, OTEL alert

**Worker Error Aspect** (`application/workers/error_aspect.py`):
- Wraps worker process_task calls
- `DomainError`: mark task FAILED, business log
- `InfrastructureError`: mark task FAILED, OTEL alert
- `CancelledError`: silent, publish TASK_CANCELLED

**Event Bus Dead Letter**:
- Failed handlers go to `dead_letter` queue
- Consumable via `consume_dead_letters()` for alerting/retry

### 6.3 Status Code Mapping

| Code | HTTP | Category |
|------|------|----------|
| TASK_NOT_FOUND | 404 | Domain |
| TASK_INVALID_STATE | 409 | Domain |
| TASK_CANCELLED | 499 | Domain |
| STORAGE_QUOTA_EXCEEDED | 507 | Domain |
| MODEL_LOAD_ERROR | 503 | Domain |
| DATABASE_ERROR | 500 | Infrastructure |
| INTERNAL_ERROR | 500 | Infrastructure |

## 7. Storage Management

### 7.1 Domain Types

```python
class FileType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    TRANSCRIPT = "transcript"
    SUMMARY = "summary"
    TEMP = "temp"
    CHUNK_DEBUG = "chunk_debug"

class CleanupTrigger(str, Enum):
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_DELETED = "task_deleted"
    SCHEDULED = "scheduled"
    MANUAL = "manual"

@dataclass
class FileRecord:
    path: str
    task_id: str
    file_type: FileType
    size_bytes: int
    created_at: datetime
    expired_at: datetime | None = None
    # to_dict() / from_dict()

@dataclass
class StoragePolicy:
    max_total_mb: float = 2048
    retention_completed_sec: int = 86400     # 24h
    retention_failed_sec: int = 7200         # 2h
    cleanup_interval_sec: int = 600          # 10min
```

### 7.2 StorageService

- `register(task_id, path, file_type)`: track file in repository
- `schedule_cleanup(trigger, task_id)`: apply retention policy, delete expired files
- `get_usage()`: total size, count by type, quota status
- Event-driven: subscribes to TASK_COMPLETED, TASK_FAILED, TASK_DELETED

### 7.3 Cleanup Policy

| Trigger | Video | Audio | Transcript | Summary | Temp |
|---------|-------|-------|-----------|---------|------|
| TASK_COMPLETED | clean | clean | keep | keep | clean |
| TASK_FAILED | clean | clean | clean | clean | clean |
| TASK_DELETED | clean | clean | clean | clean | clean |
| Scheduled (expired) | clean | clean | clean | clean | clean |

### 7.4 Future: Storage Tier

```python
class StorageTier(str, Enum):
    LOCAL = "local"
    OBJECT_STORAGE = "object"   # S3/MinIO for remote-service scenario
```

## 8. Logging -- Loguru

### 8.1 Replacement Scope

Full replacement: delete `utils/logger.py`, all modules use `from loguru import logger` directly.

### 8.2 Configuration

```python
# logging/__init__.py
def setup_logging(level: str = "INFO", json_format: bool = False):
    logger.remove()
    logger.add(
        sys.stdout,
        format=...,           # Structured with trace_id
        level=level,
        serialize=json_format,
        enqueue=True,         # Thread-safe
        backtrace=True,
        diagnose=False,       # No variable values in production
    )
```

### 8.3 OTEL Integration

`log_with_span()` helper injects current OTEL trace_id into loguru extra context automatically.

## 9. Observability -- OTEL

### 9.1 Setup

```python
# observability/telemetry.py
def setup_telemetry(endpoint: str | None):
    """Optional: no-op if no OTEL collector configured."""
```

### 9.2 Instrumentation Points

| Layer | Type | Span/Metric Name | What it measures |
|-------|------|------------------|------------------|
| Domain | Span | `task.create` | Task creation |
| Domain | Span | `model.load` | Model loading |
| Domain | Span | `storage.cleanup` | File cleanup |
| Application | Span | `pipeline.process_task` | Full pipeline |
| Application | Gauge | `worker.queue_depth` | Queue backlog per worker |
| Infra | Span | `model.load.local` | Local WhisperModel init |
| Infra | Span | `llm.request` | LLM API call + token count |
| Infra | Span | `db.query` | Database read/write |
| Infra | Span | `storage.io` | File I/O |
| API | Span | `http.request` | Auto-instrumented by FastAPI OTEL |
| Domain | Histogram | `sheng_wen.task.duration` | Task total time (create -> complete) |
| Domain | Counter | `sheng_wen.task.count` | Tasks by status |
| Domain | Histogram | `sheng_wen.model.load_duration` | Model load time |

## 10. Execution Plan

All changes, incremental execution with TDD verification per step:

| Phase | Scope | Depends On |
|-------|-------|------------|
| P1 | Logging: loguru replacement + `logging/` module | None |
| P2 | Exception hierarchy: `shared/types/exceptions.py` | P1 |
| P3 | Event bus: `application/events/` (Protocol + AsyncioEventBus) | P1 |
| P4 | Domain types: `domain/*/type.py` (dataclass contracts) | P2 |
| P5 | Model loader: `domain/model/` + `infra/model/local_loader.py` | P3, P4 |
| P6 | Storage management: `domain/storage/` + `infra/storage/` | P3, P4 |
| P7 | Task domain: `domain/task/` + `infra/db/task_repository.py` | P3, P4 |
| P8 | API refactoring: split `api.py` into `infra/api/` routes | P5, P6, P7 |
| P9 | Worker integration: wire workers through event bus + pipeline | P5, P7 |
| P10 | Observability: OTEL setup + instrumentation | P8, P9 |
| P11 | Config update: add storage/observability/llm settings | P10 |
| P12 | Integration tests + cleanup | All |

Each phase ends with:
1. Unit tests (TDD: write test first)
2. `basedpyright` type check passes
3. `ruff` lint/format passes
4. Manual smoke test: `./start.sh` -> submit task -> verify end-to-end

## 11. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Protocol-based interfaces (not ABC) | Structural subtyping, no inheritance coupling, easier mocking in tests |
| asyncio event bus first | Single-machine deployment is the primary use case; Redis adds operational overhead |
| Event bus swappable to Redis | Interface defined as Protocol; future `RedisEventBus` is a drop-in replacement |
| `run_in_executor` for model loading | WhisperModel init is CPU/GPU-bound sync code; executor prevents event loop blocking |
| Dead letter queue | Failed event handlers are captured, not silently dropped; enables alerting/retry |
| Loguru `enqueue=True` | Thread-safe logging from sync workers without blocking |
| FileRecord in SQLite | Co-located with tasks DB; enables quota tracking and expiry queries |
| Business vs Infrastructure exceptions | Different log levels, different alerting, different HTTP status codes |
