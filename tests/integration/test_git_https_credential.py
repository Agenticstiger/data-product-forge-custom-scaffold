"""A credential reaches a real HTTPS git server as the right header, and stays where it was put.

A small local HTTPS server (``git http-backend`` behind a Python server) demands
``Authorization: Basic base64(x-access-token:<token>)`` and records what every request
carried. The resolver runs against it the way an operator who allows that host and port would
run it against a private repository.

This is the one test in which ``http.<url>.extraHeader`` meets an HTTPS request. The unit tests
point the source's url at a local directory instead, so only this one shows that the key git
builds from the source's url is the url git requests, and that a redirect or a rejected token
ends the way the engine says it does. A key that did not match would send no token at all.
"""

from __future__ import annotations

import base64
import os
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional, Tuple

import pytest

from data_product_forge_custom_scaffold.resolvers.base import ResolutionError
from data_product_forge_custom_scaffold.resolvers.git import _MIN_GIT, GitResolver, _git_version

HOST = "127.0.0.1"
TOKEN = "tok-3f9a-not-a-real-token"
MANIFEST = "fluid-scaffold.yaml"


def _http_backend() -> Optional[Path]:
    git = shutil.which("git")
    if git is None:
        return None
    exec_path = subprocess.run([git, "--exec-path"], capture_output=True, text=True).stdout.strip()
    backend = Path(exec_path) / "git-http-backend"
    return backend if backend.is_file() else None


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.name == "nt", reason="needs a POSIX environment"),
    pytest.mark.skipif(
        _http_backend() is None or shutil.which("openssl") is None,
        reason="needs git http-backend and the openssl command",
    ),
]


def _basic(token: str) -> str:
    return base64.b64encode(f"x-access-token:{token}".encode()).decode()


def _backend_answer(
    backend: Path, repos: Path, request: BaseHTTPRequestHandler, path: str, query: str
) -> Tuple[int, List[Tuple[str, str]], bytes]:
    """Run ``git http-backend`` for *request*; its status, headers and body."""
    length = int(request.headers.get("Content-Length") or 0)
    env = {
        **os.environ,
        "GIT_PROJECT_ROOT": str(repos),
        "GIT_HTTP_EXPORT_ALL": "1",
        "REQUEST_METHOD": request.command,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "CONTENT_TYPE": request.headers.get("Content-Type", ""),
        "CONTENT_LENGTH": str(length),
        "HTTP_CONTENT_ENCODING": request.headers.get("Content-Encoding", ""),
        "GIT_PROTOCOL": request.headers.get("Git-Protocol", ""),
        "REMOTE_USER": "x-access-token",
    }
    out = subprocess.run(
        [str(backend)],
        input=request.rfile.read(length) if length else b"",
        env=env,
        capture_output=True,
        timeout=60,
    ).stdout
    head, _, payload = out.partition(b"\r\n\r\n")
    status, headers = 200, []
    for line in head.decode("latin-1").split("\r\n"):
        name, _, value = line.partition(":")
        if name.lower() == "status":
            status = int(value.split()[0])
        elif name:
            headers.append((name, value.strip()))
    return status, headers, payload


class _Handler(BaseHTTPRequestHandler):
    server: GitServer  # type: ignore[assignment]
    timeout = 60  # a client that goes silent ends its own handler, not the run

    def log_message(self, *args: object) -> None:  # keep the test output quiet
        pass

    def _answer(self, status: int, headers: List[Tuple[str, str]], body: bytes) -> None:
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve(self) -> None:
        sent = self.headers.get("Authorization")
        path, _, query = self.path.partition("?")
        if self.server.redirect and path.endswith("/info/refs"):
            self.server.requests.append((self.path, sent, 301))
            moved = self.path.replace("/org/repo.git", "/org/moved.git")
            return self._answer(301, [("Location", moved)], b"")
        if sent != f"Basic {_basic(self.server.token)}":
            self.server.requests.append((self.path, sent, 401))
            return self._answer(401, [("WWW-Authenticate", 'Basic realm="git"')], b"")
        self.server.requests.append((self.path, sent, 200))
        self._answer(*_backend_answer(self.server.backend, self.server.repos, self, path, query))

    def do_GET(self) -> None:  # noqa: N802 (the name http.server calls)
        self._serve()

    def do_POST(self) -> None:  # noqa: N802
        self._serve()


class GitServer(ThreadingHTTPServer):
    """A TLS git server that demands one token. ``requests`` is (path, Authorization, status)."""

    def __init__(self, repos: Path, cert: Path, key: Path, backend: Path) -> None:
        super().__init__((HOST, 0), _Handler)
        self.repos, self.backend = repos, backend
        self.token = TOKEN
        self.redirect = False
        self.requests: List[Tuple[str, Optional[str], int]] = []
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(cert, key)
        self.socket = context.wrap_socket(self.socket, server_side=True)
        self.port = self.server_address[1]
        self.url = f"https://{HOST}:{self.port}/org/repo.git"
        self._thread = threading.Thread(
            target=self.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=5)


