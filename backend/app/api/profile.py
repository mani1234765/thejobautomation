from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal, Principal, get_owned, web_only
from app.db import utcnow
from app.models import (
    AIConversation,
    AIMessage,
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    AuditEvent,
    Certification,
    Communication,
    Education,
    Experience,
    InterviewSession,
    JobSource,
    Notification,
    Project,
    Resume,
    SavedJob,
    Skill,
)
from app.schemas.profile import (
    CertificationIn,
    CertificationOut,
    EducationIn,
    EducationOut,
    ExperienceIn,
    ExperienceOut,
    FullProfile,
    ProfileIn,
    ProfileOut,
    ProjectIn,
    ProjectOut,
    SettingsIn,
    SettingsOut,
    SkillIn,
    SkillOut,
)
from app.security.core import verify_password
from app.security.uploads import get_file_store
from app.services import audit, policy_settings
from app.services import profile as svc
from app.services import resume_review as review_svc

router = APIRouter(tags=["profile"])


@router.get("/profile", response_model=FullProfile)
def get_profile(db: DB, p: CurrentPrincipal) -> FullProfile:
    return svc.full_profile(db, p.user)


@router.put("/profile", response_model=ProfileOut)
def update_profile(body: ProfileIn, db: DB, p: Principal = Depends(web_only)) -> ProfileOut:
    prof = p.user.profile
    for k, v in body.model_dump().items():
        setattr(prof, k, str(v) if k.endswith("_url") and v is not None else v)
    audit.record(db, user_id=p.id, actor="user", action="profile.updated")
    db.commit()
    return ProfileOut.model_validate(prof)


@router.post("/profile/verify", status_code=204)
def verify_profile(db: DB, p: Principal = Depends(web_only)) -> None:
    svc.verify_all(db, p.user)
    db.commit()


@router.post("/onboarding/complete", status_code=204)
def complete_onboarding(db: DB, p: Principal = Depends(web_only)) -> None:
    if not p.user.profile.target_titles:
        raise HTTPException(400, "Add at least one target job title to finish onboarding.")
    p.user.onboarding_completed_at = p.user.onboarding_completed_at or utcnow()
    audit.record(db, user_id=p.id, actor="user", action="onboarding.completed")
    db.commit()


@router.get("/settings", response_model=SettingsOut)
def get_settings_(p: CurrentPrincipal) -> SettingsOut:
    eff = policy_settings.effective(p.user.settings)
    return SettingsOut.model_validate({**eff.values, "normalized": eff.normalized})


@router.put("/settings", response_model=SettingsOut)
def update_settings(body: SettingsIn, db: DB, p: Principal = Depends(web_only)) -> SettingsOut:
    if body.ai_model_override:
        from app.ai.service import allowed_models

        if body.ai_model_override not in allowed_models():
            raise HTTPException(400, "That model isn't enabled on this server. Ask the operator to allow it.")
    s = p.user.settings
    before = {k: getattr(s, k) for k in SettingsIn.model_fields}
    for k, v in body.model_dump().items():
        if k.startswith("apprentice_") and k not in body.model_fields_set:
            continue  # added in M3: older clients that don't send them must not reset them to defaults
        setattr(s, k, v)
    changed = {k: [before[k], getattr(s, k)] for k in before if before[k] != getattr(s, k)}
    audit.record(db, user_id=p.id, actor="user", action="settings.updated", meta={"changed": changed})
    db.commit()
    return SettingsOut.model_validate(s)


# ── Profile item CRUD (experiences / projects / education / certifications) ───────────────────
def _crud(path: str, model: Any, schema_in: type[BaseModel], schema_out: type[BaseModel]) -> None:
    @router.post(f"/profile/{path}", response_model=schema_out, status_code=201, name=f"create_{path}")
    def create(body: schema_in, db: DB, p: Principal = Depends(web_only)):  # type: ignore[valid-type]
        row = model(user_id=p.id, **body.model_dump())  # type: ignore[attr-defined]
        db.add(row)
        db.commit()
        return row

    @router.put(f"/profile/{path}/{{item_id}}", response_model=schema_out, name=f"update_{path}")
    def update(item_id: str, body: schema_in, db: DB, p: Principal = Depends(web_only)):  # type: ignore[valid-type]
        row = get_owned(db, model, item_id, p.id)
        for k, v in body.model_dump().items():  # type: ignore[attr-defined]
            setattr(row, k, v)
        review_svc.mark_edited(row)  # a correction: re-extraction won't overwrite it
        db.commit()
        return row

    @router.delete(f"/profile/{path}/{{item_id}}", status_code=204, name=f"delete_{path}")
    def remove(item_id: str, db: DB, p: Principal = Depends(web_only)) -> Response:
        row = get_owned(db, model, item_id, p.id)
        review_svc.record_deletion(db, row)  # re-extraction won't bring it back
        db.delete(row)
        db.commit()
        return Response(status_code=204)


