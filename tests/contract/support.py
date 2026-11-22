import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "sdk"), str(ROOT)]
from listening_plugin import PluginContext
from listening_plugin.manifest import read_manifest
from listening_plugin.runtime import serve


def module(identity, name):
    return importlib.import_module(f"plugins.{identity}.{name}")


def worker(identity, credentials, *args):
    return module(identity, "provider").Provider(PluginContext(credentials), *args)


def sources(identity):
    return read_manifest(ROOT / "plugins" / identity / "manifest.json")["sources"]


def run_worker(provider):
    identity = type(provider).__module__.split(".")[1]
    serve(lambda: provider, sources(identity))
