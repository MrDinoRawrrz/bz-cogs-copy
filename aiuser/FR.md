AIUser RAG Integration — Requirements (Pre‑Implementation Spec)

This document specifies requirements for integrating Retrieval‑Augmented Generation (RAG) into the existing aiuser cog for Red‑DiscordBot. It is written as a pre‑implementation specification and captures what the system must do, independent of code details.

1. Purpose & Goals

Provide aiuser with grounded answers using a self‑hosted vector store (Qdrant) and local embeddings.

Run an LLM via Ollama using an OpenAI‑compatible API URL; connection must be configurable at runtime.

Keep answers concise by default; respect Discord constraints (no streaming).

Track who said what and when in indexed chat content.

Minimize storage duplication via de‑duplication of semantically identical chunks.

Offer owner/admin operational commands (ingestion, search, health, backup, budgeting).

2. Scope

In scope:

RAG data pipeline (ingest → embed → store → retrieve) for channel messages, URLs, and files.

Persona and dynamic context for a realistic companion named Dice.

Configurability of LLM backend and vector store at runtime via commands.

Backups of the vector store and owner‑only health checks.

Token/length budgeting suitable for a 16 GB unified‑memory host.

Out of scope:

Cloud services (must be self‑hostable).

Streaming replies (Discord limitation).

Moderation/NSFW policies beyond Discord/Red defaults.

3. Stakeholders & Users

Server members: converse with Dice; may trigger RAG implicitly via queries.

Guild admins: configure LLM/Qdrant; manage ingestion and budgets.

Bot owner: access health checks; manage backups and retention.

4. Assumptions & Constraints

Bot and Qdrant/Ollama run on a trusted LAN (192.168.0.0/16).

Qdrant and Ollama may run in Docker; both must be reachable from the bot by URL.

Default persistence path for Qdrant data: /home/dino/vectordb (host volume).

No PII beyond Discord user IDs and message timestamps is required; storage is local.

5. High‑Level Architecture

Ingestion: URLs, files (.txt, .md, .pdf, .docx), and selected recent channel messages.

Chunking: Long texts split into overlapping chunks.

De‑duplication: Normalized text hashed (SHA‑256); duplicates merge metadata, not vectors.

Embedding: Local sentence-transformers model embeds chunks.

Storage: Qdrant collection keyed by hash; payload stores guild/channel/user/time metadata.

Retrieval: k‑NN search with optional filters by guild/channel/user; score threshold applied.

Generation: Build message list: (a) retrieved context block (system), (b) Dice persona (system), (c) user prompt. Send to Ollama (OpenAI API).

Citations: Append footnotes [n] showing source and, when available, author + UTC timestamp.

6. Functional Requirements (FR)

FR‑1 Configuration (LLM)

