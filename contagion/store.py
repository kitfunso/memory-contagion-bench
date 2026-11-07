"""hippo-memory as the shared store: one fresh store and `hippo serve` process per run, one API key per agent."""
from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HIPPO_JS = ROOT / "node_modules" / "hippo-memory" / "bin" / "hippo.js"
CACHE = ROOT / "results" / ".cache"
TEMPLATE = CACHE / "template"
N_KEYS = 12  # agent-0..agent-11; the steward uses the admin key


def _env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    # Isolated global store, no hooks, no daily job, no rate limits (one local caller), no outbound model calls.
    env.update(HIPPO_HOME=str(home), HIPPO_SKIP_AUTO_INTEGRATIONS="1", HIPPO_CALLER_RPS="0", HIPPO_V1_RPS="0")
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("TYPESAFE_API_KEY", None)
    return env


def _hippo(args: list[str], cwd: Path, home: Path) -> str:
    out = subprocess.run(["node", str(HIPPO_JS), *args], cwd=cwd, env=_env(home),
                         capture_output=True, text=True, check=True)
    return out.stdout


def build_template() -> dict:
    """Create the empty store and its keys once; every run copies it."""
    keys_file = TEMPLATE / "keys.json"
    if keys_file.exists():
        return json.loads(keys_file.read_text())
    if TEMPLATE.exists():
        shutil.rmtree(TEMPLATE)
    (TEMPLATE / "store").mkdir(parents=True)
    (TEMPLATE / "home").mkdir()
    store, home = TEMPLATE / "store", TEMPLATE / "home"
    _hippo(["init", "--no-hooks", "--no-schedule", "--no-learn"], store, home)
    keys = {}
    for i in range(N_KEYS):
        out = _hippo(["auth", "create", "--label", f"agent-{i}", "--no-expiry", "--json"], store, home)
        keys[f"agent-{i}"] = json.loads(out)["plaintext"]
    out = _hippo(["auth", "create", "--label", "steward", "--role", "admin", "--no-expiry", "--json"], store, home)
    keys["steward"] = json.loads(out)["plaintext"]
    keys_file.write_text(json.dumps(keys))
    return keys


class HippoError(RuntimeError):
    pass


@dataclass
class HippoServer:
    """A running `hippo serve` on a private copy of the template store."""
    port: int
    run_dir: Path
    keys: dict
    proc: subprocess.Popen | None = None
    conn: http.client.HTTPConnection | None = None
    reads: int = 0
    writes: int = 0

    def __enter__(self) -> "HippoServer":
        if self.run_dir.exists():
            shutil.rmtree(self.run_dir, ignore_errors=True)
        shutil.copytree(TEMPLATE, self.run_dir, ignore=shutil.ignore_patterns("keys.json"))
        store = self.run_dir / "store"
        self.proc = subprocess.Popen(["node", str(HIPPO_JS), "serve", "--port", str(self.port)],
                                     cwd=store, env=_env(self.run_dir / "home"),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                self._call("agent-0", "GET", "/v1/memories?q=warmup&limit=1")
                return self
            except (OSError, HippoError):
                self.conn = None
                time.sleep(0.25)
        raise HippoError(f"hippo serve did not start on port {self.port}")

    def __exit__(self, *exc) -> None:
        if self.conn is not None:
            self.conn.close()
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait(timeout=30)
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def _call(self, who: str, method: str, path: str, body: dict | None = None) -> dict:
        # One keep-alive connection per server: a new socket per call exhausts Windows ephemeral ports.
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self.keys[who]}", "content-type": "application/json"}
        for attempt in (0, 1):
            if self.conn is None:
                self.conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
            try:
                self.conn.request(method, path, body=data, headers=headers)
                resp = self.conn.getresponse()
                raw = resp.read()
                break
            except (http.client.HTTPException, ConnectionError):
                self.conn.close()
                self.conn = None
                if attempt:
                    raise
        if resp.status >= 400:
            raise HippoError(f"{method} {path} as {who}: HTTP {resp.status} {raw.decode()[:200]}")
        return json.loads(raw)

    def remember(self, who: str, content: str, tags: list[str]) -> str:
        self.writes += 1
        return self._call(who, "POST", "/v1/memories", {"content": content, "tags": tags})["id"]

    def recall(self, who: str, query: str, limit: int = 5) -> list[str]:
        self.reads += 1
        q = urllib.parse.quote(query)
        return [r["id"] for r in self._call(who, "GET", f"/v1/memories?q={q}&limit={limit}")["results"]]

    def outcome(self, who: str, mem_id: str, good: bool) -> None:
        self._call(who, "POST", "/v1/outcome", {"good": good, "ids": [mem_id]})

    def supersede(self, mem_id: str, content: str) -> str:
        r = self._call("steward", "POST", f"/v1/memories/{mem_id}/supersede", {"content": content})
        return r.get("id") or r.get("newId") or r["new"]["id"]
