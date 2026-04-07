from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class LoaderType(str, Enum):
    LOCAL = "local"
    REMOTE_SERVICE = "remote"


@dataclass
class ModelSpec:
    model_id: str
    loader_type: LoaderType
    device: str = "cpu"
    compute_type: str = "int8"

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "loader_type": self.loader_type.value,
            "device": self.device,
            "compute_type": self.compute_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ModelSpec":
        return cls(
            model_id=data.get("model_id", ""),
            loader_type=LoaderType(data.get("loader_type", "local")),
            device=data.get("device", "cpu"),
            compute_type=data.get("compute_type", "int8"),
        )


@dataclass
class ModelState:
    model_id: str
    loader_type: LoaderType
    status: str = "unloaded"
    load_time_sec: float = 0.0
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        data = {
            "model_id": self.model_id,
            "loader_type": self.loader_type.value,
            "status": self.status,
            "load_time_sec": self.load_time_sec,
        }
        if self.error_message is not None:
            data["error_message"] = self.error_message
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ModelState":
        return cls(
            model_id=data.get("model_id", ""),
            loader_type=LoaderType(data.get("loader_type", "local")),
            status=data.get("status", "unloaded"),
            load_time_sec=float(data.get("load_time_sec", 0.0)),
            error_message=data.get("error_message"),
        )

