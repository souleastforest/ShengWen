from src.main.python.sheng_wen.shared.types.exceptions import (
    DatabaseError,
    DomainError,
    StorageFileNotFoundError,
    InfrastructureError,
    ModelError,
    ModelLoadError,
    ShengWenError,
    StorageError,
    StorageQuotaExceededError,
    TaskError,
    TaskNotFoundError,
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


def test_storage_file_not_found_error():
    err = StorageFileNotFoundError("/some/path.wav")
    assert isinstance(err, StorageError)
    assert err.business_code == "FILE_NOT_FOUND"
    assert err.http_status == 404
