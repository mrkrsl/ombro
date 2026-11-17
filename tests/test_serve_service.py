"""`ombro serve` as a service: pairing devices, the device-token guard, `POST /pair`, the hosts it
answers to, the in-process tick, `ombro pair`, and `ombro serve install`."""

from __future__ import annotations

import json
import os
import plistlib
import socket
import stat
import subprocess
import sys

import pytest
from starlette.testclient import TestClient

from ombro import __version__, pairing
from ombro.home import Home
from ombro.pairing import Devices, PairingError
from ombro.serve import dispatch, build_parser, service_hosts, service_lines
from ombro.serve.app import PROTOCOL, build_app
from ombro.service import ServiceInstaller

APP = "tauri://localhost"


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def home(tmp_path):
    return Home(tmp_path / "home")


# -- pairing -----------------------------------------------------------------------


def test_first_code_stands_until_a_device_pairs(home):
    clock = Clock()
    d = Devices(home, clock=clock)
    code = d.first_code()
    assert code and len(code) == 4 and "-" not in code
    clock.t += 365 * 24 * 3600  # it doesn't run out
    assert Devices(home, clock=clock).first_code() == code
    device, token = d.pair(f" {code[:2].lower()} {code[2:]} ", "Mark's  Mac")  # as typed
    assert device.name == "Mark's Mac"
    assert d.valid(token) and not d.valid("not-a-token") and not d.valid("")
    assert d.first_code() is None  # once paired, codes come only from `ombro pair`
    with pytest.raises(PairingError):
        d.pair(code, "again")  # it worked once


def test_later_codes_last_a_day_and_replace_the_last(home):
    clock = Clock()
    d = Devices(home, clock=clock)
    old, _ = d.later_code()
    new, expires = d.later_code()
    assert expires == clock.t + pairing.LATER_CODE_SECONDS
    with pytest.raises(PairingError):
        d.pair(old, "x")
    clock.t = expires + 1
    with pytest.raises(PairingError, match="run out"):
        d.pair(new, "x")


def test_wrong_codes_lock_pairing_for_a_minute(home):
    clock = Clock()
    d = Devices(home, clock=clock)
    code = d.first_code()
    for _ in range(pairing.MAX_WRONG):
        with pytest.raises(PairingError, match="isn't right"):
            d.pair("2222" if code != "2222" else "3333", "x")
    with pytest.raises(PairingError, match="Too many"):
        d.pair(code, "x")  # even the right one, while locked
    clock.t += pairing.LOCKOUT_SECONDS
    assert d.pair(code, "x")[1]


def test_a_code_is_thrown_away_after_ten_wrong_tries(home):
    clock = Clock()
    d = Devices(home, clock=clock)
    code = d.first_code()
    wrong = "2222" if code != "2222" else "3333"
    for i in range(pairing.MAX_WRONG_PER_CODE):
        clock.t += pairing.LOCKOUT_SECONDS  # slowly, so the minute lockout never starts
        with pytest.raises(PairingError):
            d.pair(wrong, "x")
    clock.t += pairing.LOCKOUT_SECONDS
    with pytest.raises(PairingError, match="isn't right"):
        d.pair(code, "x")  # the right code, too late
    assert Devices(home, clock=clock).first_code() != code  # a new one at the next start


