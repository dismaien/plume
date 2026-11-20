"""Use the application frame decoder in evaluation, without importing models."""
import importlib.util
from pathlib import Path

path=Path(__file__).resolve().parents[2]/'internal/transcription/adapters/py/nvidia/activity_timeline.py'
spec=importlib.util.spec_from_file_location('plume_activity_timeline',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
file_hash=module.file_hash
validate=module.validate
save_activity=module.save_activity
extract_segments=module.extract_segments
replay=module.replay
