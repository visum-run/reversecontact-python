# Changelog

## 0.1.3 (2026-10-29)

- Integers stay integers: counts, credits, quotas, page sizes and every other numeric field keep the type the API sent (`int` for `2`, `float` for `2.5`) instead of always becoming `float` (`2.0`).

## 0.1.2 (2026-10-29)

- The README and the examples pass the API key explicitly: `ReverseContact(api_key=os.environ["RC_API_KEY"])`. Without `api_key`, the client still reads `RC_API_KEY`.

## 0.1.1 (2026-10-29)

- README rewritten for GitHub and PyPI: links to the documentation, a quickstart that runs as pasted, a table of every resource and method, requirements, versioning and support.
- An `idempotency_key` that contains a space is refused before the request is sent, as the API refuses it.
- `JobFailedError.not_found` is also `True` for `phone-not-found`, the outcome of a phone lookup that finds no number.
- The enrich example no longer passes the deprecated `full_profile` option.

## 0.1.0 (2026-10-29)

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
