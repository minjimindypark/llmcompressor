#!/usr/bin/env python3
"""notebook.py — a memory notebook for LLM agents.

Your agent's long-term memory as one human-readable markdown file of
numbered, dated lines. Distill sessions into it, paste a compact context
block into your system prompt, edit or retire lines by hand.

  notebook.py distill session.jsonl      # extract memory lines from a transcript
  notebook.py context --budget 800       # compact block for your system prompt
  notebook.py edit L07 "new text"        # fix a line
  notebook.py kill L07                   # retire a line (struck-through, never deleted)
  notebook.py grep <word>                # search lines
  notebook.py stats session.jsonl        # transcript vs context compression ratio

Stdlib only. Memory file defaults to ./notebook.md (override with --notebook).
"""
import argparse, json, os, re, sys
from datetime import date

LINE_RE = re.compile(
    r"^- (?P<dead>~~)?\[L(?P<num>\d+)\] (?P<date>\d{4}-\d{2}-\d{2}) \| "
    r"(?P<text>.*?) \| src:(?P<src>\S+?)(?: \| refs:(?P<refs>\d+))?(?:~~ \(retired (?P<rdate>[0-9-]+)\))?$"
)
CITE_INSTRUCTION = "Cite [L##] for any answer that uses a memory line."

# ---------------------------------------------------------------- notebook I/O

def load(path):
    """Parse notebook.md -> list of dicts. Non-matching lines are preserved raw."""
    entries, extras = [], []
    if os.path.exists(path):
        for raw in open(path, encoding="utf-8"):
            raw = raw.rstrip("\n")
            m = LINE_RE.match(raw)
            if m:
                entries.append({"num": int(m["num"]), "date": m["date"],
                                "text": m["text"], "src": m["src"],
                                "refs": int(m["refs"] or 0),
                                "retired": m["rdate"] if m["dead"] else None})
            elif raw.strip():
                extras.append(raw)
    return entries, extras

def render(e):
    core = f"[L{e['num']:02d}] {e['date']} | {e['text']} | src:{e['src']}"
    if e["refs"]:
        core += f" | refs:{e['refs']}"
    if e["retired"]:
        return f"- ~~{core}~~ (retired {e['retired']})"
    return f"- {core}"

def save(path, entries, extras):
    head = extras or ["# Agent memory notebook", ""]
    body = [render(e) for e in entries]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(head + body) + "\n")

def find(entries, label):
    m = re.fullmatch(r"L?(\d+)", label, re.I)
    num = int(m.group(1)) if m else -1
    for e in entries:
        if e["num"] == num:
            return e
    sys.exit(f"error: no line {label} in notebook")

def norm(text):
    return re.sub(r"[^a-z0-9가-힣]+", " ", text.lower()).strip()

# ------------------------------------------------------------ transcript parse

def iter_messages(path):
    """Yield (msgidx, role, text) from a Claude Code JSONL transcript.
    Skips malformed lines, tool results, thinking blocks, non-chat records."""
    for idx, raw in enumerate(open(path, encoding="utf-8", errors="replace")):
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(d, dict) or d.get("type") not in ("user", "assistant"):
            continue
        content = (d.get("message") or {}).get("content")
        if isinstance(content, str):
            texts = [content]
        elif isinstance(content, list):
            texts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
        else:
            continue
        text = "\n".join(t for t in texts if t).strip()
        if text:
            yield idx, d["type"], text

# ------------------------------------------------------------------ heuristics

DECISION_WORDS = re.compile(
    r"\b(decid\w+|will use|chose|choose|must|should|always|never|don'?t|do not"
    r"|fixed|bug|because|instead|switch\w*|default|renamed?|moved|deprecated"
    r"|todo|remember|important|note that|confirmed|verified|root cause)\b"
    r"|하기로|결정|금지|완료|정본|원인|수정|확인|필수|해야", re.I)

# A line that CHANGES a value is worth more than a line that merely mentions one.
# Fluent, on-topic sentences crowd out the one turn that revised the answer, so
# revision markers are scored separately and weighted above topical keywords.
PIVOT_WORDS = re.compile(
    r"\b(actually|turns out|correction|corrected|instead of|no longer|used to be"
    r"|was wrong|not \w+ anymore|changed (?:from|to)|updated? (?:from|to)|rolled back"
    r"|reverted|replaced? (?:by|with)|overrides?|supersed\w+|as of \w+|since \w+ \d"
    r"|previously)\b"
    r"|아니라|바뀌|바꿔|바꿨|정정|취소|철회|대신|더 이상|이제는|원래는|아까|틀렸", re.I)

# "X -> Y", "from 14 to 16", "14 → 16": an explicit old-value/new-value pair.
PIVOT_SHAPE = re.compile(r"->|→|\bfrom\s+\S+\s+to\s+\S+|\b\S+\s*에서\s*\S+\s*로\b")

NOISE = re.compile(
    r"\x1b|\[\d+m|⎿|</?local-command|</?bash-|Wrote \d+ lines|tool_use|^Caveat:"
    r"|/bin/bash:|No such file|missing .*operand|command not found|Traceback"
    r"|^\$ |^\(|Is a directory")

