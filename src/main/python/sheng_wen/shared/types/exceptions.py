"""Unified exception hierarchy for ShengWen."""


class ShengWenError(Exception):
    """Base for all ShengWen exceptions."""

    def __init__(
        self,
        message: str = "",
        *,
        business_code: str = "",
        http_status: int = 500,
    ) -> None:
        super().__init__(message)
        self.business_code = business_code
        self.http_status = http_status


class DomainError(ShengWenError):
    def __init__(
        self,
        message: str = "",
        *,
        business_code: str = "",
        http_status: int = 400,
    ) -> None:
        super().__init__(message, business_code=business_code, http_status=http_status)


class TaskError(DomainError):
    pass


class TaskNotFoundError(TaskError):
    def __init__(self, task_id: str = "") -> None:
        super().__init__(
            f"Task not found: {task_id}",
            business_code="TASK_NOT_FOUND",
            http_status=404,
        )


class TaskInvalidStateError(TaskError):
    def __init__(self, message: str = "") -> None:
        super().__init__(
            message,
            business_code="TASK_INVALID_STATE",
            http_status=409,
        )


class TaskCancelledError(TaskError):
    def __init__(self, task_id: str = "") -> None:
        super().__init__(
            f"Task cancelled: {task_id}",
            business_code="TASK_CANCELLED",
            http_status=499,
        )


class TaskConflictError(TaskError):
    def __init__(self, message: str = "") -> None:
        super().__init__(
            message,
            business_code="TASK_CONFLICT",
            http_status=409,
        )


class ModelError(DomainError):
    pass


class ModelLoadError(ModelError):
    def __init__(self, message: str = "") -> None:
        super().__init__(
            message,
            business_code="MODEL_LOAD_ERROR",
            http_status=503,
        )


class ModelNotReadyError(ModelError):
    def __init__(self, model_id: str = "") -> None:
        super().__init__(
            f"Model not ready: {model_id}",
            business_code="MODEL_NOT_READY",
            http_status=503,
        )


class ModelUnavailableError(ModelError):
    def __init__(self, model_id: str = "") -> None:
        super().__init__(
            f"Model unavailable: {model_id}",
            business_code="MODEL_UNAVAILABLE",
            http_status=503,
        )


class TranscriptionError(DomainError):
    pass


class StorageError(DomainError):
    pass


class StorageQuotaExceededError(StorageError):
    def __init__(self, message: str = "") -> None:
        super().__init__(
            message,
            business_code="STORAGE_QUOTA_EXCEEDED",
            http_status=507,
        )


class StorageFileNotFoundError(StorageError):
    def __init__(self, path: str = "") -> None:
        super().__init__(
            f"File not found: {path}",
            business_code="FILE_NOT_FOUND",
            http_status=404,
        )


class LLMError(DomainError):
    pass


class LLMResponseError(LLMError):
    def __init__(self, message: str = "") -> None:
        super().__init__(
            message,
            business_code="LLM_RESPONSE_ERROR",
            http_status=502,
        )


class LLMConnectionError(LLMError):
    def __init__(self, message: str = "") -> None:
        super().__init__(
            message,
            business_code="LLM_CONNECTION_ERROR",
            http_status=502,
        )


class InfrastructureError(ShengWenError):
    def __init__(
        self,
        message: str = "",
        *,
        business_code: str = "",
        http_status: int = 500,
    ) -> None:
        super().__init__(message, business_code=business_code, http_status=http_status)


class DatabaseError(InfrastructureError):
    def __init__(self, message: str = "") -> None:
        super().__init__(
            message,
            business_code="DATABASE_ERROR",
            http_status=500,
        )


class ExternalServiceError(InfrastructureError):
    def __init__(self, message: str = "") -> None:
        super().__init__(
            message,
            business_code="EXTERNAL_SERVICE_ERROR",
            http_status=500,
        )


class ConfigurationError(InfrastructureError):
    def __init__(self, message: str = "") -> None:
        super().__init__(
            message,
            business_code="CONFIGURATION_ERROR",
            http_status=500,
        )
