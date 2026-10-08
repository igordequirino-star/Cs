# Receptor de webhook do Artia

Endpoint HTTP que recebe os webhooks do Artia e identifica a **criação de atividade**.
Sem dependências: só Node 18+.

Formato seguido: portal Developers do Artia, seção *Webhooks > Eventos e Payloads*.
O Artia envia um `POST` em JSON:

```json
{
  "organization_id": 123,
  "activity": { "id": 1, "title": "...", "folder_id": 2, "...": "..." },
  "changed_attributes": null,
  "triggered_by": { "id": 3, "name": "...", "email": "..." }
}
```

Os cabeçalhos são `X-Artia-Event`, `X-Artia-Hook-Id`, `X-Artia-Triggered-At` e, quando o
webhook tem secret, `X-Artia-Hmac-Sha256`: o HMAC SHA-256 do corpo, em hexadecimal.

## Rodar

```bash
cd webhook
ARTIA_WEBHOOK_SECRET=seu-secret PORT=3000 npm start
npm test
```

| Variável               | Padrão            | Uso                                                        |
| ---------------------- | ----------------- | ---------------------------------------------------------- |
| `PORT`                 | `3000`            | Porta HTTP                                                 |
| `WEBHOOK_PATH`         | `/webhooks/artia` | Caminho do endpoint                                        |
| `ARTIA_WEBHOOK_SECRET` | vazio             | Secret do webhook; se definido, a assinatura é verificada  |
| `DATA_DIR`             | `webhook/data`    | Onde os eventos são gravados (`events.jsonl`)              |

`GET /health` responde `{ "ok": true }`.

## O que acontece a cada disparo

1. Se houver secret, a assinatura é conferida. Sem assinatura ou com assinatura errada, a resposta é `401`.
2. O evento inteiro (cabeçalhos, resumo e payload bruto) é gravado em `data/events.jsonl`.
3. Se for criação de atividade, `onActivityCreated(event)` é chamado. Hoje ele só registra
   no log. É nesse ponto que entra a ação desejada, por exemplo avisar no Teams.
4. A resposta é `200` para qualquer evento válido, para o Artia não reenviar.

O nome exato do evento de criação não aparece na documentação. A criação é reconhecida
quando `X-Artia-Event` contém "create"/"criar". Sem esse cabeçalho, ela é reconhecida pelo
payload: tem `activity`, `changed_attributes` nulo e `deleted_at` vazio. Confira o valor
real do cabeçalho no `events.jsonl` depois do primeiro disparo.

## Testar com o Artia

O Artia precisa alcançar o endpoint por uma URL **HTTPS pública**. Algumas opções:

- **Na sua máquina, com túnel:** rode `npm start` e, em outro terminal,
  `cloudflared tunnel --url http://localhost:3000` (ou `ngrok http 3000`). Use a URL gerada
  + `/webhooks/artia`.
- **Hospedado:** Render, Railway, Fly.io ou um servidor próprio, com comando de start
  `node webhook/server.js`.

No Artia, crie o webhook com a URL acima, assine o evento de criação de atividades e
defina um secret, o mesmo valor de `ARTIA_WEBHOOK_SECRET`. Depois crie uma atividade e
veja o log e o `data/events.jsonl`.
