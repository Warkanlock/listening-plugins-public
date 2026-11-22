"""Reuse Sockseek's authenticated connection in this private worker container."""
import hashlib
import http.client
import json
import math
import os
import select
import signal
import subprocess
import time
from pathlib import Path
from uuid import UUID

from listening_plugin import CompletedTimeout, LoginUnavailable
from .configuration import effective_config

ADDRESS = "http://127.0.0.1:5030"
# Leave a quiet window after an unsuccessful sign-in across searches and transfers.
LOGIN_BACKOFF = 30 * 60


def api(path, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", 5030, timeout=1)
    try:
        conn.request(method, "/api/" + path)
        reply = conn.getresponse()
        if not 200 <= reply.status < 300:
            raise OSError("Local Sockseek session is unavailable.")
        data = reply.read(512 * 1024)
        return json.loads(data) if data else None
    finally:
        conn.close()


def has_container_init():
    try:
        return Path("/proc/1/comm").read_text().strip() in {"docker-init", "tini", "tini-static"}
    except FileNotFoundError:
        return False


def process_start(pid):
    # Field 22 follows a parenthesized command which can contain spaces.
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except FileNotFoundError:
        return None


class Session:
    def __init__(self, binary, credentials, directory=Path("/tmp/listening-sockseek")):
        self.binary = binary
        self.credentials = credentials
        self.directory = directory

    def read(self):
        try:
            return json.loads((self.directory / "session.json").read_text())
        except FileNotFoundError:
            return {}

    def save(self, state):
        path = self.directory / "session.json"
        tmp = path.with_suffix(".new")
        tmp.write_text(json.dumps(state))
        tmp.replace(path)

    def stop(self, state):
        # PID reuse must never signal a different process. Linux pidfds bind the
        # signal and wait to this specific daemon even if it exits concurrently.
        pid = state.get("pid")
        if not pid:
            return
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            return
        try:
            if process_start(pid) != state["start"]:
                return
            signal.pidfd_send_signal(fd, signal.SIGKILL)
            if not select.select([fd], [], [], 2)[0]:
                raise OSError("Could not stop the Sockseek session.")
        except ProcessLookupError:
            pass
        finally:
            os.close(fd)

    def check_login(self):
        state = self.read()
        remaining = math.ceil(state.get("retry_at", 0) - time.monotonic())
        if remaining > 0:
            with effective_config(self.credentials) as source:
                if state["config"] == hashlib.sha256(source.read_bytes()).hexdigest():
                    raise LoginUnavailable(retry_after_seconds=remaining)

    def ensure(self):
        self.directory.mkdir(mode=0o700, exist_ok=True)
        state = self.read()
        with effective_config(self.credentials) as source:
            contents = source.read_bytes()
            fingerprint = hashlib.sha256(contents).hexdigest()
            same = state.get("config") == fingerprint
            remaining = math.ceil(state.get("retry_at", 0) - time.monotonic())
            if same and remaining > 0:
                raise LoginUnavailable(retry_after_seconds=remaining)
            if same and state.get("pid") and process_start(state["pid"]) == state["start"]:
                try:
                    status = api("server/status")
                except (OSError, http.client.HTTPException):
                    status = None
                # The daemon retains completed workflows in memory. Rotate
                # between requests, after enough jobs to amortize one login.
                if status and not status["activeJobCount"] and status["totalJobCount"] < 128:
                    return state
            self.stop(state)
            proc = subprocess.Popen(
                [self.binary, "daemon", "--server-ip", "127.0.0.1", "--server-port", "5030",
                 "--config", str(source)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
            state = {"pid": proc.pid, "start": process_start(proc.pid), "config": fingerprint}
            try:
                self.save(state)
                deadline = time.monotonic() + 4
                while proc.poll() is None and time.monotonic() < deadline:
                    try:
                        api("server/status")
                        return state
                    except OSError:
                        time.sleep(.1)
                raise ConnectionError("The local Sockseek session did not start.")
            except BaseException:
                proc.kill()
                proc.wait()
                raise

    def abort(self, state):
        connected = False
        try:
            connected = api("server/status")["soulseekClient"]["isReady"]
            if connected:
                for workflow in api("workflows"):
                    if workflow["activeJobCount"]:
                        identity = UUID(workflow["workflowId"])
                        api(f"workflows/{identity}/cancel", "POST")
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if not api("server/status")["activeJobCount"]:
                        return
                    time.sleep(.1)
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            pass
        self.stop(state)
        if not connected:
            self.save({"config": state["config"], "retry_at": time.monotonic() + LOGIN_BACKOFF})
            raise LoginUnavailable(retry_after_seconds=LOGIN_BACKOFF)

    def run_foreground(self, query, options, arguments):
        # Older managers run sleep as PID 1 and cannot reap a detached daemon.
        # Keep the operation and its subprocess in the same worker on those hosts.
        self.check_login()
        if not arguments.get("capture_output"):
            arguments["stderr"] = subprocess.PIPE
        with effective_config(self.credentials) as config:
            try:
                result = subprocess.run([self.binary, query, "--config", str(config),
                                         "--no-progress", *options], **arguments)
            except subprocess.TimeoutExpired as error:
                result = None
                detail = error.stderr or b""
            else:
                if result.returncode == 0:
                    return result
                detail = result.stderr or b""
            if isinstance(detail, str):
                detail = detail.encode()
            if b"login failed" in detail.lower() or b"failed to ensure soulseek connection" in detail.lower():
                self.directory.mkdir(mode=0o700, exist_ok=True)
                self.save({"config": hashlib.sha256(config.read_bytes()).hexdigest(),
                           "retry_at": time.monotonic() + LOGIN_BACKOFF})
                raise LoginUnavailable(retry_after_seconds=LOGIN_BACKOFF)
            if result is None:
                raise CompletedTimeout
            return result

    def run(self, query, options, **arguments):
        # The room manager holds the provider operation lock across the request.
        if not has_container_init():
            return self.run_foreground(query, options, arguments)
        state = self.ensure()
        try:
            result = subprocess.run([self.binary, query, "--config", "none", "--remote", ADDRESS,
                                     "--no-progress", *options], **arguments)
        except subprocess.TimeoutExpired as error:
            self.abort(state)
            raise CompletedTimeout from error
        if result.returncode:
            self.abort(state)
        return result