_crud("experiences", Experience, ExperienceIn, ExperienceOut)
_crud("projects", Project, ProjectIn, ProjectOut)
_crud("education", Education, EducationIn, EducationOut)
_crud("certifications", Certification, CertificationIn, CertificationOut)


@router.post("/profile/skills", response_model=SkillOut, status_code=201)
def add_skill(body: SkillIn, db: DB, p: Principal = Depends(web_only)) -> Skill:
    skill = svc.upsert_skill(db, p.id, body.name, source="user", verified=body.verified)
    db.commit()
    return skill


@router.patch("/profile/skills/{skill_id}", response_model=SkillOut)
def set_skill_verified(skill_id: str, body: SkillIn, db: DB, p: Principal = Depends(web_only)) -> Skill:
    skill = get_owned(db, Skill, skill_id, p.id)
    skill.verified = body.verified
    db.commit()
    return skill


@router.delete("/profile/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str, db: DB, p: Principal = Depends(web_only)) -> None:
    skill = get_owned(db, Skill, skill_id, p.id)
    review_svc.record_skill_deletion(db, p.id, skill.name)
    db.delete(skill)
    db.commit()


# ── Data export & account deletion ─────────────────────────────────────────────────────────────
def _rows(db: DB, model: Any, user_id: str, exclude: tuple[str, ...] = ()) -> list[dict]:
    out = []
    for r in db.scalars(select(model).where(model.user_id == user_id)):
        out.append({c.key: getattr(r, c.key) for c in model.__mapper__.column_attrs if c.key not in exclude})
    return out


@router.get("/export")
def export_data(db: DB, p: Principal = Depends(web_only)) -> dict:
    uid = p.id
    data = {
        "exported_at": utcnow().isoformat(),
        "account": {"email": p.user.email, "created_at": p.user.created_at.isoformat()},
        "profile": svc.full_profile(db, p.user).model_dump(mode="json"),
        "settings": SettingsOut.model_validate(p.user.settings).model_dump(),
        "resumes": _rows(db, Resume, uid, ("storage_key", "text_content")),
        "job_sources": _rows(db, JobSource, uid),
        "saved_jobs": _rows(db, SavedJob, uid),
        "applications": _rows(db, Application, uid, ("idempotency_key",)),
        "application_events": _rows(db, ApplicationEvent, uid),
        "communications": _rows(db, Communication, uid),
        "interview_sessions": _rows(db, InterviewSession, uid),
        "notifications": _rows(db, Notification, uid),
        "audit_events": _rows(db, AuditEvent, uid),
        "assistant_conversations": [
            {
                "title": c.title,
                "messages": [
                    {"role": m.role, "content": m.content, "at": m.created_at}
                    for m in db.scalars(
                        select(AIMessage).where(AIMessage.conversation_id == c.id).order_by(AIMessage.created_at)
                    )
                ],
            }
            for c in db.scalars(select(AIConversation).where(AIConversation.user_id == uid))
        ],
    }
    answers = []
    for app_id in [a["id"] for a in data["applications"]]:
        answers += [
            {"application_id": app_id, "question": x.question, "answer": x.answer, "confidence": x.confidence}
            for x in db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.application_id == app_id))
        ]
    data["application_answers"] = answers
    audit.record(db, user_id=uid, actor="user", action="account.exported")
    db.commit()
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(data)


class DeleteAccount(BaseModel):
    confirm_email: str
    password: str


@router.post("/account/delete", status_code=204)
def delete_account(body: DeleteAccount, response: Response, db: DB, p: Principal = Depends(web_only)) -> None:
    if body.confirm_email.strip().lower() != p.user.email:
        raise HTTPException(400, "Type your account email to confirm deletion.")
    # Re-authenticate: a hijacked or unattended session must not be enough to erase an account.
    if not verify_password(p.user.password_hash, body.password):
        raise HTTPException(400, "Your password is incorrect.")
    uid = p.id
    db.delete(p.user)  # cascades through every user-owned table
    db.commit()
    get_file_store().delete_user(uid)
    response.delete_cookie("jp_session", path="/")
    response.delete_cookie("jp_csrf", path="/")
