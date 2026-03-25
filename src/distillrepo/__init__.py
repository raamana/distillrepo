"""distillrepo package."""

from importlib.metadata import PackageNotFoundError, version

from .api import analyze, bundle

try:
    __version__ = version("distillrepo")
except PackageNotFoundError:
    # Running from source (not installed as a distribution).
    __version__ = "0.25"

__all__ = ["__version__", "analyze", "bundle"]
