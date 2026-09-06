# notebook.py

Your agent's long-term memory as **one markdown file you can read and correct**.

`notebook.py` folds conversation transcripts into numbered, dated lines. You paste a
compact block of those lines into your system prompt. When a line is wrong, you open the
file and fix it — and the next session uses the corrected line.

```markdown
- [L01] 2026-09-01 | The database is Postgres 16, upgraded from 14 on Aug 3 | src:session-a.jsonl:112 | refs:3
- [L02] 2026-09-01 | Deploy with `make ship`, never `npm run deploy` — the latter skips migrations | src:session-a.jsonl:340
- ~~[L04] 2026-09-02 | The staging box is 10.0.0.4~~ (retired 2026-09-03)
```

Every line carries the transcript and line number it came from, so you can check where a
memory came from. Retired lines are struck through, never deleted.

## Install

One file, Python 3 standard library only. No dependencies, no service, no database.

```
curl -O https://raw.githubusercontent.com/minjimindypark/llmcompressor/main/notebook.py
python3 notebook.py --help
```

## Quick start

```
python3 notebook.py install
```

Once. After that there is nothing to run and nothing to paste. When a Claude Code session
ends, that project's notebook is updated; when the next one starts, the current block is
handed to the model automatically.

**Nothing is written into your projects.** Notebooks live in `~/.claude/notebooks/<project>.md`,
one per project. `install` backs up your `~/.claude/settings.json` first, and
`python3 notebook.py uninstall` puts it back.

When the agent has a fact wrong, that is when you open the notebook:

```
python3 notebook.py grep postgres         # find the line
python3 notebook.py edit L07 "new text"   # or just edit the file
```

The correction is in effect from the next session on.

## Use

```
python3 notebook.py install                   # hook it up, once
python3 notebook.py uninstall                 # unhook it
python3 notebook.py grep postgres             # search what it remembered
python3 notebook.py edit L07 "new text"       # fix one wrong memory
python3 notebook.py kill L07                  # retire a line (kept, struck through)

python3 notebook.py sync                      # what the SessionEnd hook runs
python3 notebook.py inject                    # what the SessionStart hook runs
python3 notebook.py distill session.jsonl     # fold one transcript in by hand
python3 notebook.py stats session.jsonl       # how much the transcript shrank
```

Notebooks default to `~/.claude/notebooks/<project>.md` (`--notebook` to point elsewhere;
`sync --into FILE` also mirrors the block into a file such as `CLAUDE.md` or `AGENTS.md`,
which is how you put the memory in the repo for a team to review).
`distill` reads Claude Code JSONL transcripts; each run adds only new lines, and bumps a
`refs` counter when a memory resurfaces in another session. `context` puts the most
referenced lines first and stops at your token budget.

## What it is for

An agent that starts every session from zero re-learns the same project every time, and
you pay for that in tokens and in wrong answers. The usual fix is a memory service that
stores and retrieves for you. This one keeps the memory in a file you own, in plain lines
you can read, so that when the agent has learned something false you can correct it in one
line instead of arguing with it.

Measured on one real transcript (84 MB, 4.87 M characters of message text): the emitted
context block was 3,025 characters — a 1,609:1 reduction against message text. Your ratio
depends on your transcripts and your budget; run `stats` to see yours.

---

Want more than one file can do? The full product is in development —
[reserve early access ($5, refundable)](https://agent-notebook.vercel.app/a1-agentdev-losescontext?utm_source=github&utm_medium=readme&utm_content=A1).

## Limits

- `distill` ranks candidate lines with a small logistic-regression model trained on 700
  labelled sentences from real transcripts. Held-out sentences from the same projects:
  top-25 precision 0.88 against 0.80 for the hand-written rules it replaces. Sentences from
  four projects the model never saw, including transcripts from an older machine: 0.88
  against 0.76, and 0.82 against 0.68 at top-50. Keeping everything scores 0.61 there, so
  that is the floor. It still over-collects.
  Reading and pruning the file is part of the design, not a workaround.
- The features are structural (has a number, has a date, is a question, is about what is
  happening *now*) rather than vocabulary, so the scorer does not depend on one project's
  words. `FEATURES` and `WEIGHTS` in the file are the entire model — retrain and replace
  them if your transcripts look different.
- Transcript parsing targets Claude Code JSONL. Other formats need a different reader.
- No embeddings, no ranking beyond reference count and recency. `grep` is the search.

## License

Apache-2.0. See [LICENSE](LICENSE).
