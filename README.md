# Reverse Contact Python SDK

The official Python SDK for the Reverse Contact API provides typed synchronous and asynchronous clients for all 21 V2 operations. Python 3.11 or later is required.

## Install

```bash
pip install reversecontact
export RC_API_KEY="your-api-key"
```

Keep API keys on your server. Both `rc_*` API keys and `oat_*` tokens work; the SDK does not restrict token prefixes.

## Quickstart

Enrich a person from an email address. Lookup methods return `None` for a no-match response, which is free on these routes.

```python
from reversecontact import ReverseContact

with ReverseContact() as client:
    response = client.enrich.person(email="jane@example.com", full_profile=True)
    if response is None:
        print("No match found")
    elif response.data is not None:
        print(response.data.person)
        print(response.data.is_full_profile)
        print(response.quotas)
        print(response.metadata)
        print(response.http_response.headers)
```

Count before searching. Count calls are free, while search bills for delivered results, so item iterators require an explicit `max_results` budget.

```python
with ReverseContact() as client:
    count = client.search.count_people(current_position_title="Engineer")
    print(count.data.count if count.data else None)
    for person in client.search.iter_people(
        current_position_title="Engineer", max_results=25, per_page=20
    ):
        if person is not None:
            print(person.id, person.first_name)
```

A live fetch returns a job handle immediately. Save its id before waiting, and reuse the id after a process restart.

```python
with ReverseContact() as client:
    job = client.fetch.person_live(url="https://social.com/in/janedoe")
    print("Save job id:", job.id)
    snapshot = job.wait(timeout=1800)
    if snapshot.data:
        print(snapshot.data.status, snapshot.data.result)
    # Later: client.jobs.wait(saved_job_id)
```

## Asynchronous client

The asynchronous client has the same resource methods and keyword arguments. Await individual calls and page retrievals; use `async for` for item iterators.

```python
import asyncio
from reversecontact import AsyncReverseContact


async def main():
    async with AsyncReverseContact() as client:
        response = await client.enrich.company(domain="example.com")
        print(response.data if response else "No match found")
        async for person in client.search.iter_people(first_name="Jane", max_results=10):
            if person is not None:
                print(person.id)
        job = await client.contact.find_email(
            first_name="Jane", last_name="Doe", company_domain="example.com"
        )
        snapshot = await job.wait()
        print(snapshot.data.result if snapshot.data else None)


asyncio.run(main())
```

## Configuration and request options

The default base URL is `https://api.reversecontact.com`. Override it with `RC_BASE_URL` or `base_url`; paths already include `/v2`.

```python
import httpx
from reversecontact import ReverseContact

with ReverseContact(
    api_key="your-api-key",  # Defaults to RC_API_KEY
    base_url="https://api.reversecontact.com",  # Defaults to RC_BASE_URL
    timeout=httpx.Timeout(90.0),  # Seconds per attempt
    max_retries=4,  # Four retries after the first attempt
    default_headers={"X-Integration": "my-product"},
    app_info={"name": "my-product", "version": "1.0"},
) as client:
    response = client.usage.retrieve(
        timeout=45.0, max_retries=0, headers={"X-Correlation-ID": "my-request"}
    )
```

Every operation accepts keyword-only `timeout`, `max_retries`, and `headers` overrides. `contact.find_email` and `contact.find_phone` also accept `idempotency_key`. A timeout can be a float or `httpx.Timeout`; the default is 90 seconds, including on an injected HTTP client. A single long poll uses at least `wait + 15` seconds for each finite HTTP timeout component. In a `wait()` helper, the remaining overall deadline caps every HTTP timeout component. Async waits also cap the complete in-flight poll; sync waits can exceed the deadline by one bounded HTTP phase.

Inject `http_client=httpx.Client(...)` or `httpx.AsyncClient(...)` for custom transports, connection pools, or proxies. Context managers and `close()` close only SDK-owned clients. You remain responsible for closing an injected client. `sleep` and `clock` are injectable for deterministic tests; the async client expects an asynchronous sleep function.

Use `client.with_options(timeout=30, max_retries=0)` to copy client options while sharing its connection pool. Header overrides merge with the existing defaults. Closing the copy leaves the pool open; keep its owning client open for the copy's lifetime. The reserved `X-Request-Origin` header is removed after all header merges.

