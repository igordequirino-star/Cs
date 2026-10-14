#!/usr/bin/env python3
"""Resumo de atividades atrasadas de um projeto do Artia para um canal do Teams.

Recebe um snapshot do projeto (pastas + atividades, coletados pelo conector do
Artia), compara com o histórico da última execução, monta um Adaptive Card e
envia para o webhook do Teams (Power Automate).

Uso:
    TEAMS_WEBHOOK_URL=... python3 scripts/atrasadas_teams.py \
        --snapshot snapshot.json --hoje 2026-10-14 [--dry-run]

Formato do snapshot:
    {
      "projeto": {"id": "5272141", "nome": "Implantação CRM"},
      "pastas": [{"id": "...", "name": "...", "parent": {"id": "...", "name": "..."}}],
      "atividades": [ <itens de artia_list_activities, sem alteração> ]
    }

Regra de atraso: atividade não encerrada (status false e sem término real)
com término previsto anterior a hoje.

O histórico (reports/atrasadas-projeto-<id>.json) só é atualizado depois que o
Teams aceita a mensagem, para que as "novas" não se percam numa falha de envio.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
REPORTS = RAIZ / "reports"
MAX_POR_RESPONSAVEL = 20


def fmt_data(iso):
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")


def caminho_pasta(folder_id, pastas, projeto_id):
    """Caminho da pasta abaixo do projeto, ex.: '04-Testes › Homologação'."""
    nomes = []
    atual = str(folder_id)
    visitados = set()
    while atual and atual != projeto_id and atual not in visitados:
        visitados.add(atual)
        pasta = pastas.get(atual)
        if not pasta:
            break
        nomes.append(pasta["name"])
        atual = str((pasta.get("parent") or {}).get("id") or "")
    return " › ".join(reversed(nomes)) or "(raiz do projeto)"


def atrasadas(atividades, hoje):
    vistas = set()
    resultado = []
    for a in atividades:
        if a["id"] in vistas:
            continue
        vistas.add(a["id"])
        fim = a.get("estimatedEnd")
        if a.get("status") or a.get("actualEnd") or not fim:
            continue
        if date.fromisoformat(fim) < hoje:
            resultado.append(a)
    return resultado


def montar_card(projeto_nome, hoje, lista, novas_ids, pastas, projeto_id):
    por_resp = defaultdict(list)
    for a in lista:
        nome = ((a.get("responsible") or {}).get("name") or "Sem responsável").strip()
        por_resp[nome].append(a)
    ordem = sorted(por_resp, key=lambda n: (-len(por_resp[n]), n.lower()))

    titulo = f"⚠️ {projeto_nome} — atividades atrasadas em {hoje.strftime('%d/%m/%Y')}"
    fatos = [
        {"title": "Total de atrasadas", "value": str(len(lista))},
        {"title": "Novas desde a última execução", "value": str(len(novas_ids))},
    ]
    fatos += [{"title": n, "value": str(len(por_resp[n]))} for n in ordem]
    body = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "wrap": True, "text": titulo},
        {"type": "FactSet", "facts": fatos},
    ]

    if not lista:
        body.append({"type": "TextBlock", "wrap": True, "text": "✅ Nenhuma atividade atrasada no projeto."})

    for nome in ordem:
        itens = sorted(por_resp[nome], key=lambda a: a["estimatedEnd"])
        body.append({
            "type": "TextBlock", "weight": "Bolder", "size": "Medium", "separator": True,
            "spacing": "Medium", "wrap": True, "text": f"👤 {nome} ({len(itens)})",
        })
        for a in itens[:MAX_POR_RESPONSAVEL]:
            dias = (hoje - date.fromisoformat(a["estimatedEnd"])).days
            novo = " 🆕" if a["id"] in novas_ids else ""
            pasta = caminho_pasta(a.get("folderId"), pastas, projeto_id)
            pct = a.get("completedPercent") or 0
            body.append({
                "type": "TextBlock", "wrap": True, "spacing": "Small",
                "text": (f"**{a['title'].strip()}**{novo}  \n"
                         f"Pasta: {pasta} · Término previsto: {fmt_data(a['estimatedEnd'])} · "
                         f"**{dias} dia(s) de atraso** · {pct:g}% concluído"),
            })
        if len(itens) > MAX_POR_RESPONSAVEL:
            body.append({"type": "TextBlock", "wrap": True, "spacing": "Small", "isSubtle": True,
                         "text": f"… e mais {len(itens) - MAX_POR_RESPONSAVEL} atividade(s)."})

    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard", "version": "1.4", "body": body,
            },
        }],
    }


def enviar(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status


def registrar_erro(msg):
    REPORTS.mkdir(exist_ok=True)
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(REPORTS / "erros.log", "a", encoding="utf-8") as f:
        f.write(f"[{agora}] {msg}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--hoje", required=True, help="YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="só imprime o card, sem enviar nem gravar histórico")
    args = ap.parse_args()

    snap = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    projeto_id = str(snap["projeto"]["id"])
    projeto_nome = snap["projeto"]["nome"]
    hoje = date.fromisoformat(args.hoje)
    pastas = {str(p["id"]): p for p in snap.get("pastas", [])}

    estado_path = REPORTS / f"atrasadas-projeto-{projeto_id}.json"
    anteriores = set()
    if estado_path.exists():
        anteriores = set(json.loads(estado_path.read_text(encoding="utf-8")).get("atrasadas", []))

    lista = atrasadas(snap["atividades"], hoje)
    novas = {a["id"] for a in lista} - anteriores
    payload = montar_card(projeto_nome, hoje, lista, novas, pastas, projeto_id)

    resumo = {"total": len(lista), "novas": len(novas)}
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(json.dumps(resumo), file=sys.stderr)
        return 0

    url = os.environ.get("TEAMS_WEBHOOK_URL")
    if not url:
        registrar_erro(f"projeto {projeto_id}: TEAMS_WEBHOOK_URL não definida")
        print("TEAMS_WEBHOOK_URL não definida", file=sys.stderr)
        return 2
    try:
        status = enviar(url, payload)
    except (urllib.error.URLError, TimeoutError) as e:
        detalhe = getattr(e, "code", None) or getattr(e, "reason", e)
        registrar_erro(f"projeto {projeto_id}: falha ao enviar para o Teams ({detalhe})")
        print(f"Falha ao enviar para o Teams: {detalhe}", file=sys.stderr)
        return 1

    (REPORTS / "payloads").mkdir(parents=True, exist_ok=True)
    (REPORTS / "payloads" / f"atrasadas-projeto-{projeto_id}-{hoje.isoformat()}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    estado_path.write_text(json.dumps({
        "data": hoje.isoformat(),
        "projeto_id": int(projeto_id),
        "atrasadas": [a["id"] for a in lista],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**resumo, "http_status": status}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
