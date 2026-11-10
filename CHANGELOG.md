# Changelog

## 0.4.2 (2026-11-10)

- `search.people_auto_suggestion` completes eight more People filters from what the profiles hold, most common first: `field` takes `languages`, `positionTitle`, `currentPositionTitle`, `companyName`, `currentCompanyName`, `schoolName`, `city` and `region` besides `skills`, `industry`, `countryCode` and `area`. Each name is the filter the value goes back into (`city` and `region` are `location.city` and `location.region`). These values come in lowercase, as the profiles are indexed (`software engineer`, `sao paulo`), and their `label` is the value itself.
- A `query` now has a minimum length per field: 3 characters for `positionTitle`, `currentPositionTitle`, `companyName` and `currentCompanyName`, 2 for `skills`, `languages`, `schoolName`, `city` and `region`, 1 for `industry`, `countryCode` and `area`. A shorter query is refused with a 422 (`UnprocessableEntityError`, code `VALIDATION_ERROR`) that names the minimum. A one-letter `skills` query was accepted before.

## 0.4.1 (2026-11-09)

- `bulk.enrich_persons` takes `families=["check"]`: the free person check of each item that has a profile URL (`linkedinUrl`), at no cost. It tells whether the profile is in the database, when it was last updated and how much of it there is. It runs alone, not with another family nor with `live`. Each result line of such a task carries `check` (`TaskResultLineCheck` in `reversecontact.types`: `exists`, `last_update`, `experience_count`, `skill_count`, `school_count`), and its `status` reads `found` or `not_found`.
- `search.people_auto_suggestion` completes industries, country codes and areas too: `field` takes `industry`, `countryCode` and `area` besides `skills`. A country answers to its ISO code or its name (`FR` and `fran` both give France first), an area to its id, acronym or name (`DACH`). Each suggestion now carries `label`, the text to show, next to `value`, the text to send in the filter (`value="FR"`, `label="France"`; a skill's label is the skill itself).
- `lists.export` accepts `format="ndjson"` and returns one JSON object per line (`ListExportLine` in `reversecontact.types`): the row `lists.members.list` returns, then `person` (a persons List, in the `/v2/enrich/persons` shape), `company` (a companies List, or the Company details of a person member, in the `/v2/enrich/companies` shape), `isFullProfile` and `customColumns`. The CSV stays the default, and `fields="..."` chooses its columns.

## 0.4.0 (2026-11-05)

- Breaking: the background jobs of the Bulk API are now called tasks, with no compatibility for the old routes. `client.bulk` keeps every method name (`enrich_persons`, `list`, `list_all`, `retrieve`, `wait`, `results`, `rows`, `iter_rows`, `cancel`); what changes:
  - `bulk.enrich_persons` posts to `/v2/bulk` (was `/v2/enrich/persons/bulk`) and the other methods call `/v2/tasks`, `/v2/tasks/{taskId}`, `/v2/tasks/{taskId}/results`, `/v2/tasks/{taskId}/rows` and `/v2/tasks/{taskId}/cancel` (were under `/v2/jobs`). `bulk.list_all` keeps its page bounds (50 by default, `per_page` up to 100), now those of `GET /v2/tasks`.
  - `bulk.enrich_persons` takes `task_name`, sent as `taskName` (was `name`), and its answer holds the task in `data.task` (was `data.job`).
  - The task object carries `task_name` (`taskName`, was `name`); its `links.self` and `links.results` point to `/v2/tasks/<id>`.
  - The first argument of `bulk.retrieve`, `bulk.wait`, `bulk.results`, `bulk.rows`, `bulk.iter_rows` and `bulk.cancel` is named `task_id` (was `job_id`).
  - Renamed types in `reversecontact.types`: `JobResultLine` is `TaskResultLine` (with `TaskResultLineContactEmail` and `TaskResultLineContactPhone`), `GetJobData` is `GetTaskData`, `GetJobRowsData` is `GetTaskRowsData`, `ListJobsData` and `ListJobsItem` are `ListTasksData` and `ListTasksItem`, `CancelJobData` is `CancelTaskData`, `BulkEnrichPersonsDataJob` is `BulkEnrichPersonsDataTask`, and so for their `Credits`, `Error`, `Links` and `Progress` parts.
  - Error codes say TASK: `TASK_NOT_FOUND`, `TASK_INVALID_INPUT`, `TASK_NOT_CANCELLABLE`, `TASK_RESULTS_NOT_READY`, `TASK_RESULTS_FORMAT_UNAVAILABLE`, `TASK_ROWS_BUSY`, `TASK_STARTED_BY_A_PERSON`, `TASK_MAX_CREDITS`, and `BULK_TASK_*` for the former `BULK_JOB_*` codes (for example `BULK_TASK_INSUFFICIENT_CREDITS`).
  - The webhook sent when a task created with `webhook_url` settles is the event `task.updated` (was `job.updated`), with `taskId` (was `jobId`); `pollUrl` and `resultsUrl` point to `/v2/tasks/<id>`.
- The async tasks of live fetches and contact lookups say task too; every former name stays importable as a deprecated alias of the same object or class:
  - `client.tasks` (sync and async clients) reads and waits for them by id (`retrieve`, `wait`, on `GET /v2/webhooks/{webhookId}`); `client.jobs` returns the same object, kept as a deprecated alias. The first argument of `tasks.wait` is named `task_id` (was `job_id`).
  - `fetch.person_live`, `fetch.company_live` and the `contact` methods return a `Task` or `AsyncTask` (were `Job` and `AsyncJob`, kept as aliases), whose snapshots are `TaskSnapshot` (`JobSnapshot` kept).
  - `TaskTimeoutError` and `TaskFailedError` carry `task_id`; `job_id` stays as a read-only alias, and `JobTimeoutError` and `JobFailedError` are the same classes, so `except` and `isinstance` work with either name. The messages say task, and the timeout names the wait to resume with: `tasks.wait`, `bulk.wait` or `lists.operations.wait` (it always said `jobs.wait`).
  - `reversecontact.types.TaskAckData` is the acknowledgement of a live fetch (was `JobAckData`, kept as an alias).
- `lists.import_signals` answers with the task record in `data.task` (was `data.job`).

## 0.3.1 (2026-11-04)

- `bulk.list_all` asks for 50 jobs per page by default and accepts `per_page` up to 100, the bounds of `GET /v2/jobs`: it asked for 200 and the API refused the first page.

## 0.3.0 (2026-11-04)

- `bulk.rows` reads the results of a Bulk enrich job a page at a time (`cursor`, `limit` up to 100), in the order sent, from the start of the run; each item is a `JobResultLine` and the page carries the job status in `extras["status"]`. `bulk.iter_rows` yields the results one at a time as they become ready: it waits `Retry-After` while the next item runs, and ends with the job or when it is paused; a cancelled job first finishes the items it had started, within the hour. Free.
- `bulk.results` accepts `format="ndjson"` and returns one `JobResultLine` JSON object per line; the CSV stays the default. In both, an item a cancelled job had started and not finished yet reads `running`.
- `reversecontact.types` exports `JobResultLine` and `GetJobRowsData`.

## 0.2.0 (2026-11-04)

- `lists` manages your saved lists: `list`, `list_all`, `create`, `retrieve`, `delete`, `export` (CSV) and `import_signals`.
- `lists.members` reads, adds and removes the members of a list: `list`, `list_all`, `add` and `remove`.
- `lists.operations` follows an import: `retrieve`, `cancel` and `wait`.
- `bulk.enrich_persons` starts a Bulk enrich job of up to 25,000 people. `bulk.list`, `bulk.list_all`, `bulk.retrieve`, `bulk.wait`, `bulk.results` (CSV) and `bulk.cancel` list, follow, download and stop these jobs.

## 0.1.8 (2026-11-03)

- `search.people_auto_suggestion` returns the skills that start with what someone has typed, most common first, to build a search box in your own product. Free.
- `search.people`, `search.count_people` and their iterator accept `school_id` and `school_linkedin_id`.

## 0.1.7 (2026-11-03)

- `contact.verify_email` checks whether an email address can receive mail. It returns a job whose result holds `email` and `status`: `valid`, `risky`, `invalid` or `unknown`. The verdicts are a string enum, `EmailVerificationStatus`, and each submit carries an `Idempotency-Key` like the finders.

## 0.1.6 (2026-10-30)

- `search.people`, `search.organizations`, `search.count_people` and `search.count_organizations` accept `location.area`, one zone id or a list (`"MR:EMEA"`, `["MC:DACH", "IG:EU"]`): main regions, UN regions and subregions, market clusters and institutional groups.
- `search.people`, `search.count_people` and `search.iter_people` accept `connections_count`, a connection count range where one bound is enough (`{"min": 501}`).
- The closed lists of the API are string enums, importable from `reversecontact`: `Industry`, `CountryCode`, `JobFunction`, `JobSubFunction`, `Seniority`, `ContractType`, `Area` and `EmailType` (`Industry.SOFTWARE_DEVELOPMENT`, `Area.MC_DACH`). The parameters still accept any plain string.

## 0.1.5 (2026-10-29)

- `contact.find_email` and `contact.find_phone` accept `id`, a person id (`prs_…`) from a search, fetch or enrich result, in place of `url` or a name with company domain.

## 0.1.4 (2026-10-29)

- Counts, page sizes, employee and follower counts, founding years, tenure filters, rate limit windows and `execution_time_ms` are typed `int`, as the API now documents them as integers. Credit amounts stay `int | float`.

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
- Add synchronous and asynchronous clients for all 32 public V2 operations.
- Add typed models, errors, bounded retries, job polling, webhook parsing, and cursor pagination.
- Require explicit search result budgets and reuse List replay identifiers across retries.
- Include examples, offline contract tests, package smoke checks, and an opt-in free-endpoint smoke.
- Preserve HTTP status on interrupted bodies and limit other POST retries to safe connection failures.
- Bound polling HTTP timeouts by the overall deadline and return complete job envelopes to callers and callbacks.
- Keep synchronous polling in the caller's thread; cancel asynchronous polls on deadline expiry.
- Snapshot pagination filters, validate budgets and page sizes, and expose typed page extras.
- Add shared-pool client copies, opt-in debug logging, and stricter webhook delivery validation.
- Reject retries when any server hint exceeds 60 seconds and raise HTTP errors for non-JSON lookup 404s.
