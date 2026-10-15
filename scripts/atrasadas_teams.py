#!/usr/bin/env python3
"""Alerta de atividades atrasadas de um projeto do Artia, enviado no Teams
para cada responsável.

Recebe a lista de atividades do projeto (todas as pastas e subpastas, já
coletadas pelo conector do Artia), compara com o estado salvo na execução
anterior e envia a cada responsável um Adaptive Card só com as atividades
dele, pelo webhook do Power Automate do chat dessa pessoa.

Uso:
    TEAMS_WEBHOOKS='{"lucas.musse@artia.com": "https://...", ...}' \
    python3 scripts/atrasadas_teams.py \
        --atividades atividades.json --projeto-id 5272141 \
        --projeto-nome "Implantação CRM" \
        --projeto-url https://app2.artia.com/a/4430980/p/5272141 \
        --hoje 2026-10-15

TEAMS_WEBHOOKS é um objeto JSON cuja chave é o e-mail ou o nome do
responsável (sem diferenciar maiúsculas nem acentos) e o valor é o webhook.
Fica fora do repositório, que é público.

Atividade atrasada: em aberto, término previsto antes de hoje e menos de
100% concluída. Cada responsável recebe alerta quando tem atrasadas, ou uma
única vez quando zera as que tinha. O estado de cada pessoa em
reports/atrasadas-projeto-<id>.json só é atualizado quando o envio para ela
dá certo, para que as "novas" não se percam.
"""
import argparse
import json
import os
import sys
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone

REPORTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports")


def br(d):
    return d.strftime("%d/%m/%Y")


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.casefold().split())


def chave_pessoa(nome, email):
    return normalizar(email) if email else normalizar(nome)


def carregar_estado(caminho):
    if not os.path.exists(caminho):
        return {}
    with open(caminho, encoding="utf-8") as f:
        estado = json.load(f)
    if "por_responsavel" in estado:
        return estado["por_responsavel"]
    # Formato antigo: uma lista única de atrasadas do projeto. Vale como
    # histórico de todo mundo, para não marcar tudo como novo na migração.
    ids = set(estado.get("atrasadas", []))
    return {"*": {"data": estado.get("data"), "atrasadas": sorted(ids)}}


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
        resp = a.get("responsible") or {}
        nome = (resp.get("name") or "Sem responsável").strip()
        email = (resp.get("email") or "").strip()
        resultado.append({
            "id": str(a["id"]),
            "titulo": (a.get("title") or "Atividade sem nome").strip(),
            "pasta": ((a.get("parent") or {}).get("name") or "").strip(),
            "responsavel": nome,
            "email": email,
            "chave": chave_pessoa(nome, email),
            "fim": fim,
            "dias": (hoje - fim).days,
            "concluido": float(a.get("completedPercent") or 0),
            "situacao": ((a.get("customStatus") or {}).get("statusName") or "").strip(),
        })
    return resultado


