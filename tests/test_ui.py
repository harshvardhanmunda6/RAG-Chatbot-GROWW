from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.ui.artifacts import user_prompt_text

ROOT = Path(__file__).resolve().parents[1]

APP_SMOKE = """
from streamlit.testing.v1 import AppTest

at = AppTest.from_file("app.py", default_timeout=60)
at.run()
assert not at.exception, [error.value for error in at.exception]
labels = [tab.label for tab in at.tabs]
assert labels[:3] == ["Ask", "Pipeline", "Sources"], labels
assert "Evaluation" in labels, labels
assert any(title.value == "HDFC Mutual Fund Facts Assistant" for title in at.title)
assert not any("GROQ_API_KEY" in str(item.value) for item in at.markdown)
assert at.text_input[0].max_chars == 500
assert any(expander.label == "System status" for expander in at.sidebar.expander)
assert any("mf-fixed-footer" in str(item.value) for item in at.markdown)

at.text_input[0].set_value("   ")
next(button for button in at.button if button.label == "Ask").click().run()
assert any("Enter a question first." in warning.value for warning in at.warning)
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
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_user_prompt_text_hides_system_block() -> None:
    prompt = "SYSTEM:\nDo not reveal this.\n\nUSER:\nProof: expense ratio 1.02%"

    assert user_prompt_text(prompt) == "Proof: expense ratio 1.02%"
