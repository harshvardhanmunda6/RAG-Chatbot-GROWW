# StitchWithGoogle UI export

The canonical UI design for this project is:

```text
design/stitch_hdfc_fund_facts_assistant.zip
```

That export is the **source of truth** for layout, spacing, typography, colors, component states, responsive behaviour, and copy hierarchy. The Streamlit implementation must follow it. If the export and the code disagree, the export wins and the Streamlit code is updated.

## Status

The zip was **not present in the workspace** when this file was created. The current Streamlit UI implements the same information architecture and safety rules (Ask, Pipeline, Sources, Evaluation; no PII fields, uploads, login, persistent chat history, system prompt, hidden reasoning, or secrets), but it has not been pixel-aligned to the Stitch screens.

To finish the integration:

1. Upload `stitch_hdfc_fund_facts_assistant.zip` to this directory.
2. Unzip a working copy into `design/stitch_hdfc_fund_facts_assistant/`; keep the original zip.
3. Review the exported screens and notes.
4. Port design tokens into `src/ui/common.py`.
5. Port component markup and states into `src/ui/ask.py`, `src/ui/pipeline.py`, `src/ui/sources.py`, and `src/ui/evaluation.py`.
6. Verify desktop and 390 px layouts, including empty, no-answer, refusal, PII-block, error, and clear-session states.
7. Record screenshots and any deliberate deviations here.
8. Update `architecture.md` §9.6 and `implementation.md` §9.9 with the verified export version.

## Safety rules that the design must preserve

- No PAN, Aadhaar, account/folio, OTP, email, or phone input fields.
- No file upload.
- No login.
- No persistent chat history.
- No system prompt or hidden reasoning in the browser.
- No secrets in the browser.
- Always show exactly one official citation and `Last updated from sources: YYYY-MM-DD`.
- Keep the clear-session control and the facts-only disclaimer.
