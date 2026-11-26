import io
import zipfile
from datetime import date

import pytest
from docx import Document

from app.domain.docx_document import ParagraphNotFound, apply_edits, read_paragraphs
from app.domain.resume_parser import Line, classify_heading, parse_document, total_months
from tests.fixtures import build_docx_resume, build_pdf_resume

TODAY = date(2026, 11, 1)


@pytest.mark.parametrize("variant", ["standard", "alt", "casual"])
def test_section_detection_handles_naming_conventions(variant):
    parsed = parse_document(build_docx_resume(variant), "docx", TODAY)
    types = [s["canonical_type"] for s in parsed.sections]
    for expected in ("summary", "experience", "internships", "education", "skills", "certifications"):
        assert expected in types, (variant, types)
    assert any(t in types for t in ("projects", "academic_projects"))


def test_contact_and_entities_extracted():
    parsed = parse_document(build_docx_resume(), "docx", TODAY)
    assert parsed.contact["name"] == "Asha Verma"
    assert parsed.contact["email"] == "asha.verma@example.com"
    assert parsed.contact["phone"] and "98765" in parsed.contact["phone"]
    assert parsed.contact["linkedin"]

    by_cat = {}
    for e in parsed.experiences:
        by_cat.setdefault(e.category, []).append(e)
    assert {e.employer for e in by_cat["professional"]} >= {"Finlytics Pvt Ltd"}
    assert any("Nimbus" in e.employer for e in by_cat["professional"])
    assert by_cat["internship"][0].title == "Data Science Intern"
    fin = next(e for e in by_cat["professional"] if "Finlytics" in e.employer)
    assert fin.title == "Software Engineer" and fin.is_current and fin.start_date == "2023-01-01"
    assert len(fin.bullets) == 3


def test_experience_categories_are_not_merged():
    parsed = parse_document(build_docx_resume(), "docx", TODAY)
    # Jul 2021 → Nov 2026 professional ≈ 5.3y; the internship must not be added.
    assert 5.0 <= parsed.years_experience <= 5.6
    assert parsed.internship_months == 3


def test_projects_education_skills():
    parsed = parse_document(build_docx_resume(), "docx", TODAY)
    proj = parsed.projects[0]
    assert proj.name == "JobBoard Aggregator"
    assert proj.category == "academic"  # mentions capstone/university
    assert {"Next.js", "TypeScript", "PostgreSQL"} <= set(proj.technologies)
    edu = parsed.education[0]
    assert "National Institute of Technology" in edu.institution
    assert edu.degree and edu.degree.lower().startswith("b.tech")
    assert edu.grade and "8.6" in edu.grade
    for s in ("Python", "Go", "SQL", "Docker", "Kubernetes", "Kafka", "FastAPI", "AWS", "Redis", "React"):
        assert s in parsed.skills, s
    assert all(parsed.skills[s] for s in parsed.skills)  # every skill has evidence


def test_quality_flags_weak_verbs():
    parsed = parse_document(build_docx_resume(), "docx", TODAY)
    codes = {i["code"] for i in parsed.quality["issues"]}
    assert "weak_verb" in codes  # "Responsible for CI/CD…"
    assert 0 <= parsed.quality["score"] <= 100


def test_pdf_parsing():
    parsed = parse_document(build_pdf_resume(), "pdf", TODAY)
    assert parsed.contact["email"] == "ravi.k@example.com"
    assert parsed.experiences and parsed.experiences[0].category == "internship"
    assert {"Python", "SQL", "Tableau", "Excel"} <= set(parsed.skills)
    assert parsed.years_experience == 0 and parsed.seniority == "fresher"


def test_heading_classifier_rejects_body_text():
    assert classify_heading(Line("b1", "Built APIs for payments and reporting systems")) is None
    assert classify_heading(Line("b1", "Jan 2020 - Mar 2021")) is None
    assert classify_heading(Line("b1", "Work History", is_bold=True))[0] == "experience"
    assert classify_heading(Line("b1", "Technical Skills & Tools", is_bold=True))[0] == "skills"


def test_overlapping_ranges_not_double_counted():
    r = [(date(2020, 1, 1), date(2020, 12, 1)), (date(2020, 6, 1), date(2021, 5, 1))]
    assert total_months(r) == 17


# ── Formatting preservation ────────────────────────────────────────────────────────────────────
def _parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def test_docx_edit_preserves_everything_else():
    original = build_docx_resume()
    paras = {p.text: p for p in read_paragraphs(original)}
    target = paras["Responsible for CI/CD pipelines using GitHub Actions and Docker."]
    table_para = next(p for p in read_paragraphs(original) if p.text.startswith("B.Tech"))
    label_para = next(p for p in read_paragraphs(original) if p.text.startswith("Languages:"))

    edited = apply_edits(
        original,
        {
            target.id: "Automated CI/CD pipelines using GitHub Actions and Docker.",
            table_para.id: "B.Tech in Computer Science (CGPA: 8.6/10)",
            label_para.id: "Languages: Python, SQL, Go, TypeScript",
        },
    )
    before, after = _parts(original), _parts(edited)
    # Package parts other than the main document are untouched: styles, numbering, headers, footers.
    for name in before:
        if name != "word/document.xml":
            assert before[name] == after.get(name), name

    od, nd = Document(io.BytesIO(original)), Document(io.BytesIO(edited))
    # Margins / page setup unchanged.
    for a, b in zip(od.sections, nd.sections, strict=True):
        assert (a.left_margin, a.right_margin, a.page_width) == (b.left_margin, b.right_margin, b.page_width)

    new = {p.id: p for p in read_paragraphs(edited)}
    old = {p.id: p for p in read_paragraphs(original)}
    assert new[target.id].text.startswith("Automated")
    assert new[target.id].is_list and new[target.id].style == old[target.id].style
    # Bold label run retained.
    labelled = next(p for p in nd.paragraphs if p.text.startswith("Languages:"))
    assert labelled.runs[0].text == "Languages: " and labelled.runs[0].bold
    # Untouched paragraphs identical.
    for pid, p in old.items():
        if pid not in (target.id, table_para.id, label_para.id):
            assert new[pid] == p
    # The table cell edit happened inside the table.
    assert nd.tables[0].cell(0, 0).paragraphs[1].text.startswith("B.Tech in Computer Science (CGPA")


def test_docx_edit_keeps_trailing_date_run():
    original = build_docx_resume()
    header = next(p for p in read_paragraphs(original) if p.text.startswith("Software Engineer"))
    new_text = header.text.replace("Software Engineer", "Senior Software Engineer")
    edited = apply_edits(original, {header.id: new_text})
    nd = Document(io.BytesIO(edited))
    p = next(p for p in nd.paragraphs if "Finlytics" in p.text)
    assert p.text == new_text
    assert p.runs[0].bold


def test_unknown_paragraph_id_rejected():
    with pytest.raises(ParagraphNotFound):
        apply_edits(build_docx_resume(), {"b999": "x"})
