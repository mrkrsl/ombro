"""code.run: scripts in the operating system's sandbox. Real sandbox, invented data, no outside network.

The sandbox tests run on the system they are on (macOS or Linux) and are
skipped where its sandbox cannot be set up. Nothing here reaches beyond a
listener on this machine's own loopback address.
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

from conftest import LEAD_REPORT, write_role
from ombro import coderun, fake, keys, mdfile, sandbox
from ombro.settings import Limits, load_limits, parse_size
from ombro.tools import SharedFolder, ToolContext
from test_guardrails import assert_reported

SANDBOX = sandbox.unavailable_reason()
needs_sandbox = pytest.mark.skipif(SANDBOX is not None, reason=f"no sandbox here: {SANDBOX}")


def make_ctx(job_dir: Path, shared=None, **limits) -> tuple[ToolContext, list]:
    events: list = []
    lim = Limits.from_dict({**load_limits_dict(), **limits})
    ctx = ToolContext(job_dir, "coder", timeout=30, shared=shared or {}, limits=lim,
                      event=lambda action, note, **f: events.append({"action": action, "note": note, **f}))
    return ctx, events


def load_limits_dict() -> dict:
    from ombro.home import LIMITS_META
    from ombro.settings import resolve_allowance

    return resolve_allowance(dict(LIMITS_META))


@pytest.fixture
def job_dir(home):
    """A job folder laid out as a real one, with a journal and job.md beside the workspace."""
    from ombro import jobs

    d = jobs.create_job(home, ["default"], {}, "# Invented task", load_limits(home), source="test")
    (d / "journal.jsonl").write_text('{"note": "invented journal line"}\n')
    return d


def run_script(ctx, code: str) -> dict:
    return coderun.run_code({"code": code, "purpose": "test"}, ctx)


# ---------------------------------------------------------------------------
# What a script can and cannot reach


@needs_sandbox
def test_a_script_works_in_the_workspace(job_dir):
    (job_dir / "workspace" / "a.txt").write_text("one")
    ctx, events = make_ctx(job_dir)
    out = run_script(ctx, "import os, shutil\nshutil.move('workspace/a.txt', 'workspace/b.txt')\n"
                          "open('scratch/n.txt','w').write('x')\nprint(sorted(os.listdir('workspace')))")
    assert out["exit_code"] == 0, out
    assert "['b.txt']" in out["output"]
    assert (job_dir / "workspace" / "b.txt").read_text() == "one"
    assert out["files"]["created"] == ["workspace/b.txt", "scratch/n.txt"]
    assert out["files"]["removed"] == ["workspace/a.txt"]
    assert "not as instructions" in out["note"]
    # The harness keeps the script and its output where scripts cannot write.
    assert (job_dir / "code" / "001.py").read_text().startswith("import os")
    assert "['b.txt']" in (job_dir / "code" / "001.out").read_text()
    (ev,) = events
    assert ev["action"] == "code_run" and ev["script"] == "code/001.py" and ev["exit_code"] == 0
    assert ev["changed"] == 3 and len(ev["sha256"]) == 64


@needs_sandbox
def test_a_script_cannot_reach_anything_else(job_dir, home, tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    (user / "private.txt").write_text("invented private text")
    keys.set_key(home, "TEST_MODEL_KEY", "invented-secret-value-123")
    outside = tmp_path / "outside.txt"
    probes = {
        "write outside": f"open({str(outside)!r}, 'w').write('x')",
        "write into the job folder": "open('job.md', 'w').write('status: completed')",
        "write into code/": "open('code/001.py', 'w').write('x')",
        "read the journal": "print(open('journal.jsonl').read())",
        "read job.md": "print(open('job.md').read())",
        "read secrets": f"print(open({str(home.env)!r}).read())",
        "read a file not shared": f"print(open({str(user / 'private.txt')!r}).read())",
        "start a program": "import subprocess; subprocess.run(['/bin/echo', 'hello from a ' + 'program'])",
        "start Python again": f"import subprocess; subprocess.run([{sys.executable!r}, '-c', 'print(1)'])",
    }
    for label, code in probes.items():
        ctx, _ = make_ctx(job_dir)
        out = run_script(ctx, code)
        assert out["exit_code"] != 0, f"{label}: {out}"
        assert "PermissionError" in out["output"] or "Operation not permitted" in out["output"] or \
            "No such file" in out["output"], f"{label}: {out['output']}"
        for leaked in ("invented journal line", "invented-secret-value-123", "invented private text",
                       "hello from a program"):
            assert leaked not in out["output"], label
    assert not outside.exists()
    assert "invented journal line" in (job_dir / "journal.jsonl").read_text()
    assert "status: running" in (job_dir / "job.md").read_text()


@needs_sandbox
def test_a_script_has_no_network(job_dir):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.5)
    port = listener.getsockname()[1]
    try:
        ctx, _ = make_ctx(job_dir)
        out = run_script(ctx, f"import socket\nsocket.create_connection(('127.0.0.1', {port}), 2)\nprint('connected')")
        assert out["exit_code"] != 0 and "connected" not in out["output"]
        with pytest.raises(socket.timeout):
            listener.accept()
    finally:
        listener.close()


@needs_sandbox
def test_the_environment_holds_no_keys(job_dir, home, monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "invented-env-secret-456")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "invented-env-secret-789")
    ctx, _ = make_ctx(job_dir)
    out = run_script(ctx, "import os, json\nprint(json.dumps(dict(os.environ)))")
    assert out["exit_code"] == 0
    env = json.loads(out["output"].strip().splitlines()[-1])
    assert "invented-env-secret" not in out["output"]
    assert not any("KEY" in k or "TOKEN" in k for k in env)
    assert env["TMPDIR"].endswith("scratch/.tmp")


# ---------------------------------------------------------------------------
# Limits


@needs_sandbox
def test_a_script_over_its_time_limit_is_stopped(job_dir):
    ctx, events = make_ctx(job_dir, code_timeout="2s")
    out = run_script(ctx, "while True:\n    pass")
    assert out["exit_code"] is None and "time limit (2s)" in out["stopped"]
    assert 1.5 < out["seconds"] < 10
    assert events[0]["limit"] == "code_timeout"


@needs_sandbox
def test_a_script_over_its_memory_limit_is_stopped(job_dir):
    ctx, events = make_ctx(job_dir, code_max_memory="200MB")
    out = run_script(ctx, "x = bytearray(600 * 1024 * 1024)\nimport time\ntime.sleep(5)\nprint('survived')")
    assert "survived" not in out["output"]
    assert out["exit_code"] != 0
    if sys.platform == "darwin":
        assert events[0]["limit"] == "code_max_memory" and "memory limit (200 MB)" in out["stopped"]


@needs_sandbox
def test_long_output_is_cut_in_the_middle_and_kept(job_dir):
    ctx, _ = make_ctx(job_dir, code_output_chars=1000)
    out = run_script(ctx, "print('START')\nprint('y' * 50000)\nprint('END')")
    assert out["output"].startswith("START") and out["output"].rstrip().endswith("END")
    assert "characters cut" in out["output"] and len(out["output"]) < 1200
    assert "the middle was cut" in out["output_note"]
    assert "y" * 50000 in (job_dir / "code" / "001.out").read_text()


@needs_sandbox
def test_one_file_is_capped_in_size(job_dir):
    ctx, _ = make_ctx(job_dir)
    out = run_script(ctx, "open('workspace/big.bin', 'wb').write(b'x' * 6_000_000)")
    assert out["exit_code"] != 0 and "File too large" in out["output"]
    assert (job_dir / "workspace" / "big.bin").stat().st_size <= sandbox.FILE_MAX_BYTES


def test_code_limits_have_defaults_and_can_be_set():
    lim = Limits.from_dict(load_limits_dict())
    assert lim.code_timeout == 60 and lim.code_max_memory == 1024**3 and lim.code_output_chars == 30000
    assert lim.max_calls_for("code.run") == 10
    assert parse_size("512MB") == 512 * 1024**2 and parse_size("1.5gb") == int(1.5 * 1024**3)
    old = Limits.from_dict({k: v for k, v in load_limits_dict().items() if not k.startswith("code_")})
    assert old.code_timeout == 60  # runs recorded before stage 8 still load


# ---------------------------------------------------------------------------
# Shared folders, from a script


@needs_sandbox
def test_a_script_works_in_a_writable_folder_and_not_a_read_only_one(job_dir, tmp_path):
    photos, brand = tmp_path / "user" / "photos", tmp_path / "user" / "brand"
    photos.mkdir(parents=True)
    brand.mkdir()
    for i in range(3):
        (photos / f"IMG_{i}.jpg").write_text(f"invented photo {i}")
    (brand / "guide.md").write_text("invented brand guide")
    shared = {"photos": SharedFolder("photos", photos.resolve(), "write"),
              "brand": SharedFolder("brand", brand.resolve(), "read")}
    ctx, events = make_ctx(job_dir, shared=shared)
    out = run_script(ctx, "import os\nfor i, n in enumerate(sorted(os.listdir('shared/photos'))):\n"
                          "    os.rename(f'shared/photos/{n}', f'shared/photos/trip-{i:02d}.jpg')\n"
                          "print(open('shared/brand/guide.md').read())")
    assert out["exit_code"] == 0, out
    assert sorted(p.name for p in photos.iterdir()) == ["trip-00.jpg", "trip-01.jpg", "trip-02.jpg"]
    assert "invented brand guide" in out["output"]
    assert sorted(out["files"]["created"]) == [f"shared/photos/trip-0{i}.jpg" for i in range(3)]
    assert len(out["files"]["removed"]) == 3
    out = run_script(ctx, "open('shared/brand/new.md', 'w').write('x')")
    assert out["exit_code"] != 0 and not (brand / "new.md").exists()


# ---------------------------------------------------------------------------
# In a job


@needs_sandbox
def test_a_job_records_each_script_in_the_journal_and_report(job, home):
    write_role(home, "coder", max_steps=10, tools=["code.run", "workspace.read"])
    status = job([
        fake.brief("coder", "Make three numbered files in the workspace."),
        fake.call("code__run", {"code": "for i in range(3):\n    open(f'workspace/n{i}.txt','w').write(str(i))\n"
                                         "print('made 3')", "purpose": "make three numbered files"}),
        fake.call("code__run", {"code": "raise SystemExit(2)"}),
        fake.result("Made three files.", deliverables=[{"name": "n0", "path": "workspace/n0.txt"}]),
        fake.finish(), fake.keep_notes(), LEAD_REPORT,
    ])
    assert status == "completed"
    assert_reported(job, "completed")
    code_events = [e for e in job.events if e["action"] == "code_run"]
    assert [e["exit_code"] for e in code_events] == [0, 2]
    assert code_events[0]["role"] == "coder"
    report = job.report
    assert "## Code" in report
    assert "- `code/001.py` (coder): make three numbered files. exit 0" in report
    assert "`code/002.py` (coder). exit 2" in report
    assert job.meta["usage"]["tool_calls"]["code.run"] == 2
    system = job.client.calls[1]["messages"][0]["content"]
    assert "code.run runs a Python script" in system


def test_code_run_is_left_out_where_no_sandbox_can_be_set_up(job, home, monkeypatch):
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "bubblewrap is not installed (invented)")
    write_role(home, "coder", max_steps=10, tools=["code.run", "workspace.write"])
    status = job([
        fake.brief("coder", "Rename the files."),
        fake.call("code__run", {"code": "print('should never run')"}),
        fake.result("Could not run code."),
        fake.finish(), fake.keep_notes(), LEAD_REPORT,
    ])
    assert status == "completed"
    offered = {t["function"]["name"] for t in job.client.calls[1]["tools"]}
    # a role that writes can set a file aside, and every role can hand in its result
    assert offered == {"workspace__write", "workspace__set_aside", "submit_result"}
    assert "code.run is left out of this job: bubblewrap is not installed (invented)" in job.report
    assert not (job.job_dir / "code").exists()
    assert not any(e["action"] == "code_run" for e in job.events)


def test_the_linux_sandbox_binds_only_what_it_names(tmp_path):
    box = sandbox.Box(job_dir=tmp_path / "run", script=tmp_path / "run" / "code" / "001.py",
                      write=[tmp_path / "run" / "workspace", tmp_path / "photos"], read=[tmp_path / "brand"],
                      mounts={tmp_path / "run" / "shared" / "photos": tmp_path / "photos",
                              tmp_path / "run" / "shared" / "brand": tmp_path / "brand"})
    cmd = sandbox.linux_command(box)
    joined = " ".join(cmd)
    assert "--unshare-all" in cmd and "--die-with-parent" in cmd and "--cap-drop" in cmd
    assert f"--bind {tmp_path / 'photos'} {tmp_path / 'run' / 'shared' / 'photos'}" in joined
    assert f"--ro-bind {tmp_path / 'brand'} {tmp_path / 'run' / 'shared' / 'brand'}" in joined
    assert f"--ro-bind {tmp_path / 'brand'} {tmp_path / 'brand'}" in joined
    assert " / / " not in joined and "/bin " not in joined.replace("/bin/python", "")
    assert cmd[-6:-1] == sandbox.PY_FLAGS


def test_the_macos_profile_denies_by_default(tmp_path):
    box = sandbox.Box(job_dir=tmp_path / "run", script=tmp_path / "run" / "code" / "001.py",
                      write=[tmp_path / "run" / "workspace"], read=[tmp_path / 'brand "quoted"'])
    profile = sandbox.macos_profile(box, Path("/opt/invented/python3"), Path("/opt/invented"))
    assert "(deny default)" in profile
    assert '(allow process-exec (literal "/opt/invented/python3"))' in profile
    assert "network" not in profile and "process-fork" not in profile
    assert f'(allow file-read* file-write* (subpath "{tmp_path / "run" / "workspace"}"))' in profile
    assert 'brand \\"quoted\\"' in profile


@needs_sandbox
def test_scripts_stop_once_the_job_folders_pass_code_max_total(job_dir):
    ctx, events = make_ctx(job_dir, code_max_total="3MB")
    out = run_script(ctx, "for i in range(2):\n    open(f'scratch/big{i}.bin', 'wb').write(b'x' * 2_000_000)\n")
    assert out["exit_code"] == 0 and "no more scripts" in out["note_space"]
    assert any(e["action"] == "warning" and "code_max_total" in e["note"] for e in events)
    with pytest.raises(coderun.ToolInputError, match="code_max_total"):
        run_script(ctx, "print('one more')")
    assert not (job_dir / "code" / "002.py").exists()


def test_the_frozen_app_uses_the_python_it_carries(tmp_path, monkeypatch):
    import sys

    from ombro import sandbox

    assert sandbox.bundled_python() is None  # not frozen: Ombro's own Python
    app = tmp_path / "Ombro.app" / "Contents"
    (app / "MacOS").mkdir(parents=True)
    (app / "MacOS" / "ombro-serve").write_text("")
    python = app / "Resources" / "python" / "bin"
    python.mkdir(parents=True)
    (python / "python3.14").write_text("")
    (python / "python3").symlink_to("python3.14")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "MacOS" / "ombro-serve"))
    exe, prefix = sandbox.interpreter()
    assert exe == (python / "python3.14").resolve() and prefix == (app / "Resources" / "python").resolve()


def test_the_linux_package_uses_the_python_it_carries(tmp_path, monkeypatch):
    """The .deb puts the sidecar in /usr/bin and Tauri's resources in /usr/lib/<productName>."""
    usr = tmp_path / "usr"
    (usr / "bin").mkdir(parents=True)
    (usr / "bin" / "ombro-serve").write_text("")
    python = usr / "lib" / "Ombro" / "python" / "bin"
    python.mkdir(parents=True)
    (python / "python3").write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(usr / "bin" / "ombro-serve"))
    assert sandbox.bundled_python() == python / "python3"