Debug logging is off by default. Set `RC_LOG=debug` before constructing a client, or configure `logging.getLogger("reversecontact")` at `logging.DEBUG`. When no logging handler is configured, `RC_LOG=debug` adds a stderr handler. Diagnostics report method, path, status, attempt, retry delay and request id. API keys, request bodies and query values are excluded.

## Resource reference

Path ids are positional. Body and query arguments use snake_case; their exact API camelCase spellings also work. Unknown keyword arguments pass through unchanged. Known nested fields in dictionaries also accept snake_case. Omitted fields stay omitted, and explicit `None` is sent as JSON null in a body. No URL domain is validated locally.

| Resource | Methods | Example |
| --- | --- | --- |
| Enrichment | `enrich.person`, `enrich.company` | `client.enrich.company(domain="example.com")` |
| Cached fetch | `fetch.person`, `fetch.company` | `client.fetch.person(url="https://social.com/in/janedoe")` |
| Cache checks | `fetch.check_person`, `fetch.check_company` | `client.fetch.check_company(url="https://social.com/company/acme")` |
| Live fetch | `fetch.person_live`, `fetch.company_live` | `client.fetch.company_live(url="https://social.com/company/acme")` |
| People search | `search.people`, `search.people_by_prompt`, `search.people_lookalike` | `client.search.people_by_prompt(prompt="Engineers in Paris", per_page=10)` |
| Company search | `search.organizations`, `search.organizations_by_prompt` | `client.search.organizations(company_name="Acme", per_page=10)` |
| Counts | `search.count_people`, `search.count_organizations` | `client.search.count_organizations(company_name="Acme")` |
| Filter catalogs | `search.people_fields`, `search.organizations_fields` | `client.search.people_fields()` |
| Contact discovery | `contact.find_email`, `contact.find_phone` | `client.contact.find_phone(url="https://social.com/in/janedoe")` |
| Jobs | `jobs.retrieve`, `jobs.wait` | `client.jobs.retrieve("saved-job-id", wait=20)` |
| Usage | `usage.retrieve` | `client.usage.retrieve().quotas` |

## Contact finders

`contact.find_email` and `contact.find_phone` return a job handle. Poll with `job.wait()` or save `job.id` and resume with `client.jobs.wait(saved_id)`.

### Safe retries

Each contact submit sends one UUID v4 `Idempotency-Key`, reused on every retry of that call. The server retains a successful acknowledgement for 24 hours per workspace and endpoint. Reusing the same key and body returns that acknowledgement without starting or charging another job. `job.replayed` reports whether this happened; the raw header is also available in `job.http_response.headers`.

Persist your own key with the request body before submitting to safely retry after a process restart. Pass it as `idempotency_key` or in `headers={"Idempotency-Key": saved_key}`; the option takes precedence. Keys must contain 1 to 255 printable ASCII characters with no leading or trailing whitespace. Invalid keys raise `ReverseContactError` before sending. A changed body with the same key raises `UnprocessableEntityError` with code `IDEMPOTENCY_KEY_REUSED`. An in-flight request returns `IDEMPOTENCY_KEY_IN_USE` and is retried after `Retry-After`, within `max_retries`.

```python
with ReverseContact() as client:
    job = client.contact.find_email(
        first_name="Jane",
        last_name="Doe",
        company_domain="example.com",
        idempotency_key="saved-contact-request-123",
    )
    print(job.id, job.replayed)
```

## Responses and types

Normal calls return `Result[T]` with `.data`, `.quotas`, `.metadata`, and `.http_response`. Enrichment nests the profile under `.data.person` or `.data.company`; `.data.is_full_profile` identifies its shape. Cached fetches return the profile directly in `.data`. Usage returns `.data is None`; its information is in `.quotas`.

Models use Pydantic v2, with snake_case attributes and camelCase aliases. Unknown fields remain accessible through `.model_extra` and their exact API name. Missing fields and unexpected nulls are accepted, and response enums accept future string values. Fields and array elements accept null, so check them before use. `model.to_dict()` returns the API shape with aliases, preserving explicit nulls and omitting fields absent from the response.

```python
from reversecontact import Person

person = Person.model_validate({"firstName": "Jane", "futureField": 0})
assert person.first_name == "Jane"
assert person.model_extra["futureField"] == 0
assert person.to_dict() == {"firstName": "Jane", "futureField": 0}
```

