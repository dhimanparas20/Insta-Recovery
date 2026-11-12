"""Survey every HTML file in the export: entry count + a short text sample.

Usage: python -I tools/survey.py <export_dir> <out_file>
Skips per-thread chat files (summarised by count only).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import igparse as P  # noqa: E402

exp, out = Path(sys.argv[1]), Path(sys.argv[2])
lines = []
chat_files = 0
for f in sorted(exp.rglob("*.html")):
    rel = f.relative_to(exp).as_posix()
    if "/messages/inbox/" in rel or "/messages/message_requests/" in rel:
        chat_files += 1
        continue
    root = P.load(f)
    ents = P.entries(root)
    m = P.main_node(root)
    sample = " | ".join(m.text(" ").split())[:300]
    lines.append(f"{len(ents):6d}  {rel}\n        {sample}")
lines.append(f"chat thread html files: {chat_files}")
out.write_text("\n".join(lines), encoding="utf-8")
print(f"wrote {out} ({len(lines)} files)")
