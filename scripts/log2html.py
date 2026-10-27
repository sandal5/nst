#!/usr/bin/env python3
"""Convert a muse/ work-log Markdown file to a standalone HTML page for human reading.

Usage: python3 scripts/log2html.py muse/YYYY-MM-DD.md [more files...]
Writes the same path with .html extension. Supports the subset of Markdown the
work logs use: #/##/### headings, **bold**, `code`, - lists, paragraphs, --- rules.
Dialogue turns (paragraphs starting with **Gordon** or **Anthony**) get styled.
"""
import html
import re
import sys

CSS = """
body{font-family:-apple-system,'Hiragino Sans','Noto Sans JP',sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;line-height:1.75;color:#1a1a1a;background:#fafafa}
h1{font-size:1.5rem;border-bottom:3px solid #2563eb;padding-bottom:.4rem}
h2{font-size:1.2rem;margin-top:2rem;color:#1e40af}
h3{font-size:1.05rem}
p{margin:.55rem 0}
.turn-g{border-left:4px solid #2563eb;background:#eff6ff;padding:.45rem .8rem;border-radius:0 8px 8px 0}
.turn-a{border-left:4px solid #9ca3af;background:#fff;padding:.45rem .8rem;border-radius:0 8px 8px 0}
ul{margin:.4rem 0 .8rem}
code{background:#e5e7eb;padding:0 .3em;border-radius:4px;font-size:.92em}
hr{border:none;border-top:1px solid #d1d5db;margin:1.5rem 0}
.meta{color:#6b7280;font-size:.9rem}
"""


def inline(t):
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return t


def convert(md_text):
    out = []
    in_list = False
    for raw in md_text.split("\n"):
        line = raw.rstrip()
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if not line:
            continue
        if line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line == "---":
            out.append("<hr>")
        else:
            cls = ""
            if line.startswith("**Gordon**"):
                cls = ' class="turn-g"'
            elif line.startswith("**Anthony**"):
                cls = ' class="turn-a"'
            elif line.startswith("（"):
                cls = ' class="meta"'
            out.append(f"<p{cls}>{inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def main():
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as f:
            md = f.read()
        title = next((l[2:] for l in md.split("\n") if l.startswith("# ")), "工作日志")
        page = (
            '<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>\n"
            + convert(md)
            + "\n</body></html>\n"
        )
        out_path = path[: -len(".md")] + ".html" if path.endswith(".md") else path + ".html"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(page)
        print("wrote", out_path)


if __name__ == "__main__":
    main()