def montar_card(nome, itens, novas, saiu, ultima, hoje, projeto, url):
    primeiro = nome.split()[0] if nome else ""
    if itens:
        titulo = (f"⚠️ {primeiro}, você tem {len(itens)} atividade(s) atrasada(s) "
                  f"no projeto {projeto}")
    else:
        titulo = f"✅ Tudo em dia, {primeiro}! Nenhuma atividade atrasada no projeto {projeto}"

    fatos = [{"title": "Suas atrasadas", "value": str(len(itens))}]
    if ultima:
        fatos.append({"title": f"Novas desde {br(ultima)}", "value": str(len(novas))})
        fatos.append({"title": "Saíram do atraso", "value": str(len(saiu))})

    corpo = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "wrap": True, "text": titulo},
        {"type": "TextBlock", "isSubtle": True, "spacing": "None", "wrap": True,
         "text": f"Situação em {br(hoje)}"},
        {"type": "FactSet", "facts": fatos},
    ]
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
        corpo.append({"type": "TextBlock", "wrap": True, "separator": True, "spacing": "Medium",
                      "text": f"**{a['titulo']}**{marca}  \n" + " · ".join(detalhes)})
    if itens:
        corpo.append({"type": "TextBlock", "wrap": True, "spacing": "Medium", "isSubtle": True,
                      "text": "Atualize o andamento ou replaneje as datas no Artia."})

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
    p.add_argument("--dry-run", action="store_true", help="só imprime os cards, não envia nem salva estado")
    args = p.parse_args()

    hoje = date.fromisoformat(args.hoje)
    with open(args.atividades, encoding="utf-8") as f:
        dados = json.load(f)
    atividades = dados["items"] if isinstance(dados, dict) else dados
    # A mesma atividade pode vir repetida se as consultas por pasta se sobrepuserem.
    atividades = list({str(a["id"]): a for a in atividades}.values())

    webhooks = {}
    if not args.dry_run:
        try:
            webhooks = {normalizar(k): v for k, v in json.loads(os.environ.get("TEAMS_WEBHOOKS") or "{}").items()}
        except json.JSONDecodeError as e:
            registrar_erro(args.projeto_id, f"TEAMS_WEBHOOKS inválido: {e}")
            print(f"Erro: TEAMS_WEBHOOKS não é um JSON válido ({e})", file=sys.stderr)
            return 2

    caminho_estado = os.path.join(REPORTS, f"atrasadas-projeto-{args.projeto_id}.json")
    estado = carregar_estado(caminho_estado)
    legado = estado.pop("*", None)

    por_pessoa = defaultdict(list)
    nomes = {}
    for a in atrasadas(atividades, hoje):
        por_pessoa[a["chave"]].append(a)
        nomes[a["chave"]] = (a["responsavel"], a["email"])

    # Quem tem atrasadas agora, ou tinha na última execução e precisa saber que zerou.
    pessoas = set(por_pessoa) | {k for k, v in estado.items() if v.get("atrasadas")}
    resultado = {"total": sum(len(v) for v in por_pessoa.values()), "enviados": [], "falhas": [], "sem_webhook": []}
    novo_estado = dict(estado)

    for chave in sorted(pessoas):
        itens = por_pessoa.get(chave, [])
        anterior = estado.get(chave) or legado or {}
        nome, email = nomes.get(chave) or (estado[chave].get("nome", chave), estado[chave].get("email", ""))
        ids = {a["id"] for a in itens}
        ids_antes = set(anterior.get("atrasadas", []))
        ultima = date.fromisoformat(anterior["data"]) if anterior.get("data") else None
        if legado and chave not in estado:
            # No formato antigo não se sabe de quem era cada atividade, e a
            # pessoa ainda não recebeu alerta: é o primeiro, sem "novas".
            ids_antes &= ids
            ultima = None
        novas, saiu = ids - ids_antes, ids_antes - ids
        payload = montar_card(nome, itens, novas, saiu, ultima, hoje, args.projeto_nome, args.projeto_url)
        info = {"responsavel": nome, "atrasadas": len(itens), "novas": len(novas), "saíram": len(saiu)}

        if args.dry_run:
            print(json.dumps({"para": nome, "payload": payload}, ensure_ascii=False, indent=2))
            resultado["enviados"].append(info)
            continue

        webhook = webhooks.get(normalizar(email)) or webhooks.get(normalizar(nome))
        if not webhook:
            registrar_erro(args.projeto_id, f"sem webhook do Teams para {nome} <{email}>; alerta não enviado")
            resultado["sem_webhook"].append(info)
            continue
        try:
            status = enviar(webhook, payload)
            erro = None if status < 300 else f"HTTP {status}"
        except (urllib.error.URLError, TimeoutError) as e:
            erro = str(e)
        if erro:
            registrar_erro(args.projeto_id, f"falha ao enviar alerta para {nome}: {erro}")
            resultado["falhas"].append({**info, "erro": erro})
            continue
        novo_estado[chave] = {"nome": nome, "email": email, "data": hoje.isoformat(), "atrasadas": sorted(ids)}
        resultado["enviados"].append(info)

    if legado and (resultado["falhas"] or resultado["sem_webhook"]):
        novo_estado["*"] = legado  # ainda há quem dependa do histórico antigo
    if not args.dry_run:
        with open(caminho_estado, "w", encoding="utf-8") as f:
            json.dump({"projeto_id": int(args.projeto_id), "por_responsavel": novo_estado},
                      f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
    print(json.dumps(resultado, ensure_ascii=False), file=sys.stderr if args.dry_run else sys.stdout)
    return 1 if resultado["falhas"] else 0


if __name__ == "__main__":
    sys.exit(main())
