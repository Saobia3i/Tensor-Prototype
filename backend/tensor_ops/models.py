"""Persistent data model. All tenant-owned tables carry tenant_id and are protected by RLS (see migrations)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def tenant_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow,
                                                 server_default=func.now())


# ---------------------------------------------------------------- global (non-tenant) tables
class Partner(Timestamps, Base):
    """White-label partner (vCISO / MSP) that resells Tensor delivery under its own brand."""
    __tablename__ = "partners"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(200))
    brand_name: Mapped[str] = mapped_column(String(200))
    brand_color: Mapped[str] = mapped_column(String(16), default="#1f2937")
    contact_email: Mapped[str | None] = mapped_column(String(320))
    price_per_client_year: Mapped[float] = mapped_column(Float, default=4500.0)


class User(Timestamps, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    external_subject: Mapped[str | None] = mapped_column(String(255), unique=True)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False)
    is_platform_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("partners.id"))
    expert_title: Mapped[str | None] = mapped_column(String(200))  # shown on sign-offs, e.g. "CISA, Lead Compliance Expert"
    can_sign: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Framework(Timestamps, Base):
    __tablename__ = "frameworks"
    id: Mapped[uuid.UUID] = pk()
    code: Mapped[str] = mapped_column(String(40), unique=True)  # SOC2, ISO27001, ISO42001, PCIDSS
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(40))
    source_url: Mapped[str | None] = mapped_column(String(500))
    license_note: Mapped[str] = mapped_column(Text)


class FrameworkRequirement(Base):
    __tablename__ = "framework_requirements"
    __table_args__ = (UniqueConstraint("framework_id", "ref"),)
    id: Mapped[uuid.UUID] = pk()
    framework_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("frameworks.id", ondelete="CASCADE"), index=True)
    ref: Mapped[str] = mapped_column(String(40))
    paraphrase: Mapped[str] = mapped_column(Text)  # Tensor's own short paraphrase, never normative text


class Control(Timestamps, Base):
    """Tensor's internal control ontology (Tensor-authored wording)."""
    __tablename__ = "controls"
    id: Mapped[uuid.UUID] = pk()
    code: Mapped[str] = mapped_column(String(40), unique=True)  # e.g. TC-AC-01
    domain: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(300))
    objective: Mapped[str] = mapped_column(Text)
    guidance: Mapped[str] = mapped_column(Text)
    evidence_examples: Mapped[list[str]] = mapped_column(JSONB, default=list)
    keywords: Mapped[list[str]] = mapped_column(JSONB, default=list)
    est_hours: Mapped[float] = mapped_column(Float, default=2.0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    snapshot_question: Mapped[str | None] = mapped_column(String(80))  # intake question key used by the Snapshot


class ControlMapping(Base):
    __tablename__ = "control_mappings"
    __table_args__ = (UniqueConstraint("control_id", "requirement_id"),)
    id: Mapped[uuid.UUID] = pk()
    control_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("controls.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("framework_requirements.id", ondelete="CASCADE"))
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | expert_validated
    validated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class Job(Base):
    """Durable job queue. Payload holds IDs only, never document content."""
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "run_after"),)
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|succeeded|failed|dead|cancelled
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- tenant tables
class Tenant(Timestamps, Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")  # prospect | active | offboarded
    partner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("partners.id"), index=True)
    employee_count: Mapped[int | None] = mapped_column(Integer)
    industry: Mapped[str | None] = mapped_column(String(80))
    retention_days: Mapped[int] = mapped_column(Integer, default=730)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)  # Tensor itself as "tenant zero"


class Membership(Timestamps, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id"),)
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    role: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active")


class Engagement(Timestamps, Base):
    __tablename__ = "engagements"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    name: Mapped[str] = mapped_column(String(200))
    service_type: Mapped[str] = mapped_column(String(40), default="audit_ready_year")
    frameworks: Mapped[list[str]] = mapped_column(JSONB, default=list)
    scope: Mapped[str] = mapped_column(Text, default="")
    systems: Mapped[list[str]] = mapped_column(JSONB, default=list)
    start_date: Mapped[date | None] = mapped_column(Date)
    target_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), default="planning")
    delivery_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    signer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    contract_value_usd: Mapped[float] = mapped_column(Float, default=0.0)
    version: Mapped[int] = mapped_column(Integer, default=1)


