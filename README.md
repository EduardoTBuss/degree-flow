# Degree Flow

An interactive graduation planner for UFPel Computer Engineering, with a
visual curriculum flow, prerequisite validation, and semester planning.

> This is a local, single-user demo. It starts with **My plan** and every
> course marked as incomplete.

## Requirements

- Python 3.11+
- Node.js 20.19+ or 22.12+

## Run locally

### Windows (PowerShell)

```powershell
git clone https://github.com/EduardoTBuss/degree-flow.git
cd degree-flow
./run.ps1
```

### Linux / macOS

```bash
git clone https://github.com/EduardoTBuss/degree-flow.git
cd degree-flow
make run
```

Open [http://localhost:8000](http://localhost:8000). The same server delivers
the interface and the API.

To rebuild the frontend on Windows:

```powershell
./run.ps1 -Rebuild
```

## Development

Run these commands in separate terminals:

```bash
make dev-backend   # API on :8000
make dev-frontend  # interface on :5173
```

## Tests

```bash
make test
```

On Windows, after running `run.ps1` at least once:

```powershell
backend/.venv/Scripts/python -m pip install -r backend/requirements-dev.txt
backend/.venv/Scripts/python -m pytest -c backend/pytest.ini backend/tests
```

SQLite is created at `data/app.db` and is not tracked. The repository contains
no accounts, academic records, local databases, or student PDFs.

For technical details, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## License

[MIT](LICENSE) © 2026 Eduardo Timm Buss.