def _git(cwd: Path, *args: str) -> str:
    identity = ["-c", "user.email=t@t.t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
    done = subprocess.run(
        ["git", *identity, *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


@pytest.fixture
def server(tmp_path: Path, monkeypatch):
    """A private bundle repository behind the server, and an operator who allows it."""
    backend = _http_backend()
    assert backend is not None
    if _git_version() < _MIN_GIT:
        pytest.skip("git is too old to carry a credential in the environment")

    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q")
    _git(work, "symbolic-ref", "HEAD", "refs/heads/main")
    (work / MANIFEST).write_text("apiVersion: forge.scaffold/v1\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "c1")
    _git(work, "tag", "-a", "-m", "v1", "v1")
    repos = tmp_path / "srv"
    (repos / "org").mkdir(parents=True)
    _git(work, "clone", "-q", "--bare", str(work), str(repos / "org" / "repo.git"))

    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1"]
            + ["-keyout", str(key), "-out", str(cert), "-subj", f"/CN={HOST}"]
            + ["-addext", f"subjectAltName=IP:{HOST}"],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, OSError):
        pytest.skip("openssl could not make a certificate")

    live = GitServer(repos, cert, key, backend)
    config = tmp_path / "gitconfig"
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    monkeypatch.setenv(
        "GIT_SSL_NO_VERIFY", "true"
    )  # a self-signed certificate; the header is the point
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, HOST)
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("FLUID_CUSTOM_SCAFFOLD_NOCACHE", raising=False)
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_AUTH_HOSTS", f"{HOST}:{live.port}")
    monkeypatch.setenv("FLUID_SCAFFOLD_TOKEN", TOKEN)
    try:
        yield SimpleNamespace(
            url=live.url,
            sha=_git(work, "rev-parse", "HEAD"),
            requests=live.requests,
            serving=live,
            root=tmp_path,
        )
    finally:
        live.close()


def _source(url: str, ref: str) -> dict:
    return {"kind": "git", "url": url, "ref": ref, "auth": {"secret_ref": "FLUID_SCAFFOLD_TOKEN"}}


@pytest.mark.parametrize("ref", ["main", "v1", "commit"])
def test_every_request_carries_the_header_and_nothing_is_left_on_disk(server, ref: str) -> None:
    ref = server.sha if ref == "commit" else ref

    bundle = GitResolver().resolve(_source(server.url, ref))

    assert (bundle.bundle_root / MANIFEST).is_file()
    assert bundle.resolved_version == server.sha
    assert server.requests
    assert {status for _, _, status in server.requests} == {200}
    assert {sent for _, sent, _ in server.requests} == {f"Basic {_basic(TOKEN)}"}
    on_disk = "\n".join(
        p.read_text(errors="replace") for p in (server.root / "cache").rglob("*") if p.is_file()
    )
    assert TOKEN not in on_disk and _basic(TOKEN) not in on_disk


def test_the_clone_records_the_plain_url(server) -> None:
    bundle = GitResolver().resolve(_source(server.url, "main"))

    assert _git(bundle.bundle_root, "config", "--get", "remote.origin.url") == server.url


def test_a_token_the_server_rejects_is_an_error_that_says_what_to_check(server) -> None:
    server.serving.token = "a-different-token"

    with pytest.raises(ResolutionError) as refused:
        GitResolver().resolve(_source(server.url, "v1"))

    message = str(refused.value)
    assert "did not accept the credential from FLUID_SCAFFOLD_TOKEN" in message
    assert TOKEN not in message and _basic(TOKEN) not in message
    assert {status for _, _, status in server.requests} == {401}


def test_a_redirect_is_not_followed_and_the_error_says_so(server) -> None:
    server.serving.redirect = True

    with pytest.raises(ResolutionError, match="never sent to a redirect target"):
        GitResolver().resolve(_source(server.url, "v1"))

    assert [status for _, _, status in server.requests] == [301]  # asked once, not followed
    assert all("moved.git" not in path for path, _, _ in server.requests)


def test_a_port_the_operator_did_not_list_is_sent_nothing(server, monkeypatch) -> None:
    monkeypatch.setenv("FLUID_CUSTOM_SCAFFOLD_AUTH_HOSTS", HOST)  # the https default port only

    with pytest.raises(ResolutionError, match="not a host this operator allows"):
        GitResolver().resolve(_source(server.url, "v1"))

    assert server.requests == []
