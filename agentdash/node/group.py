"""Private group roster and evidence-backed progress reviews, held on the PA node."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

STATES = {"on_track", "attention", "unknown"}


def roster(pa: Path, org: Path) -> list[dict]:
    members = yaml.safe_load((pa / "configs/group_roster.yaml").read_text())["members"]
    rows = [dict(m) for m in members]
    known = {m.get("org", m["slug"]) for m in rows}
    main = org / "main.org"
    if main.exists():
        personnel = main.read_text().split("* Projects", 1)[0].split("*** Alumni", 1)[0]
        role = ""
        for line in personnel.splitlines():
            if line.startswith("**"):
                role = line.lstrip("* ").strip()
            match = re.search(r"\[\[file:([\w-]+)\.org\]\[([^\]]+)\]\]", line)
            if line.startswith("- ") and match and match[1] not in known:
                rows.append(
                    {
                        "slug": match[1],
                        "org": match[1],
                        "name": match[2],
                        "role": role,
                        "expected": False,
                    }
                )
                known.add(match[1])
    return rows


def evidence(pa: Path, org: Path, member: dict) -> dict:
    path = org / (member.get("org", member["slug"]) + ".org")
    notes = path.read_text() if path.is_file() else ""
    reports = []
    for summary in sorted((pa / "data/weekly_reports").glob("*/summary.json"))[-4:]:
        data = json.loads(summary.read_text())
        rec = data.get("members", {}).get(member["slug"])
        if rec:
            rec = dict(rec)
            report = summary.parent / (member["slug"] + ".md")
            if rec.get("status") == "received" and not rec.get("report") and report.exists():
                rec["report"] = report.read_text()
            reports.append(
                {"week": summary.parent.name, "checked_at": data.get("checked_at"), **rec}
            )
    return {
        "name": member["name"],
        "role": member.get("role", ""),
        "org_source": str(path),
        "org_notes": notes,
        "reports": reports,
    }


def fingerprint(context: dict) -> str:
    return hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()


def review_path(pa: Path, slug: str) -> Path:
    if not re.fullmatch(r"[\w-]+", slug):
        raise ValueError("Invalid member")
    return pa / "data/group_reviews" / (slug + ".json")


def show(pa: Path, org: Path) -> dict:
    rows = []
    for m in roster(pa, org):
        context = evidence(pa, org, m)
        file = review_path(pa, m["slug"])
        review = json.loads(file.read_text()) if file.exists() else None
        stale = bool(
            review
            and (
                review.get("fingerprint") != fingerprint(context)
                or (datetime.now(UTC) - datetime.fromisoformat(review["reviewed_at"])).days >= 14
            )
        )
        rows.append(
            {
                "slug": m["slug"],
                "name": m["name"],
                "role": m.get("role", ""),
                "state": review["state"] if review and not stale else "unknown",
                "review": review,
                "stale": stale,
                "has_notes": bool(context["org_notes"]),
                "org_source": context["org_source"],
                "latest_report": {
                    k: context["reports"][-1].get(k) for k in ("week", "status", "checked_at")
                }
                if context["reports"]
                else None,
            }
        )
    return {"ok": True, "members": rows}


def save(pa: Path, slug: str, text: str, context: dict) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    review = json.loads(text)
    if not isinstance(review, dict):
        raise ValueError("Review must be a JSON object")
    if review.get("state") not in STATES or not isinstance(review.get("reason"), str):
        raise ValueError("Model returned an invalid progress review")
    if not isinstance(review.get("evidence"), list) or not all(
        isinstance(x, str) for x in review["evidence"]
    ):
        raise ValueError("Review must contain evidence references")
    if review["state"] != "unknown" and not review["evidence"]:
        raise ValueError("A progress assessment requires supporting evidence")
    sources = context["org_notes"] + json.dumps(context["reports"], ensure_ascii=False)
    if any(not ref.strip() or ref not in sources for ref in review["evidence"]):
        raise ValueError("Review contains an unrecognized evidence reference")
    review = {k: review[k] for k in ("state", "reason", "evidence")}
    review.update(reviewed_at=datetime.now(UTC).isoformat(), fingerprint=fingerprint(context))
    file = review_path(pa, slug)
    file.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace; concurrent requests never expose a partial assessment.
    with tempfile.NamedTemporaryFile(mode="w", dir=file.parent, delete=False) as f:
        json.dump(review, f)
        temp = Path(f.name)
    temp.replace(file)
    return review
