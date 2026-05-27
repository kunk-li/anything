# Exception Handling Module Design

## 1. Goals

- **Single error envelope**: Every response (success or failure) carries the
  same seven-field shape: `code / message / data / trace_id / retryable /
  details / cost_time`.
- **Stable error codes**: Codes follow `MODULE_ACTION_REASON` naming; once
  published, codes never change (message text can be updated).
- **Retry hints**: `retryable` flag tells the caller whether exponential
  backoff is worth attempting.

## 2. Code Taxonomy

Error codes are prefixed by module to keep grep-ability:

- `CONFIG_*` — configuration issues (missing yaml key, schema invalid)
- `VECTOR_*` — vector database operations
- `DOCUMENT_*` — document parsing / storage
- `RAG_*` — RAG pipeline (embedding / retrieval / generation)
- `AGENT_*` — agent execution (tool not found, timeout, exceeded retries)
- `API_*` — HTTP layer (rate limit, body too large)
- `AUTH_*` — authentication / authorization
- `EVAL_*` — evaluation harness

## 3. HTTP Status Code Mapping

The application layer is the only place that translates business codes to HTTP
status. The interface and business layers never assume an HTTP-specific shape.

- `2xx` — SUCCESS
- `400` — caller fault (missing/invalid params)
- `401/403` — auth required / forbidden
- `404` — resource not found
- `415` — unsupported file type
- `429` — rate limited
- `500` — server-side failure
- `504` — execution timeout

## 4. Retry Strategy

`retryable=true` codes are eligible for client-side exponential backoff:
`VECTOR_QUERY_FAILED`, `TOOL_CALL_FAILED`, `AGENT_TIMEOUT`, `API_RATE_LIMITED`.

The recommended backoff schedule is 200ms → 400ms → 800ms with a maximum of
3 retries. Beyond that, the caller should surface the error to the user.

## 5. Details Field

`details` carries structured context for the failure, never plain text dumps.
For example, on `VECTOR_QUERY_FAILED`:

```json
{
  "index": "faiss_default",
  "operation": "query",
  "top_k": 10,
  "embedding_dim": 1024
}
```

This lets the operator pinpoint root cause without reading server logs.