`reversecontact.types` exports the generated request and response models, including `Person`, `Company`, `EnrichPersonBody`, `SearchPeopleBody`, and `PollWebhookData`. Request dictionaries and keyword arguments remain usable without constructing models. The package includes `py.typed` for type checkers and IDE completion.

## Pagination and credit budgets

Search methods return `Page[T]` or `AsyncPage[T]`. A page exposes `.data` as an item array, `.next_cursor`, `.has_next_page()`, and `.get_next_page()`, which returns `None` after the last page. Quotas and request metadata describe that page. Pages also expose `.count`, `.per_page`, and `.interpretation`. All additional payload fields are retained in `.extras`. Pages and iterators copy their filters, including nested dictionaries, so later caller mutations do not alter subsequent requests.

```python
with ReverseContact() as client:
    page = client.search.people(first_name="Jane", per_page=10)
    if page.has_next_page():
        next_page = page.get_next_page()  # Another potentially billed request
```

Search helpers are `iter_people`, `iter_people_by_prompt`, `iter_people_lookalike`, `iter_organizations`, and `iter_organizations_by_prompt`. Each requires `max_results`, reduces `perPage` to the remaining result budget, and stops at a null cursor. `max_results=0` makes no requests. Pagination uses cursors; there are no page numbers. The default page size is 25 and the maximum is 100; larger values raise instead of being clamped. Search budgets must be non-negative integers; `None` is rejected. Repeated cursors raise `ReverseContactError`.

## Errors and retries

All SDK errors inherit from `ReverseContactError`. API errors carry `.status`, `.code`, `.message`, `.details`, `.request_id`, `.headers`, `.body`, and `.http_response`. Validation details and suggestions remain intact. Non-JSON gateway failures become typed HTTP errors instead of JSON parsing exceptions.

```python
from reversecontact import APIError, DailyLimitError, KeyCreditLimitError, ReverseContact

with ReverseContact() as client:
    try:
        client.search.people(first_name="Jane", per_page=5)
    except (DailyLimitError, KeyCreditLimitError) as error:
        print(error.retry_after, error.reset_at)
    except APIError as error:
        print(error.status, error.code, error.request_id, error.details)
```

HTTP classes are `BadRequestError` (400), `AuthenticationError` (401), `InsufficientCreditsError` (402), `PermissionDeniedError` (403), `NotFoundError` (404), `ConflictError` (409), `GoneError` (410), `UnprocessableEntityError` (422), `RateLimitError` (429), `UnavailableForLegalReasonsError` (451), and `InternalServerError` (5xx). Unknown statuses become `APIError`. `DailyLimitError` specializes `RateLimitError`; `KeyCreditLimitError` takes precedence for the key-credit error code and exposes `.period`, `.limit`, `.used`, and `.required`.

The SDK retries ordinary 429s, HTTP 500/502/503/504, and connection failures. It honors server retry hints, otherwise uses exponential backoff of 2, 4, 8, then 15 seconds, plus up to 250 milliseconds of jitter. For 429s, `details.retryAfter` takes precedence over `Retry-After`. If any of `Retry-After`, `details.retryAfter`, or `details.retryAfterSeconds` exceeds 60 seconds, the error is raised immediately, regardless of priority. Daily limits and key credit limits are never retried. The only retryable 409 is `IDEMPOTENCY_KEY_IN_USE` on a contact submit; it honors `Retry-After`. Other 4xx errors are never retried.

Timeouts raise `APITimeoutError`, a subtype of `APIConnectionError`. GET requests and idempotent contact submits can retry timeouts and connection failures after sending. Other POSTs retry only `httpx.ConnectError` or `httpx.ConnectTimeout` when no connection was established. After headers arrive, an unreadable error body retains its HTTP status and normal status retry policy. An unreadable 2xx body raises `APIConnectionError` carrying `.status`, with retries limited to GET and contact submits. Contact submits also retry malformed JSON acknowledgements. `max_retries=0` disables automatic request retries.

The four cached lookup and enrichment methods return `None` for JSON no-match responses with HTTP 404. The codes `NOT_FOUND` and `HTTP_404`, including non-JSON 404s from a proxy or wrong base URL, raise `NotFoundError`. A 404 from any other operation also raises `NotFoundError`.

## Jobs

