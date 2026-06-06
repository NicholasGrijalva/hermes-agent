"""MANNA plugin — drive the local automation backend from the CLI.

Adds:
- ``/manna`` slash command: new | list | run.
- ``on_session_start`` hook: best-effort ensure the local MANNA backend is up
  (probe /health; if down and MANNA_BACKEND_CMD is set, spawn it).

Backend URL: ``MANNA_BACKEND_URL`` (default http://127.0.0.1:8010). The backend
runs fully local (RUN_MODE=local, SQLite) — no Temporal/Postgres.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.error
import urllib.request

BACKEND = os.environ.get("MANNA_BACKEND_URL", "http://127.0.0.1:8010").rstrip("/")


# ── HTTP to the local backend (stdlib only) ──

def _http(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BACKEND}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"_error": f"{e.code}: {e.read().decode()[:200]}"}
    except Exception as e:  # noqa: BLE001 — surface unreachable backend as text
        return {"_error": f"backend unreachable at {BACKEND} ({e})"}


def _backend_up() -> bool:
    return "_error" not in _http("GET", "/health")


# ── plain-English -> {trigger, steps} (mirrors the frontend scaffolder) ──

_CONN_WORDS = {
    "calendar": ["calendar", "schedule", "event", "booking", "date", "remind"],
    "gmail": ["email", "gmail", "inbox", "mail"],
    "square": ["charge", "invoice", "payment", "pay", "order", "receipt"],
}


def _guess_conn(phrase: str):
    p = phrase.lower()
    for cid, words in _CONN_WORDS.items():
        if any(w in p for w in words):
            return cid
    return None


def _scaffold(sentence: str) -> dict:
    clean = " ".join(sentence.split())
    trigger, rest = (clean.split(",", 1) + [""])[:2] if "," in clean else (clean, "")
    steps = []
    for part in [s.strip() for s in rest.replace(" then ", ",").split(",") if s.strip()]:
        steps.append({"label": part[:1].upper() + part[1:], "connectionId": _guess_conn(part)})
    return {"trigger": trigger.strip(), "steps": steps}


# ── operations ──

def _create(sentence: str) -> str:
    r = _http("POST", "/automation/processes", _scaffold(sentence))
    if "_error" in r:
        return f"Could not create: {r['_error']}"
    return f"Created {r['id']} — '{r['trigger']}' · webhook {r['hook_url']}"


def _list() -> str:
    r = _http("GET", "/automation/processes")
    if "_error" in r:
        return f"Could not list: {r['_error']}"
    procs = r.get("processes", [])
    if not procs:
        return "No processes yet. Try: /manna new when someone orders a cake, email them"
    return "\n".join(f"- {p['id']} [{p['status']}] {p['trigger']}" for p in procs)


def _run(token: str, payload: dict | None) -> str:
    r = _http("POST", f"/automation/hooks/{token}", payload or {})
    if "_error" in r:
        return f"Could not run: {r['_error']}"
    return f"Fired — execution {r.get('execution_id')}"


# ── slash command ──

_HELP = """/manna — local automations (backend: %s)
  /manna new <plain English>   create a process ("when X, do Y")
  /manna list                  list your processes
  /manna run <token> [json]    fire a process's webhook
""" % BACKEND


def _handle_slash(raw: str):
    argv = (raw or "").strip().split(maxsplit=1)
    if not argv or argv[0] in {"help", "-h", "--help"}:
        return _HELP
    sub, arg = argv[0], (argv[1] if len(argv) > 1 else "")
    if sub == "new":
        return _create(arg) if arg else "Usage: /manna new <plain English>"
    if sub == "list":
        return _list()
    if sub == "run":
        parts = arg.split(maxsplit=1)
        if not parts:
            return "Usage: /manna run <token> [json]"
        payload = None
        if len(parts) > 1:
            try:
                payload = json.loads(parts[1])
            except Exception:
                return "Payload must be valid JSON."
        return _run(parts[0], payload)
    return f"Unknown subcommand: {sub}\n\n{_HELP}"


# ── sidecar (best-effort) ──

def _ensure_backend(**_):
    if _backend_up():
        return None
    cmd = os.environ.get("MANNA_BACKEND_CMD")
    if cmd:
        try:
            subprocess.Popen(shlex.split(cmd), start_new_session=True)
        except Exception:  # noqa: BLE001
            pass
    return None


def register(ctx) -> None:
    ctx.register_hook("on_session_start", _ensure_backend)
    ctx.register_command(
        "manna",
        handler=_handle_slash,
        description="Create and run local MANNA automations.",
    )
