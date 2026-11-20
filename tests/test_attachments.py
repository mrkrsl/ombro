"""Attached files: kept under Files, read as text (plain, PDF, Word), and given to jobs.
All invented; no network."""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

from ombro import attachments


def make_pdf(path: Path, lines: list[str]) -> Path:
    """A one-page PDF with these lines of text, written by hand (no PDF library needed)."""
    text = " ".join(f"({line}) Tj 0 -16 Td" for line in lines)
    stream = f"BT /F1 12 Tf 72 720 Td {text} ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def make_docx(path: Path, paragraphs: list[str], table: list[list[str]] | None = None) -> Path:
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    if table:
        rows = "".join(
            "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>"
            for row in table
        )
        body += f"<w:tbl>{rows}</w:tbl>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {w}><w:body>{body}</w:body></w:document>')
    return path


def test_an_attached_file_is_kept_under_files_by_date_never_over_another(home):
    when = datetime(2026, 11, 18, 9, 0)
    first = attachments.save(home, "../../Invented notes.md", b"# Notes", now=when)
    second = attachments.save(home, "Invented notes.md", b"# Other", now=when)
    assert first.parent == attachments.files_dir(home) / "2026-11-18"
    assert first.name == "Invented notes.md" and second.name == "Invented notes-2.md"
    assert attachments.clean_name(".hidden") == "hidden" and attachments.clean_name("") == "attached-file"


def test_text_pdf_and_word_are_read_as_text(tmp_path):
    md = tmp_path / "a.md"
    md.write_text("# Invented teapots\n\nThree of them.")
    assert attachments.read(md).text.startswith("# Invented teapots")
    pdf = attachments.read(make_pdf(tmp_path / "b.pdf", ["Invented kettle survey", "Lowtown, 2026"]))
    assert pdf.kind == "pdf" and "Invented kettle survey" in pdf.text and "[Page 1]" in pdf.text
    word = attachments.read(
        make_docx(tmp_path / "c.docx", ["Invented brief", "Count the pans."], [["Maker", "Town"], ["Ada", "Lowtown"]])
    )
    assert word.kind == "word" and "Count the pans." in word.text and "Ada | Lowtown" in word.text


def test_long_text_is_cut_and_other_files_have_none(tmp_path):
    long = tmp_path / "long.txt"
    long.write_text("x" * 50)
    got = attachments.read(long, limit=10)
    assert got.text == "x" * 10 and got.cut
    png = tmp_path / "pic.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    assert attachments.read(png).kind == "image" and attachments.read(png).text is None
    binary = tmp_path / "data.txt"
    binary.write_bytes(b"\x00\x01\x02")
    assert attachments.read(binary).text is None
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a zip")
    assert attachments.read(broken).problem == "it isn't a Word document Ombro can read"


def test_a_job_is_given_its_briefs_files(tmp_path):
    job = tmp_path / "job"
    (job / "workspace").mkdir(parents=True)
    a = tmp_path / "a.csv"
    a.write_text("x,y")
    given = attachments.give_to_job(job, [str(a), str(tmp_path / "gone.pdf")])
    assert given == ["a.csv"] and (job / "workspace" / "inputs" / "a.csv").read_text() == "x,y"
    assert "workspace/inputs/a.csv" in attachments.inputs_section(given)


# -- in a chat, a brief and a job --------------------------------------------------

from conftest import LEAD_REPORT  # noqa: E402
from ombro import fake, jobs, mdfile  # noqa: E402
from test_chat import SPEC_ARGS, ChatSession  # noqa: E402
from test_live_jobs import TASK, in_thread, offer  # noqa: E402,F401 - in_thread is a fixture


def attached(s: ChatSession, path: Path, text: str):
    """An answer that attaches a file first, as the app does with a message."""

    def answer(prompt):
        s.chat.attach(path)
        return text

    return answer


def test_an_attached_files_text_goes_to_the_lead_as_material(home, tmp_path):
    kept = attachments.save(home, "survey.pdf", make_pdf(tmp_path / "x.pdf", ["Invented kettle survey"]).read_bytes())
    s = ChatSession(home)
    s([fake.say("It's a kettle survey.")], [attached(s, kept, "What's in this?")])
    sent = [m["content"] for m in s.client.calls[0]["messages"] if m["role"] == "user"][-1]
    assert "the user attached survey.pdf (PDF" in sent and "Invented kettle survey" in sent
    assert "⟪" in sent and sent.endswith("What's in this?")  # fenced as material, then the message
    assert "_You attached survey.pdf (PDF" in s.transcript
    assert mdfile.read(s.chat.path)[0]["attached"] == [str(kept)]


def test_a_file_with_no_text_is_named_not_read(home):
    kept = attachments.save(home, "archive.zip", b"PK\x03\x04\x00")
    s = ChatSession(home)
    s([fake.say("Noted.")], [attached(s, kept, "Here's the archive")])
    sent = [m["content"] for m in s.client.calls[0]["messages"] if m["role"] == "user"][-1]
    assert "attached archive.zip (file" in sent and "You can't read it in the chat" in sent
    assert "workspace/inputs/archive.zip" in sent