Live fetches and contact finders return `Job[T]` or `AsyncJob[T]`. A handle exposes `.id`, `.replayed`, `.status`, `.poll_url`, `.data`, `.quotas`, `.metadata`, and `.http_response`. `retrieve(wait=20)` polls once. `wait(timeout=1800, poll_wait=20, on_poll=callback)` returns `Result[JobSnapshot[T]]`, with the snapshot under `.data` and quotas, metadata, and the raw response alongside it. `retrieve()` and `jobs.wait()` use the same envelope; `on_poll` receives it too.

`wait()` long polls with `wait=20` by default. An early answer waits out the remaining `Retry-After` interval before the next poll starts; an already held poll can continue immediately. A missing header defaults to two seconds for jobs. Poll retries are independent of the creating call: five consecutive retryable failures raise, and a good response resets the counter. The overall deadline caps each poll's HTTP timeout. The query wait is also reduced to the remaining whole seconds. Async polls are cancelled on expiry. Sync polls run directly in the caller's thread, with no background workers; an expired deadline raises `JobTimeoutError` when the HTTP phase finishes or times out. A bounded overshoot of one HTTP phase is possible. The error preserves the job id and last observed status.

`JobFailedError` includes `.job_id`, `.error_code`, `.credits_consumed`, `.result`, and `.not_found`. `JobTimeoutError` includes `.job_id` and `.last_status`; the server job may still finish. Resume with `jobs.wait(saved_id)`. A non-null `delivery_status` on a succeeded job does not make the job fail. An expired result raises `GoneError`. The SDK never recreates a failed job, even for retryable job error codes: a new job can be billed again, so the caller decides.

## Webhook deliveries

Pass `webhook_url` to a job-creating method to receive a push delivery. `parse_webhook_delivery` accepts a decoded body, a JSON string, or bytes and returns the typed `{webhookId, data}` wrapper; malformed deliveries raise `ReverseContactError`. `data.errorCode` must be a string or null; `.is_error` is true only for a non-empty string.

```python
from reversecontact import parse_webhook_delivery

delivery = parse_webhook_delivery(
    {"webhookId": "saved-job-id", "data": {"errorCode": "email-not-found"}}
)
print(delivery.webhook_id, delivery.is_error, delivery.data)
```

Use an HTTPS endpoint, respond with 2xx within 10 seconds, and process the delivery asynchronously. Deduplicate on `webhookId` because rare duplicates can occur. You can still poll a job that uses push delivery.

## Best practices

- Keep keys on your server and use separate keys for integrations.
- Check counts and available filter fields before paid searches; set explicit result budgets.
- Prefer cached fetches before live fetches when freshness allows it.
- Handle lookup no-matches and distinguish quota limits from transient failures.
- Persist job ids before waiting, and include request ids when contacting support.
- Observe quotas returned by the API; do not assume a fixed credit charge for every workspace.

Read the [API documentation](https://docs.reversecontact.com), [error and retry guide](https://docs.reversecontact.com/docs/guides/errors-retries), [polling guide](https://docs.reversecontact.com/docs/guides/polling), and [production checklist](https://docs.reversecontact.com/docs/guides/production-checklist).

## Development

Dependencies are managed with uv. Run the following checks from the repository root (or `sdks/python` in the monorepo). The committed models and resource facades work without external files. In the monorepo, `uv run --frozen python scripts/generate.py` regenerates them from `../openapi/public-v2.json` and `../operations.json`; add `--check` to verify freshness. In the public repository, generation and snapshot-dependent tests skip with an explicit reason because these inputs are not published. Unit tests, lint, type checks, builds and wheel smoke checks run in both repositories. Handwritten quota models follow runtime nullability rather than the snapshot's stricter declarations.

```bash
uv sync --frozen
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen mypy examples scripts/smoke_wheel.py
uv run --frozen pytest -q
uv build --no-build-isolation
uv run --isolated --no-project --with dist/*.whl python scripts/smoke_wheel.py
```

Tests mock every request. The optional `RC_SDK_LIVE=1 uv run --frozen pytest -q tests/test_live.py` smoke calls only free endpoints and uses `RC_BASE_URL` and `RC_API_KEY`; run it against an authorized environment. Examples can consume credits and are never run by the test suite.

## Contributing

This repository is published from the Reverse Contact monorepo. Report issues in [this repository](https://github.com/visum-run/reversecontact-python/issues). Pull requests are reviewed here and merged upstream before the next release is published.

## License

This SDK is licensed under [Apache-2.0](LICENSE).
