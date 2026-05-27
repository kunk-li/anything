# Logging Module Design

## 1. Purpose

The logging module is the **single source of truth** for runtime observability
in the anything system. All modules — base support, data layer, business layer,
interface layer, application layer — write to a unified logger via the
`SystemLogger` interface.

## 2. Core Principles

- **trace_id propagation**: Every log entry includes the `trace_id` that flows
  through the entire request chain from API ingress to downstream LLM call.
- **structured fields**: JSON-friendly log format with timestamp, level,
  logger_name, trace_id, session_id, message.
- **multi-process safety**: Locks must be acquired and released in pairs.
  Each process should initialize its own log handles or ensure thread-safe
  sharing.
- **log writing must not crash main flow**: Any exception in the logging
  subsystem is swallowed and logged to stderr as last resort.

## 3. Log Levels

The standard Python `logging` levels apply:

- `DEBUG` — verbose tracing for development
- `INFO` — normal request lifecycle markers (start, complete)
- `WARNING` — degraded mode (e.g. LLM 401 → DummyLLM fallback)
- `ERROR` — failed request or contract violation
- `CRITICAL` — startup failure or unrecoverable state

## 4. Configuration

Log level and output destination are configured via the global
`config.yaml` under `global.log_level` and `log.output_dir`.
Per-module overrides go through `ConfigManager.get_config("log.<module>.level")`.

## 5. Best Practices

- Always log with `extra={"trace_id": ..., "session_id": ...}` so structured
  ingestion (Loki / ELK) can index correctly.
- Avoid logging secrets — only log last 4 chars of API keys.
- Use `logger.exception()` to capture full stack traces; never raise inside
  exception handlers without logging first.
