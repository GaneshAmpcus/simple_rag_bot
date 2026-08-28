# Rag_Chatbot — Custom Bots Platform: Reference & Build Plan

**Status:** Phases 0-5 implemented (see "Implementation status" note
below section 4). No code changes yet for Phase 6 onward.
**Purpose:** (1) document how the system works *today*, precisely, so an
agent picking up this repo doesn't have to reverse-engineer it from
scratch, and (2) lay out the phased plan for turning it from "one bot
per user" into "each user can create and configure multiple custom
bots, each with its own instructions, knowledge base, and tool
authorizations." Update this file as decisions are made or plans
change — it's meant to stay current, not be a one-time snapshot.

Repos involved:
- `simple-rag/` — FastAPI backend (this plan's main focus)
- `simple-rag-ui/` — React/Vite frontend
- `E:\Ganesh\MCP\mcp_implemnation` — the standalone MCP server (tools
  live here; see its own architecture note below)

---

## 1. Current system, as it actually works today

### 1.1 The core idea

One FastAPI process. One shared LangGraph agent (`agent_graph`, built
once at startup in `main.py`) that **every user gets by default**.
Users only get a different, per-user graph if they've selected MCP
tools and/or connected Gmail — otherwise they all share the exact
same compiled graph object. There is currently no concept of "a bot"
as a distinct, user-owned, configurable entity — there's one
application-wide assistant with one fixed personality, one fixed
knowledge base, and optional per-*user* tool add-ons.

### 1.2 Request flow (`/chat`)

```
POST /chat {session_id?, message}
  -> get_current_user (JWT, security.py)
  -> load/create ChatSession (models.py, owned by user_id)
  -> load last HISTORY_TURN_LIMIT=5 turns as LangChain messages
  -> _get_graph_for_user(user_id)  [main.py]
       -> shared `agent_graph` UNLESS user has MCP tools selected
          and/or Gmail connected, in which case a per-user graph is
          built (and cached) with those extra tools spliced in
  -> graph.ainvoke({messages, user_id})
  -> check_output() (guardrails_layer.py, output-side NeMo rail)
  -> persist both messages, update session title if new
  -> add_turn() -> mem0 long-term memory write (per user_id, fail-open)
```

### 1.3 The LangGraph graph itself (`graph.py`, `nodes.py`)

```
START -> [pre_rails, currently disabled via SKIP_PRE_RAILS=True]
      -> memory_recall   (mem0 search, filtered by user_id)
      -> intent_classify (cheap LLM call: "tool_needed" vs "chat")
      -> tool_needed? -> agent (LLM w/ tools bound) <-> tools <-> tool_scan
                          -> output_rails -> END
      -> chat?        -> chat (LLM, no tools) -> output_rails -> END
```

- `agent_node`/`chat_node` rebuild their **system prompt** on every
  call (not once at graph-build time) because it depends on
  `state["user_memories"]`, which changes per turn. The prompt text
  itself, though, is fixed and global — `prompts.py`'s `_AGENT_BASE`
  and `CHAT_SYSTEM_PROMPT` are hardcoded constants, not
  per-user/per-bot configurable data.
- `tool_scan_node` and the `<untrusted_data>` wrapping
  (`tools_registry.py::_wrap_untrusted`) are the prompt-injection
  defenses on tool output — keep this pattern for any new tool.
- Groq/Llama occasionally emits malformed tool calls;
  `agent_node` has retry + payload-recovery logic for this. Don't
  touch without understanding `_recover_tool_call_from_error`.

### 1.4 Tools (`tools_registry.py`)

`build_default_tools(state)` is the single place that assembles the
tool list, called fresh every time `build_graph(state)` runs:

1. `knowledge_base_search` — bound to `state["store"]`, one **global**
   FAISS index (`data/index/`). `rag.py::_discover_stores` *can*
   discover sibling directories under `data/index/` as additional
   named KBs (`kb_name` param exists end-to-end in `rag.py`/`vector_store.py`),
   but nothing in the product today creates more than the one default
   KB — this discovery mechanism is unused headroom, not a built
   feature.
2. `knowledge_graph_search` — optional, only exists if Neo4j env vars
   are set (`graph_store.py::get_neo4j_graph`). Global, not
   per-user/per-bot.
3. `duckduckgo_search` — default web-search fallback, **only added if
   the user has no MCP tools selected** (see `mcp_selected` flag).
4. Whatever's in `state["mcp_tools"]` — per-user extra tools, see 1.5.

### 1.5 Per-user tool authorization (MCP + Gmail)

This is the most relevant prior art for the bot-scoping work, and the
pattern it establishes should be **generalized**, not replaced.

**MCP (generic remote tools, e.g. `get_weather`):**
- `models.py::McpConnection` — one row per **user** (`unique=True` on
  `user_id`). Holds the OAuth token (`token_json`), client info, and
  `selected_tools_json` (which of the MCP server's tools this user
  turned on).
- `mcp_tools.py` — OAuth against the MCP server (referred to as
  "Horizon" in comments — whatever auth FastMCP Cloud puts in front of
  the deployed server) via the `mcp` SDK's `OAuthClientProvider`.
  `_pending[user_id]` bridges the SDK's redirect/callback callbacks to
  real HTTP routes (`/mcp/authorize` → browser redirect → Google/Horizon
  consent → `/mcp/callback`).
  - `ReauthorizationRequired` — custom exception added after a real
    production bug: if a stored token is dead (expired, unrefreshable)
    and something calls `get_tools()` **outside** an active
    `/mcp/authorize` flow (e.g. `/mcp/tools` GET), the SDK tries to
    redo the full OAuth redirect and had nowhere to put the result —
    raw `KeyError` crash. Now caught, token cleared, clean error
    surfaced instead. Any new code path that calls `_build_mcp_client(...).get_tools()`
    must handle this exception (unwrap it — anyio wraps it in an
    `ExceptionGroup`, see the `BaseExceptionGroup` import at the top of
    `mcp_tools.py`).
  - **Known limitation, not yet fixed:** `/mcp/callback` currently
    matches an incoming callback to "whichever user has a pending
    authorization" rather than decoding `user_id` from OAuth's `state`
    param. Fine for single-user testing; **not safe under concurrent
    users** authorizing at the same time. This must be fixed before
    Phase 3 below (per-bot MCP auth will make concurrent authorizations
    far more likely, since a user might connect several bots in one
    sitting).
- `routers/mcp.py` — `/mcp/authorize`, `/mcp/callback`, `/mcp/status`,
  `/mcp/tools`, `/mcp/tools/select`.

**Gmail (a specific external service, real Google OAuth):**
- `models.py::GmailConnection` — one row per **user**, same
  `unique=True` on `user_id` pattern.
- `gmail_oauth.py` — plain Google OAuth2 (not MCP-flavored) run
  **entirely in this backend**. Deliberate architectural choice
  (see its own docstring): Gmail is a tool this app owns and is the
  sole consumer of, so there's no reason to make the MCP server hold a
  Google client secret or proxy Google's OAuth — simpler to just do it
  here.
- `tools/gmail_tool.py` (backend) — thin **per-call** wrappers, NOT
  reusing the cached-graph tool-fetch path. Fetches a **fresh** Google
  access token from `gmail_oauth.get_valid_access_token()` on every
  single tool invocation and forwards it as the `X-Gmail-Access-Token`
  header to the MCP server's tool call. This deliberately trades a bit
  of per-call latency for correctness — the alternative (baking the
  token into `_user_agent_graphs`'s cached graph) would silently start
  failing an hour into a session when the token expires.
- **The actual Gmail API calls live on the MCP server**
  (`E:\Ganesh\MCP\mcp_implemnation\tools\gmail_tool.py`), reading that
  header via FastMCP's `get_http_headers()`. The MCP server holds **no
  Google secret** — auth logic is 100% backend-owned, tool
  logic is 100% MCP-server-owned. **This split is the template to
  replicate for any future externally-authed tool**, per-bot.
- `routers/gmail.py` — `/gmail/authorize`, `/gmail/callback` (uses
  signed `state`, not the "whichever user is pending" hack — do this
  for MCP too), `/gmail/status`.

**How the two are reconciled in the graph (`main.py::_get_graph_for_user`):**
Gmail tool names (`GMAIL_TOOL_NAMES` in `tools/gmail_tool.py`) are
just entries in the *same* MCP tool-selection list the UI shows
(`/mcp/tools` lists them because they're real tools on the MCP
server) — but at graph-build time they're routed to the fresh-token
wrapper builder instead of the generic `mcp_tools.get_selected_tools()`
path, and any user with a Gmail tool selected **skips the
`_user_agent_graphs` cache entirely** (rebuilds every chat message) to
guarantee token freshness. This cache-bypass-for-freshness pattern
will need to generalize to "any bot with an externally-authed tool
selected," not just Gmail specifically.

### 1.6 Data model today (`models.py`)

```
User (id, email, hashed_password)
 ├── ChatSession (id, user_id, title)      — no bot concept
 │    └── Message (id, session_id, role, content)
 ├── McpConnection (1:1 per user)          — token + selected tools
 └── GmailConnection (1:1 per user)        — Google OAuth tokens
```

No `Bot`/`Agent` table. No per-bot anything. Knowledge base is one
global FAISS index on disk (`data/index/`), not tied to a user or bot
at all — **every user currently shares the same document store**.

### 1.7 Memory (`memory_layer.py`)

mem0, scoped by `user_id` only (not session, not bot). Recalled once
per turn (`memory_recall_node`) and written once per turn
(`add_turn()` in `main.py`, fail-open). Uses its own FAISS index
(`data/mem0_index/`), separate from the document KB index.

### 1.8 Guardrails (`guardrails_layer.py`, `guardrails_config/`)

NeMo Guardrails, config in `guardrails_config/config.yml` +
`rails/persona.co`. Input rail is currently **disabled**
(`SKIP_PRE_RAILS = True` in `graph.py` — flagged, not forgotten).
Output rail (PII masking via `mask_pii`) still runs, both inside the
graph (`output_rails_node`) and again in `main.py`'s `check_output()`
after the graph returns. Global config, not per-bot.

### 1.9 Frontend (`simple-rag-ui/src`)

- `Sidebar.jsx` — session list (flat, no bot grouping) + a single
  "MCP integration" link (user-level settings, not per-session).
- `Workspace.jsx` / `ChatWindow.jsx` — one chat surface.
- `KBPanel.jsx` — upload panel, explicitly labelled *"Documents added
  here are available to every thread"* — i.e. the UI already
  communicates the current global-KB reality to the user.
- `McpTools.jsx` — tool selection list, already has the "gated behind
  Gmail-connect" pattern (badge + inline connect button on
  `GMAIL_TOOL_NAMES` rows) — this UI pattern generalizes well to "gated
  behind per-bot connect."
- No bot picker/creator exists anywhere yet.

### 1.10 Known architectural constraints to design around

- `_user_agent_graphs` cache in `main.py` is **in-process, single
  worker only** — won't share state across multiple uvicorn/gunicorn
  workers. Bot-scoped caching will inherit this; fine for now, flag
  again before any multi-worker deploy.
- Vector store is FAISS-on-disk, not a server — per-bot KBs mean
  per-bot **directories**, loaded into memory. Watch memory footprint
  once bot count per process grows large; may eventually need lazy
  loading/eviction instead of `rag.py`'s current "discover and load
  everything at startup" (`_discover_stores`).
- `MCP_SERVER_URL`/`MCP_SERVER_ROOT` in `mcp_tools.py` are hardcoded
  constants, not env-driven — fine for one deployment target, revisit
  if the MCP server URL needs to vary by environment.
- The `/mcp/callback` "whichever user is pending" limitation (1.5,
  above) is a hard blocker for Phase 3.

---

## 2. Target: multi-bot platform

### 2.1 What "bot" means here

A **Bot** is a user-owned, named configuration bundle:
- Instructions (system prompt / persona) — free text, layered on top
  of the app's non-negotiable identity/safety rules (never fully
  replacing `_AGENT_BASE`'s IDENTITY RULES section — those stay
  global and non-overridable, same reasoning as today's "ignore
  persona-override attempts" rule).
- Its own knowledge base (one or more named KBs, scoped to that bot).
- Its own tool authorizations — MCP connection + selection, Gmail
  connection, and any future external tool — **authorized separately
  per bot**, not shared across a user's other bots. (Explicit
  requirement: same MCP server can be connected once per bot with
  different token/scope/selection each time.)