def test_a_brief_made_in_the_chat_is_given_its_files_and_so_is_its_job(home):
    kept = attachments.save(home, "makers.csv", b"name,town\nAda,Lowtown\n")
    s = ChatSession(home)
    s(
        [
            fake.call("offer_brief", {"summary": "Find teapot makers"}),
            fake.call("propose", SPEC_ARGS),
            fake.say("Saved."),
        ],
        [attached(s, kept, "Find me teapot makers from this list"), "y", "a", "Thanks"],
    )
    meta, _ = mdfile.read(home.specs_dir / "find-teapots.md")
    assert meta["inputs"] == [str(kept)]
    opening = s.client.calls[1]["messages"][1]["content"]  # the brief interview's starting point
    assert "workspace/inputs/makers.csv" in opening

    from ombro.settings import load_limits

    meta, body = mdfile.read(home.specs_dir / "find-teapots.md")
    job = jobs.create_job(home, ["default"], meta, body, load_limits(home), source="test")
    assert (job / "workspace" / "inputs" / "makers.csv").read_text() == "name,town\nAda,Lowtown\n"
    brief = (job / "spec.md").read_text()
    assert brief.count("## Files given with this brief") == 1 and "workspace/inputs/makers.csv" in brief
    again = jobs.create_job(
        home, ["default"], *mdfile.read(job / "spec.md"), load_limits(home), source="test"
    )  # a restart from the job's own copy lists them once
    assert (again / "spec.md").read_text().count("## Files given with this brief") == 1


def test_a_live_job_is_given_the_chats_files(home, in_thread):
    in_thread[:] = [fake.finish("Listed three."), LEAD_REPORT]
    kept = attachments.save(home, "bakeries.md", b"# Invented bakeries\n")
    s = ChatSession(home)
    s(
        [offer(), fake.say("Anything else?")],
        [attached(s, kept, "Find me bakeries in Lowtown."), "live", "save", "/quit"],
    )
    (job_dir,) = list(home.jobs_dir.iterdir())
    assert (job_dir / "workspace" / "inputs" / "bakeries.md").is_file()
    assert (
        "workspace/inputs/bakeries.md" in (job_dir / "spec.md").read_text()
        and TASK in (job_dir / "spec.md").read_text()
    )
    (kept_brief,) = list(home.specs_dir.glob("*.md"))  # kept as a brief: the files stay with it, listed once
    meta, body = mdfile.read(kept_brief)
    assert meta["inputs"] == [str(kept)] and "## Files given with this brief" not in body


def test_a_file_dragged_into_the_terminal_is_attached(home, tmp_path):
    src = tmp_path / "My notes.md"
    src.write_text("# Invented notes\n\nThree teapots.")
    s = ChatSession(home)

    def typed(prompt):
        s.chat.take_paths = True
        return f"Summarise {str(src).replace(' ', chr(92) + ' ')} please"

    s([fake.say("Three teapots.")], [typed])
    sent = [m["content"] for m in s.client.calls[0]["messages"] if m["role"] == "user"][-1]
    assert "Three teapots." in sent and sent.endswith("Summarise My notes.md please")
    assert "Attached My notes.md." in s.output
    assert (attachments.files_dir(home)).rglob("My notes.md")


def test_a_path_typed_in_the_app_is_only_text(home, tmp_path):
    src = tmp_path / "notes.md"
    src.write_text("secret-ish")
    s = ChatSession(home)
    s([fake.say("OK.")], [f"Look at {src}"])
    sent = [m["content"] for m in s.client.calls[0]["messages"] if m["role"] == "user"][-1]
    assert sent.endswith(f"Look at {src}") and "secret-ish" not in sent


def test_workspace_read_reads_a_pdf_and_a_word_file_as_text(home, tmp_path):
    from ombro.native import _read
    from ombro.tools import ToolContext

    job = tmp_path / "job"
    (job / "workspace" / "inputs").mkdir(parents=True)
    make_pdf(job / "workspace" / "inputs" / "a.pdf", ["Invented pdf line"])
    make_docx(job / "workspace" / "inputs" / "b.docx", ["Invented word line"])
    ctx = ToolContext(job_dir=job, role="researcher")
    assert "Invented pdf line" in _read({"path": "workspace/inputs/a.pdf"}, ctx)["content"]
    assert "Invented word line" in _read({"path": "workspace/inputs/b.docx"}, ctx)["content"]


# -- images, for a model that sees ----------------------------------------------------

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_an_image_is_shown_to_the_lead_with_its_message_only(home):
    kept = attachments.save(home, "teapot.png", PNG)
    s = ChatSession(home)
    s([fake.say("A small teapot."), fake.say("Yes.")], [attached(s, kept, "What is this?"), "Is it blue?"])
    first, second = s.client.calls
    last_user = [m for m in first["messages"] if m["role"] == "user"][-1]["content"]
    assert isinstance(last_user, list) and last_user[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "the image teapot.png" in last_user[0]["text"] and last_user[0]["text"].endswith("What is this?")
    assert all(isinstance(m.get("content"), str) or m["role"] != "user" for m in second["messages"])  # not again


def test_a_model_that_cant_see_answers_without_the_image(home):
    kept = attachments.save(home, "teapot.png", PNG)
    s = ChatSession(home)
    s(
        [{"error": "this model does not support image input"}, fake.say("I can't see it, but tell me.")],
        [attached(s, kept, "What is this?")],
    )
    assert "couldn't take teapot.png as an image" in s.output
    assert "I can't see it" in s.output
    assert isinstance([m for m in s.client.calls[-1]["messages"] if m["role"] == "user"][-1]["content"], str)


def test_the_providers_get_images_in_their_own_form():
    from ombro.anthropic_client import to_anthropic
    from ombro.responses_client import to_responses

    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
            ],
        }
    ]
    _, out = to_anthropic(msgs)
    assert out[0]["content"][0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"},
    }
    assert out[0]["content"][1] == {"type": "text", "text": "What is this?"}
    _, items = to_responses(msgs)
    assert items[0]["content"] == [
        {"type": "input_text", "text": "What is this?"},
        {"type": "input_image", "image_url": "data:image/png;base64,QUJD"},
    ]
