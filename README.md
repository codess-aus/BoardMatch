# BoardMatch

**An AI agent for finding and winning paid board seats.**

Paid board and NED (non-executive director) positions are an *invisible market* — rarely
advertised, heavily network-driven, and opaque about remuneration. BoardMatch surfaces that
hidden market and coaches the candidate through it.

## What it does

| Capability | Module | What you see in the demo |
|---|---|---|
| **Opportunity discovery** | `boardmatch/discovery.py` | Aggregates board vacancies across sources and flags **paid vs. voluntary**, with remuneration where disclosed |
| **Fit & gap analysis** | `boardmatch/fit.py` | Scores the candidate 0–100 against each board's stated needs and names the *specific* gaps |
| **Positioning coach** | `boardmatch/coach.py` | Drafts a board CV (very different from an executive resume), a director bio, and outreach messages |
| **Network path-finder** | `boardmatch/network.py` | Ranks warm introduction routes ("Alex Chen sits on that board") |
| **Readiness tracker** | `boardmatch/readiness.py` | Pipeline dashboard plus a board-readiness score that improves as gaps close |

## Scoping

Following the hackathon scoping tip, **one well-structured source is treated as live** — a
government board vacancy register (`boardmatch/data/gov_vacancies.json`) — while ASX
announcements, AICD listings, the not-for-profit register and LinkedIn postings are **mocked**
(`boardmatch/data/mock_sources.json`). Every source is normalised through the same adapter
(`discovery.load_source`), so swapping in a real HTTP or Azure AI Agent Service connector does
not change any downstream code.

## Run it

```bash
pip install -r requirements.txt

# Two-minute CLI demo: paid seats, fit scores, gaps, warm intros, board CV, outreach
python -m boardmatch

# Web app + API at http://127.0.0.1:8000 (OpenAPI docs at /docs)
uvicorn boardmatch.api:app --reload
```

Tests:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/opportunities?paid_only=true&min_fee_aud=50000&sector=Health` | Discovery + fit + gaps + warm intro path |
| `GET /api/opportunities/{id}` | Single opportunity with full analysis |
| `GET /api/opportunities/{id}/intro-paths` | All ranked introduction routes |
| `POST /api/coach/board-cv?opportunity_id=...` | Tailored board CV |
| `POST /api/coach/bio` | Director bio |
| `POST /api/coach/outreach?opportunity_id=...` | Outreach to the nominations committee |
| `POST /api/tracker` | Move an application through the pipeline |
| `GET /api/readiness` | Board-readiness score, pipeline and next actions |

## Microsoft tech

The demo runs fully offline with deterministic logic so it is reproducible on a hackathon
stage; each capability has a documented production integration point:

- **Azure OpenAI / Azure AI Agent Service / Semantic Kernel** — set `AZURE_OPENAI_ENDPOINT`,
  `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_DEPLOYMENT` and `boardmatch/coach.py` generates the
  board CV, bio and outreach with the model instead of templates (each response reports which
  engine produced the draft). Without credentials it falls back to templates.
- **Azure AI Document Intelligence** — `profiles.candidate_from_cv_text` mirrors the shape of a
  Document Intelligence result, so the real parser can replace it directly.
- **Microsoft Graph** — `Candidate.connections` models the network graph that powers
  `network.paths_for`; the outreach drafts are ready to send via Outlook.
- **Power BI / Fabric** — `GET /api/readiness` returns the dashboard dataset (score components,
  stage counts, next actions).
- **Copilot Studio** — the REST API doubles as the tool surface for a conversational front end.

## Scoring model

Fit score (0–100): required skills 60, desirable skills 20, sector match 10, governance
credentials 10 (full marks for the AICD Company Directors Course, half for existing board
experience). Board-readiness score (0–100): credentials 40, core governance skill coverage 30,
pipeline momentum 30.

## Note on data

All opportunities, the sample candidate and the network are **synthetic demo data**. No real
person, organisation or vacancy is represented.