class EngagementControl(Timestamps, Base):
    """Per-engagement assessment of one internal control."""
    __tablename__ = "engagement_controls"
    __table_args__ = (UniqueConstraint("engagement_id", "control_id"),)
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    engagement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), index=True)
    control_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("controls.id"))
    status: Mapped[str] = mapped_column(String(30), default="not_started")
    applicability_rationale: Mapped[str] = mapped_column(Text, default="")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewer_notes: Mapped[str] = mapped_column(Text, default="")
    next_review_date: Mapped[date | None] = mapped_column(Date)
    owner_name: Mapped[str] = mapped_column(String(200), default="")  # control owner at the client (from assessments)
    risk_rating: Mapped[str] = mapped_column(String(10), default="medium")  # high | medium | low
    self_assessment: Mapped[str | None] = mapped_column(String(20))  # client's own view: fully|partially|not|na
    assessment_notes: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)


class ControlTest(Timestamps, Base):
    """SOC 2 Type II operating-effectiveness test (sample-based)."""
    __tablename__ = "control_tests"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    engagement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), index=True)
    engagement_control_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("engagement_controls.id", ondelete="CASCADE"), index=True)
    test_date: Mapped[date] = mapped_column(Date)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    population: Mapped[int | None] = mapped_column(Integer)
    samples_tested: Mapped[int] = mapped_column(Integer, default=0)
    deviations: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str] = mapped_column(String(30))  # pass | pass_with_exception | fail
    procedure: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    tester_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence_items.id"))


class AuditException(Timestamps, Base):
    """A deviation found in testing that may be reported in the SOC 2 report unless remediated."""
    __tablename__ = "audit_exceptions"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    engagement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), index=True)
    engagement_control_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("engagement_controls.id", ondelete="CASCADE"))
    test_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("control_tests.id", ondelete="SET NULL"))
    code: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10))  # critical | high | medium | low
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | remediated | closed | risk_accepted
    opened_on: Mapped[date] = mapped_column(Date)
    target_date: Mapped[date | None] = mapped_column(Date)
    closed_on: Mapped[date | None] = mapped_column(Date)
    owner_name: Mapped[str] = mapped_column(String(200), default="")
    audit_impact: Mapped[str] = mapped_column(String(300), default="")
    remediation_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("remediation_tasks.id", ondelete="SET NULL"))
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class Milestone(Timestamps, Base):
    __tablename__ = "milestones"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    engagement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    planned_date: Mapped[date] = mapped_column(Date)
    completed_on: Mapped[date | None] = mapped_column(Date)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)


class EvidenceRequest(Timestamps, Base):
    __tablename__ = "evidence_requests"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    engagement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), index=True)
    engagement_control_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("engagement_controls.id"))
    title: Mapped[str] = mapped_column(String(300))
    instructions: Mapped[str] = mapped_column(Text, default="")
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|submitted|accepted|rejected|cancelled


class EvidenceItem(Timestamps, Base):
    __tablename__ = "evidence_items"
    __table_args__ = (Index("ix_evidence_tenant_sha", "tenant_id", "sha256"),)
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    engagement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence_requests.id"))
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, default=uuid.uuid4)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(300))
    original_filename: Mapped[str] = mapped_column(String(300))
    declared_mime: Mapped[str | None] = mapped_column(String(120))
    detected_type: Mapped[str | None] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(80), default="upload")  # upload | connector:github | policy
    source_ref: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="awaiting_upload")
    scan_result: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    expires_at: Mapped[date | None] = mapped_column(Date)
    sensitivity: Mapped[str] = mapped_column(String(20), default="confidential")
    retention_until: Mapped[date | None] = mapped_column(Date)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    upload_token_hash: Mapped[str | None] = mapped_column(String(64))
    upload_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    page_count: Mapped[int | None] = mapped_column(Integer)
    status_version: Mapped[int] = mapped_column(Integer, default=1)


class EvidenceChunk(Base):
    __tablename__ = "evidence_chunks"
    __table_args__ = (Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),)
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence_items.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(40))
    tsv: Mapped[Any] = mapped_column(TSVECTOR, nullable=True)


class EvidenceControlLink(Timestamps, Base):
    __tablename__ = "evidence_control_links"
    __table_args__ = (UniqueConstraint("evidence_id", "engagement_control_id"),)
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence_items.id", ondelete="CASCADE"), index=True)
    engagement_control_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("engagement_controls.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed | approved | rejected
    rationale: Mapped[str] = mapped_column(Text, default="")
    source_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_artifacts.id"))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class AIRun(Base):
    __tablename__ = "ai_runs"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    engagement_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    task_type: Mapped[str] = mapped_column(String(60))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(40))
    schema_version: Mapped[str] = mapped_column(String(40))
    input_refs: Mapped[list[str]] = mapped_column(JSONB, default=list)
    request_id: Mapped[str | None] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20))  # succeeded | invalid_output | failed | over_budget
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIArtifact(Timestamps, Base):
    __tablename__ = "ai_artifacts"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    engagement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), index=True)
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_runs.id"))
    task_type: Mapped[str] = mapped_column(String(60))
    subject_type: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB)  # original model output, never modified
    final_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # reviewer-edited version
    confidence: Mapped[float | None] = mapped_column(Float)
    decision: Mapped[str | None] = mapped_column(String(40))
    review_state: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed|approved|edited|rejected|superseded
    validation_warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_artifacts.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(20))  # approve | reject | edit
    comment: Mapped[str] = mapped_column(Text, default="")
    edited_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    review_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RemediationTask(Timestamps, Base):
    __tablename__ = "remediation_tasks"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    engagement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), index=True)
    engagement_control_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("engagement_controls.id"))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), default="open")
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)


