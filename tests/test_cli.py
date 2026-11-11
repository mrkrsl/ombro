"""The command line, end to end, with a fake model configured in models.md. No network."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time

import pytest

from conftest import write_role
from ombro import fake, keys, mdfile, runs
from ombro.client import ModelError, ModelReply
from ombro.home import Home
from ombro.init_cmd import connected_model, run_init

REPORT = fake.say("## What was done\nCLI-REPORT-MARKER.\n\n## What was not done and why\nNothing.\n\n## Decisions for you\nNone.")


def use_fake_model(home: Home, script: list) -> None:
    (home.root / "script.json").write_text(json.dumps(script))
    mdfile.write(
        home.models,
        {"models": {"default": {"api": "fake", "script": "script.json", "price_per_mtok": {"input": 1, "output": 1}},
                    "cheap": "default"}},
    )


def ombro(home: Home, *args, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ombro", *args],
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "OMBRO_HOME": str(home.root)},
    )


@pytest.fixture
def spec_file(tmp_path):
    p = tmp_path / "spec.md"
    p.write_text("---\nlimits:\n  max_rounds: 3\n---\n\n# Invented task\n\nName two fictional rivers in Lowtown.\n")
    return p


SCRIPT = [
    fake.brief("researcher", "Name two fictional rivers."),
    fake.result("Named the Wend and the Pell."),
    fake.finish("Done."),
    REPORT,
]


def test_run_attached_then_inspect(home, spec_file):
    use_fake_model(home, SCRIPT)
    r = ombro(home, "run", "--spec", str(spec_file))
    assert r.returncode == 0, r.stderr
    assert "CLI-REPORT-MARKER" in r.stdout and "briefed" in r.stdout
    run_dir = runs.latest_run(home, "default")
    meta = runs.read_run(run_dir)[0]
    assert meta["status"] == "completed"
    assert meta["limits"]["max_rounds"] == 3  # the spec's override was fixed into the run
    assert (run_dir / "spec.md").read_text().count("fictional rivers") == 1

    listing = ombro(home, "runs")
    assert run_dir.name in listing.stdout and "completed" in listing.stdout
    status = ombro(home, "status", run_dir.name[:15])
    assert "Rounds" in status.stdout and "completed" in status.stdout
    assert "CLI-REPORT-MARKER" in ombro(home, "report").stdout
    j = ombro(home, "journal", run_dir.name)
    assert "briefed" in j.stdout and "returned" in j.stdout and "model_call" not in j.stdout
    assert "model_call" in ombro(home, "journal", "--all").stdout


def test_run_with_skip_interview_is_recorded(home):
    use_fake_model(home, SCRIPT)
    r = ombro(home, "run", "--skip-interview", "Name two fictional rivers.")
    assert r.returncode == 0, r.stderr
    meta = runs.read_run(runs.latest_run(home, "default"))[0]
    assert "skip-interview" in meta["source"]


def test_run_without_a_spec_explains(home):
    use_fake_model(home, SCRIPT)
    r = ombro(home, "run")
    assert r.returncode == 2 and "--spec" in r.stderr


def test_commands_without_a_home_say_run_init(tmp_path):
    r = ombro(Home(tmp_path / "nothing"), "runs")
    assert r.returncode == 2 and "ombro init" in r.stderr


def wait_for(run_dir, predicate, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        meta = runs.read_run(run_dir)[0]
        if predicate(meta):
            return meta
        time.sleep(0.1)
    raise AssertionError(f"timed out; run.md says {runs.read_run(run_dir)[0]}")


def test_detached_run_completes(home, spec_file):
    use_fake_model(home, SCRIPT)
    r = ombro(home, "run", "--spec", str(spec_file), "--detach")
    assert r.returncode == 0, r.stderr
    run_dir = runs.latest_run(home, "default")
    assert run_dir.name in r.stdout
    meta = wait_for(run_dir, lambda m: m["status"] != "running")
    assert meta["status"] == "completed"
    assert "CLI-REPORT-MARKER" in (run_dir / "report.md").read_text()


def test_stop_a_detached_run(home, spec_file):
    use_fake_model(home, [fake.brief("researcher", "Slow work."), fake.result("Partial.", sleep=2), REPORT])
    ombro(home, "run", "--spec", str(spec_file), "--detach")
    run_dir = runs.latest_run(home, "default")
    wait_for(run_dir, lambda m: m.get("pid") and m.get("spend", {}).get("model_calls", 0) >= 1)
    r = ombro(home, "stop", run_dir.name)
    assert r.returncode == 0, r.stdout + r.stderr
    meta = wait_for(run_dir, lambda m: m["status"] != "running")
    assert meta["status"] == "stopped"
    report = (run_dir / "report.md").read_text()
    assert "ombro stop" in report and "Partial." in report


def test_dead_run_is_reaped_by_the_next_command(home):
    use_fake_model(home, SCRIPT)
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()  # a pid that is certainly gone
    from ombro.settings import load_limits

    run_dir = runs.create_run(home, "default", {}, "Invented brief.", load_limits(home), source="test")
    runs.update_run(run_dir, pid=proc.pid, started_at="2026-11-11T10:00:00Z")
    with (run_dir / "journal.jsonl").open("a") as f:
        f.write(json.dumps({"ts": "2026-11-11T10:00:01Z", "role": "lead", "action": "briefed",
                            "note": "researcher: invented", "target": "researcher", "task": "Invented task."}) + "\n")
    r = ombro(home, "runs")
    assert "marked failed" in r.stderr
    meta = runs.read_run(run_dir)[0]
    assert meta["status"] == "failed"
    report = (run_dir / "report.md").read_text()
    assert "process ended" in report and "researcher was working on this" in report


# -- init, connection step ----------------------------------------------------------


class ScriptedPrompts:
    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, prompt):
        self.asked.append(prompt)
        return self.answers.pop(0)


def test_init_connects_after_a_failed_test_call(tmp_path):
    home = Home(tmp_path / "fresh")
    results = iter([ModelError("HTTP 401: bad key"), ModelReply(content="OK")])

    class Client:
        def complete(self, *a, **k):
            r = next(results)
            if isinstance(r, Exception):
                raise r
            return r

    said = []
    ask = ScriptedPrompts(["1", "", "1", "deepseek-chat"])
    secret = ScriptedPrompts(["sk-test-INVENTED-wrong", "sk-test-INVENTED-right", ""])
    assert run_init(home, ask=ask, ask_secret=secret, say=said.append, client_factory=lambda s, h: Client())
    out = "\n".join(said)
    assert "test call failed" in out and "Connected" in out
    assert keys.get_key(home, "DEEPSEEK_API_KEY") == "sk-test-INVENTED-right"
    assert "INVENTED" not in out  # keys are never echoed
    assert stat.S_IMODE(os.stat(home.env).st_mode) == 0o600
    spec = connected_model(home)
    assert spec.endpoint == "https://api.deepseek.com" and spec.model == "deepseek-chat" and spec.priced
    assert (home.crew_dir("default") / "roles" / "lead" / "soul.md").is_file()
    assert keys.get_key(home, "FIRECRAWL_API_KEY") is None  # search key skipped


def test_init_other_endpoint_without_key(tmp_path):
    home = Home(tmp_path / "fresh")

    class Client:
        def complete(self, *a, **k):
            return ModelReply(content="OK")

    said = []
    ask = ScriptedPrompts(["2", "http://localhost:11434/v1", "invented-local-model", ""])
    secret = ScriptedPrompts([""])
    run_init(home, ask=ask, ask_secret=secret, say=said.append, client_factory=lambda s, h: Client())
    spec = connected_model(home)
    assert spec.endpoint == "http://localhost:11434/v1" and spec.key is None and not spec.priced
    assert any("no price" in s for s in said)
