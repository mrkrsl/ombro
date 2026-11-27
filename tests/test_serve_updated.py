"""A service notices that Ombro was updated under it, and stops so its supervisor starts the new one."""

from __future__ import annotations

import os
import threading

from ombro.serve import updated
from ombro.serve.updated import EXIT_UPDATED, UpdateWatch, supervised


def replace(path, text: str) -> None:
    """As an installer does: write the new file beside it, then move it into place."""
    new = path.with_name(path.name + ".new")
    new.write_text(text)
    os.replace(new, path)


def test_unchanged_program_is_not_an_update(tmp_path):
    prog = tmp_path / "ombro-serve"
    prog.write_text("0.5.0")
    w = UpdateWatch(prog)
    assert not w.check()
    assert not w.check()


def test_replaced_program_is_an_update_once_it_settles(tmp_path):
    prog = tmp_path / "ombro-serve"
    prog.write_text("0.5.0")
    w = UpdateWatch(prog)
    replace(prog, "0.5.1 new")
    assert not w.check()  # seen once: the install may still be writing it
    assert w.check()


def test_a_file_still_being_written_waits(tmp_path):
    prog = tmp_path / "ombro-serve"
    prog.write_text("0.5.0")
    w = UpdateWatch(prog)
    replace(prog, "0.5.1 par")
    assert not w.check()
    replace(prog, "0.5.1 partial, more")
    assert not w.check()
    assert w.check()


def test_missing_program_mid_swap_is_not_an_update(tmp_path):
    prog = tmp_path / "ombro-serve"
    prog.write_text("0.5.0")
    w = UpdateWatch(prog)
    prog.unlink()  # the updater has moved the old app away; the new one isn't there yet
    assert not w.check()
    assert not w.check()


def test_no_program_at_start_never_reports(tmp_path):
    w = UpdateWatch(tmp_path / "missing")
    (tmp_path / "missing").write_text("x")
    assert not w.check()
    assert not w.check()


def test_program_file_unfrozen_is_the_package(monkeypatch):
    import ombro

    monkeypatch.delattr("sys.frozen", raising=False)
    assert updated.program_file() == __import__("pathlib").Path(ombro.__file__)


def test_supervised():
    assert supervised({"XPC_SERVICE_NAME": "local.ombro.serve"})
    assert supervised({"INVOCATION_ID": "abc123"})
    assert not supervised({"XPC_SERVICE_NAME": "0"})
    assert not supervised({})


def test_watch_calls_back_once(tmp_path, monkeypatch):
    prog = tmp_path / "ombro-serve"
    prog.write_text("0.5.0")
    monkeypatch.setattr(updated, "program_file", lambda: prog)
    hit = threading.Event()
    calls = []
    stop = threading.Event()
    t = updated.watch(lambda: (calls.append(1), hit.set()), every=0.01, stop=stop)
    replace(prog, "0.5.1 new")
    assert hit.wait(2)
    t.join(2)
    stop.set()
    assert calls == [1]


class FakeServer:
    should_exit = False


def test_supervised_service_stops_with_the_updated_code(monkeypatch, capsys):
    from ombro import serve

    monkeypatch.setattr(updated, "supervised", lambda: True)
    monkeypatch.setattr(updated, "watch", lambda cb: cb())
    server, flag = FakeServer(), threading.Event()
    serve.watch_for_update(server, flag)
    assert server.should_exit and flag.is_set()
    assert "updated" in capsys.readouterr().out
    assert EXIT_UPDATED != 0


def test_unsupervised_server_only_says_so(monkeypatch, capsys):
    from ombro import serve

    monkeypatch.setattr(updated, "supervised", lambda: False)
    monkeypatch.setattr(updated, "watch", lambda cb: cb())
    server, flag = FakeServer(), threading.Event()
    serve.watch_for_update(server, flag)
    assert not server.should_exit and not flag.is_set()
    assert "start it again" in capsys.readouterr().out
