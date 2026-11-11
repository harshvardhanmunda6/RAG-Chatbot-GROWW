# FundFacts Lovable restyle

The UI follows the linked Lovable prototype at
[newai-iota-indol.vercel.app](https://newai-iota-indol.vercel.app/). That
prototype is the visual source of truth for branding, sidebar, hero, banner,
chat messages, composer, spacing, colors, and responsive behaviour.

The earlier `stitch_hdfc_fund_facts_assistant.zip` export was never present in
the workspace and is not required for this restyle.

## Implemented design

- Sidebar: `assets/logo.svg` shield mark, `FundFacts` wordmark, `Mutual fund
  assistant` subtitle, `✨ New chat`, two stat cards (`HDFC funds`, `Public
  sources`), nine topic pills under `EXPLORE A TOPIC`, and a four-line `WHY
  TRUST IT` note.
- Main: centered `Mutual Fund Facts Assistant` hero, subtitle, three full-width
  suggestion buttons, and a gradient facts-only banner.
- Chat: `st.chat_message` with avatars hidden; mint user bubbles right-aligned;
  white assistant cards with a status label, answer text, inline `🔗` source
  URL, and `Last updated from sources: YYYY-MM-DD`.
- Status labels: `✓ Verified from source` (green), `🛡 Facts-only policy`
  (red), `Not in sources` (blue), and `Temporarily unavailable` (red).
- Loading: three pulsing green dots inside an assistant card.
- Composer: fixed rounded `st.form`, borderless 500-character input, round green
  `↑` send button disabled while empty, and Enter-to-submit.
- Tokens: surface `#FAF8FF`, low `#F2F3FF`, mid `#EAEDFF`, ink `#131B2E`,
  muted `#3C4A43`, primary `#006C4F`, accent `#00B386`, link `#005AC2`, error
  `#BA1A1A`; Inter body text and Plus Jakarta Sans headings.
- Pipeline, Sources, and Evaluation remain available from the collapsed
  `System status` expander in the sidebar.
- The linked prototype has no dark-mode toggle, RECENT history, source-card
  expander, copy/feedback controls, or live character counter, so those were
  removed to match it.

## Verification

- AppTest covers startup, branding copy, the 500-character cap, suggestion and
  topic buttons, PII redaction before display, blank submits, New chat, the
  collapsed status/developer selector, and a source-linked answer.
- A local `streamlit run app.py` health check returns HTTP 200.
- Browser-driven screenshot verification is unavailable in this environment.

## Safety rules that the design preserves

- No PAN, Aadhaar, account/folio, OTP, email, or phone input fields.
- No file upload.
- No login.
- No persistent chat history beyond the browser session.
- No system prompt or hidden reasoning in the browser.
- No secrets in the browser.
- Always show an official citation when a factual answer has one, plus the
  last-updated date.
- Keep the facts-only disclaimer and PII blocking.
- Keep corpus and LLM diagnostics out of the main chat.
