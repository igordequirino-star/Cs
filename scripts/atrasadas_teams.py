#!/usr/bin/env python3
"""Resumo de atividades atrasadas de um projeto Artia para um canal do Teams.

Recebe um JSON com as atividades coletadas do projeto (de todas as pastas e
subpastas), identifica as atrasadas, compara com o histórico da última
execução e envia um Adaptive Card ao webhook do Teams (Power Automate) em
TEAMS_WEBHOOK_URL.

Entrada (arquivo JSON):
  {
    "projeto_id": 5272141,
    "projeto_nome": "Implantação CRM",
    "projeto_url": "https://app2.artia.com/a/4430980/p/5272141",
    "data": "2026-10-15",
    "atividades": [ <itens como devolvidos pela listagem de atividades do Artia> ]
  }

Uma atividade é atrasada quando está em aberto (status false) e o término
previsto (estimatedEnd) é anterior à data de referência.

O histórico fica em reports/atrasadas-projeto-<id>.json e só é atualizado
quando o envio ao Teams dá certo, para que as "novas" não se percam numa
execução com falha. Falhas vão para reports/erros.log e o card fica salvo em
reports/payloads/ para reenvio.

Uso:
  python3 scripts/atrasadas_teams.py coleta.json            # envia
  python3 scripts/atrasadas_teams.py coleta.json --dry-run  # só imprime o card
  python3 scripts/atrasadas_teams.py --resend reports/payloads/<arquivo>.json
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPORTS = REPO / "reports"
PAYLOADS = REPORTS / "payloads"
ERROS = REPORTS / "erros.log"


def br(d):
    return d.strftime("%d/%m/%Y")


def atrasadas(atividades, hoje):
    vistas = {}
    for a in atividades:
        fim = a.get("estimatedEnd")
        if a.get("status") or not fim:
            continue
        fim = date.fromisoformat(fim[:10])
        if fim < hoje:
            vistas[str(a["id"])] = {**a, "_fim": fim, "_dias": (hoje - fim).days}
    return sorted(vistas.values(), key=lambda a: (-a["_dias"], a["title"]))


def montar_card(projeto, url, hoje, lista, novas):
    por_resp = defaultdict(list)
    for a in lista:
        nome = (a.get("responsible") or {}).get("name") or "Sem responsável"
        por_resp[nome].append(a)
    ordem = sorted(por_resp, key=lambda n: (-len(por_resp[n]), n))

    titulo = f"⚠️ {projeto} — atividades atrasadas em {br(hoje)}"
    body = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "wrap": True, "text": titulo},
        {"type": "FactSet", "facts": [
            {"title": "Total de atrasadas", "value": str(len(lista))},
            {"title": "Novas desde a última execução", "value": str(len(novas))},
        ]},
    ]

    if not lista:
        body[0]["text"] = f"✅ {projeto} — nenhuma atividade atrasada em {br(hoje)}"
    else:
        body.append({"type": "TextBlock", "weight": "Bolder", "size": "Medium", "separator": True,
                     "spacing": "Medium", "wrap": True, "text": "Por responsável"})
        body.append({"type": "FactSet", "facts": [
            {"title": n, "value": f"{len(por_resp[n])} ({sum(1 for a in por_resp[n] if str(a['id']) in novas)} nova(s))"}
            for n in ordem
        ]})
        if novas:
            body.append({"type": "TextBlock", "weight": "Bolder", "size": "Medium", "separator": True,
                         "spacing": "Medium", "wrap": True, "text": f"🆕 Novas desde a última execução ({len(novas)})"})
            body.append({"type": "TextBlock", "wrap": True, "spacing": "Small", "text": "\n".join(
                f"- {a['title']} — {(a.get('responsible') or {}).get('name') or 'Sem responsável'}"
                for a in lista if str(a["id"]) in novas)})
        for n in ordem:
            body.append({"type": "TextBlock", "weight": "Bolder", "size": "Medium", "separator": True,
                         "spacing": "Medium", "wrap": True, "text": f"👤 {n} ({len(por_resp[n])})"})
            for a in por_resp[n]:
                pasta = (a.get("parent") or {}).get("name") or projeto
                status = (a.get("customStatus") or {}).get("statusName") or "Em aberto"
                pct = a.get("completedPercent") or 0
                body.append({"type": "TextBlock", "wrap": True, "spacing": "Small", "text":
                             f"**{a['title']}**{' 🆕' if str(a['id']) in novas else ''}  \n"
                             f"Pasta: {pasta} · Término previsto: {br(a['_fim'])} · "
                             f"**{a['_dias']} dia(s) de atraso** · {pct:g}% concluído · {status}"})

    card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard", "version": "1.4", "body": body}
    if url:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Abrir projeto no Artia", "url": url}]
    return {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}


def enviar(payload, tentativas=3):
    url = os.environ.get("TEAMS_WEBHOOK_URL")
    if not url:
        raise RuntimeError("variável TEAMS_WEBHOOK_URL não definida no ambiente")
    dados = json.dumps(payload, ensure_ascii=False).encode()
    erro = None
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, data=dados, method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                if 200 <= r.status < 300:
                    return r.status
                erro = f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            erro = f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}"
        except Exception as e:  # rede, timeout
            erro = str(e)
        time.sleep(2 ** (i + 1))
    raise RuntimeError(f"falha após {tentativas} tentativas: {erro}")


def registrar_erro(projeto_id, msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with ERROS.open("a") as f:
        f.write(f"{ts} [projeto {projeto_id}] Falha ao enviar notificação ao Teams: {msg}\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("coleta", nargs="?")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--resend")
    args = p.parse_args()

    if args.resend:
        print("Reenviado:", enviar(json.loads(Path(args.resend).read_text())))
        return

    coleta = json.loads(Path(args.coleta).read_text())
    pid = coleta["projeto_id"]
    hoje = date.fromisoformat(coleta["data"])
    estado_arq = REPORTS / f"atrasadas-projeto-{pid}.json"
    anteriores = set(json.loads(estado_arq.read_text())["atrasadas"]) if estado_arq.exists() else set()

    lista = atrasadas(coleta["atividades"], hoje)
    novas = {str(a["id"]) for a in lista} - anteriores
    payload = montar_card(coleta["projeto_nome"], coleta.get("projeto_url"), hoje, lista, novas)

    resumo = {"total": len(lista), "novas": len(novas), "resolvidas": len(anteriores - {str(a["id"]) for a in lista})}
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(json.dumps(resumo, ensure_ascii=False), file=sys.stderr)
        return

    try:
        enviar(payload)
    except Exception as e:
        PAYLOADS.mkdir(parents=True, exist_ok=True)
        (PAYLOADS / f"atrasadas-projeto-{pid}-{hoje.isoformat()}.json").write_text(
            json.dumps(payload, ensure_ascii=False))
        registrar_erro(pid, str(e))
        print(json.dumps({**resumo, "enviado": False, "erro": str(e)}, ensure_ascii=False))
        sys.exit(1)

    estado_arq.write_text(json.dumps(
        {"data": hoje.isoformat(), "projeto_id": pid, "atrasadas": [str(a["id"]) for a in lista]},
        ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({**resumo, "enviado": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
