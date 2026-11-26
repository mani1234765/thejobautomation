"""Builders for representative DOCX/PDF resumes used across tests."""

from __future__ import annotations

import io
from functools import cache

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor


@cache
def build_docx_resume(variant: str = "standard") -> bytes:
    """Cached so repeated calls return identical bytes (python-docx embeds save timestamps). A realistic resume with headers/footers, a two-column table, bullets and styled runs.

    `variant` changes section naming conventions so parsing robustness can be tested.
    """
    names = {
        "standard": (
            "Professional Summary",
            "Work Experience",
            "Internships",
            "Technical Projects",
            "Education",
            "Technical Skills",
            "Certifications",
        ),
        "alt": (
            "PROFILE",
            "WORK HISTORY",
            "INTERNSHIP EXPERIENCE",
            "ACADEMIC PROJECTS",
            "ACADEMIC BACKGROUND",
            "CORE COMPETENCIES",
            "LICENSES AND CERTIFICATIONS",
        ),
        "casual": (
            "About Me",
            "Where I've worked:",
            "Internship",
            "Side Projects",
            "Education",
            "Tech Stack",
            "Courses",
        ),
    }[variant]
    doc = Document()
    sec = doc.sections[0]
    sec.header.paragraphs[0].text = "Asha Verma — Resume"
    sec.footer.paragraphs[0].text = "Page 1"

    name = doc.add_paragraph()
    r = name.add_run("Asha Verma")
    r.bold = True
    r.font.size = Pt(20)
    r.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
    name.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph("asha.verma@example.com | +91 98765 43210 | Bengaluru, Karnataka | linkedin.com/in/ashaverma")

    def heading(text: str) -> None:
        doc.add_heading(text, level=2)

    heading(names[0])
    doc.add_paragraph("Backend engineer with 3 years building Python and FastAPI services on AWS.")

    heading(names[1])
    p = doc.add_paragraph()
    p.add_run("Software Engineer").bold = True
    p.add_run(" at Finlytics Pvt Ltd, Bengaluru\tJan 2023 – Present")
    doc.add_paragraph(
        "Built FastAPI microservices handling 2M requests/day on AWS with PostgreSQL.", style="List Bullet"
    )
    doc.add_paragraph("Responsible for CI/CD pipelines using GitHub Actions and Docker.", style="List Bullet")
    doc.add_paragraph("Reduced p95 latency by 40% by adding Redis caching.", style="List Bullet")
    p = doc.add_paragraph()
    p.add_run("Associate Developer").bold = True
    p.add_run(" | Nimbus Labs | Pune\tJul 2021 – Dec 2022")
    doc.add_paragraph("Developed React dashboards backed by Django REST APIs.", style="List Bullet")

    heading(names[2])
    p = doc.add_paragraph()
    p.add_run("Data Science Intern").bold = True
    p.add_run(" – Quanta Analytics\tMay 2020 – Jul 2020")
    doc.add_paragraph("Trained scikit-learn models with Pandas for churn prediction.", style="List Bullet")

    heading(names[3])
    p = doc.add_paragraph()
    p.add_run("JobBoard Aggregator").bold = True
    p.add_run(" — Next.js, TypeScript, PostgreSQL")
    doc.add_paragraph("Final year capstone project aggregating job feeds at university.", style="List Bullet")

    heading(names[4])
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "National Institute of Technology, Trichy"
    table.cell(0, 0).add_paragraph("B.Tech in Computer Science, CGPA: 8.6/10")
    table.cell(0, 1).text = "2017 – 2021"

    heading(names[5])
    p = doc.add_paragraph()
    p.add_run("Languages: ").bold = True
    p.add_run("Python, Go, SQL, TypeScript")
    p = doc.add_paragraph()
    p.add_run("Tools: ").bold = True
    p.add_run("Docker, Kubernetes, Git, Kafka, Figma-ish Tool")

    heading(names[6])
    doc.add_paragraph("AWS Certified Developer – Associate, Amazon Web Services, 2022", style="List Bullet")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def build_pdf_resume() -> bytes:
    """A minimal single-page text PDF (no external dependency)."""
    lines = [
        "Ravi Kumar",
        "ravi.k@example.com | 9876543210 | Hyderabad, Telangana",
        "SUMMARY",
        "Fresher with internship experience in data analysis.",
        "EXPERIENCE",
        "Data Analyst Intern - Brightside | Jun 2024 - Aug 2024",
        "- Built Tableau dashboards and SQL reports.",
        "EDUCATION",
        "JNTU Hyderabad, B.Tech in ECE, 2021 - 2025",
        "SKILLS",
        "Python, SQL, Excel, Tableau",
    ]
    content = "BT /F1 11 Tf 50 780 Td 14 TL " + " ".join(f"({ln}) Tj T*" for ln in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out
