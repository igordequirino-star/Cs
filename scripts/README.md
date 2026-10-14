# Rotina de atividades atrasadas → Teams

`atrasadas_teams.py` recebe as atividades de um projeto do Artia (incluindo as
de todas as pastas e subpastas), identifica as atrasadas, compara com a última
execução e envia um resumo (Adaptive Card) para o canal do Teams via webhook do
Power Automate.

**Atrasada** = atividade não encerrada, com menos de 100% concluído e término
previsto anterior a hoje.

O resumo traz:

- total de atividades atrasadas;
- quantas são novas desde a última execução (marcadas com 🆕) e quantas saíram da lista;
- contagem e detalhamento por responsável (pasta, término previsto, dias de atraso, % concluído, situação).

## Histórico

`reports/atrasadas-projeto-<id>.json` guarda os IDs atrasados da última execução.
Ele só é regravado depois que o Teams aceita a mensagem, então uma falha de envio
não "consome" as novidades. Falhas vão para `reports/erros.log`.

## Execução manual

```bash
# 1. Salve as atividades coletadas do Artia em um JSON (lista de atividades)
# 2. Pré-visualize sem enviar:
python3 scripts/atrasadas_teams.py --atividades atividades.json \
  --projeto-id 5272141 --projeto-nome "Implantação CRM" \
  --projeto-url https://app2.artia.com/a/4430980/p/5272141 --dry-run
# 3. Envie (o webhook não fica no repositório):
TEAMS_WEBHOOK_URL='https://...' python3 scripts/atrasadas_teams.py --atividades atividades.json \
  --projeto-id 5272141 --projeto-nome "Implantação CRM" \
  --projeto-url https://app2.artia.com/a/4430980/p/5272141
```

## Rotina agendada

A rotina do Claude Code ("Atrasadas Implantação CRM → Teams") roda em dias úteis
pela manhã (horário de Brasília). A cada execução ela coleta as atividades pelo
conector do Artia (organização DAX System, grupo de trabalho 4430980, projeto
5272141, percorrendo pastas e subpastas recursivamente), executa este script e
faz commit do histórico atualizado no branch `claude/nice-knuth-hlgy4t`.