FR‑1.1 The system shall allow setting LLM base URL (e.g., http://<host>:11434/v1) via a command.

FR‑1.2 The system shall allow setting LLM model name via a command.

FR‑1.3 The system shall allow setting the API key (optional for Ollama) via a command.

FR‑1.4 The system shall display current LLM settings without exposing full secrets.

FR‑2 Configuration (Vector Store)

FR‑2.1 The system shall allow setting Qdrant base URL and collection name via a command.

FR‑2.2 The system shall allow setting a minimum similarity/score threshold.

FR‑3 Ingestion

FR‑3.1 The system shall ingest URLs by fetching and extracting readable text.

FR‑3.2 The system shall ingest uploaded files of types: .txt, .md, .pdf, .docx.

FR‑3.3 The system shall ingest recent messages from the current channel (admin‑initiated),
recording author, author_id, message_id, and created_at (UTC ISO).

FR‑3.4 The system shall chunk long inputs to a bounded size with overlap.

FR‑3.5 The system shall de‑duplicate chunks using a normalized‑text hash; duplicates update metadata (sources, last_seen) rather than storing new vectors.

FR‑4 Retrieval & Answering

FR‑4.1 The system shall perform vector search with: k results (configurable), min score, and optional filters by guild/channel/user.

FR‑4.2 The system shall include a retrieved context system block when hits exist.

FR‑4.3 The system shall include a Dice persona system block using dynamic variables.

FR‑4.4 The system shall return concise answers by default.

FR‑4.5 When context is used, the system shall append human‑readable citations [n] including source; if available, also author and timestamp.

FR‑5 Commands (User/Operator)

FR‑5.1 aiuser llm set-url|set-model|set-key|show — manage LLM config.

FR‑5.2 aiuser rag set-qdrant|minscore|stats|clear — manage vector store.

FR‑5.3 aiuser rag addurl|addfile|addhere — ingest sources.

FR‑5.4 aiuser rag search <query> — show top hits (with author/time preview).

FR‑5.5 aiuser rag said [@user] [limit] — list indexed snippets for a user (with timestamps).

FR‑5.6 aiuser_backup set-days|set-hour|set-dir|now|status|list — backups.

FR‑5.7 aiuser_health — owner‑only health check for Qdrant and Ollama.

FR‑5.8 dice <prompt> — generate a response using RAG + persona.

FR‑6 Backups & Retention

FR‑6.1 On cog startup, the system shall trigger the first Qdrant snapshot backup.

FR‑6.2 The system shall create a backup daily at a configurable hour.

FR‑6.3 The system shall store snapshots in a configurable directory (default /home/dino/vectordb-backups).

FR‑6.4 The system shall enforce retention in whole days (delete older snapshots), configurable.

FR‑7 Health & Access Control

FR‑7.1 The system shall expose an owner‑only command that checks:

Qdrant readiness and version.

Ollama OpenAI endpoint reachability and basic chat completion.

FR‑7.2 Health outputs shall not leak secrets or indexed content.

FR‑8 Budgeting & Performance

FR‑8.1 The system shall enforce configurable budgets:

Max context characters passed to LLM.

Max recent history messages included.

Max retrieved chunks.

Max max_tokens for generation.

FR‑8.2 Defaults shall be tuned for a 16 GB host (balanced latency and cost).

FR‑9 Persona & Dynamic Variables

FR‑9.1 The system shall apply a persona for “Dice” (DOB: 1997‑10‑14; nerdy, curious, lightly witty; follows Discord rules).

FR‑9.2 The system shall compute and inject dynamic variables, including: bot_name, server_name, channel_name, user_display, now_iso, dice_age_years, dice_birthday_iso, rules_channel_mention, rag_collection, rag_min_score, and user_roles.

7. Data Model (Stored Payload Fields)

content_hash (string; SHA‑256 of normalized text; primary id)

text (string; chunk body)

source (string; primary ingest source)

sources (string[])

first_seen (UTC ISO), last_seen (UTC ISO)

guild_id (int), channel_id (int)

author (string display), author_id (int)

message_id (int), created_at (UTC ISO)

score (float; returned by search only)

Indexes (Qdrant payload indexes): guild_id, channel_id, author_id, content_hash.

8. Non‑Functional Requirements (NFR)

Security & Privacy: Owner‑only health; admin‑only ingestion/config; no streaming; avoid sensitive data in logs; citations include only public metadata.

Reliability: Daily backups with retention; first backup on startup; health checks report readiness.

Performance: Reasonable latency on 16 GB host; budgets prevent oversized prompts.

Operability: All operations controllable via Discord commands; errors yield readable messages.

Portability: Qdrant and Ollama deployable via Docker; reachable over LAN.

9. Deployment Requirements

docker‑compose must provide containers for Qdrant and, optionally, Ollama, with ports published to the host (e.g., 6333, 11434) and persistent volumes.

Services must be accessible from the local network (192.168.0.0/16).

Data volume for Qdrant: /home/dino/vectordb (host path). Ollama cache: /home/dino/ollama (host path).

10. Permissions & Safety

Only bot owner can run aiuser_health.

Only admins (or users with Manage Server) can modify RAG/LLM config and run ingestion/backup commands.

The bot must follow Discord ToS and local server rules.

11. Acceptance Criteria (Sample)

Config: After aiuser llm set-url http://192.168.1.10:11434/v1, the value is persisted and shown by aiuser llm show (without full key).

Ingest URL: aiuser rag addurl <page> stores chunks; aiuser rag stats shows increased count.

Ingest File: Upload a .pdf or .docx, run aiuser rag addfile, chunks indexed successfully.

Ingest Channel: aiuser rag addhere 500 indexes recent non‑bot messages with author + timestamps.

De‑dupe: Re‑ingesting the same content updates last_seen/sources but does not create new vectors.

Search: aiuser rag search "<query>" returns hits with author + created_at previews.

Persona: dice <prompt> produces a concise reply in Dice’s style; if context exists, citations [n] are appended.

Health: aiuser_health indicates Qdrant ready and Ollama reachable; non‑owner cannot run it.

Backups: On first cog load, a snapshot is created; daily scheduled backup appears in the backup directory; retention prunes old snapshots.

Budgeting: Changing budgets via aiuser_budget … updates limits used in generation.

12. Risks & Mitigations

Model mismatch / dim change: Recreate collection if embed dimension changes.

Large files: Chunking and budgets mitigate prompt growth; admin may lower budget_max_retrieval_chunks.

Network exposure: Limit host firewall to LAN; avoid exposing ports publicly.

Storage growth: De‑dupe and retention policies reduce footprint; admins can rag clear by guild.

13. Future Enhancements (Non‑blocking)

Incremental re‑embedding with alternative models.

Per‑guild collections or namespaces.

Optional message filters (by role/channel categories).

Observability counters (ingested chunks, dedup hits, latencies).