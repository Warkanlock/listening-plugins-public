"""Read the configured catalog and stream bounded files from the same origin."""
import json
import socket
from contextlib import contextmanager
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from listening_plugin.contract import CompletedTimeout, settings

MAX_CATALOG = 1024 * 1024
MAX_AUDIO = 512 * 1024 * 1024


@contextmanager
def network_timeout():
    try:
        yield
    except URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise CompletedTimeout from error
        raise
    except (TimeoutError, socket.timeout) as error:
        raise CompletedTimeout from error


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class CatalogSource:
    def __init__(self, credentials):
        self.credentials = credentials

    def origin(self):
        configured = settings(self.credentials).get("catalog_url")
        if configured is None:
            configured = (self.credentials / "catalog-url").read_text(encoding="utf-8")
        value = configured.strip().rstrip("/")
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment):
            raise ValueError("Configure a plain HTTP or HTTPS catalog URL.")
        return value

    @contextmanager
    def open(self, path):
        opener = build_opener(ProxyHandler({}), NoRedirects())
        url = f"{self.origin()}/{path}"
        with network_timeout():
            response = opener.open(url, timeout=15)
        with response:
            yield response

    def recordings(self):
        with self.open("catalog.json") as response:
            with network_timeout():
                payload = response.read(MAX_CATALOG + 1)
        if len(payload) > MAX_CATALOG:
            raise ValueError("The catalog is too large.")
        releases = json.loads(payload)
        if not isinstance(releases, list):
            raise ValueError("The catalog must contain a list of recordings.")
        return releases

    def download(self, name, destination):
        created = False
        try:
            with self.open("audio/" + name) as response, destination.open("xb") as output:
                created = True
                remaining = MAX_AUDIO
                while True:
                    with network_timeout():
                        chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError("The recording is too large.")
                    output.write(chunk)
        except Exception:
            if created:
                destination.unlink(missing_ok=True)
            raise
