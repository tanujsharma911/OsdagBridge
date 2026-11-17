"""Layout regression checks for the PDF design report.

Builds a report from placeholder data with enough girders that the per-girder
tables run over several pages, compiles it with pdflatex and checks the log:

* no LaTeX errors (missing logo images aside),
* nothing taller than the page (Overfull \\vbox) and no header/footer box that
  is too small for its content (fancyhdr warnings),
* every longtable repeats its head on later pages (\\endhead present).

Skipped when pdflatex is not installed.
"""
import os
import re
import shutil
import subprocess

import pytest

from osdagbridge.core.reports.report_generator import (
    ReportFigures, ReportMetadata, ReportOptions, ReportPayload, ReportRequest,
    generate_report,
)
from osdagbridge.core.utils.common import (
    KEY_DESIGN_MODE, KEY_MP_GD_MEMBER_ID, KEY_MP_GD_SELECT_GIRDER, KEY_TS_NO_OF_GIRDERS,
)

pytestmark = pytest.mark.skipif(shutil.which("pdflatex") is None,
                                reason="pdflatex not installed")

N_GIRDERS = 14


@pytest.fixture(scope="module")
def report_log(tmp_path_factory):
    out = tmp_path_factory.mktemp("report")
    md = ReportMetadata(project_name="Layout Test", project_location="Test", designer="D",
                        client="C", company="Co", job_number="J1",
                        report_date="2026-01-01", subtitle="Rev 0")
    opts = ReportOptions(sections=["loads", "analysis", "design_checks", "drawings"],
                         include_figures=False, include_toc=True, include_pdf=True)
    inputs = {KEY_DESIGN_MODE: "Custom", KEY_TS_NO_OF_GIRDERS: N_GIRDERS}
    for i in range(1, N_GIRDERS + 1):
        inputs[f"{KEY_MP_GD_SELECT_GIRDER}.G{i}"] = f"G{i}"
        inputs[f"{KEY_MP_GD_MEMBER_ID}.G{i}.M1"] = f"G{i}M1"
    payload = ReportPayload(metadata=md, options=opts, inputs=inputs, analysis_summary={},
                            design_checks=[], figures=ReportFigures(), output_dict={})
    request = ReportRequest(metadata=md, options=opts, output_dir=str(out), file_stem="layout")
    result = generate_report(payload, request)
    assert result.tex_path and os.path.exists(result.tex_path)

    # generate_report compiles in a temp dir it deletes; compile again to read the log.
    build = tmp_path_factory.mktemp("build")
    shutil.copy(result.tex_path, build / "layout.tex")
    for _ in range(2):
        subprocess.run(["pdflatex", "-interaction=nonstopmode", "layout.tex"], cwd=build,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    tex = (build / "layout.tex").read_text(encoding="utf-8")
    log = (build / "layout.log").read_text(encoding="utf-8", errors="ignore")
    return tex, log


def test_no_latex_errors(report_log):
    _, log = report_log
    errors = [e for e in re.findall(r"^! (.*)", log, re.M) if "assets/" not in e]
    assert errors == []


def test_nothing_overflows_the_page(report_log):
    _, log = report_log
    assert "Overfull \\vbox" not in log


def test_header_and_footer_fit(report_log):
    _, log = report_log
    assert not re.search(r"fancyhdr Warning: \\(headheight|footskip) is too small", log)
    assert "could not patch cline" not in log


def test_every_longtable_repeats_its_head(report_log):
    tex, _ = report_log
    tables = re.findall(r"\\begin\{longtable\}.*?\\end\{longtable\}", tex, re.S)
    assert tables
    assert all(r"\endhead" in t for t in tables)
