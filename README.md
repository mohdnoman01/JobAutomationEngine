# JobAutomation

JobAutomation is a deterministic job research and discovery pipeline.

## Current architecture

- Load companies from CSV
- Research company websites
- Detect ATS type
- Parse careers pages into `Job` objects

## Current capabilities

- Company loading from CSV
- Company website research
- ATS detection for Greenhouse, Lever, Ashby, and unknown
- Greenhouse job parsing from careers HTML
- Job discovery orchestration through the Greenhouse parser

## Testing

Run:

```bash
python -m pytest
```

## Playwright browser driver

Install the Playwright Python dependency with `python -m pip install -r requirements.txt`.
Install its Chromium browser binary separately with `python -m playwright install chromium`.
The application does not download browser binaries automatically.

## Application submission safety boundary

Submission completion requires persisted browser-action observations and
adapter-verified evidence bound to the application, job URL, payload, and
attempt. SQLite stores one unique completion row with the evidence identity,
attempt outcome, and application completion status in one transaction. Its
unique constraints and BEGIN IMMEDIATE prevent a second process from consuming
the same evidence for another completion. Recovery projects only an existing
matching SQLite completion into the JSON files; stale submitting state or
legacy evidence alone is never treated as success.

SQLite is the authority only for the successful completion boundary.
Preparation, browser-action authorization, browser observations, verification
records, failed attempts, human-action states, and unknown outcomes remain in
application_attempts.json. Other application fields and lifecycle updates
remain in applications.json; after a SQLite commit, these JSON files are
updated as projections. The tracker overlays committed completion state when
reading JSON, so an interrupted projection does not permit another submission.
Legacy JSON is left untouched and is not imported as trusted SQLite evidence.

The store uses SQLite's rollback journal, synchronous=FULL, an immediate
write transaction, and a configurable busy timeout. This targets a local
filesystem with working SQLite file locks. It does not make the remaining JSON
state transactional with SQLite, and it does not guarantee correctness on
network/synchronized filesystems or against storage/controller failures.

SQLite schema version 2 also defines separate lifecycle history, migration
run/unit, quarantine, and transition-event tables. The current runtime does
not read or write those tables, and no legacy JSON migration executor exists.
Schema initialization preserves and upgrades validated version-1 completion
records; this is schema support, not a production data migration.

The browser/evidence interfaces run in the same Python process as the engine.
Private methods, underscore-prefixed methods, and in-process tokens do not
isolate trusted operations from arbitrary code in that process. Stronger
isolation requires moving the trusted browser/evidence recorder to a separate
process or service. No live ELEKS confirmation mechanism has been validated;
local fixture verification does not establish production ELEKS confirmation
behavior.

## Roadmap

- Lever parser
- Ashby parser
- Unknown/manual fallback
- Contact discovery and application tracking later
