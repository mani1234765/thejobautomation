"""Resume Review & Editor: corrections, persistence, re-extraction and use by matching/tailoring/answers."""

import io
from functools import cache

import pytest
from docx import Document

from app.db import session_factory
from app.models import Experience, Resume, User
from app.security.uploads import get_file_store
from app.services import applications as applications_svc
from app.services import profile as profile_svc
from app.services import resumes as resumes_svc
from tests.conftest import make_client
from tests.fixtures import build_docx_resume

V = "/api/v1"


@cache  # python-docx embeds save timestamps; cache so every call returns identical bytes
def build_misfiled_docx() -> bytes:
    """A resume whose school is listed under Experience with too few cues to be moved automatically."""
    doc = Document()
    doc.add_paragraph().add_run("Ravi Kumar").bold = True
    doc.add_paragraph("ravi.kumar@example.com | +91 98765 43210 | Hyderabad, Telangana")
    doc.add_heading("Summary", level=2)
    doc.add_paragraph("Backend developer building Python services.")
    doc.add_heading("Experience", level=2)
    p = doc.add_paragraph()
    p.add_run("Software Engineer").bold = True
    p.add_run(" at Finlytics Pvt Ltd\tJan 2023 – Present")
    doc.add_paragraph("Built FastAPI services handling 2M requests/day.", style="List Bullet")
    doc.add_paragraph("Reduced p95 latency by 40% with Redis caching.", style="List Bullet")
    doc.add_paragraph("Associate Developer at Nimbus Labs  Jul 2021 – Dec 2022", style="List Bullet")
    doc.add_paragraph("Developed React dashboards.", style="List Bullet")
    p = doc.add_paragraph()
    p.add_run("St. Xavier's Academy").bold = True
    p.add_run("\t2014 – 2016")
    doc.add_paragraph("Captain of the debate team", style="List Bullet")
    doc.add_heading("Skills", level=2)
    doc.add_paragraph("Python, FastAPI, PostgreSQL, Redis, Docker, React")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _upload(api, data: bytes, name: str = "cv.docx") -> dict:
    r = api.post(f"{V}/resumes", files={"file": (name, data, "application/octet-stream")})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def misfiled(user_api):
    resume = _upload(user_api, build_misfiled_docx())
    review = user_api.post(f"{V}/resumes/{resume['id']}/review/import").json()
    return user_api, resume["id"], review


def _find(review: dict, kind: str, text: str) -> dict:
    return next(e for e in review["entries"][kind] if text in e["label"])


def _url(rid: str, kind: str, eid: str, action: str = "") -> str:
    return f"{V}/resumes/{rid}/review/entries/{kind}/{eid}" + (f"/{action}" if action else "")


# ── Import & payload ───────────────────────────────────────────────────────────────────────────
def test_review_lists_every_section_with_confidence(user_api):
    resume = _upload(user_api, build_docx_resume())
    rid = resume["id"]
    before = user_api.get(f"{V}/resumes/{rid}/review").json()
    assert before["imported"] is False and not any(before["entries"].values())
    review = user_api.post(f"{V}/resumes/{rid}/review/import").json()
    counts = {k: len(v) for k, v in review["entries"].items()}
    assert counts == {"experience": 3, "projects": 1, "education": 1, "certifications": 1}
    assert review["summary"]["text"].startswith("Backend engineer")
    assert {"Python", "Kafka"} <= {s["name"] for s in review["skills"]}
    for group in review["entries"].values():
        for e in group:
            assert e["confidence"] >= review["review_threshold"] and not e["needs_review"]
            assert e["source_ids"] and e["user_edited"] is False
    assert review["needs_review"] == 0


# ── Moving entries ─────────────────────────────────────────────────────────────────────────────
def test_education_misfiled_as_experience_can_be_moved_to_education(misfiled):
    api, rid, review = misfiled
    school = _find(review, "experience", "Xavier")
    r = api.post(_url(rid, "experience", school["id"], "move"), json={"to": "education"})
    assert r.status_code == 200, r.text
    review = r.json()
    assert not any("Xavier" in e["label"] for e in review["entries"]["experience"])
    [edu] = review["entries"]["education"]
    assert edu["institution"] == "St. Xavier's Academy"
    assert edu["start_date"] == "2014-01-01" and edu["end_date"] == "2016-12-01"
    assert edu["details"] == ["Captain of the debate team"]  # nothing is lost
    assert edu["user_edited"] and edu["source_ids"] == school["source_ids"]
    # The corrected profile is what the rest of JobPilot reads.
    full = api.get(f"{V}/profile").json()
    assert [e["institution"] for e in full["education"]] == ["St. Xavier's Academy"]
    assert all("Xavier" not in e["employer"] for e in full["experiences"])


