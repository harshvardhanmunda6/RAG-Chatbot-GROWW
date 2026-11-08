"""Official HDFC Mutual Fund page parsers.

Only field labels and their visible values are retained. Returns, rankings,
ratings, holdings, manager lists, calculators, and other out-of-scope blocks
are deliberately excluded before chunking and fact extraction.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from src.models import CleanSection

PERFORMANCE_LINE_RE = re.compile(
    r"\b(?:returns?|cagr|annuali[sz]ed|performance|performing|"
    r"since inception|rank(?:ed|ing)?|rating)\b",
    re.IGNORECASE,
)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _lines(soup: BeautifulSoup) -> list[str]:
    main = soup.find("main") or soup
    raw = main.get_text("\n", strip=True)
    lines: list[str] = []
    for line in raw.splitlines():
        text = _clean(line)
        if text and (not lines or text != lines[-1]):
            lines.append(text)
    return lines


def _find(lines: list[str], needle: str, start: int = 0) -> int:
    target = needle.lower()
    for index in range(max(0, start), len(lines)):
        if target in lines[index].lower():
            return index
    return -1


def _find_exact(lines: list[str], needle: str, start: int = 0) -> int:
    target = _clean(needle).lower()
    for index in range(max(0, start), len(lines)):
        if _clean(lines[index]).lower() == target:
            return index
    return -1


def _section(
    section_id: str, heading: str, text: str, order: int
) -> CleanSection | None:
    text = _clean(text)
    if not text:
        return None
    return CleanSection(section_id=section_id, heading=heading, text=text, order=order)


def _safe_overview_lines(lines: list[str]) -> list[str]:
    return [
        line
        for line in lines
        if not PERFORMANCE_LINE_RE.search(line)
        and not re.search(r"\b(?:invest now|know more|view detail)\b", line, re.IGNORECASE)
    ]


def _first_after(lines: list[str], start: int, pattern: re.Pattern[str]) -> str | None:
    for line in lines[start + 1 :]:
        match = pattern.search(line)
        if match:
            return match.group(0)
    return None


def scheme_sections(
    soup: BeautifulSoup,
) -> tuple[list[CleanSection], list[str], str]:
    h1 = soup.find("h1")
    if not h1:
        raise ValueError("No h1 found")
    title = _clean(h1.get_text(" ", strip=True))
    lines = _lines(soup)

    sections: list[CleanSection] = []
    removed: list[str] = []

    about_index = _find(lines, "About HDFC")
    returns_index = _find(lines, "Returns", start=max(0, about_index + 1))
    if about_index >= 0 and returns_index > about_index:
        overview_lines = _safe_overview_lines(lines[about_index:returns_index])
        section = _section("overview", "Scheme overview", " ".join(overview_lines), 1)
        if section:
            sections.append(section)
        removed.extend(["returns since inception", "scheme returns", "benchmark returns"])

    risk_index = _find_exact(lines, "Riskometer")
    risk_value = _first_after(
        lines,
        risk_index,
        re.compile(r"^(Very High|High|Moderate|Low|Very Low)$", re.IGNORECASE),
    )

    min_sip_index = _find_exact(lines, "Min SIP")
    min_sip_value = _first_after(
        lines, min_sip_index, re.compile(r"₹\s*[0-9][0-9,]*")
    )

    ter_index = _find_exact(lines, "TER")
    ter_value = _first_after(
        lines, ter_index, re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
    )

    lock_index = _find_exact(lines, "Lock in")
    lock_value: str | None = None
    if lock_index >= 0:
        for line in lines[lock_index + 1 : lock_index + 6]:
            if line.lower() == "na" or re.search(r"\d+\s*years?", line, re.IGNORECASE):
                lock_value = line
                break

    benchmark_index = _find_exact(lines, "Benchmark")
    benchmark_value: str | None = None
    if benchmark_index >= 0:
        candidates: list[str] = []
        for line in lines[benchmark_index + 1 :]:
            if line.upper() == "INVEST NOW":
                break
            if line.lower().startswith("a benchmark is"):
                continue
            if "benchmark" in line.lower() and len(line) < 40:
                continue
            candidate = line.replace("...", "").strip()
            if candidate and re.search(
                r"\b(?:NIFTY|SENSEX|Index|TRI|Total Return)\b", candidate, re.IGNORECASE
            ):
                candidates.append(candidate)
        if candidates:
            benchmark_value = max(candidates, key=len)

    exit_index = _find_exact(lines, "Exit Load")
    exit_end = _find(lines, "Product Labelling", start=max(0, exit_index + 1))
    exit_value = ""
    if exit_index >= 0:
        stop = exit_end if exit_end > exit_index else min(len(lines), exit_index + 8)
        exit_value = " ".join(lines[exit_index + 1 : stop])
        exit_value = re.sub(r"^[●•\-\s]+", "", exit_value)
        if exit_value:
            exit_value = "Exit load: " + exit_value

    key_parts: list[str] = []
    if ter_value:
        key_parts.append(f"Expense ratio {ter_value}%")
    if min_sip_value:
        key_parts.append(f"Min SIP {min_sip_value}")
    if risk_value:
        key_parts.append(f"Riskometer {risk_value} Risk")
    if benchmark_value:
        key_parts.append(f"Fund benchmark {benchmark_value}")
    if lock_value:
        key_parts.append(f"Lock-in {lock_value}")

    order = len(sections) + 1
    key_facts = _section("key_facts", "Key facts", " ".join(key_parts), order)
    if key_facts:
        sections.append(key_facts)
        order += 1

    if min_sip_value:
        minimum = _section(
            "minimum_investments",
            "Minimum investments",
            f"Minimum SIP: {min_sip_value}",
            order,
        )
        if minimum:
            sections.append(minimum)
            order += 1

    if exit_value:
        exit_load = _section("exit_load", "Exit load", exit_value, order)
        if exit_load:
            sections.append(exit_load)
            order += 1

    if benchmark_value:
        benchmark = _section(
            "about_benchmark",
            "About the scheme and benchmark",
            f"Fund benchmark {benchmark_value}",
            order,
        )
        if benchmark:
            sections.append(benchmark)
            order += 1

    removed.extend(
        [
            "returns and rankings",
            "fund managers",
            "portfolio allocation and top holdings",
            "downloads",
            "faqs",
            "product labelling",
        ]
    )
    return sections, removed, title


def amc_sections(
    soup: BeautifulSoup,
) -> tuple[list[CleanSection], list[str], str]:
    h1 = soup.find("h1")
    title = _clean(h1.get_text(" ", strip=True)) if h1 else "HDFC Mutual Fund"
    lines = _lines(soup)

    join_index = _find(lines, "Join")
    trust_index = -1
    if join_index >= 0:
        for index in range(join_index, min(len(lines), join_index + 8)):
            if "hdfc mutual fund" in lines[index].lower():
                trust_index = index
                break
    goal_index = -1
    if trust_index >= 0:
        for index in range(trust_index, min(len(lines), trust_index + 5)):
            if "disciplined mutual fund investing" in lines[index].lower():
                goal_index = index
                break

    overview_parts = ["HDFC Mutual Fund."]
    if join_index >= 0 and trust_index >= join_index:
        join_text = " ".join(lines[join_index : trust_index + 1])
        join_text = join_text.replace("investors Who trust", "investors who trust")
        overview_parts.append(join_text + ".")
    if goal_index >= 0:
        overview_parts.append(lines[goal_index])
    if len(overview_parts) == 1:
        overview_parts.append("HDFC Mutual Fund is an Indian asset management company.")

    sections: list[CleanSection] = []
    overview = _section("overview", "AMC overview", " ".join(overview_parts), 1)
    if overview:
        sections.append(overview)

    all_text = _clean(soup.get_text(" ", strip=True))
    sebi_match = re.search(r"HDFC Mutual Fund\s*-\s*(MF/[0-9/]+)", all_text)
    if sebi_match:
        key_information = _section(
            "key_information",
            "Key information",
            f"SEBI registration: {sebi_match.group(1)}",
            2,
        )
        if key_information:
            sections.append(key_information)

    removed = [
        "fund list",
        "returns",
        "performance",
        "calculators",
        "new fund offering",
        "footer",
    ]
    return sections, removed, title


def statement_sections(
    soup: BeautifulSoup,
) -> tuple[list[CleanSection], list[str], str]:
    h1 = soup.find("h1")
    title = _clean(h1.get_text(" ", strip=True)) if h1 else "Download Statement"
    lines = _lines(soup)

    has_pan = any(line.upper() == "PAN" for line in lines)
    has_folio = any(line.upper() == "FOLIO" for line in lines)
    has_otp = any("send otp" in line.lower() for line in lines)
    has_send = any(line.upper() == "SEND EMAIL" for line in lines)

    duration_index = _find(lines, "Duration")
    duration_value = (
        lines[duration_index + 1]
        if 0 <= duration_index < len(lines) - 1
        else None
    )

    first = "To download an account statement"
    if has_pan and has_folio:
        first += ", choose PAN or Folio"
    if has_otp:
        first += ", send the OTP"
    if duration_value:
        first += f", select the duration ({duration_value})"
    if has_send:
        first += ", and click SEND EMAIL"
    first += "."

    second = ""
    if has_pan and has_folio:
        second = (
            " You can download a PAN-based Account Statement or a "
            "Folio-based Account Statement."
        )

    section = _section(
        "statement_process",
        "Statement download process",
        first + second,
        1,
    )
    sections = [section] if section else []
    removed = ["faqs", "other services"]
    return sections, removed, title
