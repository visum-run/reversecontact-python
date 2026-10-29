# Changelog

## 0.1.0 (unreleased)

- Require Python 3.11 or later and use modern standard-library typing and async deadlines.
- License the SDK under Apache-2.0.
- Add standalone repository CI, trusted PyPI publishing, provenance attestations and release notes.

- Add idempotent contact submits with automatic or caller-provided keys, safe retries and replay detection.
- Add synchronous and asynchronous clients for all 21 production V2 operations.
- Add typed models, errors, bounded retries, job polling, webhook parsing, and cursor pagination.
- Require explicit search result budgets.
- Include examples, offline contract tests, package smoke checks, and an opt-in free-endpoint smoke.
- Preserve HTTP status on interrupted bodies and limit other POST retries to safe connection failures.
- Bound polling HTTP timeouts by the overall deadline and return complete job envelopes to callers and callbacks.
- Keep synchronous polling in the caller's thread; cancel asynchronous polls on deadline expiry.
- Snapshot pagination filters, validate budgets and page sizes, and expose typed page extras.
- Add shared-pool client copies, opt-in debug logging, and stricter webhook delivery validation.
- Reject retries when any server hint exceeds 60 seconds and raise HTTP errors for non-JSON lookup 404s.
