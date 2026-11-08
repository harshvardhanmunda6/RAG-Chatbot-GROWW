"""Tests for the approved source-loading phase."""

from __future__ import annotations

from bs4 import BeautifulSoup

from src.cleaner import _amc_sections, _scheme_sections
from src.config import load_settings
from src.source_registry import is_url_allowed, load_registry


def _scheme_html() -> str:
    return """
    <div class="layout-main width100">
      <header>
        <h1>HDFC Test Fund Direct Growth</h1>
        <span>Equity Large Cap Very High Risk</span>
      </header>
      <div class="fundDetails_fundDetailsContainer__x">
        NAV: 01 Jan '26 Rs 100 Min. for SIP Rs 100 Expense ratio 1.02% Rating 5
      </div>
      <div class="returnStats_returnStatsContainer__x">+8.76 % 3Y annualised</div>
      <div class="returnCalculator_container__x">Historic returns +18.63 %</div>
      <div id="holdingsContainer">Holdings ICICI Bank 9.79%</div>
      <div class="exitLoadStampDutyTax_container__x">
        <div class="exitLoadStampDutyTax_contentContainer__x">
          Exit load Exit load of 1% if redeemed within 1 year
          Stamp duty on investment: 0.005%
          Tax implication If you redeem within one year, returns are taxed.
        </div>
      </div>
      <div class="investmentObjective_container__x">
        About the fund Fund benchmark NIFTY 100 Total Return Index
        The fund is rated Very High risk.
      </div>
      <div class="fundHouse_container__x">
        Fund house HDFC Mutual Fund Rank (total assets) #2 in India
      </div>
      <div>Minimum investments Min. for 1st investment Rs 100 Min. for SIP Rs 100</div>
    </div>
    """


def test_scheme_cleaner_keeps_only_allowed_sections():
    soup = BeautifulSoup(_scheme_html(), "lxml")
    sections, removed, title = _scheme_sections(soup)

    assert title == "HDFC Test Fund Direct Growth"
    by_id = {section.section_id: section for section in sections}
    assert set(by_id) == {
        "overview",
        "key_facts",
        "minimum_investments",
        "exit_load",
        "about_benchmark",
        "fund_house",
    }
    assert "Rating 5" not in by_id["key_facts"].text
    assert "Tax implication" not in by_id["exit_load"].text
    assert "Exit load of 1%" in by_id["exit_load"].text
    assert "Rank (total assets)" not in by_id["fund_house"].text
    assert any("returnStats" in item for item in removed)
    assert any("returnCalculator" in item for item in removed)
    assert any("holdingsContainer" in item for item in removed)


def test_amc_cleaner_keeps_overview_and_key_information():
    html = """
    <div class="valign-wrapper"><h1>HDFC Mutual Fund</h1></div>
    <div class="valign-wrapper vspace-between amcMainPage_infoContainer__x">
      AUM Rs 9,34,075 Cr No of Schemes 84 AMC Age 26 years
    </div>
    <div class="bodyLarge">Marketing description with performance claims.</div>
    <div>
      <h2>Key information about HDFC Mutual Fund</h2>
      <table>
        <tr><td>Mutual fund name</td><td>HDFC Mutual Fund</td></tr>
        <tr><td>AMC Incorporation Date</td><td>10 Dec 1999</td></tr>
      </table>
    </div>
    """
    sections, _, title = _amc_sections(BeautifulSoup(html, "lxml"))
    by_id = {section.section_id: section for section in sections}

    assert title == "HDFC Mutual Fund"
    assert "AUM" in by_id["overview"].text
    assert "10 Dec 1999" in by_id["key_information"].text
    assert "Marketing description" not in by_id["overview"].text


def test_registry_has_seed_and_official_sources():
    settings = load_settings()
    sources, errors = load_registry(settings)

    assert errors == []
    assert len(sources) == 17
    seeds = [source for source in sources if source.source_type == "seed"]
    official = [
        source
        for source in sources
        if source.source_type in {"amc", "sebi", "amfi", "registrar"}
    ]
    assert len(seeds) == 5
    assert len(official) == 12
    assert all(source.citable is False for source in seeds)
    assert sum(1 for source in official if source.citable) == 9
    assert sum(1 for source in official if not source.citable) == 3
    assert all(
        is_url_allowed(source.url, settings.sources["allowed_domains"]) for source in sources
    )