def test_move_after_edit_keeps_the_users_text(misfiled):
    api, rid, review = misfiled
    school = _find(review, "experience", "Xavier")
    body = {**school, "employer": "St. Xavier's Academy, Hyderabad", "title": "Higher Secondary"}
    assert api.put(_url(rid, "experience", school["id"]), json=body).status_code == 200
    review = api.post(_url(rid, "experience", school["id"], "move"), json={"to": "education"}).json()
    [edu] = review["entries"]["education"]
    assert edu["institution"] == "St. Xavier's Academy, Hyderabad" and edu["degree"] == "Higher Secondary"


def test_move_to_projects_summary_and_skills_and_change_category(misfiled):
    api, rid, review = misfiled
    fin = _find(review, "experience", "Finlytics")
    review = api.post(
        _url(rid, "experience", fin["id"], "move"), json={"to": "experience", "category": "internship"}
    ).json()
    assert _find(review, "experience", "Finlytics")["category"] == "internship"
    school = _find(review, "experience", "Xavier")
    review = api.post(_url(rid, "experience", school["id"], "move"), json={"to": "projects"}).json()
    proj = _find(review, "projects", "Xavier")
    assert proj["description"] == "Captain of the debate team"
    review = api.post(_url(rid, "projects", proj["id"], "move"), json={"to": "summary"}).json()
    assert "Captain of the debate team" in review["summary"]["text"]
    assert not review["entries"]["projects"]
    bad = api.post(_url(rid, "experience", fin["id"], "move"), json={"to": "education", "category": "internship"})
    assert bad.status_code == 400


# ── Split & merge ──────────────────────────────────────────────────────────────────────────────
def test_split_two_roles_merged_into_one_then_merge_back(misfiled):
    api, rid, review = misfiled
    fin = _find(review, "experience", "Finlytics")
    assert len(fin["bullets"]) == 4  # the second role's heading was read as a bullet
    r = api.post(_url(rid, "experience", fin["id"], "split"), json={"at": 2})
    assert r.status_code == 200, r.text
    review = r.json()
    fin = _find(review, "experience", "Finlytics")
    nimbus = _find(review, "experience", "Nimbus")
    assert len(fin["bullets"]) == 2 and nimbus["bullets"] == ["Developed React dashboards."]
    assert nimbus["title"] == "Associate Developer" and nimbus["start_date"] == "2021-07-01"
    assert nimbus["end_date"] == "2022-12-01"
    assert api.post(_url(rid, "experience", fin["id"], "split"), json={"at": 9}).status_code == 400

    merged = api.post(f"{V}/resumes/{rid}/review/merge", json={"kind": "experience", "ids": [fin["id"], nimbus["id"]]})
    assert merged.status_code == 200, merged.text
    exp = merged.json()["entries"]["experience"]
    fin = _find(merged.json(), "experience", "Finlytics")
    assert len(exp) == 2 and len(fin["bullets"]) == 3 and fin["start_date"] == "2021-07-01" and fin["is_current"]


# ── Editing, validation, persistence ───────────────────────────────────────────────────────────
def test_edit_add_delete_and_validation(misfiled):
    api, rid, review = misfiled
    fin = _find(review, "experience", "Finlytics")
    bad = api.put(
        _url(rid, "experience", fin["id"]),
        json={**fin, "start_date": "2024-01-01", "end_date": "2023-01-01", "is_current": False},
    )
    assert bad.status_code == 422 and "before the start date" in bad.json()["error"]["message"]
    assert _find(api.get(f"{V}/resumes/{rid}/review").json(), "experience", "Finlytics")["start_date"] == "2023-01-01"

    added = api.post(
        f"{V}/resumes/{rid}/review/entries", json={"kind": "certifications", "data": {"name": "CKA", "issuer": "CNCF"}}
    )
    assert added.status_code == 201
    cert = _find(added.json(), "certifications", "CKA")
    assert cert["user_edited"] and cert["verified"]
    assert api.post(f"{V}/resumes/{rid}/review/entries", json={"kind": "skills", "data": {}}).status_code == 422
    review = api.delete(_url(rid, "certifications", cert["id"])).json()
    assert not review["entries"]["certifications"]
    assert api.put(_url(rid, "certifications", cert["id"]), json={"name": "x"}).status_code == 404