class SignOff(Base):
    """Immutable expert attestation over an exact content hash."""
    __tablename__ = "signoffs"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    engagement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), index=True)
    subject_type: Mapped[str] = mapped_column(String(40))  # engagement_control | report | snapshot | questionnaire | policy
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    signer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    signer_name: Mapped[str] = mapped_column(String(200))
    signer_title: Mapped[str] = mapped_column(String(200))
    statement: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Questionnaire(Timestamps, Base):
    __tablename__ = "questionnaires"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    engagement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    requester: Mapped[str] = mapped_column(String(200), default="")
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="drafting")  # drafting|in_review|approved|delivered


class QuestionnaireItem(Timestamps, Base):
    __tablename__ = "questionnaire_items"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    questionnaire_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("questionnaires.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="unanswered")  # unanswered|drafted|approved|needs_info
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_artifacts.id"))


class Snapshot(Timestamps, Base):
    """Free Readiness Snapshot for a prospect: gaps, dated plan, all-in quote, named signer."""
    __tablename__ = "snapshots"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    contact_name: Mapped[str] = mapped_column(String(200))
    contact_email: Mapped[str] = mapped_column(String(320))
    frameworks: Mapped[list[str]] = mapped_column(JSONB, default=list)
    intake: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="intake")  # intake|drafted|signed|sent|won|lost
    signer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    founding_client: Mapped[bool] = mapped_column(Boolean, default=False)


class Policy(Timestamps, Base):
    __tablename__ = "policies"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    engagement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), index=True)
    template_key: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    body_markdown: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|client_approved|published
    approved_by_client: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence_items.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)


class Report(Timestamps, Base):
    __tablename__ = "reports"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    engagement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # evidence_index | gap_report | weekly_status
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved | released
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    html: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    generated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DownloadToken(Base):
    __tablename__ = "download_tokens"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    subject_type: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Connector(Timestamps, Base):
    __tablename__ = "connectors"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    engagement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # non-secret, e.g. {"org": "acme"}
    secret_ref: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="pending_authorization")
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text)
    customer_approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class SecretRecord(Base):
    """Dev/local secret store (encrypted). Production swaps in a cloud secret manager via connectors/secrets.py."""
    __tablename__ = "secret_records"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    ref: Mapped[str] = mapped_column(String(200), unique=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TimeEntry(Base):
    """Year-1 'map everything': who spent how long on which control, with or without AI."""
    __tablename__ = "time_entries"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    engagement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), index=True)
    engagement_control_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("engagement_controls.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    minutes: Mapped[int] = mapped_column(Integer)
    activity: Mapped[str] = mapped_column(String(40))  # evidence_collection|review|policy|questionnaire|signoff|meeting|other
    role_class: Mapped[str] = mapped_column(String(20), default="engineer")  # engineer | expert
    ai_assisted: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    work_date: Mapped[date] = mapped_column(Date, default=lambda: utcnow().date())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeletionRequest(Timestamps, Base):
    __tablename__ = "deletion_requests"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    scope: Mapped[str] = mapped_column(String(40))  # evidence_item | engagement | tenant
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="requested")  # requested|approved|completed|rejected
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AuditEvent(Base):
    """Append-only. UPDATE/DELETE are revoked from the app role and blocked by trigger."""
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_type: Mapped[str] = mapped_column(String(20))  # user | system | worker
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(20), default="success")
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())


TENANT_TABLES = [
    "tenants", "memberships", "engagements", "engagement_controls", "evidence_requests", "evidence_items",
    "evidence_chunks", "evidence_control_links", "ai_runs", "ai_artifacts", "review_decisions",
    "remediation_tasks", "signoffs", "questionnaires", "questionnaire_items", "snapshots", "policies", "reports",
    "download_tokens", "connectors", "secret_records", "time_entries", "deletion_requests",
    "control_tests", "audit_exceptions", "milestones",
]
APPEND_ONLY_TABLES = ["audit_events", "signoffs", "review_decisions"]
