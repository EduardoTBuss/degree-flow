# Degree Flow

Planejador local de grade e formatura para Engenharia de Computação da UFPel,
com fluxograma interativo, validação de pré-requisitos e organização por semestre.

> Esta é uma demo local e monousuário. Ela inicia com o plano **Meu plano** e
> todas as disciplinas marcadas como pendentes de conclusão.

## Requisitos

- Python 3.11+
- Node.js 20.19+ ou 22.12+

## Rodar localmente

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

Acesse [http://localhost:8000](http://localhost:8000). O mesmo servidor entrega
a interface e a API.

Para reconstruir o frontend no Windows:

```powershell
./run.ps1 -Rebuild
```

## Desenvolvimento

Em dois terminais:

```bash
make dev-backend   # API em :8000
make dev-frontend  # interface em :5173
```

## Testes

```bash
make test
```

No Windows, depois de executar `run.ps1` ao menos uma vez:

```powershell
backend/.venv/Scripts/python -m pip install -r backend/requirements-dev.txt
backend/.venv/Scripts/python -m pytest -c backend/pytest.ini backend/tests
```

O SQLite é criado em `data/app.db` e não é versionado. O repositório não inclui
contas, históricos acadêmicos, bancos locais ou PDFs de estudantes.

Detalhes técnicos: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
