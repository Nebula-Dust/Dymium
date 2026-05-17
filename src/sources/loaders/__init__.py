"""Source loaders and detection helpers."""
from .acquisition import acquire_source
from .detection import detect_source, file_sha256
__all__ = ["acquire_source", "detect_source", "file_sha256"]
