# Degree Flow Architecture

## Current state

Degree Flow is a local, single-user demo. It combines a React frontend, a
FastAPI service, an I/O-free planning engine, and SQLite persistence. The
backend also serves the frontend build, so the complete application runs from
one local address.

```text
React + Vite
     |  /api/v1
     v
FastAPI
     |-- engine: pure rules and algorithms
     |-- importers/scrapers: external adapters
     `-- persistence: SQLAlchemy + SQLite
```

## Components

### Frontend

The frontend lives in `frontend/` and uses React, TypeScript, Vite, React Flow,
and TanStack Query. Its main areas cover:

- interactive curriculum flow and semester planning;
- course offerings, schedules, and recommendations;
- enrollment-stage tracking.

The interface communicates only with the API. Prerequisite, timetable, course
load, and feasibility validation remain in the backend.

### API and domain

The backend lives in `backend/app/`:

- `api/`: HTTP routes and serialization;
- `engine/`: validation, automatic planning, critical path, schedules,
  recommendations, and enrollment campaigns;
- `persistence/`: models, repositories, and SQLite migrations;
- `seed_import/`: idempotent import of the public curriculum;
- `importers/historico/`: local academic-record parsing;
- `scrapers/ufpel/`: adapters for the institutional portal.

Scraper fixtures are trimmed offline snapshots of public UFPel pages. Student
lists and unrelated personal information were removed from those snapshots.

Modules under `engine/` receive data structures and do not access databases,
files, or the network. This keeps domain rules independently testable.

### Local data

`seed/curriculum.json` contains public curriculum data only. On the first boot,
the backend creates:

- a database at `data/app.db`;
- one plan named `My plan`;
- local states with every course marked `falta` (incomplete).

The `data/` directory is ignored by Git. Reimporting the seed refreshes the
catalog without replacing planning data already stored in the local database.

### PDF import

When a user imports an academic record, the PDF is processed in memory to
produce a reviewable change proposal. Student PDFs are never tracked. The test
suite generates fictional documents in memory to exercise this flow.

## Implemented features

- interactive curriculum graph with drag and drop;
- course status, completion term, and manual locks;
- prerequisite, offering, credit-load, and timetable validation;
- automatic planning and critical-path analysis;
- curriculum-version transitions;
- reviewable academic-record import;
- course offerings, recommendations, and class selection;
- credit-hour and elective requirements;
- support for regular, correction, and special enrollment stages;
- light, dark, and system themes.

## Demo limitations

- no authentication or user isolation;
- one local database per installation;
- the initial curriculum targets UFPel Computer Engineering;
- institutional portal data may change, so manual import remains a fallback;
- recommendation weights still require feedback from real usage.

The current version should therefore run locally. A multi-user deployment must
add authentication, data isolation, production migrations, and a privacy policy
before accepting real academic data.

## Next steps

1. Collect feedback from the demo.
2. Improve onboarding and error messages.
3. Expand interface testing.
4. Define the authentication and hosting model.
5. Support additional degrees without coupling the planning engine to UFPel.
