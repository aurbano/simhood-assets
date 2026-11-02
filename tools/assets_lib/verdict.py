"""The check results as commit statuses and as the comment's verdict section (D10)."""

from __future__ import annotations

from typing import Any

from .comment import md

MAX_ROWS = 30
MAX_LINES = 40
DCO_CONTEXT = "assets/dco"
VALIDATE_CONTEXT = "assets/validate"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def dco_status(dco: dict | None) -> tuple[str, str]:
    """(state, description) of the `assets/dco` status; a missing result is an error."""
    if dco is None:
        return "error", "the sign-off check did not finish; see the run"
    return ("success" if dco["ok"] else "failure"), dco["description"][:140]


def validate_status(report: dict | None) -> tuple[str, str]:
    if report is None:
        return "error", "the validator did not finish; see the run"
    errors, warnings = report["errors"], report["warnings"]
    if report["ok"]:
        return "success", f"{_plural(len(report['models']), 'model')} checked, {_plural(len(warnings), 'warning')}"[:140]
    first = errors[0]
    where = first.get("path") or "the pull request"
    return "failure", f"{_plural(len(errors), 'problem')}; first: {where}: {first['message']}"[:140]


def affected_ids(report: dict) -> list[str]:
    """Model ids the pictures may be shown for: affected and still present (a deleted model has no picture)."""
    return sorted({m["id"] for m in report["models"] if m.get("change") != "deleted"})


def _ai(m: dict) -> str:
    value = m.get("ai_generated")
    if value == "yes":
        parts = [p for p in (m.get("ai_tool"), m.get("ai_model")) if p]
        return "AI-generated" + (f" with {md(' / '.join(parts), 80)}" if parts else "")
    if value == "no":
        return "no AI"
    return "not stated"


def _dims(size: dict | None) -> str:
    if not size:
        return "—"
    return " × ".join(f"{size[k]:g}" for k in ("length", "width", "height") if size.get(k) is not None)


def _model_rows(report: dict, slots: dict[str, Any] | None) -> list[str]:
    errors_by: dict[str, int] = {}
    warns_by: dict[str, int] = {}
    for key, store in (("errors", errors_by), ("warnings", warns_by)):
        for f in report[key]:
            if f.get("model"):
                store[f["model"]] = store.get(f["model"], 0) + 1
    lines = [
        "| Model | Change | Slot | Triangles | Size in metres (slot wants) | Licence | AI | Findings |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m in report["models"][:MAX_ROWS]:
        slot = (slots or {}).get(m.get("slot") or "")
        wants = f" ({_dims(slot.size)})" if slot is not None else ""
        if m.get("triangles") is None:
            tri = "—"
        else:
            tri = f"{m['triangles']:,} of {m['budget']:,}" if m.get("budget") else f"{m['triangles']:,}"
        e, w = errors_by.get(m["id"], 0), warns_by.get(m["id"], 0)
        notes = ", ".join(x for x in (_plural(e, "problem") if e else "", _plural(w, "warning") if w else "") if x) or "none"
        change = m.get("change", "")
        if m.get("renamed_from"):
            change += f" (from {md(m['renamed_from'], 80)})"
        lines.append(
            f"| {md(m['id'])} | {md(change)} | {md(m.get('slot') or '—')} | {tri} | {_dims(m.get('size_m'))}{wants} "
            f"| {md(m.get('licence') or '—')} | {_ai(m)} | {notes} |"
        )
    if len(report["models"]) > MAX_ROWS:
        lines.append("")
        lines.append(f"_{len(report['models']) - MAX_ROWS} more models are in the full report (the `verdict` artifact of the run)._")
    return lines


def _findings(title: str, items: list[dict]) -> list[str]:
    if not items:
        return []
    out = ["", f"**{title}**", ""]
    for f in items[:MAX_LINES]:
        out.append(f"- {md(f.get('path') or 'pull request')}: {md(f['message'], 400)}")
    if len(items) > MAX_LINES:
        out.append(f"- …and {len(items) - MAX_LINES} more in the full report")
    return out


def _slot_lines(report: dict, slots: dict[str, Any] | None) -> list[str]:
    """The catalogue status line a contributor changes when a model fills a slot."""
    status = {s["id"]: s["status"] for s in report.get("slots", [])}
    seen: set[str] = set()
    out = []
    for m in report["models"]:
        sid = m.get("slot")
        if m.get("change") in (None, "deleted") or not sid or sid in seen:
            continue
        seen.add(sid)
        if status.get(sid) == "missing":
            slot = (slots or {}).get(sid)
            where = f"`{md(slot.file, 120)}`" if slot is not None and slot.file else "its file in `catalogue/`"
            out.append(
                f"- Slot {md(sid)} is still marked `missing`. In {where}, change its `status = \"missing\"` line to `status = \"have\"` "
                "(or `\"stand-in\"` if the model is only a placeholder). That is the one catalogue change a contribution makes."
            )
    return out


def verdict_section(
    report: dict | None, dco: dict | None, head_sha: str, run_url: str, slots: dict[str, Any] | None = None
) -> str:
    sha7 = head_sha[:7]
    out = [f"**Checked commit `{sha7}`** ([run details]({run_url}))", ""]
    d_state, d_desc = dco_status(dco)
    v_state, v_desc = validate_status(report)
    words = {"success": "Passed", "failure": "Needs changes", "error": "Did not finish"}
    out += [
        "| Check | Result |", "|---|---|",
        f"| Sign-off (`assets/dco`) | {words[d_state]}: {md(d_desc, 140)} |",
        f"| Validation (`assets/validate`) | {words[v_state]}: {md(v_desc, 140)} |", "",
    ]
    if dco is not None and not dco["ok"]:
        out += ["**Sign-off.** Every commit needs a `Signed-off-by` line with the email you committed with."]
        if dco.get("refused"):
            out += ["", md(dco["refused"], 400), ""]
        else:
            out += ["These commits do not have one:", ""]
            out += [f"- `{f['sha'][:7]}` ({md(f['subject'], 120)}): {md(f['reason'], 300)}" for f in dco["failures"][:MAX_LINES]]
            out += ["", "To fix the last commit run `git commit --amend -s`; for several, `git rebase --signoff <base branch>`; then force-push the branch.", ""]
    if report is None:
        out += ["The validator did not produce a report. The run details above say why; the maintainers will take a look.", ""]
        return "\n".join(out)
    if report["models"]:
        out += ["**Models in this pull request**", ""] + _model_rows(report, slots)
    else:
        out += ["No model files are affected by this pull request."]
    out += _findings("Problems to fix", report["errors"]) + _findings("Warnings", report["warnings"])
    slot_lines = _slot_lines(report, slots)
    if slot_lines:
        out += ["", "**Slot status**", ""] + slot_lines
    if report["ok"] and dco is not None and dco["ok"]:
        out += ["", "Everything checks out. A maintainer will review it next."]
    else:
        out += ["", "Push a fix to this branch and the checks run again; this comment updates on its own."]
    return "\n".join(out)
