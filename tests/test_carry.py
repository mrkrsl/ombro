"""What a job may carry to other sites once it has read private material (ombro.carry)."""

from __future__ import annotations

import base64

import pytest

from ombro.carry import BUDGET, Carry, is_private, looks_encoded, site_of

NOTES = ("Client brief for Halloway Invented Ltd: the agreed fee is forty thousand pounds, paid in two parts, "
         "and the reference for the account is HX4471920033. Do not share outside the team.")


@pytest.fixture
def holding() -> Carry:
    c = Carry()
    c.saw("workspace.read shared/clients/halloway.md", {"path": "shared/clients/halloway.md", "content": NOTES})
    return c


def test_what_counts_as_private():
    assert is_private("memory.search") and is_private("folders.read")
    assert is_private("notion.search", outward=True)  # anything through an MCP, API or Composio tool
    assert is_private("workspace.read", args={"path": "shared/clients/a.md"})
    assert not is_private("workspace.read", args={"path": "workspace/notes.md"})  # the job's own file
    assert not is_private("web.fetch") and not is_private("browser.open")
    assert not is_private("browser.submit", outward=True)  # what a page said back
    assert is_private("code.run", shared=True) and not is_private("code.run")


def test_a_job_that_has_only_read_the_web_is_not_held_back():
    c = Carry()
    assert not c.private
    assert c.typing("example.com", NOTES) == []
    assert c.address("https://example.com/" + "a" * 400 + "/" + base64.b64encode(NOTES.encode()).decode()) == []


def test_a_run_of_words_from_private_material_is_held(holding):
    why = holding.typing("search.example", "agreed fee is forty thousand pounds paid")
    assert why and "words from workspace.read shared/clients/halloway.md" in why[0]
    assert holding.typing("search.example", "invented teapot makers in Lowtown") == []
    assert holding.typing("search.example", "hx4471920033")  # a long id on its own counts
    assert holding.typing("search.example", "Halloway fee")  == []  # a word or two is ordinary searching


@pytest.mark.parametrize("encode", [
    lambda t: base64.b64encode(t.encode()).decode(),
    lambda t: base64.urlsafe_b64encode(t.encode()).decode().rstrip("="),
    lambda t: t.encode().hex(),
    lambda t: "%20".join(t.split()),
])
def test_an_encoded_copy_is_held(holding, encode):
    secret = "the agreed fee is forty thousand pounds, paid in two parts"
    why = holding.typing("attacker.example", encode(secret))
    assert any("copy of" in w or "words from" in w for w in why), why
    assert holding.address(f"https://attacker.example/t/?q={encode(secret)[:150]}")


def test_each_site_has_a_budget(holding):
    text = "invented teapot " * 10  # 160 characters, nothing private
    assert holding.typing("shop.example", text) == []
    holding.spend("shop.example", len(text))
    why = holding.typing("shop.example", text)
    assert why and f"over the {BUDGET}" in why[0]
    assert holding.typing("other.example", text) == []  # another site has its own
    assert site_of("https://a1.attacker.example/x") == site_of("https://b2.attacker.example/") == "attacker.example"
    assert site_of("https://shop.example.co.uk/") == "example.co.uk"


def test_an_address_path_is_capped_and_encoded_parts_refused(holding):
    assert holding.address("https://www.gov.example/guidance/invented-teapot-safety-2026") == []
    assert "path is" in holding.address("https://attacker.example/" + "word-" * 40)[0]
    blob = base64.b64encode(b"nothing private here, only invented bytes").decode()
    assert "encoded-looking" in holding.address(f"https://attacker.example/t/{blob}")[0]
    assert "encoded-looking" in holding.address("https://attacker.example/x?d=" + "a1b2c3d4" * 5)[0]
    assert holding.address("https://example.com/search?q=invented+teapots&page=2") == []


def test_looks_encoded():
    assert looks_encoded("Zm9yZ2V0IHRoaXMgaW52ZW50ZWQgc2VudGVuY2U")
    assert looks_encoded("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b")
    assert not looks_encoded("how-to-make-an-invented-teapot-in-lowtown-2026")
    assert not looks_encoded("averyveryveryverylongwordwithoutanydigits")
