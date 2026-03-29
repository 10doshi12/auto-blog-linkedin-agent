# auto-blog-linkedin-agent

> Monitors your GitHub, writes blog posts and LinkedIn content, and publishes them. Every week. You don't have to do anything.

---

## Part of the 6-Month AI & ML Engineering Challenge

This is **Week 1** of a personal challenge: one new AI agent or ML-powered project built every week for 6 months, from scratch, no exceptions.

The point isn't to build polished products. It's to cover real ground fast, across the parts of AI/ML engineering that actually matter: LLM orchestration, prompt design, agent architecture, vector databases, fine-tuning, and getting things running in production. Each week targets a different slice of that.

> **This week's focus:** Automation pipelines with LLMs, structured JSON output via prompt engineering, async Python agent design, and CI/CD-driven publishing.

Follow the full challenge: [try.except website](https://tryexcept.app)

---

## What It Does

Every week, `auto-blog-linkedin-agent` runs on a GitHub Actions cron and does the following:

1. **Scans** your public repos for anything created in the current IST week
2. **Reads** each new repo's README to understand what got built
3. **Calls an LLM** (via OpenRouter) to generate two pieces of content per repo: a story-driven blog post for general readers and a technical deep-dive for developers
4. **Saves** the blog post to Supabase (`posts` table) and the project entry to the `projects` table
5. **Posts to LinkedIn** using the ugcPosts API when LinkedIn credentials are configured
6. **Records** every processed repo in an audit table so it doesn't reprocess anything across runs

Every week's project gets written up and published. You don't have to remember.

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Language | Python 3.13 | Modern async, clean typing |
| Package manager | `uv` | Fast, deterministic, lockfile-native |
| LLM gateway | OpenRouter (raw `httpx`) | Model flexibility, no SDK lock-in |
| Database | Supabase (PostgreSQL) | Hosted Postgres + auto REST API |
| LinkedIn | ugcPosts API (OAuth) | Official API, durable long-lived tokens |
| GitHub API | REST with fine-grained PAT | Read-only, least-privilege access |
| Schema validation | Pydantic v2 | Strict LLM output contracts |
| HTTP client | `httpx` (sync) | Simple, no async complexity for API calls |
| Env loading | `python-dotenv` | 12-factor config |
| CI/CD | GitHub Actions (weekly cron) | Serverless, free, zero infra |

---

## Repository Structure

```
auto-blog-agent/
├── agent/
│   ├── config/
│   │   ├── settings.py          # Env vars, SecretStr wrappers
│   │   └── agent_config.py      # Pure constants, frozen Pydantic models
│   ├── core/
│   │   ├── github.py            # GitHub API, repo fetching, README extraction
│   │   ├── llm.py               # OpenRouter calls, LLMOutput validation + retry
│   │   ├── linkedin.py          # ugcPosts API publisher
│   │   └── database.py          # Supabase client, all DB read/write operations
│   ├── prompts/
│   │   └── templates.py         # SYSTEM_PROMPT + build_user_prompt()
│   ├── schemas/
│   │   ├── llm_output.py        # LLMOutput Pydantic model (8 fields)
│   │   ├── blog_post.py         # BlogPost + BlogPostInsert models
│   │   └── project.py           # Project + ProjectInsert models
│   └── utils/
│       └── logger.py            # get_logger(), stdout, GitHub Actions log format
├── index.py                     # Entry point, main() + async pipeline
├── pyproject.toml
├── uv.lock
├── .env.example                 # All keys, no real values
└── .github/
    └── workflows/
        └── agent.yml            # Weekly cron, runs every Monday IST
```

---

## How It Works

### Pipeline overview

The agent runs in two phases.

**Phase 1: Sync setup** (sequential, each step depends on the previous)

```
main()
 ├── 1. check_openrouter_connection()   → abort early if LLM unreachable
 ├── 2. get_current_week()              → read site_config.current_week from Supabase
 ├── 3. data_to_send_LLM(owner)         → fetch GitHub repos created this IST week + their READMEs
 └── 4. get_processed_repo_ids()        → load already-processed repo IDs for dedup
```

**Phase 2: Async processing** (concurrent per repo, IO-bound)

```
asyncio.gather(
  _process_repo(repo_1),  ─┐
  _process_repo(repo_2),   ├── all repos processed concurrently
  _process_repo(repo_3),  ─┘
)

Per repo:
  ├── generate_blog_content(readme)       → LLM call, validates JSON output
  ├── BlogPostInsert.from_llm_output()    → build blog payload
  ├── ProjectInsert.from_llm_output()     → build project payload
  ├── save_blog_post(post)                → insert into posts table
  ├── save_project(project)               → insert into projects table
  ├── post_to_linkedin(linkedin_post)     → best-effort, isolated error handling
  └── mark_repo_processed(...)            → always runs, success or failure
```

> **Why `asyncio.to_thread`?** Both `httpx` (sync client) and `supabase-py` are synchronous libraries. Rather than rewriting them, `asyncio.to_thread` pushes each call into a thread pool. The event loop stays unblocked, repos get processed concurrently, and nothing had to change in the underlying libs.

### LLM output contract

The agent tells the LLM to return exactly this JSON, no markdown fences, no preamble:

```json
{
  "title": "10–120 chars",
  "excerpt": "40–280 chars, one or two sentence summary",
  "content": "150+ words, non-technical story-driven Markdown blog post",
  "technical_content": "150+ words, architecture/stack deep-dive Markdown",
  "category": "ai-ml | full-stack",
  "metric": "max 100 chars, one concrete real-world metric",
  "tags": ["lowercase-kebab-case", "1–8 tags"],
  "linkedin_post": "30–3000 chars, plain text, no Markdown"
}
```

If the LLM returns bad JSON or fails Pydantic validation, it retries with the fallback model. If both fail, the repo gets marked `"failed"` in the audit table and the run continues with the next one.

### Content routing

| LLM Field | Destination |
|---|---|
| `title` | `posts.title` + `projects.title` |
| `excerpt` | `posts.excerpt` + `projects.description` |
| `content` | `posts.content` (reader-facing blog) |
| `technical_content` | `projects.content` (developer-facing write-up) |
| `category` | `projects.category` |
| `metric` | `projects.metric` |
| `tags` | `posts.tags` + `projects.tags` |
| `linkedin_post` | Published to LinkedIn; stored in DB on failure |

---

## Database Schema

### `posts` — blog content

```sql
id                    uuid        PK, gen_random_uuid()
slug                  text        NOT NULL
title                 text        NOT NULL
excerpt               text        NOT NULL
content               text        NOT NULL
tags                  text[]      NOT NULL, default '{}'
reading_time_minutes  integer     NOT NULL
week_number           integer     NULLABLE
published             boolean     NOT NULL, default false
published_at          timestamptz NULLABLE
created_at            timestamptz NOT NULL, default now()
```

### `projects` — portfolio entries

```sql
id              uuid        PK, gen_random_uuid()
slug            text        NOT NULL
title           text        NOT NULL
description     text        NOT NULL
content         text        NULLABLE
category        text        NOT NULL    -- "ai-ml" or "full-stack"
tags            text[]      NOT NULL, default '{}'
metric          text        NULLABLE
github_url      text        NULLABLE
live_url        text        NULLABLE
has_detail_page boolean     NOT NULL, default false
featured        boolean     NOT NULL, default false
display_order   integer     NOT NULL, default 99
created_at      timestamptz NOT NULL, default now()
```

### `site_config` — single-row config

```sql
current_week  integer   -- week number stamped onto blog posts
week_focus    text      -- informational only, not used by agent
```

### `agent_processed_repos` — dedup + audit trail

```sql
repo_id         bigint      PK  (GitHub's numeric repo ID)
repo_name       text        NOT NULL
status          text        NOT NULL    -- "success" | "skipped" | "failed"
skip_reason     text        NULLABLE
blog_post_id    uuid        NULLABLE, FK -> posts(id) ON DELETE SET NULL
processed_at    timestamptz NOT NULL, default now()
raw_llm_output  jsonb       NULLABLE    -- stores generated LLM output plus LinkedIn status
```

> **LLM output is always retained:** Every processed repo stores the validated LLM response in `raw_llm_output`. The same payload also carries a `linkedin_status` field so you can tell whether the post was published, skipped, or failed. Recover it with:
> ```sql
> SELECT
>   repo_name,
>   raw_llm_output->>'linkedin_status',
>   raw_llm_output->>'linkedin_post'
> FROM agent_processed_repos
> WHERE status = 'success' AND raw_llm_output IS NOT NULL;
> ```

---

## Using This On Your Own

### Prerequisites

Before you start, you'll need:

- Python 3.13+
- [`uv`](https://github.com/astral-sh/uv) installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- A [Supabase](https://supabase.com) project with the schema above applied
- An [OpenRouter](https://openrouter.ai) API key
- A GitHub [fine-grained PAT](https://github.com/settings/tokens) with `repo:read` scope
- Optional: a LinkedIn access token with `ugcPosts` write access plus `LINKEDIN_PERSON_URN` if you want automatic posting

---

### Step 1 — Clone and install

```bash
git clone https://github.com/10doshi12/auto-blog-agent.git
cd auto-blog-agent
uv sync
```

---

### Step 2 — Set up your Supabase schema

Run this in your Supabase SQL editor:

```sql
-- Blog posts
CREATE TABLE posts (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug                 text NOT NULL,
  title                text NOT NULL,
  excerpt              text NOT NULL,
  content              text NOT NULL,
  tags                 text[] NOT NULL DEFAULT '{}',
  reading_time_minutes integer NOT NULL,
  week_number          integer,
  published            boolean NOT NULL DEFAULT false,
  published_at         timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now()
);

-- Portfolio projects
CREATE TABLE projects (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug            text NOT NULL,
  title           text NOT NULL,
  description     text NOT NULL,
  content         text,
  category        text NOT NULL,
  tags            text[] NOT NULL DEFAULT '{}',
  metric          text,
  github_url      text,
  live_url        text,
  has_detail_page boolean NOT NULL DEFAULT false,
  featured        boolean NOT NULL DEFAULT false,
  display_order   integer NOT NULL DEFAULT 99,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- Agent config (seed with one row)
CREATE TABLE site_config (
  current_week  integer,
  week_focus    text
);
INSERT INTO site_config (current_week) VALUES (1);

-- Audit trail
CREATE TABLE agent_processed_repos (
  repo_id         bigint PRIMARY KEY,
  repo_name       text NOT NULL,
  status          text NOT NULL,
  skip_reason     text,
  blog_post_id    uuid REFERENCES posts(id) ON DELETE SET NULL,
  processed_at    timestamptz NOT NULL DEFAULT now(),
  raw_llm_output  jsonb
);
```

---

### Step 3 — Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in your values:

```env
# GitHub
GITHUB_TOKEN=github_pat_...          # fine-grained PAT, repo read only
GITHUB_USERNAME=your_username

# LLM
OPENROUTER_API_KEY=sk-or-...

# Supabase
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...

# LinkedIn (optional, only for auto-posting)
LINKEDIN_ACCESS_TOKEN=...
LINKEDIN_PERSON_URN=urn:li:person:XXXXXXX

# Optional
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
ENVIRONMENT=development
DRY_RUN=false
```

> **On `SecretStr`:** Sensitive values are wrapped in `SecretStr`. Call `.get()` to access the raw string in code. Printing it directly returns `**********`, so secrets don't leak into logs accidentally.

If `LINKEDIN_ACCESS_TOKEN` or `LINKEDIN_PERSON_URN` is missing, the agent still runs. It skips LinkedIn publishing, but still stores the full generated LLM output in `agent_processed_repos.raw_llm_output` for recovery or inspection.

---

### Step 4 — Run locally

**Do a dry run first.** It makes a real LLM call but skips all writes to Supabase and LinkedIn:

```bash
DRY_RUN=true uv run python index.py
```

If there are no new repos this week, you'll see:

```
INFO  | __main__            | === Auto Blog Agent starting ===
INFO  | agent.core.llm      | OpenRouter connection OK
INFO  | agent.core.database | current_week = 1
INFO  | agent.core.github   | Fetching repos for user: your_username
INFO  | agent.core.github   | Total public repos fetched: 12
INFO  | agent.core.github   | Repos created this IST week: 0
INFO  | __main__            | No new repos found this week, nothing to process
```

If repos are found, it'll proceed to generate content for each. In dry run mode it skips Supabase content inserts and LinkedIn publishing, but it still writes the processed repo audit row with the generated LLM output.

**Real run:**

```bash
uv run python index.py
```

> If you did a dry run first, clear `agent_processed_repos` before the real run: `DELETE FROM agent_processed_repos;`

---

### Step 5 — Deploy with GitHub Actions

Add this at `.github/workflows/agent.yml`:

```yaml
name: Auto Blog Agent

on:
  schedule:
    - cron: '30 1 * * 3'   # Every Wednesday at 07:00 IST (01:30 UTC)
    - cron: '30 1 * * 0'   # Every Sunday at 07:00 IST (01:30 UTC)
  workflow_dispatch:         # Manual trigger available

jobs:
  run-agent:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v3

      - name: Install dependencies
        run: uv sync

      - name: Run agent
        env:
          GITHUB_TOKEN: ${{ secrets.GH_TOKEN_PAT }}
          GITHUB_USERNAME: ${{ secrets.GH_USERNAME }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
          LINKEDIN_ACCESS_TOKEN: ${{ secrets.LINKEDIN_ACCESS_TOKEN }}
          LINKEDIN_PERSON_URN: ${{ secrets.LINKEDIN_PERSON_URN }}
          ENVIRONMENT: production
        run: uv run python index.py
```

Then add everything from your `.env` as repository secrets under `Settings > Secrets and variables > Actions`.

---

## Configuration Reference

All constants live in `agent/config/agent_config.py` as frozen Pydantic models. `DRY_RUN` is the exception; it lives in `.env`.

| Key | Default | Description |
|---|---|---|
| `config.llm.primary_model` | `openai/gpt-oss-20b` | First-choice model |
| `config.llm.fallback_model` | `meta-llama/llama-3.1-8b-instruct` | Used if primary fails JSON/validation |
| `config.llm.max_tokens` | `8192` | Max output tokens per call |
| `config.llm.temperature` | `0.7` | Generation creativity |
| `config.github.max_repos_per_run` | `5` | How many repos to process per run |
| `config.github.max_readme_length` | `20000` | README chars sent to LLM (truncated beyond this) |
| `config.github.skip_forked_repos` | `True` | Skips forks |
| `config.content.blog_post_tone` | `"professional"` | Tone injected into system prompt |
| `config.content.linkedin_post_max_length` | `2800` | LinkedIn character budget |
| `config.content.hashtag_count` | `5` | Hashtags appended to LinkedIn posts |
| `config.behaviour.skip_repos_without_readme` | `True` | Skips repos with no README |
| `config.behaviour.disable_linkedin_posting` | `False` | Skips posting to LinkedIn if True (useful for testing outputs) |

---

## Known Gotchas

**`agent_processed_repos` writes even in dry run mode.** Rows are committed regardless of `DRY_RUN`, and each row now includes the generated LLM output. Before switching to a real run, clear it:
```sql
DELETE FROM agent_processed_repos;
```

**LinkedIn is optional, but every LLM response is still recorded.** If `disable_linkedin_posting=True`, `LINKEDIN_ACCESS_TOKEN` is missing, `LINKEDIN_PERSON_URN` is missing, the publish call fails, or the run is a dry run, the generated content still lands in `raw_llm_output` with a `linkedin_status` value. Keep an eye on rows where `raw_llm_output IS NOT NULL`.

**IST week boundary is computed once per run.** `data_to_send_LLM()` calculates the week range upfront and passes it to all repo checks. This matters because computing it per-repo would produce redundant log lines and inconsistent results if a run crosses midnight.

**`blog_post_id` is passed as a string.** `mark_repo_processed` receives it as `str`, but the FK column in Supabase is `uuid`. Supabase handles the cast on insert, so it's fine in practice.

---

## Contributing

If you want to add a new publishing target (Dev.to, Hashnode, Twitter/X), swap the LLM backend, or rework the prompts, open an issue first. It's easier to align on the approach before code gets written.

```bash
# Fork the repo, then:
git checkout -b feat/your-feature
# make changes
git commit -m "feat: your feature description"
git push origin feat/your-feature
# Open a pull request
```


# NEXT STEPS
- Resolve Major Security Flaws
- Add more robust error handling and logging around LinkedIn API calls, including token refresh logic
- Automated LinkedIn refresh token handling: Implement a mechanism to automatically refresh LinkedIn OAuth tokens when they expire, ensuring uninterrupted posting without manual intervention.
- Implement a retry mechanism for transient failures (network issues, rate limits) with exponential backoff
- Add unit tests for core functions (LLM output validation, GitHub data fetching, DB operations) using mocks to simulate external dependencies
- Extend the LLM prompt to include more specific instructions for generating LinkedIn posts that comply with platform guidelines and best practices
- Create separate functions and workflows for LinkedIn post generation and posting so that it is not only limited to blog content generation but can also be generalized for any post on LinkedIn.
- Add functionality to handle updates to existing repos (e.g., if a README changes, update the corresponding blog post)

---

## License

MIT. See [LICENSE](LICENSE).

---

<div align="center">
  <sub>Built as part of the 6-Month AI & ML Engineering Challenge · <a href="https://github.com/10doshi12">@10doshi12</a></sub>
</div>