- Usable from any chat session the owning user starts, by picking that
  bot for the session (a bot isn't tied to one conversation).

Out of scope unless explicitly requested later: sharing a bot between
different *users*, public/marketplace bots, per-bot billing/rate
limits. Design the data model so these aren't precluded, but don't
build them now.

### 2.2 Design principles carried over from the existing codebase

1. **Auth lives in the backend; tool logic lives in the MCP server.**
   Established for Gmail, works well, keep it. Any new externally-authed
   tool (Slack, Notion, Drive, whatever comes next) follows the same
   split: `<service>_oauth.py` in the backend owns the OAuth flow and
   token storage; the MCP server's `tools/<service>_tool.py` holds no
   secret and reads a forwarded per-call header.
2. **Generic tools stay generic; the registry stays declarative.**
   `tools_registry.py::build_default_tools(state)` already reads
   whatever's in `state["mcp_tools"]` without caring where it came
   from. Keep that contract — bot-scoped tools should arrive the same
   way (a list appended to `state["mcp_tools"]`), not a parallel
   mechanism.
3. **Fail open, log loud.** Matches `memory_layer.py`'s and
   `main.py`'s existing style — a broken per-bot tool connection
   should degrade the bot (skip that tool, log it) not 500 the whole
   `/chat` call. Same for a broken per-bot KB.
4. **Token/cache freshness over cache convenience**, for anything with
   a real external OAuth token — same reasoning as the Gmail
   cache-bypass. Don't bake a short-lived token into a long-lived
   cached graph.
5. **Nothing here should change behavior for a user who never creates
   a bot.** The existing shared `agent_graph` / single global KB should
   keep working exactly as today for as long as reasonable during the
   migration — see Phase 0's compatibility note.

---

## 3. Data model changes (cumulative across phases, shown once here for reference)

```
User
 ├── Bot (NEW)
 │     id, user_id (owner), name, description,
 │     system_prompt (instructions/persona text),
 │     default_kb_name (fk-ish, see below), created_at, updated_at
 │
 │     ├── BotKnowledgeBase (NEW, optional if you want multiple KBs/bot)
 │     │     id, bot_id, name, index_path, created_at
 │     │     — Phase 2 may start with just one implicit KB per bot
 │     │       (bot_id doubles as the KB key) and only introduce this
 │     │       table if/when "multiple KBs per bot" is actually needed.
 │     │
 │     ├── McpConnection  (CHANGED: add bot_id, drop the user-level
 │     │     unique constraint, add unique(user_id, bot_id) instead)
 │     │
 │     └── GmailConnection (CHANGED: same — add bot_id, re-scope
 │           the unique constraint to (user_id, bot_id))
 │
 └── ChatSession (CHANGED: add nullable bot_id fk)
       └── Message (unchanged)
```

Migration notes:
- `McpConnection`/`GmailConnection` becoming bot-scoped is a breaking
  schema change for any existing connected user — decide at Phase 3/4
  time whether to (a) migrate existing rows to a user's auto-created
  "default bot," or (b) it's still pre-launch/internal and a clean
  wipe is acceptable. Don't guess this now; confirm before writing the
  migration.
- `ChatSession.bot_id` should be **nullable** — a session with no bot
  keeps using the global default assistant (backward compat, principle
  #5 above).

---

## 4. Phased plan

> **Implementation status (update this as work lands):** Phases 0-2
> below are done. Notes on what actually shipped, and where it
> diverged from the plan as originally written:
> - `Bot` model, `ChatSession.bot_id`, `/bots` CRUD (`routers/bots.py`)
>   — as planned.
> - Bot instructions: `prompts.py`'s `build_agent_system_prompt`/
>   `build_chat_system_prompt` take `bot_instructions`, inserted right
>   after IDENTITY RULES, exactly as planned. Threaded through
>   `nodes.py` and `graph.py`'s `GraphState` as `state["bot_instructions"]`.
> - Per-bot KB: **no `BotKnowledgeBase` table was added** — a bot's KB
>   is keyed by `rag.bot_kb_name(bot_id)` (`f"bot_{bot_id}"`), reusing
>   the multi-KB `kb_name` mechanism `rag.py`/`vector_store.py` already
>   had (previously unused headroom, per §1.4). One KB per bot, per the
>   plan's simplification.
> - `rag.py::build_pipeline_state()` no longer eager-loads every
>   subdirectory under `data/index/` — it loads only the global default
>   KB at startup. `_get_kb_store()` now lazily loads (and caches in
>   `state["stores"]`) any other `kb_name` on first use. This is the
>   "needs to become lazy" change the original plan flagged, done as
>   part of Phase 2 rather than deferred.
> - `main.py`: `_get_graph_for_user` was renamed
>   `_get_graph_for_session(user_id, bot_id)`; the per-user graph cache
>   (`_user_agent_graphs`) is now keyed by `(user_id, bot_id)` instead
>   of `user_id` alone. A session with `bot_id=None` and no MCP/Gmail/
>   Calendar selections still returns the exact shared `agent_graph`
>   object, unchanged — verified this stays true after adding bots.
> - `/ingest` takes an optional `bot_id` (form field); `/query` now
>   actually uses its existing `kb_name` param (previously accepted but
>   silently ignored — fixed as a drive-by since Phase 2 needed the
>   same code path).
> - **The MCP/Gmail/Calendar layer evolved independently of this plan**
>   before Phase 3 started (multi-server `McpServer`/`McpConnection`,
>   an auto-provisioned "Horizon" server, a `CalendarConnection` for
>   Calendar/Meet tools) — see the live source, not §1.5 above, for the
>   current shape of that layer. §1.5's description of a single
>   hardcoded MCP server is now historical context for *why* the
>   per-bot-auth pattern looks the way it does, not a description of
>   the current code. Phase 3/4 below should be re-scoped against the
>   actual current `mcp_tools.py`/`models.py` before starting, not
>   against §1.5's snapshot.
> - Frontend: bot CRUD (`BotsPage.jsx`), a bot picker + bot-tagged
>   session list in `Sidebar.jsx`, and `botId` threaded through
>   `Workspace.jsx` → `ChatWindow.jsx`/`KBPanel.jsx` (including updating
>   `KBPanel`'s copy, which previously — accurately — said uploads were
>   global).
> - **Phase 5 (memory scoping) shipped, decided per-bot** (confirmed
>   2026-08-28, discussed against the trade-offs in §Phase 5 below).
>   No DB migration needed — mem0's `user_id` filter is just an opaque
>   string, so `memory_layer.memory_key(user_id, bot_id)` composes
>   `f"{user_id}:{bot_id}"` for a real bot, or returns `user_id`
>   unchanged for `bot_id=None` — same "no bot" fallback-bucket pattern
>   as `McpConnection`/`GmailConnection`/`CalendarConnection`'s
>   `bot_id=NULL` rows, so every memory written before Phase 5 (and
>   every bot-less session's memory) stays exactly where it was,
>   untouched. `nodes.py::memory_recall_node` now reads `state["bot_id"]`
>   (added to `graph.py`'s `GraphState`) and searches under that key;
>   `main.py::chat_route` passes `session.bot_id` into both the graph
>   invocation and `add_turn()`. Drive-by fix while touching
>   `add_turn`'s 413-fallback path: it was calling
>   `memory.add(messages=[...], ...)` with a literal placeholder
>   instead of the turn's actual messages — now passes the real list.

Each phase should be independently shippable and leave the app in a
working state. Don't start a phase until the previous one's acceptance
criteria are met.

### Phase 0 — Foundations (no user-visible change yet)

**Goal:** land the `Bot` table and the schema changes, without wiring
them into `/chat` yet.

- Add `Bot` model (`models.py`) — id, user_id, name, description,
  system_prompt, timestamps. No KB/tool fields yet.
- Add `bot_id` nullable FK to `ChatSession`.
- `routers/bots.py` — CRUD: `POST/GET/PATCH/DELETE /bots`,
  `GET /bots/{id}`. Standard ownership check pattern
  (`_get_owned_session`-style helper, same as `routers/sessions.py`).
- Frontend: nothing yet, or a bare-bones bot list page behind a flag.

**Acceptance:** can create/list/edit/delete bots via API; existing
`/chat` flow completely unaffected (bot_id always null on new
sessions still).

### Phase 1 — Bot instructions/persona wired into chat

**Goal:** a session tied to a bot gets that bot's system prompt
instead of the generic one. This is the smallest slice that makes
"custom bot" real.

- `prompts.py`: `build_agent_system_prompt`/`build_chat_system_prompt`
  take an optional `bot_instructions: str = ""` param, inserted as an
  additional block **after** the non-negotiable IDENTITY RULES section
  — same pattern already used for the memory block
  (`_memory_block`/`_MEMORY_BLOCK_TEMPLATE`). Explicitly keep IDENTITY
  RULES un-overridable — the whole point of that section today is
  resisting persona-override attempts; a bot's *legitimate* persona
  should layer on top, not replace it.
- `nodes.py`: `agent_node`/`chat_node` need the bot's instructions
  threaded through `state` (new `state["bot_instructions"]` key,
  same wiring style as `state["user_memories"]`).
- `main.py::chat_route`: if `req.session_id`'s session has a `bot_id`,
  load the `Bot` row, pass its `system_prompt` into the graph
  invocation state. New session creation should accept an optional
  `bot_id` (schema change: `ChatRequest` gets `bot_id: str | None`,
  used only when `session_id` is omitted).
- Decide: does changing a bot's instructions rebuild/bust any cached
  graph for that bot's active users? (At this phase, instructions flow
  through `state` per-invocation, not baked into the compiled graph,
  so — no rebuild needed. Confirm this stays true as later phases add
  bot-specific *tools*, which **do** get baked into the compiled graph
  the way MCP tools are today.)
- Frontend: bot create/edit form (name, description, instructions
  textarea); bot picker when starting a new chat; show active bot name
  in `ChatWindow`/`Sidebar`.

**Acceptance:** two bots with different instructions produce
noticeably different responses to the same message; a session with no
bot behaves exactly as today.

### Phase 2 — Per-bot knowledge base

**Goal:** each bot has its own document store; uploading to bot A
never surfaces in bot B's answers.

- `vector_store.py`/`rag.py`: index path becomes
  `data/index/bots/{bot_id}/` instead of the flat `data/index/` +
  sibling-directory discovery. Decide whether to keep the existing
  global `knowledge_base` around as "no bot selected" fallback, or
  retire it — pick one and document the choice here.
- `rag.py::build_pipeline_state()` currently eager-loads every
  discovered store at process startup — reconsider for per-bot stores;
  likely needs to become lazy (load-on-first-use per bot_id, with an
  in-memory cache) rather than scanning/loading all bots' indices at
  boot. Watch the memory-footprint note in §1.10.
- `main.py::/ingest`: takes a required `bot_id`, checks ownership,
  writes to that bot's store only.
- `tools_registry.py::build_kb_tool`: unchanged in shape, just called
  with the bot's store instead of the global one when building a
  bot-scoped graph.
- Frontend: `KBPanel.jsx` becomes bot-scoped (currently says "available
  to every thread" — that copy needs to change), shown when a bot is
  selected; update or drop the global KB panel per the retire/keep
  decision above.

**Acceptance:** uploading a doc to bot A and asking bot B about it
gets a "no relevant information found," not the doc's content.

### Phase 3 — Per-bot MCP tool authorization

**Goal:** a user can connect the same MCP server independently for
each bot, with independent tool selection.

- **Fix the `/mcp/callback` "whichever user is pending" limitation
  first** (§1.5) — becomes materially riskier once one user can have
  several concurrent per-bot authorizations in flight. Decode
  `user_id` (and now `bot_id`) from the signed OAuth `state` param
  (same approach `gmail_oauth.py`/`routers/gmail.py` already use for
  Gmail — port that pattern here).
- `McpConnection`: add `bot_id`, re-scope uniqueness to
  `(user_id, bot_id)`. `mcp_tools.py`'s functions
  (`_build_mcp_client`, `has_valid_connection`, `list_tools`,
  `get_selected_tools`, `set_selected_tools`, `start_authorization`)
  all take `bot_id` alongside `user_id`; `_pending` keying becomes
  `(user_id, bot_id)` instead of `user_id` alone.
- `routers/mcp.py`: routes become bot-scoped, e.g.
  `/bots/{bot_id}/mcp/authorize`, `/bots/{bot_id}/mcp/callback`,
  `/bots/{bot_id}/mcp/status`, `/bots/{bot_id}/mcp/tools`,
  `/bots/{bot_id}/mcp/tools/select`.
- `main.py::_get_graph_for_user` becomes (effectively)
  `_get_graph_for_bot_session(user_id, bot_id)` — cache key extends to
  include `bot_id`.
- Frontend: `McpTools.jsx` moves from a user-level settings page to a
  per-bot settings tab; `Sidebar.jsx`'s single "MCP integration" link
  becomes part of the bot editor instead.

**Acceptance:** connecting MCP for bot A and selecting `get_weather`
does not make `get_weather` available in bot B unless separately
authorized+selected there too.

### Phase 4 — Per-bot Gmail (and template for future external tools)

**Goal:** same generalization as Phase 3, applied to Gmail, and
written so the *next* external tool after Gmail is a copy-paste of
this pattern, not a redesign.

- `GmailConnection`: add `bot_id`, re-scope uniqueness to
  `(user_id, bot_id)`.
- `gmail_oauth.py`: functions take `bot_id`; signed `state` already
  encodes what's needed here (extend it to include `bot_id`).
- `routers/gmail.py`: `/bots/{bot_id}/gmail/authorize`, `/callback`,
  `/status`.
- `tools/gmail_tool.py` (backend): `build_gmail_tools(user_id, bot_id, tool_names)`
  — still fetches a fresh token per call, still bypasses the
  bot-session cache when gmail tools are selected for that bot.
- **Write up the resulting pattern explicitly** (a short "adding a new
  externally-authed per-bot tool" checklist) at the bottom of this
  file once Phase 4 ships — that's the reusable contract for Slack/
  Notion/Drive/whatever comes next, and it should live here so it
  doesn't have to be re-derived from reading two source files again.

**Acceptance:** connecting Gmail for bot A does not connect it for bot
B; each bot's Gmail tools use that bot's own stored token.

### Phase 5 — Memory scoping decision

**Goal:** decide and implement whether mem0 memory is per-user (as
today, shared across all of a user's bots) or per-(user, bot).

This is a **product decision, not just an engineering one** — a
"cooking assistant" bot probably shouldn't recall facts learned by a
user's "legal contract reviewer" bot. Recommend per-bot scoping
(mem0's `user_id` filter becomes `f"{user_id}:{bot_id}"` or a real
composite key) but confirm before implementing, since it's a one-way
door for existing memory data (a rescoping migration can't cleanly
retrofit which past memories "belong" to which future bot).

- `memory_layer.py::add_turn`/`nodes.py::memory_recall_node`: scoping
  key change, if approved.

### Phase 6 — Frontend polish / bot-centric UX

**Goal:** the UI stops being "one chat app with a settings page bolted
on" and becomes "pick a bot, or manage your bots."

- Bot list/dashboard as a real landing surface (not just a form).
- `Sidebar.jsx`: group sessions by bot, or add a bot switcher.
- Bot editor: instructions, KB upload, MCP connect/select, Gmail
  connect — all in one place, reusing `McpTools.jsx`'s existing
  "locked until connected" badge pattern for every gated tool.
- Empty/first-run state: guide a new user to create their first bot.

### Phase 7 (stretch, not committed) — Sharing / reuse beyond one user

Only pursue if actually requested: sharing a bot's config with other
users, duplicating a bot as a starting template, exporting/importing a
bot's instructions+KB, or a public bot directory. Flag here rather
than designing now, so Phase 0-3's schema doesn't need to guess at
requirements nobody's confirmed yet.

---

## 5. Open questions to resolve before/during the phases above

- **Existing global KB & existing MCP/Gmail connections**: migrate
  into an auto-created "default bot" per user, or treat as disposable
  dev data? Affects the Phase 2–4 migrations.
- **Multiple KBs per bot, or exactly one?** Phase 2 assumes "start
  with one, add `BotKnowledgeBase` table later if needed" — confirm
  that's an acceptable simplification.
- **Neo4j knowledge graph** — stays global, becomes per-bot, or stays
  unused (it's optional/off unless Neo4j env vars are set today)? Not
  addressed in the phases above; punt until it's actually configured
  in an environment that also has bots.
- **Rate limiting / cost control per bot** — not addressed; flag if
  bots start making this a cost-management problem.

---

## 6. Quick file-map reference

| Concern | File(s) |
|---|---|
| Auth (JWT) | `security.py`, `routers/auth.py` |
| DB engine/session | `config/database.py` |
| Data model | `models.py` |
| Chat orchestration | `main.py` (`/chat`), `graph.py`, `nodes.py` |
| Prompts | `prompts.py` |
| Tool registry | `tools_registry.py` |
| RAG pipeline / vector store | `rag.py`, `vector_store.py`, `embeddings.py`, `loaders.py` |
| Knowledge graph (optional) | `graph_store.py` |
| Long-term memory | `memory_layer.py` |
| Guardrails | `guardrails_layer.py`, `guardrails_config/` |
| MCP (generic remote tools) — backend | `mcp_tools.py`, `routers/mcp.py` |
| MCP server (tool implementations) | `E:\Ganesh\MCP\mcp_implemnation\server.py`, `.../tools/` |
| Gmail — backend (auth) | `gmail_oauth.py`, `routers/gmail.py`, `tools/gmail_tool.py` |
| Gmail — MCP server (tool logic) | `E:\Ganesh\MCP\mcp_implemnation\tools\gmail_tool.py` |
| Sessions API | `routers/sessions.py`, `schemas.py` |
| Frontend chat UI | `simple-rag-ui/src/components/{ChatWindow,Workspace,Sidebar,MessageBubble}.jsx` |
| Frontend KB UI | `simple-rag-ui/src/components/KBPanel.jsx` |
| Frontend MCP/Gmail UI | `simple-rag-ui/src/components/{McpTools,McpConnected,GmailConnected}.jsx` |
