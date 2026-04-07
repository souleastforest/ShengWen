from src.main.python.sheng_wen.config.settings import StorageConfig, ObservabilityConfig


def test_storage_config_defaults():
    cfg = StorageConfig()
    assert cfg.max_total_mb == 2048
    assert cfg.retention_completed_sec == 86400
    assert cfg.cleanup_interval_sec == 600


def test_observability_config_defaults():
    cfg = ObservabilityConfig()
    assert cfg.enabled is False
    assert cfg.otlp_endpoint == ""
    assert cfg.service_name == "sheng-wen"


def test_storage_config_custom():
    cfg = StorageConfig(max_total_mb=4096, base_dir="/data")
    assert cfg.max_total_mb == 4096
    assert cfg.base_dir == "/data"
