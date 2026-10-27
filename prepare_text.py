#!/usr/bin/env python3
"""
prepare_text.py   (Step 3 of the project: the documents)
=========================================================
Takes the .txt files you copied from the SEC pages (10-K_2022-10-28.txt, 10-Q_2026-07-31.txt ...)
and prepares them for search:

  1. CLEAN   removes web-page menu text, page footers and image names; turns table rows
             into readable "iPad | 28,023 | 5% | 26,694" lines.
  2. SPLIT   finds the sections ("Item 7. Management's Discussion ...").
  3. CHUNK   cuts each section into pieces of about 1,000 characters (what the search will
             look through) and attaches labels: document type, date, fiscal year, section.

File names must contain the form and date, e.g.  10-K_2022-10-28.txt  or  10-Q_2026-07-31.txt

Usage
  python prepare_text.py --text 10-K_annual_reports 10-Q_quarterly_reports
  python prepare_text.py --text text --out prepared_text --chunk-size 1000

Output (default folder: prepared_text/)
  clean/*.txt        the cleaned documents
  chunks.jsonl       one chunk per line (used by the search step)
  chunks.csv         same, to look at in Excel
  text_report.csv    one row per document: size, sections found, number of chunks, warnings
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"(10-K|10-Q)_(\d{4})-(\d{2})-(\d{2})", re.I)
FOOTER_RE = re.compile(r"^\s*Apple Inc\.\s*\|\s*\d{4}\s+Form 10-[KQ]\s*\|\s*\d+\s*$")
IMAGE_RE = re.compile(r"^\s*\S+\.(jpg|jpeg|png|gif)\s*$", re.I)
ITEM_RE = re.compile(r"^\s*Item\s+(\d+[A-C]?)\.\s+(\S.*?)\s*$", re.I)
PART_RE = re.compile(r"^\s*PART\s+(IV|III|II|I)\b.{0,50}$")  # uppercase only, so sentences that mention "Part II" are not mistaken
NUMBERISH = re.compile(r"^[\s$(]*[\d.,]+")


def doc_info(path: Path):
    m = NAME_RE.search(path.name)
    if not m:
        return None
    form, y, mo, d = m.group(1).upper(), int(m.group(2)), int(m.group(3)), int(m.group(4))
    if form == "10-K":
        period = "FY"
    else:
        period = {1: "Q1", 2: "Q1", 4: "Q2", 5: "Q2", 7: "Q3", 8: "Q3"}.get(mo)
    # Apple files each report in the same calendar year as its fiscal year
    return {"doc_type": form, "filing_date": "%04d-%02d-%02d" % (y, mo, d), "fiscal_year": y,
            "fiscal_period": period or "?"}


# --------------------------------------------------------------------------- cleaning
def format_table_line(line: str) -> str:
    cells = [c.strip() for c in line.split("\t")]
    out = []
    for c in cells:
        if c in ("", "$", ")"):
            continue
        if c == "%" and out:
            out[-1] += "%"
        else:
            out.append(c)
    return " | ".join(out)


def clean(raw: str):
    text = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = text.split("\n")
    notes = []
    # 1) drop the web-page menu text above the real document
    start = next((i for i, l in enumerate(lines) if l.strip().upper() == "UNITED STATES"), None)
    if start is None:
        notes.append("could not find the start of the filing ('UNITED STATES')")
        start = 0
    elif start > 0:
        notes.append("removed %d lines of web-page menu text" % start)
    lines = lines[start:]
    # 2) drop footers, image names, 'Table of Contents' lines
    kept, removed = [], 0
    for l in lines:
        if FOOTER_RE.match(l) or IMAGE_RE.match(l) or l.strip().lower() == "table of contents":
            removed += 1
            continue
        kept.append(l)
    # 3) tables: tab-separated rows -> 'a | b | c'; glue a row label that was split from its numbers
    merged = []
    i = 0
    while i < len(kept):
        l = kept[i].rstrip()
        nxt = kept[i + 1] if i + 1 < len(kept) else ""
        if ("\t" not in l and l.strip() and len(l.strip()) <= 60 and not l.strip().endswith((".", ":", ";", "?"))
                and "\t" in nxt and NUMBERISH.match(nxt.replace("\t", " ").strip() or "x")
                and not ITEM_RE.match(l)):
            merged.append(format_table_line(l + "\t" + nxt))
            i += 2
            continue
        merged.append(format_table_line(l) if "\t" in l else re.sub(r"[ ]{2,}", " ", l).strip())
        i += 1
    # 4) collapse blank lines
    out, blank = [], 0
    for l in merged:
        if not l.strip():
            blank += 1
            if blank > 1:
                continue
            out.append("")
        else:
            blank = 0
            out.append(l)
    return "\n".join(out).strip() + "\n", notes, removed


# --------------------------------------------------------------------------- sections
def split_sections(clean_text: str):
    """Return [(section_label, [lines])]. Everything before the first real heading (cover page and
    table of contents) is skipped: it holds no useful content."""
    sections, current, label, part = [], None, None, ""
    for line in clean_text.split("\n"):
        pm = PART_RE.match(line)
        if pm and "|" not in line:
            part = "Part %s" % pm.group(1)
            continue  # a part marker is a heading, not content
        im = ITEM_RE.match(line)
        if im and "|" not in line and len(im.group(2)) < 140:
            label = "%sItem %s. %s" % (part + ", " if part else "", im.group(1).upper(), im.group(2).strip().rstrip("."))
            current = []
            sections.append((label, current))
            continue
        if current is not None and line.strip():
            current.append(line.strip())
    return sections


# --------------------------------------------------------------------------- chunking
def split_long(par: str, limit: int):
    if len(par) <= limit:
        return [par]
    parts, cur = [], ""
    for s in re.split(r"(?<=[.!?])\s+", par):
        if cur and len(cur) + len(s) + 1 > limit:
            parts.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        parts.append(cur)
    return parts


def chunk_section(lines, size, overlap_chars=200):
    """Group paragraphs into chunks of about `size` characters. Each chunk starts with the last
    ~200 characters of the previous one (overlap) so an idea cut at the boundary is still found."""
    paras = []
    for l in lines:
        paras += split_long(l, int(size * 1.6))
    chunks, cur, new_len, tail = [], [], 0, ""
    for p in paras:
        if cur and new_len + len(p) + 1 > size:
            chunks.append("\n".join(([tail] if tail else []) + cur))
            last = cur[-1]
            tail = last
            if len(last) > overlap_chars:
                tail = last[-overlap_chars:]
                tail = tail[tail.find(" ") + 1:] if " " in tail else tail
            cur, new_len = [], 0
        cur.append(p)
        new_len += len(p) + 1
    if cur:
        body = "\n".join(cur)
        if chunks and len(body) < 150 and len(chunks[-1]) + len(body) < size * 1.6:
            chunks[-1] += "\n" + body  # do not leave a tiny orphan chunk
        else:
            chunks.append((tail + "\n" if tail else "") + body)
    return chunks


def main():
    ap = argparse.ArgumentParser(description="Clean and chunk Apple filings for search.")
    ap.add_argument("--text", nargs="+", default=["text"], help="One or more folders with the .txt files (sub-folders are searched too)")
    ap.add_argument("--out", default="prepared_text", help="Output folder")
    ap.add_argument("--chunk-size", type=int, default=1000, help="Target characters per chunk (default 1000)")
    args = ap.parse_args()

    out = Path(args.out)
    out_abs = out.resolve()
    # read every given folder; never read our own output folder (it holds cleaned copies with the same names)
    files = sorted({p for folder in args.text for p in Path(folder).rglob("*.txt")
                    if doc_info(p) and out_abs not in p.resolve().parents}, key=lambda p: (doc_info(p)["filing_date"], p.name))
    if not files:
        sys.exit("No files named like 10-K_2022-10-28.txt or 10-Q_2026-07-31.txt found in: %s" % ", ".join(args.text))
    (out / "clean").mkdir(parents=True, exist_ok=True)
    all_chunks, report = [], []
    for p in files:
        info = doc_info(p)
        raw = p.read_text(encoding="utf-8", errors="replace")
        warnings = []
        if len(raw) < 50_000:
            warnings.append("file is only %d KB - a full report is normally over 100 KB; was it copied completely?" % (len(raw) // 1000))
        text, notes, removed = clean(raw)
        (out / "clean" / p.name).write_text(text, encoding="utf-8")
        secs = split_sections(text)
        labels = [s[0] for s in secs]
        want = "Item 7" if info["doc_type"] == "10-K" else "Item 2"
        if not any(l.split("Item", 1)[-1].strip().startswith(want.split()[1] + ".") and "Management" in l for l in labels):
            warnings.append("MD&A section ('%s. Management's Discussion...') not found" % want)
        n = 0
        for label, lines in secs:
            for k, body in enumerate(chunk_section(lines, args.chunk_size)):
                n += 1
                cid = "%s_%s_%03d" % (info["doc_type"], info["filing_date"], n)
                when = "annual report" if info["fiscal_period"] == "FY" else info["fiscal_period"]
                header = "Apple %s FY%d %s | filed %s | %s" % (info["doc_type"], info["fiscal_year"], when, info["filing_date"], label)
                all_chunks.append({"chunk_id": cid, **info, "section": label, "source_file": p.name,
                                   "text": body, "text_for_embedding": header + "\n" + body, "n_chars": len(body)})
        report.append({"file": p.name, "doc_type": info["doc_type"], "fiscal_year": info["fiscal_year"],
                       "fiscal_period": info["fiscal_period"], "kb_original": len(raw) // 1000, "kb_clean": len(text) // 1000,
                       "sections_found": len(secs), "chunks": n, "removed_lines": removed,
                       "notes": "; ".join(notes), "warnings": "; ".join(warnings)})
        flag = "  !! " + "; ".join(warnings) if warnings else ""
        print("%-26s %4d KB -> %4d KB | %2d sections | %4d chunks%s" % (p.name, len(raw) // 1000, len(text) // 1000, len(secs), n, flag))

    with open(out / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    with open(out / "chunks.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["chunk_id", "doc_type", "filing_date", "fiscal_year", "fiscal_period", "section",
                                          "source_file", "n_chars", "text"], extrasaction="ignore")
        w.writeheader()
        w.writerows(all_chunks)
    with open(out / "text_report.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(report[0].keys()))
        w.writeheader()
        w.writerows(report)
    sizes = sorted(c["n_chars"] for c in all_chunks)
    print("\n%d documents -> %d chunks (median %d characters, shortest %d, longest %d)" % (
        len(files), len(all_chunks), sizes[len(sizes) // 2], sizes[0], sizes[-1]))
    print("Written to %s" % out.resolve())
    if any(r["warnings"] for r in report):
        print("Some documents have warnings - see text_report.csv")


if __name__ == "__main__":
    main()