def test_corrections_survive_a_restart(misfiled):
    api, rid, review = misfiled
    fin = _find(review, "experience", "Finlytics")
    api.put(_url(rid, "experience", fin["id"]), json={**fin, "title": "Senior Software Engineer"})
    api.put(f"{V}/resumes/{rid}/review/summary", json={"text": "Corrected summary."})

    restarted = make_client()  # a fresh application instance and database session
    assert (
        restarted.post(
            f"{V}/auth/login", json={"email": "asha@example.com", "password": "correct-horse-42"}
        ).status_code
        == 200
    )
    review = restarted.get(f"{V}/resumes/{rid}/review").json()
    assert _find(review, "experience", "Finlytics")["title"] == "Senior Software Engineer"
    assert review["summary"]["text"] == "Corrected summary."


def test_other_users_cannot_read_or_change_a_review(misfiled):
    api, rid, review = misfiled
    fin = _find(review, "experience", "Finlytics")
    other = make_client()
    other.post(
        f"{V}/auth/register", json={"email": "eve@example.com", "password": "correct-horse-42", "full_name": "Eve"}
    )
    assert other.get(f"{V}/resumes/{rid}/review").status_code == 404
    assert other.put(_url(rid, "experience", fin["id"]), json=fin).status_code == 404


def test_original_file_is_never_modified(misfiled):
    api, rid, review = misfiled
    school = _find(review, "experience", "Xavier")
    api.post(_url(rid, "experience", school["id"], "move"), json={"to": "education"})
    api.put(f"{V}/resumes/{rid}/review/summary", json={"text": "Changed"})
    assert api.get(f"{V}/resumes/{rid}/download").content == build_misfiled_docx()
    detail = api.get(f"{V}/resumes/{rid}").json()  # the raw extraction is kept separately, unchanged
    assert any("Xavier" in e["employer"] for e in detail["extracted"]["experiences"])


# ── Re-extraction ──────────────────────────────────────────────────────────────────────────────
def test_reextraction_preserves_corrections_deletions_and_moves(misfiled):
    api, rid, review = misfiled
    fin = _find(review, "experience", "Finlytics")
    school = _find(review, "experience", "Xavier")
    api.put(_url(rid, "experience", fin["id"]), json={**fin, "title": "Senior Software Engineer"})
    api.post(_url(rid, "experience", school["id"], "move"), json={"to": "education"})
    skill = next(s for s in review["skills"] if s["name"] == "Docker")
    api.delete(f"{V}/resumes/{rid}/review/skills/{skill['id']}")
    api.put(f"{V}/resumes/{rid}/review/summary", json={"text": ""})

    preview = api.get(f"{V}/resumes/{rid}/reextract").json()
    assert {k["current_label"] for k in preview["kept"]} == {
        "Senior Software Engineer · Finlytics Pvt Ltd",
        "St. Xavier's Academy",
    }
    assert all(k["differs"] for k in preview["kept"])
    assert not preview["add"] and not preview["update"] and "Docker" not in preview["skills"]
    assert preview["summary"] is None  # cleared on purpose: not refilled
    assert (
        api.get(f"{V}/resumes/{rid}/review").json()["entries"] == api.get(f"{V}/resumes/{rid}/review").json()["entries"]
    )

    r = api.post(f"{V}/resumes/{rid}/reextract", json={})
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["changes"]["kept_corrections"] == 2 and after["changes"]["added"] == 0
    assert _find(after, "experience", "Finlytics")["title"] == "Senior Software Engineer"
    assert [e["institution"] for e in after["entries"]["education"]] == ["St. Xavier's Academy"]
    assert not any("Xavier" in e["label"] for e in after["entries"]["experience"])
    assert "Docker" not in {s["name"] for s in after["skills"]}
    assert not after["summary"]["text"]
    # The old import endpoint is non-destructive too.
    api.post(f"{V}/resumes/{rid}/apply-to-profile", json={"replace": True})
    again = api.get(f"{V}/resumes/{rid}/review").json()
    assert _find(again, "experience", "Finlytics")["title"] == "Senior Software Engineer"
    assert len(again["entries"]["experience"]) == 1 and len(again["entries"]["education"]) == 1


