# Demand Intake

A configurable Streamlit interview that captures business demands and prepares a draft for engineering refinement. Application rules select every question and its missing fields. The LLM interprets free text, phrases selected questions using retrieved context, and generates the final narrative; it never plans the interview.

## Run

Use Python 3.13. From this folder:

```bash
python -m venv .venv
```

Activate the environment (Windows PowerShell):

```powershell
.venv\Scripts\Activate.ps1
```

Then:

```bash
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set `OPENAI_API_KEY`. Optionally override `OPENAI_MODEL`.

```bash
python build_rag_index.py
python -m streamlit run app.py
```

Index construction embeds the corpus as a separate preparation step. During an interview, retrieval is invoked only after the domain answer has status `answered`. Missing, uncertain and unsupported domains cause no retrieval or query embedding calls. The shipped synthetic knowledge covers HR and Finance. Other domains continue with generic discovery.

If the index is absent or stale, the accepted answer remains saved and the application continues with its configured question. Refresh the index after changing knowledge documents or embedding settings.

## Configuration

| File | Purpose |
|---|---|
| `config/fields.yaml` | Field IDs, labels, types, descriptions, allowed values, required status and applicability conditions. |
| `config/questions.yaml` | Question text, collection parts, conditions, phases, priorities, answer attempts, dropdown choices, retrieval eligibility and question-specific `context_fields`. These fields govern both the RAG query and the facts sent for contextual wording. |
| `config/generic.yaml` | Flow, dependencies, model and RAG settings, prompts, UI text, output and presentation policies. |

`Configuration(path)` accepts the generic settings file and loads `fields.yaml` and `questions.yaml` beside it. There is one current configuration layout; the old combined layout is removed.

## File relationships

| File | Responsibility and connections |
|---|---|
| `app.py` | Renders snapshots from `IntakeEngine`; sends versioned text, selection, confirmation and generation commands. Displays contextual questions without exposing reference captions or passages. References stay available internally for interpretation. |
| `configuration.py` | Loads the three configuration files into contracts from `models.py`; validates IDs and references using `conditions.py`. |
| `models.py` | Pydantic contracts for fields, questions, answers, extraction, visits and interview state. |
| `conditions.py` | Evaluates configured predicates and exposes referenced fields for validation and planning. |
| `context_manager.py` | Sole writer of interview state. Validates updates, applies invalidation and applicability, records answers and ambiguities, and commits private drafts. |
| `planner.py` | Selects the highest-priority eligible configured question within the current phase. Retains unanswered questions, tracks attempts, checks readiness and joins only missing question parts. |
| `intake_engine.py` | Coordinates state, planning, retrieval, contextual wording, extraction and output in serialized transactions. Accepted turns commit once; failed extraction or generation rolls back. |
| `llm_service.py` | Three structured operations: interpret free text, contextualize the application-selected question when passages exist, and generate a confirmed demand narrative. Builds the final response schema from configured generated sections. |
| `rag_service.py` | Chunks the YAML corpus, builds a local JSON vector index, filters by a known supported domain and retrieves relevant passages. Caches query vectors and reuses unchanged document embeddings. |
| `build_rag_index.py` | Command-line entry point for preparing or refreshing the index. |
| `demand_service.py` | Assembles output, copies supplied requirements and constraints, validates evidence for classification, routes through configuration and creates an idempotent in-memory mock Jira issue. |
| `knowledge_base/hr.yaml` | Synthetic HR reference documents; used only by RAG. |
| `knowledge_base/finance.yaml` | Synthetic Finance reference documents; used only by RAG. |
| `data/README.md` | Explains preparation of the generated index. |
| `tests/` | Offline extraction/provider stubs, deterministic interview and retrieval checks, output checks, transaction checks and Streamlit UI tests. |
| `requirements.txt` | Runtime dependencies. |
| `requirements-dev.txt` | Runtime dependencies plus pytest. |
| `pytest.ini` | Makes project imports available to tests. |
| `Dockerfile` | Builds a Streamlit container and includes configuration, knowledge and prepared index data. |
| `.env.example` | API key and model override template; contains no credentials. |

## Turn flow and LLM payloads

1. The application displays a configured question.
2. Dropdown selections update state directly without an LLM call. Free text uses one interpretation call.
3. `ContextManager` validates supported changes and clears dependent values when necessary.
4. `Planner` selects the next eligible question and composes only its missing parts.
5. If the selected question enables retrieval and the domain is answered and supported, the application queries RAG using the selected missing-part question plus answered values from that question’s `context_fields`. Domain is an exact filter; the query ranks chunks semantically.
6. When passages are returned, a wording call receives only the selected question, target fields, missing-part text, fixed dropdown labels, answered question-specific context facts and bounded passages. It proposes a concise contextual question. Target field IDs and source IDs are checked; provider failure, invalid output or unused passages fall back to the configured question.
7. The application displays only the contextual question; reference captions and passages are hidden. Reference information never becomes a confirmed answer without user support.
8. Once required discovery is resolved, the user reviews and confirms the summary. Generate Demand invokes one narrative generation call and creates the mock Jira issue.

Interpretation receives the latest message, displayed question and its collection field IDs, compact non-missing captured answers, field definitions, and bounded retrieved passages where available. It receives no conversation history, full state, question catalogue, phase rules or YAML documents. The field catalogue remains necessary to extract multiple facts and corrections from arbitrary free text. Captured uncertainty is included so subsequent corrections can be interpreted accurately.

Generation receives compact captured facts, the resolved route, title limits, proposed milestone stages and application-owned refinement field IDs. Section instructions and assessment taxonomy are encoded in the structured response schema. UI, Jira, routing rules, copied sections and presentation settings are not sent to the model.

Question selection requires zero LLM calls. Contextual wording adds one call only when retrieval supplies passages. Dropdown answer extraction requires no model call, although advancing to a RAG-enabled question can trigger contextual wording. Missing/uncertain/unsupported domains and empty retrieval results use configured wording without that extra call.

For example: captured domain HR and requested capability “Check leave balance” lead the application to select “Which application is being changed?”. Retrieved information may mention Workday. The wording call can ask “Is this change for Workday, or another application?”. It cannot choose an integration or ownership question. Workday remains a candidate, not a confirmed application.

The application verifies returned target field IDs and citation IDs. Instructions constrain the wording itself; metadata checks cannot prove that natural-language wording stays perfectly within scope. Live model behavior should be assessed in manual demos. Dropdown choices remain fixed even when the displayed question is contextualized.

The obsolete combined configuration, pre-domain retrieval, history packaging, hidden business objective and unused request-type taxonomy are removed. Question wording has one active, scoped path. Active validation, dependency handling, answer-attempt limits and transaction safeguards remain because they serve the current flow.

## Verification

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use fake model responses and embeddings and do not require an API key. They cover domain gating, relevance, index refresh, deterministic missing-part questions, corrections, extraction rollback, dropdowns, confirmation and generation. Live model quality and real embedding similarity require a manual run with an API key. Jira is a mock and does not persist between process restarts.

## Docker

Prepare the index before building if retrieval is needed:

```bash
python build_rag_index.py
docker build -t demand-intake .
docker run -p 8501:8501 --env-file .env demand-intake
```

Open `http://localhost:8501`.

## Output and clarification improvements

Ambiguous organizational scope gets one configured clarification with deterministic dropdown choices. Users can explicitly defer it; it then remains an engineering follow-up. Captured owner, users, scope, target timing, timing driver and timeline commitment are copied into Project charter details rather than relying on model narrative.

Assessment evidence uses separate `{field_id, quote}` entries, allowing multiple grounded sources. Every quote is checked against an answered field. Unsupported assessments are normalized to Not confirmed in both JSON and the classification table. Complexity is an initial estimate. Evidence grounding confirms the source, not the correctness of a subjective estimate.

Generation requests one concise story and observable acceptance checks. Assembly retains the first story and moves recognized refinement lines out of acceptance into follow-ups; patterns are configurable in `output.narrative_cleanup`. This bounded cleanup cannot eliminate every possible narrative duplication. Live responses still require review. Contextual wording is instructed to use at most one relevant example and avoid repeating the entire request.
