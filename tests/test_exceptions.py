from src.main.python.sheng_wen.shared.types.exceptions import (
    ConfigurationError,
    DatabaseError,
    DomainError,
    ExternalServiceError,
    FileNotFoundError as ShengWenFileNotFoundError,
    InfrastructureError,
    LLMConnectionError,
    LLMError,
    LLMResponseError,
    ModelError,
    ModelLoadError,
    ModelNotReadyError,
    ModelUnavailableError,
    ShengWenError,
    StorageError,
    StorageQuotaExceededError,
    TaskCancelledError,
    TaskConflictError,
    TaskError,
    TaskInvalidStateError,
    TaskNotFoundError,
    TranscriptionError,
)


def test_exception_hierarchy():
    assert issubclass(DomainError, ShengWenError)
    assert issubclass(InfrastructureError, ShengWenError)
    assert issubclass(TaskNotFoundError, TaskError)
    assert issubclass(ModelLoadError, ModelError)
    assert issubclass(DatabaseError, InfrastructureError)
    assert issubclass(StorageQuotaExceededError, StorageError)


def test_exception_message():
    err = TaskNotFoundError("task-123")
    assert str(err) == "Task not found: task-123"
    assert err.business_code == "TASK_NOT_FOUND"
    assert err.http_status == 404
