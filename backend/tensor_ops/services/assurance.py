"""Type II assurance: control tests -> exceptions, milestones, and spreadsheet imports (gap assessments, test logs).

Imports never change a control's workflow status or approve anything: a client self-assessment is recorded as
`self_assessment` next to Tensor's evidence-based status, and applicability stays an expert decision.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..models import (
    AuditException,
    Control,
    ControlMapping,
    ControlTest,
    Engagement,
    EngagementControl,
    Framework,
    FrameworkRequirement,
    Milestone,
    RemediationTask,
)

DEFAULT_MILESTONES = [
    (0, "Kick-off and scoping complete"), (2, "Policy set approved by client"), (4, "50% of controls evidenced"),
    (6, "All evidence collected"), (8, "Expert sign-off complete (audit-ready)"), (10, "Auditor fieldwork"),
    (14, "Report issued by the independent auditor"),
]
IMPACT = {"fail": "May be reported as a deviation in the SOC 2 report",
          "pass_with_exception": "Monitor: disclosure risk if unresolved"}


def create_default_milestones(s: Session, eng: Engagement) -> None:
    start = eng.start_date or date.today()
    for i, (wk, title) in enumerate(DEFAULT_MILESTONES):
        s.add(Milestone(tenant_id=eng.tenant_id, engagement_id=eng.id, title=title,
                        planned_date=start + timedelta(weeks=wk), ordinal=i))


def _next_code(s: Session, eng_id: uuid.UUID) -> str:
    n = s.execute(select(func.count()).select_from(AuditException).where(AuditException.engagement_id == eng_id)).scalar_one()
    return f"EXC-{n + 1:03d}"


def classify_result(samples: int, deviations: int, given: str | None) -> str:
    if given:
        return given
    if deviations <= 0:
        return "pass"
    return "pass_with_exception" if samples and deviations / samples <= 0.1 else "fail"


def severity_for(result: str, risk: str) -> str:
    if result == "fail":
        return "critical" if risk == "high" else "high"
    return "high" if risk == "high" else "medium" if risk == "medium" else "low"


def record_test(s: Session, ec: EngagementControl, *, test_date: date, samples: int, deviations: int, result: str | None,
                population: int | None = None, procedure: str = "", notes: str = "", tester_id: uuid.UUID | None = None,
                actor_id: uuid.UUID | None = None, owner_name: str = "") -> tuple[ControlTest, AuditException | None]:
    if samples < 0 or deviations < 0 or deviations > samples:
        raise ValueError("deviations must be between 0 and samples tested")
    res = classify_result(samples, deviations, result)
    if res not in ("pass", "pass_with_exception", "fail"):
        raise ValueError("result must be pass, pass_with_exception or fail")
    t = ControlTest(tenant_id=ec.tenant_id, engagement_id=ec.engagement_id, engagement_control_id=ec.id, test_date=test_date,
                    population=population, samples_tested=samples, deviations=deviations, result=res, procedure=procedure,
                    notes=notes, tester_id=tester_id)
    s.add(t)
    s.flush()
    exc = None
    if res != "pass":
        ctl = s.get(Control, ec.control_id)
        desc = f"{ctl.title if ctl else 'Control'}: {deviations} of {samples} samples deviated from the control requirement " \
               f"({'Fail' if res == 'fail' else 'Pass with exception'})."
        task = RemediationTask(tenant_id=ec.tenant_id, engagement_id=ec.engagement_id, engagement_control_id=ec.id,
                               title=f"Remediate test exception: {ctl.code if ctl else ''} ({deviations}/{samples} deviations)"[:300],
                               description=desc, severity="high" if res == "fail" else "medium",
                               due_date=test_date + timedelta(days=30))
        s.add(task)
        s.flush()
        exc = AuditException(tenant_id=ec.tenant_id, engagement_id=ec.engagement_id, engagement_control_id=ec.id, test_id=t.id,
                             code=_next_code(s, ec.engagement_id), description=desc, severity=severity_for(res, ec.risk_rating),
                             opened_on=test_date, target_date=test_date + timedelta(days=30),
                             owner_name=owner_name or ec.owner_name, audit_impact=IMPACT[res], remediation_task_id=task.id)
        s.add(exc)
        s.flush()
    audit.record(s, "control.tested", tenant_id=ec.tenant_id, actor_id=actor_id, target_type="engagement_control",
                 target_id=ec.id, result=res, samples=samples, deviations=deviations)
    return t, exc


# ------------------------------------------------------------------ spreadsheet import
def _norm(h: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


ALIASES = {
    "ref": ["standardref", "ref", "reference", "controlref", "controlid", "control", "clause", "requirementid", "id",
            "annexaref", "tscref", "criteria", "criterion", "controlreference", "controlno", "controlnumber"],
    "question": ["assessmentquestion", "requirement", "question", "description", "controldescription", "controlname",
                 "title", "controltitle", "requirementtext"],
    "compliance": ["compliance", "status", "compliancestatus", "assessment", "implementationstatus", "implemented",
                   "currentstate", "rating", "compliant"],
    "owner": ["owner", "controlowner", "responsible", "responsibleparty", "remediationowner"],
    "priority": ["priority", "risk", "riskrating", "criticality", "risklevel"],
    "notes": ["notes", "comments", "remarks", "observations", "finding", "findings", "gapdescription"],
    "evidence": ["evidence", "evidencereference", "artifacts", "artefacts", "evidencelink"],
    "category": ["category", "domain", "theme"],
    "section": ["section", "family"],
    "test_date": ["testdate", "datetested", "date", "testedon"],
    "samples": ["samples", "samplestested", "samplesize", "sample"],
    "deviations": ["deviations", "exceptions", "failures", "deviationsfound"],
    "result": ["result", "testresult", "outcome", "conclusion"],
    "population": ["population", "populationsize"],
    "procedure": ["procedure", "testprocedure", "testperformed"],
}
COMPLIANCE = {
    "fully": ["fully", "fullycompliant", "compliant", "yes", "y", "implemented", "effective", "met", "inplace", "full", "complete"],
    "partially": ["partially", "partiallycompliant", "partial", "inprogress", "partlyimplemented", "partiallymet", "somewhat"],
    "not": ["not", "notcompliant", "noncompliant", "no", "n", "notimplemented", "missing", "gap", "ineffective", "notmet",
            "absent", "none"],
    "na": ["na", "notapplicable", "excluded", "outofscope"],
}
RESULTS = {"pass": ["pass", "passed", "effective", "noexceptions", "ok"],
           "pass_with_exception": ["passwithexception", "passwithexceptions", "exception", "exceptions", "qualified"],
           "fail": ["fail", "failed", "ineffective", "deficient"]}
PRIORITY = {"high": ["high", "critical", "p1", "h", "veryhigh"], "medium": ["medium", "med", "moderate", "p2", "m"],
            "low": ["low", "p3", "l", "verylow", "minor"]}
NOT_ASSESSED = {"", "notassessed", "tbd", "pending", "unknown"}


def _lookup(table: dict[str, list[str]], v: object) -> str | None:
    n = _norm(v)
    return next((k for k, vals in table.items() if n in vals), None)


def read_table(filename: str, data: bytes) -> tuple[str, list[list[Any]]]:
    """Return (sheet_name, rows). For workbooks, choose the sheet whose header row matches the most aliases."""
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        best: tuple[int, str, list[list[Any]]] = (-1, "", [])
        for ws in wb.worksheets[:30]:
            rows = [list(r) for r in ws.iter_rows(max_row=3000, max_col=40, values_only=True)]
            score = max((_header_score(r) for r in rows[:20]), default=0)
            if score > best[0]:
                best = (score, ws.title, rows)
        return best[1], best[2]
    if name.endswith(".csv") or name.endswith(".txt"):
        text = data.decode("utf-8-sig", errors="replace")
        return "csv", [r for r in csv.reader(io.StringIO(text))][:3000]
    raise ValueError("upload .xlsx or .csv")


def _header_score(row: list[Any]) -> int:
    cells = {_norm(c) for c in row if c is not None}
    return sum(1 for al in ALIASES.values() if cells & set(al))


def _header(rows: list[list[Any]]) -> tuple[int, dict[str, int]]:
    best_i, best = -1, {}
    for i, r in enumerate(rows[:20]):
        found: dict[str, int] = {}
        for j, c in enumerate(r):
            n = _norm(c)
            for key, al in ALIASES.items():
                if n in al and key not in found:
                    found[key] = j
        if len(found) > len(best):
            best_i, best = i, found
    return best_i, best


def _ref_index(s: Session, eng: Engagement) -> tuple[dict[str, list[EngagementControl]], dict[str, EngagementControl]]:
    ecs = s.execute(select(EngagementControl, Control).join(Control, Control.id == EngagementControl.control_id)
                    .where(EngagementControl.engagement_id == eng.id)).all()
    by_code = {_norm(c.code): ec for ec, c in ecs}
    by_cid = {c.id: ec for ec, c in ecs}
    idx: dict[str, list[EngagementControl]] = defaultdict(list)
    for cid, fw, ref in s.execute(select(ControlMapping.control_id, Framework.code, FrameworkRequirement.ref)
                                  .join(FrameworkRequirement, FrameworkRequirement.id == ControlMapping.requirement_id)
                                  .join(Framework, Framework.id == FrameworkRequirement.framework_id)
                                  .where(Framework.code.in_(eng.frameworks))).all():
        if cid in by_cid:
            idx[_norm(ref)].append(by_cid[cid])
            if fw == "ISO27001" and ref.startswith("A."):
                idx[_norm(ref[2:])].append(by_cid[cid])  # "5.15" written without the "A."
    return idx, by_code


def _parse_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            continue
    return None


def import_sheet(s: Session, eng: Engagement, filename: str, data: bytes, *, kind: str, apply: bool,
                 create_tasks: bool, actor_id: uuid.UUID) -> dict[str, Any]:
    sheet, rows = read_table(filename, data)
    hi, cols = _header(rows)
    log: dict[str, Any] = {"sheet": sheet, "header_row": hi + 1, "columns": {k: v + 1 for k, v in cols.items()},
                           "rows_read": 0, "rows_used": 0, "skipped_not_assessed": 0, "unmatched": [], "duplicates": [],
                           "invalid": [], "warnings": []}
    need = ["ref", "compliance"] if kind == "assessment" else ["ref"]
    missing = [k for k in need if k not in cols]
    if hi < 0 or missing:
        log["errors"] = [f"Could not find a column for: {', '.join(missing or need)}. "
                         f"Expected headers like 'Standard Ref' and 'Compliance' (case and spacing don't matter)."]
        return log
    idx, by_code = _ref_index(s, eng)
    seen: Counter = Counter()
    per_ec: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    ec_obj: dict[uuid.UUID, EngagementControl] = {}
    tests: list[tuple[EngagementControl, dict[str, Any]]] = []

    def cell(r: list[Any], key: str) -> Any:
        j = cols.get(key)
        return r[j] if j is not None and j < len(r) else None

    for n, r in enumerate(rows[hi + 1:], start=hi + 2):
        if not any(c not in (None, "") for c in r):
            continue
        log["rows_read"] += 1
        ref = str(cell(r, "ref") or "").strip()
        if not ref:
            log["invalid"].append({"row": n, "issue": "blank reference"})
            continue
        seen[_norm(ref)] += 1
        if seen[_norm(ref)] == 2:
            log["duplicates"].append(ref)
        targets = [by_code[_norm(ref)]] if _norm(ref) in by_code else idx.get(_norm(ref), [])
        if not targets:
            log["unmatched"].append(ref)
            continue
        if kind == "assessment":
            raw = cell(r, "compliance")
            if _norm(raw) in NOT_ASSESSED:
                log["skipped_not_assessed"] += 1
                continue
            val = _lookup(COMPLIANCE, raw)
            if val is None:
                log["invalid"].append({"row": n, "issue": f"unrecognised compliance value '{raw}'"})
                continue
            rec = {"value": val, "owner": str(cell(r, "owner") or "").strip()[:200],
                   "priority": _lookup(PRIORITY, cell(r, "priority")), "notes": str(cell(r, "notes") or "").strip()[:1000],
                   "evidence": str(cell(r, "evidence") or "").strip()[:300], "ref": ref}
            for ec in targets:
                per_ec[ec.id].append(rec)
                ec_obj[ec.id] = ec
        else:
            try:
                samples = int(float(cell(r, "samples") or 0))
                devs = int(float(cell(r, "deviations") or 0))
            except ValueError:
                log["invalid"].append({"row": n, "issue": "samples/deviations must be numbers"})
                continue
            given = _lookup(RESULTS, cell(r, "result")) if cell(r, "result") not in (None, "") else None
            if cell(r, "result") not in (None, "") and given is None:
                log["invalid"].append({"row": n, "issue": f"unrecognised result '{cell(r, 'result')}'"})
                continue
            td = _parse_date(cell(r, "test_date")) or date.today()
            if devs > samples:
                log["invalid"].append({"row": n, "issue": "more deviations than samples"})
                continue
            for ec in targets[:1]:
                tests.append((ec, {"test_date": td, "samples": samples, "deviations": devs, "result": given,
                                   "population": int(float(cell(r, "population"))) if cell(r, "population") not in (None, "") else None,
                                   "procedure": str(cell(r, "procedure") or "")[:2000], "notes": str(cell(r, "notes") or "")[:2000],
                                   "owner": str(cell(r, "owner") or "")[:200]}))
        log["rows_used"] += 1
    rank = {"not": 3, "partially": 2, "fully": 1, "na": 0}
    ctl_of = {ec.id: s.get(Control, ec.control_id) for ec in list(ec_obj.values()) + [e for e, _ in tests]}
    preview = []
    if kind == "assessment":
        for ec_id, recs in per_ec.items():
            worst = max(recs, key=lambda x: rank[x["value"]])["value"]
            if len({x["value"] for x in recs}) > 1:
                log["warnings"].append(f"{len(recs)} rows map to one control; kept the weakest answer ({worst}).")
            c = ctl_of.get(ec_id)
            preview.append({"engagement_control_id": str(ec_id), "control": c.code if c else "", "title": c.title if c else "",
                            "self_assessment": worst,
                            "owner": next((x["owner"] for x in recs if x["owner"]), ""),
                            "priority": next((x["priority"] for x in recs if x["priority"]), None),
                            "refs": sorted({x["ref"] for x in recs})})
    else:
        preview = [{"engagement_control_id": str(ec.id), "control": ctl_of[ec.id].code if ctl_of.get(ec.id) else "", **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in d.items()}}
                   for ec, d in tests]
    log["warnings"] = sorted(set(log["warnings"]))[:20]
    log["unmatched"] = sorted(set(log["unmatched"]))[:100]
    log["duplicates"] = sorted(set(log["duplicates"]))[:100]
    log["controls_affected"] = len(preview)
    log["preview"] = preview[:200]
    if not apply:
        return log
    created_tasks = created_tests = created_exc = 0
    if kind == "assessment":
        for p in preview:
            ec = ec_obj[uuid.UUID(p["engagement_control_id"])]  # noqa: B007
            ec.self_assessment = p["self_assessment"]
            if p["owner"]:
                ec.owner_name = p["owner"]
            if p["priority"]:
                ec.risk_rating = p["priority"]
            notes = "; ".join(x["notes"] for x in per_ec[ec.id] if x["notes"])
            if notes:
                ec.assessment_notes = notes[:3000]
            ec.version += 1
            if create_tasks and p["self_assessment"] in ("not", "partially"):
                ctl = s.get(Control, ec.control_id)
                title = f"Close self-assessed gap: {ctl.code if ctl else ''} {ctl.title if ctl else ''}"[:300]
                exists = s.execute(select(RemediationTask.id).where(RemediationTask.engagement_id == eng.id,
                                                                    RemediationTask.title == title)).first()
                if not exists:
                    s.add(RemediationTask(tenant_id=eng.tenant_id, engagement_id=eng.id, engagement_control_id=ec.id,
                                          title=title, description=notes[:2000] or "Gap reported in client self-assessment.",
                                          severity="high" if p["self_assessment"] == "not" else "medium",
                                          due_date=date.today() + timedelta(days=21)))
                    created_tasks += 1
    else:
        for ec, d in tests:
            _, exc = record_test(s, ec, test_date=d["test_date"], samples=d["samples"], deviations=d["deviations"],
                                 result=d["result"], population=d["population"], procedure=d["procedure"], notes=d["notes"],
                                 tester_id=actor_id, actor_id=actor_id, owner_name=d["owner"])
            created_tests += 1
            created_exc += 1 if exc else 0
    log.update(applied=True, remediation_tasks_created=created_tasks, tests_created=created_tests,
               exceptions_created=created_exc)
    audit.record(s, f"import.{kind}", tenant_id=eng.tenant_id, actor_id=actor_id, target_type="engagement", target_id=eng.id,
                 rows=log["rows_used"], controls=len(preview))
    return log
