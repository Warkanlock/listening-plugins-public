"""Translate Sockseek's JSON download events into Listening progress."""

import json
import os
import select
from contextlib import contextmanager
from threading import Event, Thread

from listening_plugin.progress import report_download_progress


def read_progress(descriptor, staging, stop):
    remainder = b""
    while not stop.is_set():
        if not select.select([descriptor], [], [], 0.1)[0]:
            continue
        chunk = os.read(descriptor, 8192)
        if not chunk:
            return
        *lines, remainder = (remainder + chunk).split(b"\n")
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict) or not isinstance(data := event.get("data"), dict):
                continue
            if event.get("type") == "download_progress":
                report_download_progress(staging, data.get("bytesTransferred"), data.get("totalBytes"))
        # Oversized CLI records carry no transfer totals.
        if len(remainder) > 8192:
            remainder = b"\x00"


@contextmanager
def progress_output(staging):
    readable, writable = os.pipe()
    stop = Event()
    reader = Thread(target=read_progress, args=(readable, staging, stop))
    try:
        with os.fdopen(writable, "wb") as output:
            reader.start()
            try:
                yield output
            finally:
                stop.set()
                reader.join()
    finally:
        os.close(readable)
