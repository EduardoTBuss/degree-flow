# Arquitetura do Degree Flow

## Estado atual

O Degree Flow é uma demo local e monousuário. A aplicação reúne um frontend
React, uma API FastAPI, um motor de planejamento sem I/O e persistência SQLite.
O backend também serve o build do frontend, permitindo executar tudo em um
único endereço local.

```text
React + Vite
     │  /api/v1
     ▼
FastAPI
     ├── engine: regras e algoritmos puros
     ├── importers/scrapers: adaptadores externos
     └── persistence: SQLAlchemy + SQLite
```

## Componentes

### Frontend

O frontend fica em `frontend/` e usa React, TypeScript, Vite, React Flow e
TanStack Query. Ele apresenta três áreas principais:

- fluxograma da grade e planejamento por semestre;
- ofertas, horários e recomendações;
- acompanhamento das etapas de matrícula.

A interface consome somente a API. Validação de pré-requisitos, conflitos e
viabilidade permanece no backend.

### API e domínio

O backend fica em `backend/app/`:

- `api/`: rotas HTTP e serialização;
- `engine/`: validação, planejamento automático, caminho crítico, horários,
  recomendações e campanha de matrícula;
- `persistence/`: modelos, repositórios e migrações SQLite;
- `seed_import/`: importação idempotente da grade pública;
- `importers/historico/`: leitura local de histórico acadêmico;
- `scrapers/ufpel/`: adaptação do portal institucional.

As fixtures do scraper são recortes offline de páginas públicas da UFPel. As
listas de estudantes e dados pessoais que não participam do parsing foram
removidos desses recortes.

Os módulos de `engine/` recebem estruturas de dados e não acessam banco,
arquivos ou rede. Isso permite testar as regras separadamente da API.

### Dados locais

`seed/curriculum.json` contém somente dados curriculares públicos. No primeiro
boot, o backend cria:

- um banco em `data/app.db`;
- um plano chamado `Meu plano`;
- estados locais com todas as disciplinas em `falta`.

O diretório `data/` é ignorado pelo Git. Reimportar o seed atualiza o catálogo
sem substituir o planejamento já salvo no banco local.

### Importação de PDF

Quando o usuário escolhe importar um histórico, o arquivo é processado em
memória para montar uma proposta de alterações. A aplicação não acompanha PDFs
de estudantes; a suíte gera documentos fictícios em memória para testar esse
fluxo.

## Funcionalidades implementadas

- fluxograma interativo com drag and drop;
- status, semestre de conclusão e travas por disciplina;
- validação de pré-requisitos, oferta, carga e conflitos de horário;
- planejamento automático e caminho crítico;
- troca entre versões de grade e regras de transição;
- importação revisável de histórico;
- ofertas, recomendações e seleção de turmas;
- requisitos de horas e optativas;
- apoio às etapas de rematrícula, correção e matrícula especial;
- tema claro, escuro e automático.

## Limitações da demo

- não há autenticação nem isolamento entre usuários;
- cada instalação usa um único banco local;
- a grade inicial é específica de Engenharia de Computação da UFPel;
- dados do portal podem mudar e o import manual continua sendo o fallback;
- pesos de recomendação ainda precisam de feedback de uso real.

Por essas limitações, a versão atual deve ser executada localmente. Um deploy
multiusuário exige autenticação, isolamento de dados, migrações de produção e
política de privacidade antes de receber dados acadêmicos reais.

## Próximos passos

1. coletar feedback da demo;
2. melhorar onboarding e mensagens de erro;
3. ampliar testes de interface;
4. decidir o modelo de autenticação e hospedagem;
5. evoluir para múltiplos cursos sem acoplar o motor à UFPel.