def test_tokens_are_kept_only_as_hashes_owner_only(home):
    d = Devices(home)
    _, token = d.add("laptop")
    text = d.path.read_text()
    assert token not in text
    assert stat.S_IMODE(d.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(home.secrets.stat().st_mode) == 0o700


def test_forgetting_takes_effect_in_a_running_serve(home):
    running = Devices(home)
    _, a = Devices(home).add("one")
    _, b = Devices(home).add("two")
    assert running.valid(a) and running.valid(b)
    assert [x.name for x in Devices(home).forget("one")] == ["one"]  # another process: `ombro pair`
    assert not running.valid(a) and running.valid(b)
    Devices(home).forget_all()
    assert not running.valid(b)
    assert running.first_code()  # a fresh first code after forgetting everyone


# -- the service's guard and POST /pair ---------------------------------------------


def service_client(home, hosts=None, **kw) -> tuple[TestClient, Devices]:
    devices = Devices(home)
    app = build_app(None, home=home, devices=devices, hosts=hosts or ["127.0.0.1"], **kw)
    return TestClient(app, base_url="http://127.0.0.1"), devices


def test_pairing_over_http_gives_a_token_that_works(home):
    c, devices = service_client(home)
    assert c.get("/hello").status_code == 403
    code = devices.first_code()
    r = c.post("/pair", json={"code": code, "name": "Mac"}, headers={"Origin": APP})
    assert r.status_code == 201
    body = r.json()
    assert body["device"]["name"] == "Mac" and body["protocol"] == PROTOCOL
    auth = {"Authorization": f"Bearer {body['token']}", "Origin": APP}
    assert c.get("/hello", headers=auth).json() == {"version": __version__, "protocol": PROTOCOL}
    with c.websocket_connect("ws://127.0.0.1/events", subprotocols=["ombro", body["token"]],
                             headers={"Origin": APP}) as ws:
        assert ws.receive_json()["type"] == "event"
    # The code worked once.
    assert c.post("/pair", json={"code": code, "name": "Mac"}).status_code == 403


def test_pair_refuses_bad_requests_other_origins_and_too_many_guesses(home):
    c, devices = service_client(home)
    devices.first_code()
    assert c.post("/pair", json={}).status_code == 400
    assert c.post("/pair", json={"code": "x"}, headers={"Origin": "https://example.com"}).status_code == 403
    statuses = [c.post("/pair", json={"code": "WRONG"}).status_code for _ in range(pairing.MAX_WRONG)]
    assert statuses[:-1] == [403] * (pairing.MAX_WRONG - 1) and statuses[-1] == 429


def test_the_app_mode_has_no_pairing(home):
    c = TestClient(build_app("t0ken", home=home), base_url="http://127.0.0.1")
    assert c.post("/pair", json={"code": "x"}).status_code == 403
    assert c.get("/hello", headers={"Authorization": "Bearer t0ken"}).json()["protocol"] == PROTOCOL


def test_a_service_answers_only_its_own_names(home):
    hosts = service_hosts("100.101.102.103", ["Ombro-Box.tail1234.ts.net"])
    assert hosts == ["127.0.0.1", "100.101.102.103", "ombro-box.tail1234.ts.net"]
    assert service_hosts("0.0.0.0", []) == ["127.0.0.1"]
    c, devices = service_client(home, hosts=hosts)
    _, token = devices.add("x")
    auth = {"Authorization": f"Bearer {token}"}
    assert c.get("http://100.101.102.103/hello", headers=auth).status_code == 200
    assert c.get("http://ombro-box.tail1234.ts.net/hello", headers=auth).status_code == 200
    assert c.get("http://evil.example/hello", headers=auth).status_code == 400


def test_a_ticking_serve_needs_no_scheduler_service(home, monkeypatch):
    from ombro import schedules
    from ombro.home import ensure_home

    ensure_home(home)
    c, devices = service_client(home, tick=True)
    _, token = devices.add("x")
    r = c.get("/scheduler", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["ok"] is True
    ticks = []
    monkeypatch.setattr(schedules, "tick", lambda h, **kw: ticks.append(h.root))
    c.app.state.serve.tick_once()
    assert ticks == [home.root]


def test_service_options_are_not_for_the_app():
    with pytest.raises(SystemExit):
        dispatch(build_parser().parse_args(["--handshake", "--host", "0.0.0.0"]))


def test_start_lines_show_the_first_code_and_warn_on_public_addresses():
    text = "\n".join(service_lines("127.0.0.1", 8765, "34FA"))
    assert "34FA" in text and "Warning" not in text
    assert "Warning" not in "\n".join(service_lines("100.64.1.2", 8765, None))
    assert "Warning" in "\n".join(service_lines("8.8.8.8", 8765, None))


# -- a real service process ---------------------------------------------------------

_connect = socket.socket.connect
_create_connection = socket.create_connection
_getaddrinfo = socket.getaddrinfo


@pytest.fixture
def loopback(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket, "create_connection", _create_connection)

    def resolve(host, *args, **kwargs):
        if host != "127.0.0.1":
            raise AssertionError("a test tried to use the network")
        return _getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def test_the_service_prints_its_code_keeps_running_without_stdin_and_pairs(loopback, tmp_path):
    import httpx

    env = {**os.environ, "OMBRO_HOME": str(tmp_path / "home")}
    proc = subprocess.Popen([sys.executable, "-m", "ombro.serve", "--port", "0"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        first = proc.stdout.readline()
        port = int(first.rsplit(":", 1)[1].rstrip(".\n"))
        code = None
        while code is None:
            line = proc.stdout.readline()
            if "pairing code:" in line:
                code = line.rsplit(":", 1)[1].strip()
        base = f"http://127.0.0.1:{port}"
        r = httpx.post(base + "/pair", json={"code": code, "name": "test"}, timeout=10)
        assert r.status_code == 201
        token = r.json()["token"]
        assert proc.poll() is None  # stdin was closed from the start; a service stays up
        assert httpx.get(base + "/hello", headers={"Authorization": f"Bearer {token}"}, timeout=10).status_code == 200
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# -- ombro pair ---------------------------------------------------------------------


def run_cli(args, home_root, input_text=None):
    env = {**os.environ, "OMBRO_HOME": str(home_root)}
    return subprocess.run([sys.executable, "-m", "ombro", *args], capture_output=True, text=True, env=env,
                          input=input_text, timeout=60)


def test_ombro_pair_commands(tmp_path):
    root = tmp_path / "home"
    r = run_cli(["pair"], root)
    assert r.returncode == 0 and "Pairing code:" in r.stdout
    code = r.stdout.split("Pairing code:")[1].split()[0]
    device, _ = Devices(Home(root)).pair(code, "Laptop")
    over_ssh = json.loads(run_cli(["pair", "--device", "Desk Mac"], root).stdout)
    assert Devices(Home(root)).valid(over_ssh["token"])
    listed = run_cli(["pair", "--list"], root).stdout
    assert "Laptop" in listed and "Desk Mac" in listed and over_ssh["token"] not in listed
    assert run_cli(["pair", "--forget", "Laptop"], root).returncode == 0
    assert run_cli(["pair", "--forget", "Nobody"], root).returncode != 0
    assert run_cli(["pair", "--forget-all"], root, input_text="n\n").returncode == 1
    assert Devices(Home(root)).valid(over_ssh["token"])


# -- ombro serve install -------------------------------------------------------------


class FakeRun:
    def __init__(self, fail: tuple[str, ...] = ()):
        self.commands: list[list[str]] = []
        self.fail = fail

    def __call__(self, cmd, **kwargs):
        self.commands.append(list(cmd))
        code = 1 if cmd[0] in self.fail else 0
        return subprocess.CompletedProcess(cmd, code, "", "not allowed" if code else "")


def installer(home, tmp_path, platform="linux", **kw):
    run = kw.pop("run", FakeRun())
    return ServiceInstaller(home, platform=platform, run=run, python="/opt/ombro/bin/python", uid=501,
                            units_dir=tmp_path / "units", agents_dir=tmp_path / "agents", user="mark",
                            has_systemd=kw.pop("has_systemd", True), sleep=lambda s: None, **kw), run


def test_systemd_unit_runs_serve_with_tick_and_lingers(home, tmp_path):
    inst, run = installer(home, tmp_path)
    plan = inst.plan(8765, "100.64.0.5", ["box.ts.net"])
    assert plan.kind == "systemd" and plan.path == tmp_path / "units" / "ombro.service"
    assert ('ExecStart="/opt/ombro/bin/python" "-m" "ombro" "serve" "--tick" "--port" "8765" '
            '"--host" "100.64.0.5" "--allow-host" "box.ts.net"') in plan.text
    assert f'Environment="OMBRO_HOME={home.root}"' in plan.text
    assert "KillMode=process" in plan.text and "WantedBy=default.target" in plan.text
    assert inst.install(plan) == []
    assert plan.path.read_text() == plan.text
    assert run.commands == [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "ombro.service"],
                            ["systemctl", "--user", "restart", "ombro.service"], ["loginctl", "enable-linger", "mark"]]
    assert inst.running() is True
    assert inst.remove() and not plan.path.exists()
    assert ["systemctl", "--user", "disable", "--now", "ombro.service"] in run.commands


def test_lingering_refused_is_a_warning_not_a_failure(home, tmp_path):
    inst, _ = installer(home, tmp_path, run=FakeRun(fail=("loginctl",)))
    warnings = inst.install(inst.plan(8765, "127.0.0.1", []))
    assert len(warnings) == 1 and "sudo loginctl enable-linger mark" in warnings[0]


def test_a_failing_systemctl_fails_the_install(home, tmp_path):
    inst, _ = installer(home, tmp_path, run=FakeRun(fail=("systemctl",)))
    with pytest.raises(OSError, match="daemon-reload failed"):
        inst.install(inst.plan(8765, "127.0.0.1", []))


def test_no_systemd_says_what_to_do(home, tmp_path):
    inst, _ = installer(home, tmp_path, has_systemd=False)
    with pytest.raises(OSError, match="Docker image"):
        inst.plan(8765, "127.0.0.1", [])


def test_launch_agent_keeps_serve_alive(home, tmp_path):
    inst, run = installer(home, tmp_path, platform="darwin")
    plan = inst.plan(8765, "127.0.0.1", [])
    data = plistlib.loads(plan.text.encode())
    assert data["KeepAlive"] is True and data["RunAtLoad"] is True and data["Label"] == "local.ombro.serve"
    assert data["ProgramArguments"][-5:] == ["--tick", "--port", "8765", "--host", "127.0.0.1"]
    assert data["StandardOutPath"] == str(home.scheduler_dir / "serve.log")
    inst.install(plan)
    assert run.commands == [["launchctl", "bootstrap", "gui/501", str(plan.path)]]
    again = inst.plan(8765, "127.0.0.1", [])
    assert again.exists
    inst.install(again)
    changes = [c for c in run.commands if c[1] != "print"]
    assert changes[-2:] == [["launchctl", "bootout", "gui/501/local.ombro.serve"],
                            ["launchctl", "bootstrap", "gui/501", str(plan.path)]]


# -- ombro sandbox ------------------------------------------------------------------------------


def test_ombro_sandbox_prints_the_apparmor_profile(tmp_path):
    r = run_cli(["sandbox", "--apparmor"], tmp_path / "home")
    assert r.returncode == 0
    assert "profile ombro-bwrap /usr/bin/bwrap" in r.stdout and "userns," in r.stdout


def test_a_restricted_ubuntu_says_how_to_fix_it(monkeypatch):
    from ombro import sandbox

    monkeypatch.setattr(sandbox, "kind", lambda: "linux")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(sandbox, "userns_restricted", lambda: True)
    monkeypatch.setattr(sandbox, "run", lambda *a, **k: sandbox.Outcome(1, 0.1, b"bwrap: loopback: Failed RTM_NEWADDR", 36))
    monkeypatch.setattr(sandbox, "_probe", {})
    reason = sandbox.unavailable_reason()
    assert "Ubuntu 24.04" in reason and "apparmor_parser -r /etc/apparmor.d/ombro-bwrap" in reason


def test_serve_install_offers_the_ubuntu_sandbox_step(monkeypatch):
    from ombro import sandbox, service

    state = {"loaded": False}
    monkeypatch.setattr(sandbox, "kind", lambda: "linux")
    monkeypatch.setattr(sandbox, "userns_restricted", lambda: True)
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: None if state["loaded"] else "limited")
    said, ran = [], []

    def fake_run(cmd, **kw):
        ran.append((cmd, kw.get("input")))
        if cmd[:2] == ["sudo", "apparmor_parser"]:
            state["loaded"] = True
        return subprocess.CompletedProcess(cmd, 0)

    # --yes: only says how.
    assert service.sandbox_step(ask=None, run=fake_run, say=said.append) is False and not ran
    assert any("apparmor_parser -r /etc/apparmor.d/ombro-bwrap" in s for s in said)
    # Declined: nothing runs.
    assert service.sandbox_step(ask=lambda q: False, run=fake_run, say=said.append) is False and not ran
    # Accepted: the profile is written and loaded with sudo, then checked.
    assert service.sandbox_step(ask=lambda q: True, run=fake_run, say=said.append) is True
    assert ran[0][0] == ["sudo", "tee", "/etc/apparmor.d/ombro-bwrap"] and "userns," in ran[0][1]
    assert ran[1][0] == ["sudo", "apparmor_parser", "-r", "/etc/apparmor.d/ombro-bwrap"]
    assert said[-1].startswith("Done")


def test_serve_install_says_nothing_about_the_sandbox_where_it_works(monkeypatch):
    from ombro import sandbox, service

    monkeypatch.setattr(sandbox, "kind", lambda: "linux")
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: None)
    said = []
    assert service.sandbox_step(ask=lambda q: True, say=said.append) is True and said == []
    monkeypatch.setattr(sandbox, "kind", lambda: "macos")
    assert service.sandbox_step(ask=lambda q: True, say=said.append) is None


def test_a_service_on_another_address_also_listens_on_127_0_0_1(monkeypatch):
    import ombro.serve as serve_mod

    bound = []

    class Sock:
        def __init__(self, host, port):
            self.host, self.port = host, port or 8765

        def getsockname(self):
            return (self.host, self.port)

    monkeypatch.setattr(serve_mod, "bind", lambda port, host="127.0.0.1": bound.append((host, port)) or Sock(host, port))
    socks = serve_mod.listen(0, "100.101.102.103")  # a Tailscale address
    assert bound == [("100.101.102.103", 0), ("127.0.0.1", 8765)] and len(socks) == 2
    bound.clear()
    for host in ("127.0.0.1", "0.0.0.0"):
        assert len(serve_mod.listen(8765, host)) == 1
    assert bound == [("127.0.0.1", 8765), ("0.0.0.0", 8765)]
