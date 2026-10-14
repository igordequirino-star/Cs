---
name: alerta-atrasadas
description: Roda o alerta de atividades atrasadas do projeto "Implantação CRM" (Artia, projeto 5272141) e envia o resumo em Adaptive Card para o canal do Microsoft Teams. Use sempre que o usuário pedir para rodar, executar, gerar ou enviar o alerta, relatório ou resumo de atividades atrasadas desse projeto, ou falar em "atrasadas no Teams", mesmo sem citar a skill.
---

# Alerta de atividades atrasadas (Artia → Teams)

Toda a lógica já está pronta em `scripts/alerta_atrasadas.py`: regras de atraso, etapa, responsável,
"novas" contra o snapshot, montagem do Adaptive Card 1.4, envio ao webhook e gravação do snapshot.
**Não reescreva nem recalcule nada à mão.** Seu papel é só levar os dados do Artia até o script,
rodá-lo e relatar o resultado. Fale com o usuário em português, de forma simples, sem expor IDs,
nomes de ferramentas ou detalhes técnicos, a menos que ele pergunte.

Parâmetros fixos:
- Organização: DAX System (`organization_id` 124953)
- Grupo de trabalho (account): 4430980
- Projeto: 5272141 (o nome vem da API, não fixe)
- Snapshot: `state/atrasadas_5272141.json`
- Webhook: variável de ambiente `TEAMS_WEBHOOK_URL` (já configurada no ambiente)

## Caminho A: credenciais de API disponíveis

Se `ARTIA_CLIENT_ID` e `ARTIA_CLIENT_SECRET` estiverem definidas no ambiente, rode direto:

```bash
python3 scripts/alerta_atrasadas.py
```

Depois siga para "Finalizar".

## Caminho B: via conector Artia (padrão hoje)

1. Chame `artia_select_organization` com `organization_id` 124953 (não pergunte ao usuário qual
   organização: é sempre a DAX System). Use o id devolvido em todas as chamadas seguintes.
2. `artia_show_project` com id `5272141` → guarde o `name`.
3. Descubra todas as pastas e subpastas, nível a nível:
   - `artia_list_folders` com `account_id` 4430980 e `parent_ids` [5272141];
   - repita com `parent_ids` = ids encontrados no nível anterior, até vir lista vazia;
   - em cada chamada, se `has_more` for true, peça as páginas seguintes.
4. Para o projeto (5272141) **e** para cada pasta encontrada, chame `artia_list_activities` com
   `folder_id` e paginação completa (`has_more`). A listagem não desce para subpastas, por isso é
   preciso chamar pasta por pasta.
5. Grave um JSON no diretório de rascunho (scratchpad), por exemplo `<scratchpad>/dados.json`,
   copiando os valores exatamente como vieram, sem filtrar nada (o script decide o que está atrasado):

   ```json
   {
     "projeto": {"id": "5272141", "name": "<name do passo 2>"},
     "atividades": [
       {
         "id": "...", "title": "...", "status": false,
         "estimatedEnd": "AAAA-MM-DD", "completedPercent": 0,
         "responsible": {"id": "...", "name": "..."},
         "parent": {"id": "...", "name": "..."}
       }
     ]
   }
   ```

   Use `null` em `responsible` quando a atividade não tiver responsável. Inclua todas as
   atividades, encerradas ou não.
6. Rode:

   ```bash
   python3 scripts/alerta_atrasadas.py --dados <scratchpad>/dados.json
   ```

   O script envia o card ao Teams e só então atualiza o snapshot. Se o usuário pedir só uma
   prévia, acrescente `--dry-run` (não envia nem grava o snapshot).

## Finalizar

1. Se o script falhar, mostre o erro ao usuário e pare. Não grave nem altere o snapshot à mão.
2. Faça commit de `state/atrasadas_5272141.json` e dê push na branch de trabalho da sessão, para
   que a próxima execução compare com esta (o ambiente é descartado ao fim da sessão).
3. Responda em poucas linhas: nome do projeto, data de referência, total de atrasadas, quantas
   são novas, total por responsável e a confirmação de que a mensagem foi enviada ao Teams.
