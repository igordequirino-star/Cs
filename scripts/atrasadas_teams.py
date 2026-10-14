#!/usr/bin/env python3
"""Resumo de atividades atrasadas de um projeto Artia para um canal do Teams.

Recebe as atividades já coletadas do Artia (projeto + todas as pastas e
subpastas), identifica as atrasadas, compara com o histórico da última
execução e envia um Adaptive Card para o webhook do Teams (Power Automate).

O histórico só é atualizado depois de um envio bem-sucedido, para que uma
falha não faça as atividades "novas" sumirem do próximo resumo.

Uso:
  python3 scripts/atrasadas_teams.py \
      --atividades /caminho/atividades.json \
      --projeto-id 5272141 --projeto-nome "Implantação CRM" \
      --projeto-url https://app2.artia.com/a/4430980/p/5272141 \
      [--hoje 2026-10-14] [--dry-run]

O webhook vem de --webhook ou da variável de ambiente TEAMS_WEBHOOK_URL.

Formato de --atividades: lista JSON de atividades como devolvidas pela
listagem de atividades do Artia (id, title, status, completedPercent,
estimatedEnd, responsible.name, parent.name, customStatus.statusName).
O campo opcional "caminho" (ex.: "05-Implementação / Migração") tem
precedência sobre parent.name para indicar a pasta/subpasta.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEM_RESPONSAVEL = "Sem responsável"


def carregar_json(caminho, padrao=None):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return padrao


def esta_atrasada(atividade, hoje):
    fim = atividade.get("estimatedEnd")
    if not fim or atividade.get("status") is True:
        return False
    if (atividade.get("completedPercent") or 0) >= 100:
        return False
    return date.fromisoformat(fim[:10]) < hoje


def formatar_data(iso):
    return date.fromisoformat(iso[:10]).strftime("%d/%m/%Y")


def nome_responsavel(atividade):
    resp = atividade.get("responsible") or {}
    return (resp.get("name") or "").strip() or SEM_RESPONSAVEL


def montar_card(projeto_nome, projeto_url, hoje, atrasadas, novas_ids, resolvidas):
    por_responsavel = defaultdict(list)
    for a in atrasadas:
        por_responsavel[nome_responsavel(a)].append(a)
    # Quem tem mais atrasadas primeiro; empate em ordem alfabética.
    responsaveis = sorted(por_responsavel, key=lambda r: (-len(por_responsavel[r]), r.lower()))

    titulo = f"⚠️ {projeto_nome} — atividades atrasadas em {hoje.strftime('%d/%m/%Y')}"
    fatos = [
        {"title": "Total de atrasadas", "value": str(len(atrasadas))},
        {"title": "Novas desde a última execução", "value": str(len(novas_ids))},
    ]
    if resolvidas:
        fatos.append({"title": "Saíram da lista desde a última execução", "value": str(resolvidas)})

    corpo = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "wrap": True, "text": titulo},
        {"type": "FactSet", "facts": fatos},
    ]

    if not atrasadas:
        corpo.append({"type": "TextBlock", "wrap": True, "text": "✅ Nenhuma atividade atrasada no projeto."})
    else:
        corpo.append({
            "type": "TextBlock", "weight": "Bolder", "size": "Medium", "separator": True,
            "spacing": "Medium", "wrap": True, "text": "Por responsável",
        })
        corpo.append({
            "type": "FactSet",
            "facts": [
                {
                    "title": r,
                    "value": f"{len(por_responsavel[r])}"
                    + (f" ({n} nova(s))" if (n := sum(1 for a in por_responsavel[r] if str(a['id']) in novas_ids)) else ""),
                }
                for r in responsaveis
            ],
        })
        for r in responsaveis:
            corpo.append({
                "type": "TextBlock", "weight": "Bolder", "size": "Medium", "separator": True,
                "spacing": "Medium", "wrap": True, "text": f"👤 {r} ({len(por_responsavel[r])})",
            })
            for a in sorted(por_responsavel[r], key=lambda a: a["estimatedEnd"]):
                dias = (hoje - date.fromisoformat(a["estimatedEnd"][:10])).days
                pasta = a.get("caminho") or (a.get("parent") or {}).get("name") or "Raiz do projeto"
                situacao = (a.get("customStatus") or {}).get("statusName")
                detalhes = [
                    f"Pasta: {pasta}",
                    f"Término previsto: {formatar_data(a['estimatedEnd'])}",
                    f"**{dias} dia(s) de atraso**",
                    f"{round(a.get('completedPercent') or 0)}% concluído",
                ]
                if situacao:
                    detalhes.append(f"Situação: {situacao}")
                nova = " 🆕" if str(a["id"]) in novas_ids else ""
                corpo.append({
                    "type": "TextBlock", "wrap": True, "spacing": "Small",
                    "text": f"**{a['title']}**{nova}  \n" + " · ".join(detalhes),
                })

    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": corpo,
    }
    if projeto_url:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Abrir projeto no Artia", "url": projeto_url}]
    return {
        "type": "message",
        "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}],
    }


def enviar(webhook, payload):
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status


def registrar_erro(mensagem):
    log = REPO_ROOT / "reports" / "erros.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write(mensagem.rstrip() + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--atividades", required=True)
    p.add_argument("--projeto-id", required=True, type=int)
    p.add_argument("--projeto-nome", required=True)
    p.add_argument("--projeto-url", default="")
    p.add_argument("--hoje", default=date.today().isoformat())
    p.add_argument("--estado", help="Arquivo de histórico (padrão: reports/atrasadas-projeto-<id>.json)")
    p.add_argument("--webhook", default=os.environ.get("TEAMS_WEBHOOK_URL", ""))
    p.add_argument("--dry-run", action="store_true", help="Só imprime o payload; não envia nem grava histórico")
    args = p.parse_args()

    hoje = date.fromisoformat(args.hoje)
    estado_path = Path(args.estado or REPO_ROOT / "reports" / f"atrasadas-projeto-{args.projeto_id}.json")

    atividades = carregar_json(args.atividades)
    if not isinstance(atividades, list):
        sys.exit("--atividades precisa ser uma lista JSON de atividades")
    # Remove duplicatas (a mesma atividade pode vir de mais de uma listagem).
    atividades = list({str(a["id"]): a for a in atividades}.values())

    atrasadas = [a for a in atividades if esta_atrasada(a, hoje)]
    estado = carregar_json(estado_path, {}) or {}
    anteriores = {str(i) for i in estado.get("atrasadas", [])}
    atuais = [str(a["id"]) for a in atrasadas]
    novas_ids = set(atuais) - anteriores
    resolvidas = len(anteriores - set(atuais))

    payload = montar_card(args.projeto_nome, args.projeto_url, hoje, atrasadas, novas_ids, resolvidas)
    resumo = (
        f"{args.projeto_nome}: {len(atrasadas)} atrasada(s), {len(novas_ids)} nova(s), "
        f"{resolvidas} saíram da lista desde {estado.get('data', 'nunca')}"
    )

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(resumo, file=sys.stderr)
        return

    if not args.webhook:
        sys.exit("Webhook não informado (use --webhook ou TEAMS_WEBHOOK_URL)")

    try:
        status = enviar(args.webhook, payload)
    except (urllib.error.URLError, TimeoutError) as e:
        detalhe = e.read().decode("utf-8", "replace")[:500] if isinstance(e, urllib.error.HTTPError) else ""
        registrar_erro(f"{hoje.isoformat()} projeto {args.projeto_id}: falha ao enviar para o Teams: {e} {detalhe}")
        sys.exit(f"Falha ao enviar para o Teams: {e} {detalhe}")

    estado_path.parent.mkdir(parents=True, exist_ok=True)
    with open(estado_path, "w", encoding="utf-8") as f:
        json.dump({"data": hoje.isoformat(), "projeto_id": args.projeto_id, "atrasadas": atuais}, f, indent=2)
        f.write("\n")
    print(f"Enviado ao Teams (HTTP {status}). {resumo}")


if __name__ == "__main__":
    main()
