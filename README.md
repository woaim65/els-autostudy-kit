# ELS Autostudy Kit

A small, practical automation kit for China Life ELS-style learner portals.

It reuses an already logged-in browser session, discovers courses that still have study time left, submits progress through the learner API, and verifies the resulting chapter state. No password automation, no CAPTCHA bypass, no long-running model babysitting.

> **Status:** field-tested on a real ELS learner portal session. The implementation is intentionally conservative: dry-run first, limited concurrency by default, and no automatic re-login.

## What it does

- Reuses a live browser session managed by `bb-browser`.
- Extracts token, root API URL, and device/risk token from the logged-in page state.
- Discovers candidate courses from:
  - learning history,
  - learning center course pages,
  - training-class course pages,
  - homepage/category-column course sections, including nested columns.
- Filters out courses with no remaining study time.
- Skips already finished chapters.
- Avoids treating exam-blocked courses as study-submit failures.
- Submits progress via the request layer instead of playing videos in tabs.
- Supports a fixed worker pool, defaulting to 3 workers.
- Saves a local checkpoint so completed chapter submissions are not repeated.
- Includes a Hermes Agent skill document with the reverse-engineering notes and operating procedure.

## Repository layout

```text
.
├── scripts/
│   └── els_study_submit.py      # Main CLI script
├── skill/
│   └── china-life-els-request-layer-study-submit.md
├── docs/
│   └── api-notes.md             # Short API summary
├── README.md
├── LICENSE
└── .gitignore
```

## Requirements

- Linux or a similar shell environment.
- Python 3.10+.
- `bb-browser` available in `PATH`.
- A logged-in ELS learner portal page in the browser controlled by `bb-browser`.

The script does **not** log in for you. Log in manually first, then let the script reuse that browser session.

## Quick start

```bash
# 1. Log in manually in the bb-browser-managed browser.
# 2. Keep the portal open on home, learning center, course detail, or a column page.

# Preview the plan without submitting anything
python3 scripts/els_study_submit.py --auto --dry-run

# Run with the default 3 workers
python3 scripts/els_study_submit.py --auto --workers 3
```

For a smaller first pass:

```bash
python3 scripts/els_study_submit.py --auto --dry-run --max-pages 1
python3 scripts/els_study_submit.py --auto --workers 3 --max-pages 1
```

For one known course:

```bash
python3 scripts/els_study_submit.py --course-id <offeringCourseId> --dry-run
python3 scripts/els_study_submit.py --course-id <offeringCourseId> --max-items 3
```

## How it works

The learner UI ultimately persists progress through:

```http
POST /els/learner-study/api/course/play/save?__token=<token>
```

A matching refresh endpoint updates aggregate status:

```http
POST /els/learner-study/api/course/play/<offeringCourseId>/refresh?__token=<token>
```

The script reads course metadata and chapter state from:

```http
GET /els/learner-study/api/course/<offeringCourseId>/info
GET /els/learner-study/api/course/<offeringCourseId>/menuListNew
```

Then it submits `rawStatus: I` followed by `rawStatus: C` for chapters that are not finished and have remaining study time.

See [`docs/api-notes.md`](docs/api-notes.md) and the bundled skill document for more details.

## Local state

The script writes:

```text
~/.els_study_submit_state.json   # checkpoint of completed chapter submissions
~/.els_study_submit.log          # plain text run log
```

Delete the checkpoint if you intentionally want to re-evaluate everything from scratch:

```bash
rm ~/.els_study_submit_state.json
```

## Safety model

This project deliberately avoids:

- password automation,
- automatic re-login,
- CAPTCHA bypass,
- destructive operations,
- browser tab farms,
- model-in-the-loop monitoring.

If the session expires, log in manually again and rerun the script.

## Notes and caveats

- Some course-list endpoints use misleading field names. In the learning-center endpoint, `id` is the usable `offeringCourseId`; `offeringId` is usually the classroom/offer id and may fail if used as the course-detail id.
- Homepage/category columns can contain nested `COLUMN` objects. The script recursively scans nested columns and only keeps actual course-like objects.
- A course can show full study progress while still remaining incomplete because of an exam requirement. That is not a study-submit failure.
- Always run `--dry-run` before a large batch. The portal backend is not exactly a race car.

## License

MIT
