# Contributing to auto-blog-agent

This covers how to get set up, what kinds of contributions make sense, and how to submit them.

---

## Before you write any code

Open an issue first. Aligning on approach before code gets written saves everyone time, and it's especially important for anything touching the LLM backend, prompt structure, or new publishing targets. Those decisions have downstream effects that are worth talking through upfront.

Small fixes are fine without an issue. Typos, broken links, obvious bugs — just open a PR.

---

## What's welcome

- **New publishing targets** — Dev.to, Hashnode, Twitter/X, or anything with a usable public API
- **LLM backend changes** — swapping OpenRouter, adding model selection logic, local model support
- **Prompt improvements** — better structured output, tone changes, new content fields
- **Bug fixes** — anything broken or behaving in a way it shouldn't
- **Documentation** — clarifications, corrections, gotchas that aren't documented yet
- **Tests** — there are none right now, so adding them is genuinely useful

---

## Local setup

```bash
git clone https://github.com/10doshi12/auto-blog-agent.git
cd auto-blog-agent
uv sync
cp .env.example .env
# fill in .env with your credentials
```

Do a dry run before touching anything. It confirms your environment is wired up correctly:

```bash
DRY_RUN=true uv run python index.py
```

---

## Making changes

```bash
# Branch off main
git checkout -b feat/your-feature

# Make your changes

# Commit with a clear message
git commit -m "feat: add Dev.to publishing target"

# Push and open a PR
git push origin feat/your-feature
```

### Commit message prefixes

| Prefix | When to use |
|---|---|
| `feat:` | New feature or capability |
| `fix:` | Bug fix |
| `docs:` | Documentation only |
| `refactor:` | Code change, no behaviour change |
| `chore:` | Dependency updates, config changes |

---

## Adding a new publishing target

Follow the same pattern as `agent/core/linkedin.py`:

1. Create `agent/core/<platform>.py` with a single publish function
2. Accept the `linkedin_post` string, or add a new field to `LLMOutput` if the platform needs different content
3. Handle errors in isolation — a failed publish should never abort the rest of the pipeline
4. Add the call in `index.py` inside `_process_repo()`, after the existing LinkedIn call
5. Store failed content in `raw_llm_output` using the same recovery pattern already in place

---

## Code style

- Python 3.13, typed throughout
- Pydantic v2 for any new data models
- `httpx` sync client for HTTP calls
- Log via `get_logger(__name__)` from `agent.utils.logger`, not `print()`
- No new dependencies without a discussion first — open an issue

---

## Questions

Open an issue tagged `question`. That's the right place for anything unclear about the architecture or how to approach a contribution.