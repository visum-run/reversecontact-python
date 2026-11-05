# Reverse Contact Python SDK

[![PyPI version](https://img.shields.io/pypi/v/reversecontact)](https://pypi.org/project/reversecontact/)
[![Python versions](https://img.shields.io/pypi/pyversions/reversecontact)](https://pypi.org/project/reversecontact/)
[![CI](https://img.shields.io/github/actions/workflow/status/visum-run/reversecontact-python/ci.yml?branch=main&label=CI)](https://github.com/visum-run/reversecontact-python/actions/workflows/ci.yml)
[![License](https://img.shields.io/pypi/l/reversecontact)](https://github.com/visum-run/reversecontact-python/blob/main/LICENSE)

Reverse Contact is a B2B data API: enrich people and companies, fetch profiles, search people and organizations, and find email addresses and phone numbers.

This is the official Python client for the Reverse Contact V2 API: synchronous and asynchronous clients, typed Pydantic models, automatic retries, cursor pagination, asynchronous tasks and Lists.

[Documentation](https://docs.reversecontact.com/sdk/python) · [API reference](https://docs.reversecontact.com/api-reference) · [Get an API key](https://app.reversecontact.com) · [Changelog](https://github.com/visum-run/reversecontact-python/blob/main/CHANGELOG.md)

## Installation

```bash
pip install reversecontact
```

The package also installs with `uv add reversecontact` or `poetry add reversecontact`. Python 3.11 or later is required.

## Quickstart

Create an API key in the [Reverse Contact app](https://app.reversecontact.com) and export it:

```bash
export RC_API_KEY="your-api-key"
```

Save this file as `quickstart.py` and run `python quickstart.py`:

```python
import os

from reversecontact import ReverseContact

with ReverseContact(api_key=os.environ["RC_API_KEY"]) as client:
    response = client.enrich.person(email="jane@example.com")
    if response is None:
        print("No match found")
    elif response.data is not None:
        print(response.data.person)
        print("Full profile:", response.data.is_full_profile)
        print("Request id:", response.metadata.request_id if response.metadata else None)
        print("Credits used:", response.quotas.credits_consumed if response.quotas else None)
```

Lookup methods return `None` for a no-match response, which is free on these routes.

Pass your key as `api_key`, from any server-side source: an environment variable as above, a secret manager, or one client per key when you serve several workspaces. When `api_key` is omitted, the client reads `RC_API_KEY`.

Keep API keys on your server. Both `rc_*` API keys and `oat_*` tokens work; the SDK does not restrict token prefixes.

## Resources

| Resource | Methods | Example |
| --- | --- | --- |
| Enrichment | `enrich.person`, `enrich.company` | `client.enrich.company(domain="example.com")` |
| Cached fetch | `fetch.person`, `fetch.company` | `client.fetch.person(url="https://social.com/in/janedoe")` |
| Cache checks (free) | `fetch.check_person`, `fetch.check_company` | `client.fetch.check_company(url="https://social.com/company/acme")` |
| Live fetch (task) | `fetch.person_live`, `fetch.company_live` | `client.fetch.company_live(url="https://social.com/company/acme")` |
| People search | `search.people`, `search.people_by_prompt`, `search.people_lookalike` | `client.search.people_by_prompt(prompt="Engineers in Paris", per_page=10)` |
| Company search | `search.organizations`, `search.organizations_by_prompt` | `client.search.organizations(company_name="Acme", per_page=10)` |
| Search iterators | `search.iter_people`, `search.iter_people_by_prompt`, `search.iter_people_lookalike`, `search.iter_organizations`, `search.iter_organizations_by_prompt` | `client.search.iter_people(first_name="Jane", max_results=10)` |
| Counts (free) | `search.count_people`, `search.count_organizations` | `client.search.count_organizations(company_name="Acme")` |
| Auto-suggestion (free) | `search.people_auto_suggestion` | `client.search.people_auto_suggestion(field="skills", query="pyth")` |
| Filter catalogs (free) | `search.people_fields`, `search.organizations_fields` | `client.search.people_fields()` |
| Contact discovery (task) | `contact.find_email`, `contact.find_phone`, `contact.verify_email` | `client.contact.verify_email(email="jane@example.com")` |
| Tasks (`jobs` is a deprecated alias) | `tasks.retrieve`, `tasks.wait` | `client.tasks.retrieve("saved-task-id", wait=20)` |
| Usage (free) | `usage.retrieve` | `client.usage.retrieve().quotas` |
| Lists | `lists.list`, `lists.create`, `lists.retrieve`, `lists.delete`, `lists.export`, `lists.import_signals` | `client.lists.create(name="Prospects", entity="persons")` |
| List members | `lists.members.list`, `lists.members.add`, `lists.members.remove` | `client.lists.members.remove("list-id", member_ids=["member-id"])` |
| List operations | `lists.operations.retrieve`, `lists.operations.cancel`, `lists.operations.wait` | `client.lists.operations.retrieve("list-id", "operation-id", wait=20)` |
| Bulk enrich | `bulk.enrich_persons`, `bulk.list`, `bulk.list_all`, `bulk.retrieve`, `bulk.wait`, `bulk.cancel` | `client.bulk.retrieve("task-id")` |
| Bulk results (free) | `bulk.results`, `bulk.rows`, `bulk.iter_rows` | `client.bulk.rows("task-id", limit=100)` |

The asynchronous client has the same resources and methods. Credit costs per endpoint are listed in the [API reference](https://docs.reversecontact.com/api-reference).

The Lists API is available only in enabled environments. Disabled endpoints can return `GoneError` with an `alternative` property. CSV exports return a string: `csv_text = client.lists.export("list-id")`.

Path ids are positional. Body and query arguments use snake_case; their exact API camelCase spellings also work. Unknown keyword arguments pass through unchanged. Known nested fields in dictionaries also accept snake_case. Omitted fields stay omitted, and explicit `None` is sent as JSON null in a body. No URL domain is validated locally.

## Usage

### Search

Count before searching. Count calls are free, while search bills for delivered results, so item iterators require an explicit `max_results` budget.

```python
with ReverseContact(api_key=os.environ["RC_API_KEY"]) as client:
    count = client.search.count_people(current_position_title="Engineer")
    print(count.data.count if count.data else None)
    for person in client.search.iter_people(
        current_position_title="Engineer", max_results=25, per_page=20
    ):
        if person is not None:
            print(person.id, person.first_name)
```

### Published lists

The closed lists of the API are string enums: `Industry`, `CountryCode`, `JobFunction`, `JobSubFunction`, `Seniority`, `ContractType`, `Area` and `EmailType`. A member is its value (`Industry.SOFTWARE_DEVELOPMENT == "Software Development"`).

```python
from reversecontact import Area, Industry, Seniority

with ReverseContact(api_key=os.environ["RC_API_KEY"]) as client:
    leaders = client.search.count_people(
        current_company_industry=Industry.SOFTWARE_DEVELOPMENT,
        current_seniority=[Seniority.CXO, Seniority.OWNER_FOUNDER],
        location={"area": Area.MC_DACH},
    )
    print(leaders.data.count if leaders.data else None)
```

The parameters also accept any plain string, so a value added to a list after your SDK version still works. The API rejects a value it does not know with a 422 error.

### Live fetch

A live fetch returns a task handle immediately. Save its id before waiting, and reuse the id after a process restart.

```python
with ReverseContact(api_key=os.environ["RC_API_KEY"]) as client:
    task = client.fetch.person_live(url="https://social.com/in/janedoe")
    print("Save task id:", task.id)
    snapshot = task.wait(timeout=1800)
    if snapshot.data:
        print(snapshot.data.status, snapshot.data.result)
    # Later: client.tasks.wait(saved_task_id)
```

### Find email addresses and phone numbers

`contact.find_email` and `contact.find_phone` return a task handle. Poll with `task.wait()` or save `task.id` and resume with `client.tasks.wait(saved_id)`.

Each finder takes one person: `url`, `id` (a `prs_…` person id from a search, fetch or enrich result), or a name with `company_domain`. An id that matches no person raises `UnprocessableEntityError` before any task starts.

```python
task = client.contact.find_email(id="prs_01jbd5vewyenebxgaz869ffe5t")
```

### Verify an email address

`contact.verify_email` returns a task handle like the finders. Its result holds `email` and `status`, one of `valid`, `risky`, `invalid` or `unknown` (the `EmailVerificationStatus` enum).

```python
task = client.contact.verify_email(email="jane@example.com")
print(task.wait().data.result.status)
```

#### Safe retries

Each contact submit sends one UUID v4 `Idempotency-Key`, reused on every retry of that call. The server retains a successful acknowledgement for 24 hours per workspace and endpoint. Reusing the same key and body returns that acknowledgement without starting or charging another task. `task.replayed` reports whether this happened; the raw header is also available in `task.http_response.headers`.

Persist your own key with the request body before submitting to safely retry after a process restart. Pass it as `idempotency_key` or in `headers={"Idempotency-Key": saved_key}`; the option takes precedence. Keys must contain 1 to 255 printable ASCII characters, without spaces. Invalid keys raise `ReverseContactError` before sending. A changed body with the same key raises `UnprocessableEntityError` with code `IDEMPOTENCY_KEY_REUSED`. An in-flight request returns `IDEMPOTENCY_KEY_IN_USE` and is retried after `Retry-After`, within `max_retries`.

```python
with ReverseContact(api_key=os.environ["RC_API_KEY"]) as client:
    task = client.contact.find_email(
        first_name="Jane",
        last_name="Doe",
        company_domain="example.com",
        idempotency_key="saved-contact-request-123",
    )
    print(task.id, task.replayed)
```

### Bulk enrich

```python
with ReverseContact(api_key=os.environ["RC_API_KEY"]) as client:
    submitted = client.bulk.enrich_persons(
        items=[{"email": "jane@example.com", "external_id": "crm-42"}],
        families=["profile"],
        task_name="CRM sync",
    )
    if submitted.data and submitted.data.task and submitted.data.task.id:
        task_id = submitted.data.task.id
        # Each result in the order sent, as soon as it is ready, while the task runs.
        for line in client.bulk.iter_rows(task_id):
            if line is not None:
                print(line.row, line.status, line.person)
        # Or the whole file once the task is completed or cancelled.
        client.bulk.wait(task_id)
        ndjson = client.bulk.results(task_id, format="ndjson")
```

`bulk.enrich_persons` posts to `/v2/bulk` and answers with the task in `data.task`; `task_name` (sent as `taskName`) names it in the task history (default `Sent via API`). `bulk.list`, `bulk.retrieve`, `bulk.wait` and `bulk.cancel` read and stop every background task of the workspace under `/v2/tasks`; a task's `task_name` is the file name or the name given at creation. `bulk.list_all` asks for 50 tasks per page by default and accepts `per_page` up to 100.

`bulk.rows(task_id, cursor=..., limit=...)` reads one page of `TaskResultLine` items (at most 100), with the task status in `page.extras["status"]`. While the task runs, a page stops at the first item not finished; `iter_rows` then waits `Retry-After` (2 seconds by default) and reads the same cursor again. It ends when `next_cursor` is `None`, or when the task is paused: read on later with `rows`. A cancelled task still finishes the items it had started, within the hour: `iter_rows` waits for them, while `results` already gives them with the status `running`. `results` returns the file as text, the CSV by default or one JSON object per line with `format="ndjson"`. A task stored before these formats existed raises `ConflictError` (`TASK_RESULTS_FORMAT_UNAVAILABLE`); its CSV stays available. An unknown task id raises `NotFoundError` (`TASK_NOT_FOUND`).

With `webhook_url`, that URL receives one POST when the task settles, with the event `task.updated` in its body and in the `x-reversecontact-event` header: `{event, taskId, type, status, pollUrl, resultsUrl, occurredAt}`. It carries no results, and the same event can arrive twice: key it on `taskId` and `status`. This body is not the `{webhookId, data}` delivery of live fetches and contact lookups, so `parse_webhook_delivery` does not apply to it.

### Asynchronous client

The asynchronous client has the same resource methods and keyword arguments. Await individual calls and page retrievals; use `async for` for item iterators.

```python
import asyncio
import os

from reversecontact import AsyncReverseContact


async def main() -> None:
    async with AsyncReverseContact(api_key=os.environ["RC_API_KEY"]) as client:
        response = await client.enrich.company(domain="example.com")
        print(response.data if response else "No match found")
        async for person in client.search.iter_people(first_name="Jane", max_results=10):
            if person is not None:
                print(person.id)
        task = await client.contact.find_email(
            first_name="Jane", last_name="Doe", company_domain="example.com"
        )
        snapshot = await task.wait()
        print(snapshot.data.result if snapshot.data else None)


asyncio.run(main())
```

## Configuration and request options

The default base URL is `https://api.reversecontact.com`. Override it with `RC_BASE_URL` or `base_url`; paths already include `/v2`.

```python
import os

import httpx
from reversecontact import ReverseContact

with ReverseContact(
    api_key=os.environ["RC_API_KEY"],  # Without it, the client reads RC_API_KEY
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

Every operation accepts keyword-only `timeout`, `max_retries`, and `headers` overrides. `contact.find_email`, `contact.find_phone`, `contact.verify_email`, and `lists.create` also accept `idempotency_key`. A timeout can be a float or `httpx.Timeout`; the default is 90 seconds, including on an injected HTTP client. A single long poll uses at least `wait + 15` seconds for each finite HTTP timeout component. In a `wait()` helper, the remaining overall deadline caps every HTTP timeout component. Async waits also cap the complete in-flight poll; sync waits can exceed the deadline by one bounded HTTP phase.

Inject `http_client=httpx.Client(...)` or `httpx.AsyncClient(...)` for custom transports, connection pools, or proxies. Context managers and `close()` close only SDK-owned clients. You remain responsible for closing an injected client. `sleep` and `clock` are injectable for deterministic tests; the async client expects an asynchronous sleep function.

Use `client.with_options(timeout=30, max_retries=0)` to copy client options while sharing its connection pool. Header overrides merge with the existing defaults. Closing the copy leaves the pool open; keep its owning client open for the copy's lifetime. The reserved `X-Request-Origin` header is removed after all header merges.

Debug logging is off by default. Set `RC_LOG=debug` before constructing a client, or configure `logging.getLogger("reversecontact")` at `logging.DEBUG`. When no logging handler is configured, `RC_LOG=debug` adds a stderr handler. Diagnostics report method, path, status, attempt, retry delay and request id. API keys, request bodies and query values are excluded.

## Responses and types

Normal calls return `Result[T]` with `.data`, `.quotas`, `.metadata`, and `.http_response`. Enrichment nests the profile under `.data.person` or `.data.company`; `.data.is_full_profile` identifies its shape. Cached fetches return the profile directly in `.data`. Usage returns `.data is None`; its information is in `.quotas`.

Models use Pydantic v2, with snake_case attributes and camelCase aliases. Unknown fields remain accessible through `.model_extra` and their exact API name. Missing fields and unexpected nulls are accepted, and response enums accept future string values. Fields and array elements accept null, so check them before use. `model.to_dict()` returns the API shape with aliases, preserving explicit nulls and omitting fields absent from the response.

```python
from reversecontact import Person

person = Person.model_validate({"firstName": "Jane", "futureField": 0})
assert person.first_name == "Jane"
assert person.model_extra == {"futureField": 0}
assert person.to_dict() == {"firstName": "Jane", "futureField": 0}
```

`reversecontact.types` exports the generated request and response models, including `Person`, `Company`, `EnrichPersonBody`, `SearchPeopleBody`, and `PollWebhookData`. Request dictionaries and keyword arguments remain usable without constructing models. The package includes `py.typed` for type checkers and IDE completion.

## Pagination and credit budgets

Search and list collection methods return `Page[T]` or `AsyncPage[T]`. A page exposes `.data` as an item array, `.next_cursor`, `.has_next_page()`, and `.get_next_page()`, which returns `None` after the last page. Quotas and request metadata describe that page. Search pages also expose `.count`, `.per_page`, and `.interpretation`; Lists member pages expose `.member_count` and `.pending_intake_count`. All additional payload fields are retained in `.extras`. Pages and iterators copy their filters, including nested dictionaries, so later caller mutations do not alter subsequent requests.

```python
with ReverseContact(api_key=os.environ["RC_API_KEY"]) as client:
    page = client.search.people(first_name="Jane", per_page=10)
    if page.has_next_page():
        next_page = page.get_next_page()  # Another potentially billed request
    for saved_list in client.lists.list_all():
        if saved_list is not None:
            print(saved_list.id)
    for member in client.lists.members.list_all("list-id", max_results=100):
        if member is not None:
            print(member.id)
```

Search helpers are `iter_people`, `iter_people_by_prompt`, `iter_people_lookalike`, `iter_organizations`, and `iter_organizations_by_prompt`. Each requires `max_results`, reduces `perPage` to the remaining result budget, and stops at a null cursor. `max_results=0` makes no requests. Lists and list members are free collections, so `list_all()` accepts an optional budget. Pagination uses cursors; there are no page numbers. Default page sizes are 25 for search, 200 for Lists, and 100 for list members. Their maximum sizes are 100, 200, and 1000 respectively; larger values raise instead of being clamped. Search budgets must be non-negative integers; `None` is rejected. Repeated cursors raise `ReverseContactError`.

## Errors and retries

All SDK errors inherit from `ReverseContactError`. API errors carry `.status`, `.code`, `.message`, `.details`, `.request_id`, `.headers`, `.body`, and `.http_response`. Validation details and suggestions remain intact. Non-JSON gateway failures become typed HTTP errors instead of JSON parsing exceptions.

```python
import os

from reversecontact import APIError, DailyLimitError, KeyCreditLimitError, ReverseContact

with ReverseContact(api_key=os.environ["RC_API_KEY"]) as client:
    try:
        client.search.people(first_name="Jane", per_page=5)
    except (DailyLimitError, KeyCreditLimitError) as error:
        print(error.retry_after, error.reset_at)
    except APIError as error:
        print(error.status, error.code, error.request_id, error.details)
```

| HTTP status | Error class |
| --- | --- |
| 400 | `BadRequestError` |
| 401 | `AuthenticationError` |
| 402 | `InsufficientCreditsError` |
| 403 | `PermissionDeniedError` |
| 404 | `NotFoundError` (except lookup no-match) |
| 409 | `ConflictError` |
| 410 | `GoneError` |
| 422 | `UnprocessableEntityError` |
| 429 | `RateLimitError`, or `DailyLimitError` for daily limits |
| 451 | `UnavailableForLegalReasonsError` |
| 5xx | `InternalServerError` |
| Other | `APIError` |

`DailyLimitError` specializes `RateLimitError`; `KeyCreditLimitError` takes precedence for the key-credit error code and exposes `.period`, `.limit`, `.used`, and `.required`.

The SDK retries ordinary 429s, HTTP 500/502/503/504, and connection failures. It honors server retry hints, otherwise uses exponential backoff of 2, 4, 8, then 15 seconds, plus up to 250 milliseconds of jitter. For 429s, `details.retryAfter` takes precedence over `Retry-After`. If any of `Retry-After`, `details.retryAfter`, or `details.retryAfterSeconds` exceeds 60 seconds, the error is raised immediately, regardless of priority. Daily limits and key credit limits are never retried. The only retryable 409 is `IDEMPOTENCY_KEY_IN_USE` on a contact submit; it honors `Retry-After`. Other 4xx errors are never retried.

Timeouts raise `APITimeoutError`, a subtype of `APIConnectionError`. GET requests and idempotent contact submits can retry timeouts and connection failures after sending; DELETE requests can too before response headers arrive. Other POSTs retry only `httpx.ConnectError` or `httpx.ConnectTimeout` when no connection was established. After headers arrive, an unreadable error body retains its HTTP status and normal status retry policy. An unreadable 2xx body raises `APIConnectionError` carrying `.status`, with retries limited to GET and contact submits. Contact submits also retry malformed JSON acknowledgements. `max_retries=0` disables automatic request retries. A list creation gets one UUID idempotency key per call unless you supply one; imports similarly get one `operationId`. These identifiers remain unchanged across retries.

The four cached lookup and enrichment methods return `None` for JSON no-match responses with HTTP 404. The codes `NOT_FOUND` and `HTTP_404`, including non-JSON 404s from a proxy or wrong base URL, raise `NotFoundError`. A 404 from any other operation also raises `NotFoundError`.

## Tasks and imports

Live fetches and contact finders return `Task[T]` or `AsyncTask[T]`. A handle exposes `.id`, `.replayed`, `.status`, `.poll_url`, `.data`, `.quotas`, `.metadata`, and `.http_response`. `retrieve(wait=20)` polls once. `wait(timeout=1800, poll_wait=20, on_poll=callback)` returns `Result[TaskSnapshot[T]]`, with the snapshot under `.data` and quotas, metadata, and the raw response alongside it. `retrieve()` and `tasks.wait()` use the same envelope; `on_poll` receives it too.

`wait()` long polls with `wait=20` by default. An early answer waits out the remaining `Retry-After` interval before the next poll starts; an already held poll can continue immediately. A missing header defaults to two seconds for tasks. Poll retries are independent of the creating call: five consecutive retryable failures raise, and a good response resets the counter. The overall deadline caps each poll's HTTP timeout. The query wait is also reduced to the remaining whole seconds. Async polls are cancelled on expiry. Sync polls run directly in the caller's thread, with no background workers; an expired deadline raises `TaskTimeoutError` when the HTTP phase finishes or times out. A bounded overshoot of one HTTP phase is possible. The error preserves the task id and last observed status.

`TaskFailedError` includes `.task_id`, `.error_code`, `.credits_consumed`, `.result`, and `.not_found`. `TaskTimeoutError` includes `.task_id` and `.last_status`; the server task may still finish. Resume with the wait that raised it, as its message says: `tasks.wait(saved_id)`, `bulk.wait` or `lists.operations.wait`. A non-null `delivery_status` on a succeeded task does not make the task fail. An expired result raises `GoneError`. The SDK never recreates a failed task, even for retryable task error codes: a new task can be billed again, so the caller decides.

The former names still work and are deprecated: `client.jobs` is `client.tasks`, `Job` and `AsyncJob` are `Task` and `AsyncTask`, `JobSnapshot` is `TaskSnapshot`, `JobFailedError` and `JobTimeoutError` are `TaskFailedError` and `TaskTimeoutError` (their `.job_id` reads `.task_id`), and `reversecontact.types.JobAckData` is `TaskAckData`.

```python
with ReverseContact(api_key=os.environ["RC_API_KEY"]) as client:
    operation = client.lists.import_signals(
        "list-id", signals=[{"email": "jane@example.com"}], max_credits=10
    )
    if operation.data and operation.data.operation_id:
        final = client.lists.operations.wait("list-id", operation.data.operation_id)
        print(final.data.client_action if final.data else None)
```

List operation waiting honors `pollAfterSeconds` or `Retry-After`, falling back to two seconds when both are absent. Its callback receives the complete operation `Result`. It returns the final `Result` when `clientAction` is `done`, `resume`, or `reconcile`. Inspect the operation before deciding what to do next; the SDK never replays a reconciliation request.

## Webhook deliveries

Pass `webhook_url` to a task-creating method to receive a push delivery. `parse_webhook_delivery` accepts a decoded body, a JSON string, or bytes and returns the typed `{webhookId, data}` wrapper; malformed deliveries raise `ReverseContactError`. `data.errorCode` must be a string or null; `.is_error` is true only for a non-empty string.

```python
from reversecontact import parse_webhook_delivery

delivery = parse_webhook_delivery(
    {"webhookId": "saved-task-id", "data": {"errorCode": "email-not-found"}}
)
print(delivery.webhook_id, delivery.is_error, delivery.data)
```

Use an HTTPS endpoint, respond with 2xx within 10 seconds, and process the delivery asynchronously. Deduplicate on `webhookId` because rare duplicates can occur. You can still poll a task that uses push delivery. See the [webhooks guide](https://docs.reversecontact.com/guides/webhooks).

## Best practices

- Keep keys on your server and use separate keys for integrations.
- Check counts and available filter fields before paid searches; set explicit result budgets.
- Prefer cached fetches before live fetches when freshness allows it.
- Handle lookup no-matches and distinguish quota limits from transient failures.
- Persist task ids before waiting, and include request ids when contacting support.
- Observe quotas returned by the API; do not assume a fixed credit charge for every workspace.

Read the [error and retry guide](https://docs.reversecontact.com/guides/errors-retries), [polling guide](https://docs.reversecontact.com/guides/polling), [credits guide](https://docs.reversecontact.com/guides/credits), and [production checklist](https://docs.reversecontact.com/guides/production-checklist).

## Requirements

- Python 3.11 or later. CI covers Python 3.11 through 3.15.
- Runtime dependencies: [httpx](https://www.python-httpx.org) (0.27 or later, below 1) and [Pydantic](https://docs.pydantic.dev) v2 (2.7 or later).
- The package is fully typed and ships `py.typed`.

A Python version stays supported until its end of life; the next minor release of the SDK then raises the minimum version.

## Versioning

The SDK follows [semantic versioning](https://semver.org). While the version is 0.x, a minor release (for example 0.1 to 0.2) can change the API; patch releases do not. Pin a compatible range such as `reversecontact~=0.1.0` (which allows 0.1.x only), and read the [changelog](https://github.com/visum-run/reversecontact-python/blob/main/CHANGELOG.md) before moving to a new minor version.

## Support

- SDK bugs and feature requests: [open an issue](https://github.com/visum-run/reversecontact-python/issues).
- Questions about your account, credits, or API results: email [support@reversecontact.com](mailto:support@reversecontact.com).

Include the request id (`response.metadata.request_id`, or `error.request_id`) and any task id.

## Contributing

Issues and pull requests are welcome. This repository is published from Reverse Contact's main codebase with each release: an accepted pull request is applied there first, then reaches this repository and PyPI with the next release.

### Development

Dependencies are managed with uv. Run the following checks from the repository root:

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

The models and resource facades are generated from the API's OpenAPI description and committed, so they work without external files. The generator (`uv run --frozen python scripts/generate.py`, with `--check` to verify freshness) and the tests that compare the SDK with that description need input files this repository does not include: they skip with an explicit reason, and unit tests, lint, type checks, builds and wheel smoke checks run here. Handwritten quota models follow runtime nullability rather than the stricter OpenAPI declarations.

Tests mock every request. The optional `RC_SDK_LIVE=1 uv run --frozen pytest -q tests/test_live.py` smoke calls only free endpoints and uses `RC_BASE_URL` and `RC_API_KEY`; run it against an authorized environment. [Examples](https://github.com/visum-run/reversecontact-python/tree/main/examples) can consume credits and are never run by the test suite.

## License

Apache-2.0. See [LICENSE](https://github.com/visum-run/reversecontact-python/blob/main/LICENSE).
