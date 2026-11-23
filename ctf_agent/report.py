"""Turns a results folder into report.md and report.pdf, optionally compared with student data.

Student baseline CSV columns (header required, only `challenge` is mandatory):
    challenge, student_median_seconds, student_mean_attempts, student_solve_rate
Challenge names must match the CTFd challenge names exactly.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Optional


def fmt_secs(s) -> str:
    if s in (None, ""):
        return "-"
    s = float(s)
    if s < 60:
        return f"{s:.0f}s"
    m, sec = divmod(int(round(s)), 60)
    return f"{m}m {sec:02d}s" if m < 60 else f"{m // 60}h {m % 60:02d}m"


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_results(run_dir: Path) -> tuple[dict, list[dict]]:
    data = json.loads((Path(run_dir) / "results.json").read_text())
    return data["meta"], data["results"]


def load_baseline(path: Optional[Path]) -> dict[str, dict]:
    if not path or not Path(path).is_file():
        return {}
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("challenge") or "").strip()
            if name:
                out[name] = {"median": _num(row.get("student_median_seconds")),
                             "attempts": _num(row.get("student_mean_attempts")),
                             "solve_rate": _num(row.get("student_solve_rate"))}
    return out


def build_table(rows: list[dict], baseline: dict[str, dict]) -> tuple[list[str], list[list[str]]]:
    header = ["Challenge", "Solved", "Agent time", "Agent attempts"]
    if baseline:
        header += ["Student median", "Student attempts", "Student solve rate", "Agent speed vs student"]
    body = []
    for r in rows:
        line = [r["challenge"], "yes" if r["solved"] else f"no ({r['outcome']})",
                fmt_secs(r["seconds"]), str(r["attempts"])]
        if baseline:
            b = baseline.get(r["challenge"], {})
            med = b.get("median")
            ratio = "-"
            if med and r["solved"] and float(r["seconds"]) > 0:
                x = med / float(r["seconds"])
                ratio = f"{x:.1f}x faster" if x >= 1 else f"{1 / x:.1f}x slower"
            line += [fmt_secs(med), "-" if b.get("attempts") is None else f"{b['attempts']:.1f}",
                     "-" if b.get("solve_rate") is None else f"{b['solve_rate'] * 100:.0f}%", ratio]
        body.append(line)
    return header, body


def summary_lines(meta: dict, rows: list[dict]) -> list[str]:
    solved = [r for r in rows if r["solved"]]
    tin = sum(int(r["input_tokens"]) for r in rows)
    tout = sum(int(r["output_tokens"]) for r in rows)
    lines = [
        f"Model: {meta.get('model_id')} (Amazon Bedrock, {meta.get('region')})",
        f"Run: {meta.get('run_id')}  |  Started: {meta.get('started')}",
        f"Solved {len(solved)} of {len(rows)} challenges",
        f"Total agent time: {fmt_secs(sum(float(r['seconds']) for r in rows))}  |  "
        f"Submissions used: {sum(int(r['attempts']) for r in rows)}  |  Tokens: {tin:,} in / {tout:,} out",
    ]
    if solved:
        lines.append(f"Median time to solve: {fmt_secs(sorted(float(r['seconds']) for r in solved)[len(solved) // 2])}")
    return lines


def to_markdown(meta: dict, rows: list[dict], baseline: dict[str, dict], example: bool = False) -> str:
    header, body = build_table(rows, baseline)
    out = ["# CTF AI Agent Benchmark", ""]
    out += [f"- {l}" for l in summary_lines(meta, rows)]
    out += ["", "| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    if example:
        out += ["", "> Student columns use EXAMPLE placeholder data. Replace data/student_baseline.csv "
                    "with real numbers before presenting."]
    out += ["", "Agent time is wall-clock from the first model call to the correct flag, including "
                "tool execution. Attempts are flag submissions."]
    return "\n".join(out) + "\n"


def to_pdf(path: Path, meta: dict, rows: list[dict], baseline: dict[str, dict],
           example: bool = False) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    cell = styles["BodyText"].clone("cell", fontSize=8.5, leading=10.5)
    head = styles["BodyText"].clone("head", fontSize=8.5, leading=10.5, textColor=colors.white,
                                    fontName="Helvetica-Bold")
    doc = SimpleDocTemplate(str(path), pagesize=landscape(letter), leftMargin=0.6 * inch,
                            rightMargin=0.6 * inch, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                            title="CTF AI Agent Benchmark")
    story = [Paragraph("CTF AI Agent Benchmark", styles["Title"])]
    for line in summary_lines(meta, rows):
        story.append(Paragraph(line.replace("&", "&amp;"), styles["BodyText"]))
    story.append(Spacer(1, 12))

    header, body = build_table(rows, baseline)
    data = [[Paragraph(h, head) for h in header]] + [[Paragraph(c.replace("&", "&amp;"), cell) for c in r]
                                                     for r in body]
    first = 2.4 * inch
    rest = (doc.width - first) / (len(header) - 1)
    t = Table(data, colWidths=[first] + [rest] * (len(header) - 1), repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef2f7")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b8c2d0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))
    if example:
        story.append(Paragraph("<b>Student columns use EXAMPLE placeholder data.</b> Replace "
                               "data/student_baseline.csv with real numbers before presenting.",
                               styles["BodyText"]))
    story.append(Paragraph("Agent time is wall-clock from the first model call to the correct flag, "
                           "including tool execution. Attempts are flag submissions.", styles["BodyText"]))
    doc.build(story)


def generate(run_dir: Path, baseline_path: Optional[Path] = None) -> tuple[Path, Path]:
    meta, rows = load_results(run_dir)
    baseline = load_baseline(baseline_path)
    example = bool(baseline_path) and "example" in Path(baseline_path).name.lower()
    md, pdf = Path(run_dir) / "report.md", Path(run_dir) / "report.pdf"
    md.write_text(to_markdown(meta, rows, baseline, example))
    to_pdf(pdf, meta, rows, baseline, example)
    return md, pdf
