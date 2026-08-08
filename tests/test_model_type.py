from src.main.python.sheng_wen.domain.model.type import LoaderType, ModelSpec, ModelState


def test_model_spec_roundtrip():
    spec = ModelSpec(model_id="whisper", loader_type=LoaderType.LOCAL, device="cpu")
    data = spec.to_dict()
    restored = ModelSpec.from_dict(data)
    assert restored.model_id == "whisper"
    assert restored.loader_type == LoaderType.LOCAL


def test_model_state_defaults():
    state = ModelState(model_id="test", loader_type=LoaderType.LOCAL)
    assert state.status == "unloaded"
    assert state.load_time_sec == 0.0

