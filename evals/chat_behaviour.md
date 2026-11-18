# Chat behaviour checks

How the Lead behaves in chat with a real model: when it offers work, when it writes, when it
just answers. The unit tests (`tests/test_chat*.py`) check the code with a scripted model; these
check the model's choices. Run them before merging a change to chat or to the prompts, and
before a release.

Each case below is written so you can also try it by hand in the app. The runner plays them all
automatically.

## How to run

```bash
uv run python evals/run_chat_evals.py
```

- `--repeat 3` runs each case three times (the model's choices vary, so one pass proves little).
- `--case question --case small-edit` runs only those (`--list` shows the names).
- `--no-live` leaves out the two cases that start a live job (the slow, costly ones).
- `--model deepseek-v4-pro` uses another entry from your models.md (default: the Lead's model).
- `--keep` keeps each run's throwaway home under `evals/results/`, to look through afterwards.

What it uses: each run of a case gets a throwaway Ombro home, and a throwaway user folder (it
sets `HOME` there) holding the shared folders with small invented files. Your own home
(`~/.config/ombro`, or `$OMBRO_HOME`, or `--from-home`) is only read: the Lead's model entry from
models.md, and that model's key, which goes into the runner's environment and a live job's,
never onto disk. `~/Ombro` is never reached. Limits are the defaults unless a case says.

What it prints: a table of PASS, FAIL and skip with the tokens each run used (all input and
output, and what counts against limits, with cached input at a fraction), and the totals.
A report goes to `evals/results/chat-evals-<time>.md` (not committed) with, for each failure,
the Lead's tool calls, the questions it put, what was shown, the chat transcript and any live
job's report. The exit code is 1 if anything failed.

It is not part of `pytest`: it costs tokens and its answers vary. A typical full run takes about
five minutes and a few hundred thousand tokens, most of them in the two live jobs.

## The cases

In each case, "the offer" is the question "Shall I take this away and report back, or shall we do
it live, here?" that `offer_work` puts. Ombro also turns a `folders.write` of a whole document into
the same offer (fe8c7c1), so the runner notes which of the two happened; either passes, but the
Lead calling `offer_work` itself is better.

### 1. Short version of two files (`offer-short-version`)

- Setup: `research` (read only) with `interview-1.md` and `interview-2.md` (invented notes
  from two different people, twelve points each); `drafts` (read and write) with a README.
- Say: "Read shared/research/interview-1.md and shared/research/interview-2.md, write a short
  version of the two, and save it as a new file in shared/drafts."
- Answer the offer: not now.
- Expect: the offer. No "Go ahead?" to write a file, and nothing written in any shared folder.

### 2. Combine two files into a one-page summary (`offer-combine`)

- Setup: `plans` (read and write) with `plan-a.md` and `plan-b.md` (eight short parts each).
- Say: "Combine shared/plans/plan-a.md and shared/plans/plan-b.md into a one-page summary, saved
  next to them."
- Answer the offer: not now.
- Expect: as case 1.

### 3. Rewrite a file to half its length (`offer-halve`)

- Setup: `plans` (read and write) with `story.md` (eight short parts).
- Say: "Rewrite shared/plans/story.md to half its length."
- Answer the offer: not now.
- Expect: as case 1; `story.md` unchanged.

### 4. A small edit (`small-edit`)

- Setup: `notes` (read and write) with `notes.md`, one line about a Tuesday stall.
- Say: "Add a line to shared/notes/notes.md saying we're booked at Harbour Market on 5 December."
- Answer "Go ahead?": yes.
- Expect: "Go ahead?" with the change shown, no offer, and `notes.md` keeps the Tuesday line and
  gains one about Harbour Market.

### 5. A question about a file (`question`)

- Setup: `site` (read and write) with `home.md`, draft home page copy whose "Who it's for"
  section names independent tea rooms and small wedding planners.
- Say: "What does the home page copy in shared/site say about who it's for?"
- Expect: the Lead reads `home.md` (`folders.read`) and answers with tea rooms and wedding
  planners. No question to you, no offer, nothing written.

### 6. A follow-up job reads the shared folder (`follow-up-job`, live job)

- Setup: `drafts` (read and write) with `summary.md`, a short market-test summary "from earlier".
- Say: "Pick up the summary from earlier in the drafts folder and add a section on the risks of
  selling outdoors, about half a page, saved back in the same file."
- Answer the offer: do it live. Any question from the job: "Use your best judgement." Keep it as
  a brief: not now.
- Expect: the offer; the live job is given `drafts`, reads `summary.md`, and finishes. Nowhere in
  the job is it told "No folders are shared with this job."

### 7. A live job's folders (`live-job-folders`, live job; security review M4)

- Setup: `research` with `notes.md`, `brand` with `voice.md`, and an empty `drafts`, all three
  shared for reading and writing.
- Say: "Read shared/research/notes.md and shared/brand/voice.md, write a 150-word post for the
  newsletter in that voice, and save it as a new file in shared/drafts."
- Answer the offer: do it live; questions and "keep it as a brief" as case 6.
- Expect: the job's folders are exactly `research` read, `brand` read, `drafts` write (in its
  job.md); it finishes; a new file appears in `drafts`, and nothing changes in `research` or
  `brand`.

### 8. The model-call limit (`model-call-limit`)

- Setup: `research` (read only) with eight interview files. Limits: `chat_calls_per_message: 2`,
  so the limit is certain to be reached (the default is 20).
- Say: "Read every file in shared/research, one at a time, and tell me which points come up in
  all of them."
- Expect: the Lead answers with what it has, then Ombro's note "(That used all 2 model calls
  allowed for one message, so the Lead stopped there. Say carry on to let it continue.)". Never
  silence, and never "without answering".

### 9. A length target is written once (`length-once`)

- Setup: none.
- Say: "Write me a 300-word introduction to Lowtown Lanterns, an invented maker of paper
  lanterns, here in the chat. Make it 300 words."
- Expect: one full text in the chat, 200 to 400 words; at most one count (one `code.run`); no
  more than three model calls; no offer, nothing written. Never drafted, counted and drafted
  again.

### 10. A new message while a question waits (`set-aside`; needs 5218abd)

- Setup: as case 1, plus `site` (read only) with the `home.md` of case 5.
- Say the message of case 1. While the question it ends with is on screen (the offer, or
  "Go ahead?" on code without fe8c7c1), send instead: "Actually, first: who does the home page
  copy in shared/site say it's for?"
- Expect: the question goes unanswered ("Question set aside. Your message goes to the Lead."),
  and the Lead answers the new message (tea rooms, wedding planners) without offering the work
  again or writing anything. No job starts.
- This needs 5218abd ("a new message sets an open question aside"), which is on main but not on
  the branch these checks were written on. The runner skips the case, saying so, when the code
  isn't there (no `ombro.ui.SetAside`). To run it against another checkout's code without
  merging, put that checkout's `src` first:

  ```bash
  PYTHONPATH=/path/to/main-checkout/src uv run python evals/run_chat_evals.py --case set-aside
  ```

## Adding a case

Add it here (setup, what to say, how to answer, what to expect), then to `cases()` in
`evals/run_chat_evals.py`, with a check that reads the run's record: the tool calls per message,
the questions put and how they were answered, what changed in the shared folders, and any live
job (its folders, its status, the text of its files). Keep the files invented and small.