def score_sentence(s):
    if not (30 <= len(s) <= 300) or len(s.split()) < 4:
        return 0
    if re.match(r"^[\s#>|*`{\[\-=]", s) or s.count("`") >= 4 or NOISE.search(s):
        return 0  # markdown scaffolding / code / pasted terminal noise
    letters = sum(c.isalpha() for c in s)
    if letters < len(s) * 0.4:
        return 0  # mostly symbols/numbers -> likely code or a table row
    sc = 0
    if PIVOT_WORDS.search(s):                          sc += 1
    if PIVOT_SHAPE.search(s):                          sc += 1
    if DECISION_WORDS.search(s):                       sc += 2
    if re.search(r"\d", s):                            sc += 1
    if re.search(r"(/[\w.~-]+){2,}|\w+\.(py|md|json\w*|sh|yaml|toml)\b", s): sc += 2
    if re.search(r"[=:]\s*\S", s):                     sc += 1
    if s.endswith("?"):                                sc -= 2
    return sc

def extract_candidates(path, cap=25):
    """Top-scoring sentences from a transcript: [(score, msgidx, sentence)]."""
    seen, cands = set(), []
    for idx, _role, text in iter_messages(path):
        for s in re.split(r"(?<=[.!?。])\s+|\n+", text):
            s = re.sub(r"\s+", " ", s).strip(" -*#`")
            sc = score_sentence(s)
            key = norm(s)
            if sc >= 3 and key not in seen:
                seen.add(key)
                cands.append((sc, idx, s))
    cands.sort(key=lambda c: (-c[0], c[1]))
    return cands[:cap]

# -------------------------------------------------------------------- commands

def cmd_distill(args):
    entries, extras = load(args.notebook)
    existing = {norm(e["text"]): e for e in entries}
    nxt = max((e["num"] for e in entries), default=0) + 1
    src_base = os.path.basename(args.transcript)
    today, added, dup = date.today().isoformat(), 0, 0
    for _sc, idx, s in extract_candidates(args.transcript):
        key = norm(s)
        if key in existing:
            if not existing[key]["src"].startswith(src_base + ":"):
                existing[key]["refs"] += 1  # re-surfaced in another session
                dup += 1
            continue
        e = {"num": nxt, "date": today, "text": s.replace("|", "/"),
             "src": f"{src_base}:{idx}", "refs": 0, "retired": None}
        entries.append(e); existing[key] = e; nxt += 1; added += 1
    save(args.notebook, entries, extras)
    print(f"distilled {src_base}: +{added} new line(s), "
          f"{dup} re-surfaced (refs bumped) -> {args.notebook} "
          f"({len(entries)} total)")

def context_block(entries, budget):
    live = [e for e in entries if not e["retired"]]
    live.sort(key=lambda e: (-e["refs"], -e["num"]))  # most referenced, then newest
    limit = budget * 4  # ~4 chars per token
    head = "## Memory notebook (agent long-term memory)\n"
    tail = "\n" + CITE_INSTRUCTION
    out, used = [], len(head) + len(tail)
    for e in live:
        line = f"[L{e['num']:02d}] {e['date']} {e['text']}"
        if used + len(line) + 1 > limit:
            break
        out.append(line); used += len(line) + 1
    return head + "\n".join(out) + tail

def cmd_context(args):
    entries, _ = load(args.notebook)
    print(context_block(entries, args.budget))

def cmd_edit(args):
    entries, extras = load(args.notebook)
    e = find(entries, args.line)
    e["text"], e["date"] = args.text.replace("|", "/"), date.today().isoformat()
    save(args.notebook, entries, extras)
    print(f"edited {render(e)}")

def cmd_kill(args):
    entries, extras = load(args.notebook)
    e = find(entries, args.line)
    e["retired"] = e["retired"] or date.today().isoformat()
    save(args.notebook, entries, extras)
    print(f"retired {render(e)}")

def cmd_grep(args):
    entries, _ = load(args.notebook)
    hits = [e for e in entries if args.word.lower() in e["text"].lower()]
    for e in hits:
        print(render(e))
    if not hits:
        print(f"(no lines match '{args.word}')")

def cmd_stats(args):
    tchars = os.path.getsize(args.transcript)
    mchars = sum(len(t) for _i, _r, t in iter_messages(args.transcript))
    entries, _ = load(args.notebook)
    cchars = len(context_block(entries, args.budget))
    print(f"transcript file : {tchars:>9,} chars (~{tchars // 4:,} tokens)")
    print(f"  message text  : {mchars:>9,} chars (~{mchars // 4:,} tokens)")
    print(f"context output  : {cchars:>9,} chars (~{cchars // 4:,} tokens)")
    if cchars:
        print(f"compression     : {tchars / cchars:>9.0f}:1 vs file, "
              f"{mchars / cchars:.0f}:1 vs message text")

def main(argv=None):
    p = argparse.ArgumentParser(prog="notebook.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--notebook", default="notebook.md", help="memory file (default: notebook.md)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("distill", help="extract memory lines from a JSONL transcript")
    s.add_argument("transcript"); s.set_defaults(fn=cmd_distill)
    s = sub.add_parser("context", help="emit a compact context block for a system prompt")
    s.add_argument("--budget", type=int, default=800, help="token budget (default 800)")
    s.set_defaults(fn=cmd_context)
    s = sub.add_parser("edit", help="rewrite one line: edit L07 'new text'")
    s.add_argument("line"); s.add_argument("text"); s.set_defaults(fn=cmd_edit)
    s = sub.add_parser("kill", help="retire a line (kept, struck-through)")
    s.add_argument("line"); s.set_defaults(fn=cmd_kill)
    s = sub.add_parser("grep", help="search notebook lines")
    s.add_argument("word"); s.set_defaults(fn=cmd_grep)
    s = sub.add_parser("stats", help="transcript vs context compression ratio")
    s.add_argument("transcript")
    s.add_argument("--budget", type=int, default=800); s.set_defaults(fn=cmd_stats)
    args = p.parse_args(argv)
    args.fn(args)

if __name__ == "__main__":
    main()
