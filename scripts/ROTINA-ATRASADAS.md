# Rotina: atividades atrasadas — Implantação CRM → Teams

Procedimento seguido a cada execução da rotina agendada.

- Projeto: **Implantação CRM** — https://app2.artia.com/a/4430980/p/5272141
- Organização Artia: DAX System (id 124953) · grupo de trabalho 4430980 · projeto 5272141
- Branch com o histórico: `claude/compassionate-bardeen-i7yar6`
- Webhook do Teams: variável de ambiente `TEAMS_WEBHOOK_URL` (não versionar a URL)

## Passos

1. `git fetch origin claude/compassionate-bardeen-i7yar6 && git checkout claude/compassionate-bardeen-i7yar6 && git pull origin claude/compassionate-bardeen-i7yar6`
2. No conector Artia: selecionar a organização 124953 e obter a data de hoje.
3. Mapear **todas** as pastas do projeto, recursivamente: listar pastas com
   `parent_ids=[5272141]`, depois com os ids encontrados, e repetir até uma
   chamada voltar vazia (paginar quando `has_more` for true).
4. Listar as atividades do projeto (pasta 5272141) e de cada pasta/subpasta
   encontrada, com `estimated_end.to` = ontem, paginando até o fim.
5. Gravar num arquivo temporário (fora do repositório):
   ```json
   {"projeto_id": 5272141, "projeto_nome": "Implantação CRM",
    "projeto_url": "https://app2.artia.com/a/4430980/p/5272141",
    "data": "<hoje YYYY-MM-DD>",
    "atividades": [<itens como vieram da listagem: id, title, status, customStatus,
                    completedPercent, parent, responsible, estimatedEnd>]}
   ```
6. `python3 scripts/atrasadas_teams.py <arquivo>` — calcula as atrasadas
   (em aberto e com término previsto antes de hoje), compara com
   `reports/atrasadas-projeto-5272141.json`, envia o card ao Teams e, se o
   envio der certo, atualiza o histórico. Se falhar, registra em
   `reports/erros.log` e salva o card em `reports/payloads/` (reenvio com
   `--resend <arquivo>`).
7. Commitar as mudanças em `reports/` e dar push para
   `claude/compassionate-bardeen-i7yar6`.
