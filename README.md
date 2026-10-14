# Alerta de atividades atrasadas (Artia → Microsoft Teams)

Rotina que consulta o projeto **5272141** (grupo de trabalho 4430980) no Artia, identifica as
atividades atrasadas — inclusive dentro de pastas e subpastas — e envia um resumo em Adaptive Card
(v1.4) para um canal do Teams via fluxo do Power Automate (Workflows).

- Código: [`scripts/alerta_atrasadas.py`](scripts/alerta_atrasadas.py) (Python 3.9+, só biblioteca padrão)
- Snapshot entre execuções: [`state/atrasadas_5272141.json`](state/atrasadas_5272141.json)
- Agendamento: [`.github/workflows/alerta-atrasadas.yml`](.github/workflows/alerta-atrasadas.yml)
  — dias úteis às 07:50 (America/Sao_Paulo), mais disparo manual
- Uso pelo Claude Code: skill [`alerta-atrasadas`](.claude/skills/alerta-atrasadas/SKILL.md). Num chat
  novo, basta pedir "Rode o alerta de atividades atrasadas do projeto Implantação CRM e envie para o
  Teams" (ou `/alerta-atrasadas`). Sem credenciais de API, a skill lê o Artia pelo conector e passa os
  dados ao script com `--dados`

## Regras

| Item | Regra |
| --- | --- |
| Data de referência | hoje em America/Sao_Paulo (dd/mm/aaaa) |
| Atrasada | `status = false` (não encerrada) e `estimatedEnd` anterior à data de referência |
| Dias de atraso | dias corridos entre `estimatedEnd` e a data de referência |
| Etapa | `parent.name` (pasta imediatamente acima); "—" se a atividade está na raiz do projeto |
| Responsável | `responsible.name`; sem responsável → "Sem responsável" |
| Novas | atrasadas hoje que não estavam no snapshot da execução anterior; sem snapshot, as com término no dia anterior |

O snapshot só é gravado depois que o Teams aceita a mensagem. Uma reexecução no mesmo dia compara
com a execução anterior àquele dia (campo `execucao_anterior`), então as "novas" não somem.

## API do Artia usada

Endpoint `POST https://api.artia.com/graphql` (documentação: developers.artia.com):

1. `authenticationByClient(clientId, secret) { token }` → `Authorization: Bearer <token>`
2. `showProject(id, accountId) { name }` → nome do projeto (não fica fixo no código)
3. `listingFoldersV2(accountId, page, filter: { parentIds })` → filhos diretos, percorridos nível a nível
4. `listingActivitiesV2(accountId, folderId, page)` → atividades de cada pasta (e da raiz do projeto)

As duas listagens são paginadas (25 por página) e a rotina percorre até `totalPages`.
Subprojetos criados dentro do projeto não entram: `listingFoldersV2` devolve só pastas.

## Configuração

Secrets do repositório (Settings → Secrets and variables → Actions):

| Nome | Tipo | Descrição |
| --- | --- | --- |
| `ARTIA_CLIENT_ID` | secret | Client ID da API (configuração da organização no Artia) |
| `ARTIA_CLIENT_SECRET` | secret | Secret da API |
| `TEAMS_WEBHOOK_URL` | secret | URL do fluxo "quando uma solicitação de webhook é recebida" |
| `ARTIA_ORGANIZATION_ID` | variable (opcional) | cabeçalho `OrganizationId` (organização DAX System = 124953) |
| `ARTIA_USER_ID` | variable (opcional) | cabeçalho `UserId`, se a API exigir com token de cliente |

O agendamento do GitHub Actions só roda a partir da branch padrão do repositório.

## Execução local

```bash
export ARTIA_CLIENT_ID=... ARTIA_CLIENT_SECRET=... TEAMS_WEBHOOK_URL=...
python3 scripts/alerta_atrasadas.py --dry-run      # imprime o payload, não envia nem grava snapshot
python3 scripts/alerta_atrasadas.py                # envia e atualiza o snapshot

# Sem credenciais, a partir de um JSON com projeto e atividades (ex.: o fixture com dados reais):
python3 scripts/alerta_atrasadas.py --dados tests/fixtures/projeto_5272141.json --dry-run

python3 -m unittest discover -s tests
```
