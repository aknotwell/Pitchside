"""Print a Markdown summary of a pytest JUnit report, for the GitHub Actions run page.

Usage: python scripts/test_summary.py pytest-report.xml >> "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def summarize(report: Path) -> str:
    if not report.exists():
        return "### ❌ Tests\n\nNo test report: the test step didn't run."
    suite = ET.parse(report).getroot()
    if suite.tag == "testsuites":
        suite = suite.find("testsuite")
    total, failed, errors, skipped = (
        int(suite.get(k, 0)) for k in ("tests", "failures", "errors", "skipped")
    )
    passed = total - failed - errors - skipped
    icon = "✅" if total > 0 and failed + errors == 0 else "❌"
    lines = [
        f"### {icon} Tests: {passed}/{total} passed",
        "",
        "| Passed | Failed | Errors | Skipped | Time |",
        "|---|---|---|---|---|",
        f"| {passed} | {failed} | {errors} | {skipped} | {float(suite.get('time', 0)):.1f} s |",
    ]
    broken = [
        f"{case.get('classname')}::{case.get('name')}"
        for case in suite.iter("testcase")
        if case.find("failure") is not None or case.find("error") is not None
    ]
    if broken:
        lines += ["", "**Failing tests**", ""] + [f"- `{name}`" for name in broken]
    return "\n".join(lines)


if __name__ == "__main__":
    print(summarize(Path(sys.argv[1])))
