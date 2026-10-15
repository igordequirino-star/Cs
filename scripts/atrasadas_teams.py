#!/usr/bin/env python3
"""Resumo de atividades atrasadas de um projeto do Artia, enviado ao Teams.

Recebe a lista de atividades do projeto (todas as pastas e subpastas, já
coletadas pelo conector do Artia), compara com o estado salvo na execução
anterior, monta um Adaptive Card e envia ao webhook do Power Automate.

Uso:
    TEAMS_WEBHOOK_URL=... python3 scripts/atrasadas_teams.py \
        --atividades atividades.json --projeto-id 5272141 \
        --projeto-nome "Implantação CRM" \
        --projeto-url https://app2.artia.com/a/4430980/p/5272141 \
        --hoje 2026-10-15

Atividade atrasada: em aberto, término previsto antes de hoje e menos de
100% concluída. O estado (reports/atrasadas-projeto-<id>.json) só é
atualizado quando o envio dá certo, para que as "novas" não se percam.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone

REPORTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports")


def br(d):
    return d.strftime("%d/%m/%Y")


def carregar_estado(caminho):
    if not os.path.exists(caminho):
        return None
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def atrasadas(atividades, hoje):
    resultado = []
    for a in atividades:
        fim = a.get("estimatedEnd")
        if a.get("status") or not fim:
            continue
        if float(a.get("completedPercent") or 0) >= 100:
            continue
        fim = date.fromisoformat(fim[:10])
        if fim >= hoje:
            continue
        resultado.append({
            "id": str(a["id"]),
            "titulo": (a.get("title") or "Atividade sem nome").strip(),
            "pasta": ((a.get("parent") or {}).get("name") or "").strip(),
            "responsavel": ((a.get("responsible") or {}).get("name") or "Sem responsável").strip(),
            "fim": fim,
            "dias": (hoje - fim).days,
            "concluido": float(a.get("completedPercent") or 0),
            "situacao": ((a.get("customStatus") or {}).get("statusName") or "").strip(),
        })
    return resultado


def montar_card(lista, novas, resolvidas, ultima, hoje, nome, url):
    por_resp = defaultdict(list)
    for a in lista:
        por_resp[a["responsavel"]].append(a)
    ordem = sorted(por_resp.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))

    desde = f"desde {br(ultima)}" if ultima else "(primeira execução)"
    fatos = [
        {"title": "Total de atrasadas", "value": str(len(lista))},
        {"title": f"Novas {desde}", "value": str(len(novas))},
    ]
    if ultima:
        fatos.append({"title": "Saíram do atraso", "value": str(len(resolvidas))})

    corpo = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "wrap": True,
         "text": f"⚠️ {nome} — atividades atrasadas em {br(hoje)}"},
        {"type": "FactSet", "facts": fatos},
    ]

    if not lista:
        corpo.append({"type": "TextBlock", "wrap": True, "spacing": "Medium",
                      "text": "✅ Nenhuma atividade atrasada no projeto."})
    else:
        corpo.append({"type": "TextBlock", "weight": "Bolder", "size": "Medium",
                      "separator": True, "spacing": "Medium", "wrap": True,
                      "text": "Por responsável"})
        corpo.append({"type": "FactSet", "facts": [
            {"title": resp, "value": f"{len(itens)} ({sum(1 for i in itens if i['id'] in novas)} nova(s))"}
            for resp, itens in ordem
        ]})
        for resp, itens in ordem:
            corpo.append({"type": "TextBlock", "weight": "Bolder", "size": "Medium",
                          "separator": True, "spacing": "Medium", "wrap": True,
                          "text": f"👤 {resp} ({len(itens)})"})
            for a in sorted(itens, key=lambda i: (-i["dias"], i["titulo"].lower())):
                marca = " 🆕" if a["id"] in novas else ""
                detalhes = []
                if a["pasta"]:
                    detalhes.append(f"Pasta: {a['pasta']}")
                detalhes.append(f"Término previsto: {br(a['fim'])}")
                detalhes.append(f"**{a['dias']} dia(s) de atraso**")
                detalhes.append(f"{a['concluido']:.0f}% concluído")
                if a["situacao"]:
                    detalhes.append(a["situacao"])
                corpo.append({"type": "TextBlock", "wrap": True, "spacing": "Small",
                              "text": f"**{a['titulo']}**{marca}  \n" + " · ".join(detalhes)})

    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": corpo,
    }
    if url:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Abrir projeto no Artia", "url": url}]
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


def registrar_erro(projeto_id, msg):
    with open(os.path.join(REPORTS, "erros.log"), "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} projeto {projeto_id}: {msg}\n")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--atividades", required=True, help="JSON com a lista de atividades (ou {items: [...]})")
    p.add_argument("--projeto-id", required=True)
    p.add_argument("--projeto-nome", required=True)
    p.add_argument("--projeto-url", default="")
    p.add_argument("--hoje", default=date.today().isoformat(), help="YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true", help="só imprime o card, não envia nem salva estado")
    args = p.parse_args()

    hoje = date.fromisoformat(args.hoje)
    with open(args.atividades, encoding="utf-8") as f:
        dados = json.load(f)
    atividades = dados["items"] if isinstance(dados, dict) else dados
    # A mesma atividade pode vir repetida se as consultas por pasta se sobrepuserem.
    atividades = list({str(a["id"]): a for a in atividades}.values())

    caminho_estado = os.path.join(REPORTS, f"atrasadas-projeto-{args.projeto_id}.json")
    estado = carregar_estado(caminho_estado)
    anteriores = set(estado["atrasadas"]) if estado else set()
    ultima = date.fromisoformat(estado["data"]) if estado else None

    lista = atrasadas(atividades, hoje)
    atuais = {a["id"] for a in lista}
    novas = atuais - anteriores
    resolvidas = anteriores - atuais

    payload = montar_card(lista, novas, resolvidas, ultima, hoje,
                          args.projeto_nome, args.projeto_url)
    resumo = {"total": len(lista), "novas": len(novas), "saíram": len(resolvidas),
              "por_responsavel": {}}
    for a in lista:
        resumo["por_responsavel"][a["responsavel"]] = resumo["por_responsavel"].get(a["responsavel"], 0) + 1

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(json.dumps(resumo, ensure_ascii=False), file=sys.stderr)
        return 0

    webhook = os.environ.get("TEAMS_WEBHOOK_URL")
    if not webhook:
        registrar_erro(args.projeto_id, "TEAMS_WEBHOOK_URL não definido; nada enviado")
        print("Erro: defina TEAMS_WEBHOOK_URL", file=sys.stderr)
        return 2
    try:
        status = enviar(webhook, payload)
    except (urllib.error.URLError, TimeoutError) as e:
        registrar_erro(args.projeto_id, f"falha ao enviar ao Teams: {e}")
        print(f"Erro ao enviar ao Teams: {e}", file=sys.stderr)
        return 1
    if status >= 300:
        registrar_erro(args.projeto_id, f"Teams respondeu HTTP {status}")
        print(f"Teams respondeu HTTP {status}", file=sys.stderr)
        return 1

    with open(caminho_estado, "w", encoding="utf-8") as f:
        json.dump({"data": hoje.isoformat(), "projeto_id": int(args.projeto_id),
                   "atrasadas": sorted(atuais)}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"enviado_http": status, **resumo}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
