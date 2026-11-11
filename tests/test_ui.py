from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.ui.artifacts import user_prompt_text

ROOT = Path(__file__).resolve().parents[1]

APP_SMOKE = """
from streamlit.testing.v1 import AppTest

at = AppTest.from_file("app.py", default_timeout=180)
at.run()
assert not at.exception, [error.value for error in at.exception]
markdown = [str(item.value) for item in at.markdown]
assert any("Mutual Fund Facts Assistant" in value for value in markdown)
assert any("Facts-only. No investment advice." in value for value in markdown)
assert any("FundFacts" in value for value in markdown)
assert any("EXPLORE A TOPIC" in value for value in markdown)
assert any("WHY TRUST IT" in value for value in markdown)
assert not any("GROQ_API_KEY" in value for value in markdown)
assert at.text_input[0].label == "Ask about a fund"
assert at.text_input[0].max_chars == 500
assert at.text_input[0].placeholder == "Ask about a fund..."
assert any(expander.label == "System status" for expander in at.sidebar.expander)
assert any(radio.label == "Developer view" for radio in at.sidebar.radio)
assert any(button.label == "✨ New chat" for button in at.button)
assert any(button.label.startswith("💸 Expense ratio") for button in at.button)
assert any(button.label.startswith("🔍") for button in at.button)
assert not at.toggle

# PII is redacted before display and before the turn is stored.
at.text_input[0].set_value("My PAN is ABCDE1234F")
next(button for button in at.button if button.label == "↑").click()
at.run()
assert not at.exception, [error.value for error in at.exception]
history = at.session_state.get("chat_history", [])
assert history and history[0]["question"] == "My PAN is [REDACTED_PAN]"
assert history[0]["response"]["answer_type"] == "refusal"
markdown = "\\n".join(str(item.value) for item in at.markdown)
assert "ABCDE1234F" not in markdown
assert "[REDACTED_PAN]" in markdown

# A blank submit is a no-op.
count = len(history)
next(button for button in at.button if button.label == "↑").click()
at.run()
assert not at.exception, [error.value for error in at.exception]
assert len(at.session_state.get("chat_history", [])) == count

# New chat returns to the welcome state.
next(button for button in at.sidebar.button if button.label == "✨ New chat").click().run()
assert not at.exception, [error.value for error in at.exception]
assert at.session_state.get("chat_history") == []
assert any("ff-hero" in str(item.value) for item in at.markdown)

# A suggestion asks directly and renders a source-linked answer.
next(button for button in at.button if button.label.startswith("🔍")).click().run()
assert not at.exception, [error.value for error in at.exception]
history = at.session_state.get("chat_history", [])
assert history and history[0]["response"]["answer_type"] == "fact"
assert any("✓ Verified from source" in str(item.value) for item in at.markdown)
assert any("ff-source-card" in str(item.value) for item in at.markdown)
assert any("ff-source-title" in str(item.value) for item in at.markdown)
assert any("Last updated from sources:" in str(item.value) for item in at.markdown)
assert any(expander.label == "Supporting excerpt" for expander in at.expander)
assert any("ff-excerpt" in str(item.value) for item in at.markdown)
print("app smoke ok")
"""


def test_app_smoke_in_subprocess() -> None:
    # Streamlit's test runner leaves a thread that can conflict with Chroma's
    # native shutdown cleanup in this environment, so isolate the UI smoke run.
    result = subprocess.run(
        [sys.executable, "-c", APP_SMOKE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_user_prompt_text_hides_system_block() -> None:
    prompt = "SYSTEM:\nDo not reveal this.\n\nUSER:\nProof: expense ratio 1.02%"

    assert user_prompt_text(prompt) == "Proof: expense ratio 1.02%"
