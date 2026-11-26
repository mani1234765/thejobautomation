"""Section classification: varied headings and content-based correction of misfiled entries."""

from datetime import date

import pytest

from app.domain.resume_parser import REVIEW_THRESHOLD, Line, classify_heading, parse_document, parse_lines
from tests.fixtures import build_docx_resume

TODAY = date(2026, 11, 1)


def _doc(*blocks: tuple[str, list[str]], header: list[str] | None = None) -> list[Line]:
    """Build lines like a DOCX: bold headings, bold entry headers (prefixed '!'), bullets (prefixed '-')."""
    lines = [Line("b0", "Ravi Kumar", is_bold=True), Line("b1", "ravi@example.com | +91 98765 43210")]
    for t in header or []:
        lines.append(Line(f"b{len(lines)}", t))
    for heading, body in blocks:
        lines.append(Line(f"b{len(lines)}", heading, is_bold=True))
        for t in body:
            if t.startswith("!"):
                lines.append(Line(f"b{len(lines)}", t[1:], is_bold=True))
            elif t.startswith("-"):
                lines.append(Line(f"b{len(lines)}", t[1:].strip(), is_list=True))
            else:
                lines.append(Line(f"b{len(lines)}", t))
    return lines


EXPERIENCE = [
    "!Backend Engineer at Finlytics Pvt Ltd   Jan 2023 – Present",
    "-Built FastAPI services handling 2M requests/day",
]


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("EMPLOYMENT HISTORY", "experience"),
        ("Work Experience & Internships", "experience"),
        ("Professional Experience (5 yrs)", "experience"),
        ("Career Objective", "summary"),
        ("Executive Summary", "summary"),
        ("Academic Qualifications", "education"),
        ("Educational Qualifications", "education"),
        ("Key Projects", "projects"),
        ("Capstone Project", "academic_projects"),
        ("Tools & Technologies", "skills"),
        ("Licenses and Certifications", "certifications"),
        ("Where I studied", "education"),
    ],
)
def test_varied_headings(heading, expected):
    hit = classify_heading(Line("h", heading, is_bold=True))
    assert hit and hit[0] == expected, (heading, hit)


def test_education_under_experience_heading_is_moved_to_education():
    parsed = parse_lines(
        _doc(
            (
                "Experience",
                [
                    *EXPERIENCE,
                    "!B.Tech in Computer Science   2018 – 2022",
                    "National Institute of Technology, Warangal — CGPA 8.6/10",
                ],
            )
        ),
        TODAY,
    )
    assert [e.employer for e in parsed.experiences] == ["Finlytics Pvt Ltd"]
    assert parsed.experiences[0].confidence >= REVIEW_THRESHOLD and not parsed.experiences[0].review_reasons
    [edu] = parsed.education
    assert "National Institute of Technology" in edu.institution and edu.degree.startswith("B.Tech")
    assert edu.grade and "8.6" in edu.grade
    assert edu.confidence < REVIEW_THRESHOLD  # moved by content → user should confirm
    assert "looks like education" in edu.review_reasons[0] and "“Experience”" in edu.review_reasons[0]


def test_project_under_experience_heading_is_moved_to_projects():
    parsed = parse_lines(
        _doc(
            (
                "Work Experience",
                [
                    *EXPERIENCE,
                    "!Expense Tracker",
                    "-Built a budgeting app with React, Node.js and MongoDB — github.com/ravi/expense-tracker",
                ],
            )
        ),
        TODAY,
    )
    assert [e.employer for e in parsed.experiences] == ["Finlytics Pvt Ltd"]
    [proj] = parsed.projects
    assert proj.name == "Expense Tracker" and proj.url and "github.com" in proj.url
    assert proj.confidence < REVIEW_THRESHOLD and "looks like a project" in proj.review_reasons[0]


def test_summary_prose_under_projects_heading_is_moved_to_summary():
    prose = (
        "Backend engineer with 4 years of experience building payment and lending platforms, passionate about "
        "reliable distributed systems and mentoring junior developers in fast-moving product teams."
    )
    parsed = parse_lines(
        _doc(("Projects", [prose, "!Expense Tracker", "-Built a budgeting app using React and Node.js"])), TODAY
    )
    assert parsed.summary == prose
    assert parsed.summary_confidence is not None and parsed.summary_confidence < REVIEW_THRESHOLD
    assert [p.name for p in parsed.projects] == ["Expense Tracker"]


def test_unrecognised_heading_is_classified_by_content():
    lines = _doc(("Experience", EXPERIENCE))
    lines.append(Line("x1", "Academic Credentials", style="Heading 1"))
    lines.append(Line("x2", "Master of Science in Data Science, University of Leeds   2015 – 2016"))
    parsed = parse_lines(lines, TODAY)
    [edu] = parsed.education
    assert "University of Leeds" in edu.institution
    assert edu.confidence <= 0.65 and "wasn't recognised" in edu.review_reasons[0]
    assert len(parsed.experiences) == 1  # not appended to the previous section


def test_unclassifiable_content_is_reported_not_guessed():
    lines = _doc(("Experience", EXPERIENCE))
    lines.append(Line("x1", "Miscellaneous", style="Heading 1"))
    lines.append(Line("x2", "Available from March"))
    parsed = parse_lines(lines, TODAY)
    assert parsed.unclassified and parsed.unclassified[0]["source_ids"] == ["x2"]
    assert len(parsed.experiences) == 1


def test_summary_without_heading_is_found_and_flagged():
    prose = (
        "Data analyst with two years of experience turning messy operational data into dashboards and forecasts "
        "that product and finance teams rely on every week."
    )
    parsed = parse_lines(_doc(("Experience", EXPERIENCE), header=[prose]), TODAY)
    assert parsed.summary == prose and parsed.summary_confidence == 0.6
    assert parsed.summary_source_ids == ["b2"]


def test_correctly_labelled_resume_is_not_flagged():
    for variant in ("standard", "alt"):
        parsed = parse_document(build_docx_resume(variant), "docx", TODAY)
        entries = [*parsed.experiences, *parsed.projects, *parsed.education]
        assert entries and all(e.confidence >= REVIEW_THRESHOLD and not e.review_reasons for e in entries)
        assert all(c["confidence"] >= REVIEW_THRESHOLD for c in parsed.certifications)
        assert not parsed.unclassified


def test_page_header_text_is_not_appended_to_last_section():
    parsed = parse_document(build_docx_resume(), "docx", TODAY)
    assert [c["name"] for c in parsed.certifications] == ["AWS Certified Developer"]


def test_ambiguous_entry_is_flagged_not_moved():
    parsed = parse_lines(
        _doc(("Experience", [*EXPERIENCE, "!St. Xavier's Academy   2014 – 2016", "-Captain of the debate team"])), TODAY
    )
    school = next(e for e in parsed.experiences if "Xavier" in e.employer)  # left where the user put it…
    assert school.confidence < REVIEW_THRESHOLD  # …but flagged for review
    assert "reads more like education" in school.review_reasons[0]
    fin = next(e for e in parsed.experiences if "Finlytics" in e.employer)
    assert fin.confidence >= REVIEW_THRESHOLD and not fin.review_reasons