def test_deleted_entry_stays_deleted_and_profile_crud_edits_are_preserved(misfiled):
    api, rid, review = misfiled
    school = _find(review, "experience", "Xavier")
    fin = _find(review, "experience", "Finlytics")
    assert api.delete(f"{V}/profile/experiences/{school['id']}").status_code == 204  # deleted from Settings
    body = {
        k: fin[k]
        for k in (
            "category",
            "employer",
            "title",
            "location",
            "start_date",
            "end_date",
            "is_current",
            "bullets",
            "verified",
        )
    }
    assert api.put(f"{V}/profile/experiences/{fin['id']}", json={**body, "employer": "Finlytics"}).status_code == 200
    after = api.post(f"{V}/resumes/{rid}/reextract", json={"remove_stale": True}).json()
    assert [e["employer"] for e in after["entries"]["experience"]] == ["Finlytics"]
    assert after["changes"]["still_deleted"] == 1


def test_new_content_from_reextraction_is_flagged_for_review(misfiled, monkeypatch):
    api, rid, review = misfiled
    from app.domain import resume_parser

    original = resume_parser.parse_lines

    def with_extra(lines, today=None):
        parsed = original(lines, today)
        parsed.certifications.append(
            {
                "name": "Kubernetes Admin",
                "issuer": None,
                "source_id": lines[-1].id,
                "confidence": 0.5,
                "review_reasons": ["test"],
            }
        )
        return parsed

    monkeypatch.setattr(resume_parser, "parse_lines", with_extra)
    preview = api.get(f"{V}/resumes/{rid}/reextract").json()
    assert [a["label"] for a in preview["add"]] == ["Kubernetes Admin"]
    after = api.post(f"{V}/resumes/{rid}/reextract", json={}).json()
    [cert] = after["entries"]["certifications"]
    assert cert["needs_review"] and cert["review_reasons"] == ["test"] and not cert["verified"]
    assert after["needs_review"] >= 1
    confirmed = api.post(f"{V}/resumes/{rid}/review/confirm").json()
    assert confirmed["needs_review"] == 0 and confirmed["reviewed_at"]
    assert all(e["verified"] for g in confirmed["entries"].values() for e in g)


def test_rows_imported_before_review_existed_are_adopted_not_duplicated(user_api):
    resume = _upload(user_api, build_docx_resume())
    rid = resume["id"]
    user_api.post(f"{V}/resumes/{rid}/review/import")
    with session_factory()() as db:  # simulate pre-migration rows: no fingerprint or provenance
        for row in db.query(Experience).all():
            row.fingerprint, row.source_ids = None, []
        db.commit()
    after = user_api.post(f"{V}/resumes/{rid}/reextract", json={}).json()
    assert len(after["entries"]["experience"]) == 3 and all(e["source_ids"] for e in after["entries"]["experience"])


# ── The corrected profile is the source of truth ──────────────────────────────────────────────
def test_tailoring_matching_and_answers_use_the_corrected_profile(misfiled):
    api, rid, review = misfiled
    school = _find(review, "experience", "Xavier")
    api.post(_url(rid, "experience", school["id"], "move"), json={"to": "education"})
    skill = next(s for s in review["skills"] if s["name"] == "Redis")
    api.delete(f"{V}/resumes/{rid}/review/skills/{skill['id']}")

    with session_factory()() as db:
        resume = db.get(Resume, rid)
        user = db.get(User, resume.user_id)
        paras = resumes_svc._paragraph_map(db, resume, get_file_store())
        # The school's lines are no longer treated as experience, so tailoring won't rewrite them as a job.
        assert not set(school["source_ids"]) & set(paras)
        assert "Redis" not in profile_svc.candidate_profile(db, user).skills  # matching input
        assert (
            "Redis"
            not in resumes_svc.analyse_fit(set(profile_svc.candidate_profile(db, user).skills), _Job())[
                "matched_required"
            ]
        )
        answers = {q: v for q, v, _ in applications_svc._profile_answers(db, user)}
        assert answers["Highest education"] == "St. Xavier's Academy"
        assert answers["Current or most recent employer"] == "Finlytics Pvt Ltd"


class _Job:
    required_skills = ["Redis", "Python"]
    preferred_skills: list[str] = []
