# Disclaimer

**Facts-only. No investment advice.**

This project is a local demonstration of a retrieval-augmented, facts-only FAQ assistant. It is not a financial adviser, broker, distributor, portfolio manager, tax adviser, or recommendation engine. It does not recommend buying, selling, holding, switching, stopping, or ranking any mutual fund, scheme, plan, or portfolio.

## What the assistant does

- Answers a limited set of factual questions about HDFC Mutual Fund and the listed direct-growth schemes.
- Uses public official HDFC AMC and SEBI pages for citations.
- Shows one official source link and `Last updated from sources: YYYY-MM-DD` with each factual answer.
- Refuses investment, tax, portfolio, ranking, and performance/return questions.
- Blocks PAN, Aadhaar, account/folio numbers, OTPs, emails, and phone numbers before retrieval or generation.
- Does not retrieve, display, or store personal statements or account data.

## What the assistant does not do

- It does not provide investment, tax, legal, or portfolio advice.
- It does not calculate, compare, rank, or predict returns, CAGR, NAV, alpha, beta, or performance.
- It does not tell you which fund is better, safer, or suitable for your goals.
- It does not verify your identity, access your folio, or download your statement.
- It does not provide real-time NAV or live market data.
- It does not replace the official scheme information documents, factsheets, notices, or your own professional advice.

## Source and accuracy limits

- The corpus is intentionally small and limited to the approved source registry in `data/00_sources/sources.csv`.
- Groww pages are used only as non-citable seed/cross-check material and are never cited.
- Factual values such as expense ratios, exit loads, minimum SIPs, riskometers, and benchmarks can change. The date on each answer records when the source was retrieved; always verify the current value on the official source.
- The assistant may return a no-answer or refusal when the approved evidence does not support a safe answer. That is intentional.
- The local demo has no hosted public URL or recording.

## Privacy

- Do not enter PAN, Aadhaar, account/folio numbers, OTPs, emails, phone numbers, passwords, or other personal data.
- The guardrails block these inputs and do not echo them into traces or answers.
- The UI has no login, uploads, or persistent chat history.
- Evaluation uses synthetic PII values injected at runtime; they are redacted and must not appear in persisted artifacts.

## Affiliation

This is an independent demo. It is not affiliated with, endorsed by, or sponsored by HDFC Asset Management Company Limited, HDFC Mutual Fund, SEBI, AMFI, Groww, CAMS, KFintech, Google, or StitchWithGoogle. All trademarks and names belong to their respective owners.

## Official resources

- HDFC Mutual Fund: <https://www.hdfcfund.com/>
- HDFC factsheets: <https://www.hdfcfund.com/mutual-funds/factsheets>
- HDFC request statement: <https://www.hdfcfund.com/services/additional-info/request-statement>
- SEBI Investor Education: <https://investor.sebi.gov.in/>

Mutual fund investments are subject to market risks. Read all scheme-related documents carefully before investing.
