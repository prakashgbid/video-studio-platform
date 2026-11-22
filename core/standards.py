#!/usr/bin/env python3
"""Content-standards check for scripts and dialogue (build plan 1.10).

    python standards.py <script.md | production folder> [--profile family]

Checks every dialogue and action line against core/standards.json:
  FAIL    bad words, insults, disrespect to elders, inappropriate clothing
  TEACH   a [teaching] line, or a line full of teaching cues, spoken by someone not in
          production.json standards.teaching_speakers (teachings come only from the family)
  REVIEW  words that need context (fight, sword, lie ...): martial arts or a villain can be fine
Waive a line with a trailing  <!-- ok: reason -->. Exit code 1 if any FAIL or TEACH remains.
Writes <production>/docs/standards_report.md when run on a production.
"""
sys_path_here = __import__("os").path.dirname(__import__("os").path.abspath(__file__)); __import__("sys").path.insert(0, sys_path_here); import logical  # noqa: E402
import os, re, sys, json, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
LINE = re.compile(r"^\s*([A-Z][A-Z0-9 .'\-]{1,30}?)\s*(\[[^\]]*\])?\s*(\([^)]*\))?\s*:\s*(.+)$")

def words_re(ws):
    return re.compile(r"(?<![a-z])(" + "|".join(re.escape(w) for w in sorted(ws, key=len, reverse=True)) + r")(?![a-z])", re.I)

def check(text, prof, speakers):
    R = {k: words_re(v) for k, v in prof.items() if isinstance(v, list)}
    out = []; scene = None
    for n, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("## "): scene = raw.strip("# ").split("·")[0].strip(); continue
        if not raw.strip() or raw.lstrip().startswith(("#", "|", ">", "```")): continue
        waived = re.search(r"<!--\s*ok:(.*?)-->", raw); line = re.sub(r"<!--.*?-->", "", raw)
        m = LINE.match(line); who = m.group(1).strip() if m else None; tag = (m.group(2) or "") if m else ""
        body = m.group(4) if m else line
        hits = []
        for k, lab in (("fail_words", "FAIL bad word"), ("fail_insults", "FAIL insult"), ("disrespect", "FAIL disrespect"), ("clothing", "FAIL clothing")):
            for w in set(x.lower() for x in R[k].findall(line)): hits.append((lab, w))
        if who:
            cues = set(x.lower() for x in R["teaching_cues"].findall(body))
            teaching = "teach" in tag.lower() or len(cues) >= 2
            if teaching and speakers and who.lower() not in speakers:
                hits.append(("TEACH not family", f"{who} ({', '.join(sorted(cues)) or 'tagged'})"))
        for w in set(x.lower() for x in R["review_words"].findall(line)): hits.append(("REVIEW", w))
        for lab, w in hits:
            out.append({"line": n, "scene": scene, "who": who, "kind": lab, "word": w, "text": raw.strip()[:160],
                        "waived": waived.group(1).strip() if waived else None})
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("target"); ap.add_argument("--profile", default="family")
    a = ap.parse_args(); t = os.path.abspath(a.target)
    prof = json.load(open(os.path.join(HERE, "standards.json")))[a.profile]
    root = t if os.path.isdir(t) else None; speakers = set()
    pj = os.path.join(root or os.path.dirname(os.path.dirname(t)), "production.json")
    if os.path.exists(pj):
        speakers = {s.lower() for s in json.load(open(pj)).get("standards", {}).get("teaching_speakers", [])}
    files = [os.path.join(root, "story", f) for f in sorted(os.listdir(os.path.join(root, "story"))) if f.endswith(".md") and "ledger" not in f] if root else [t]
    allf = []
    for f in files:
        for h in check(open(f).read(), prof, speakers): h["file"] = os.path.basename(f); allf.append(h)
    live = [h for h in allf if not h["waived"]]
    block = [h for h in live if h["kind"].startswith(("FAIL", "TEACH"))]
    rev = [h for h in live if h["kind"] == "REVIEW"]
    rep = [f"# Content standards ({a.profile}) — {'PASS' if not block else 'FAIL'}", "",
           f"{len(block)} blocking, {len(rev)} to review, {len(allf) - len(live)} waived. Teaching speakers: {', '.join(sorted(speakers)) or '(none set)'}", ""]
    for h in block + rev: rep.append(f"- **{h['kind']}** `{h['word']}` — {h['file']}:{h['line']} {h['scene'] or ''} — {h['text']}")
    w = [h for h in allf if h["waived"]]
    if w: rep += ["", "Waived:"] + [f"- {h['file']}:{h['line']} `{h['word']}` — {h['waived']}" for h in w]
    print("\n".join(rep))
    if root: open(os.path.join(root, "docs", "standards_report.md"), "w").write("\n".join(rep) + "\n")
    sys.exit(1 if block else 0)

if __name__ == "__main__": main()
