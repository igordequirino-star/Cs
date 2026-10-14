#!/usr/bin/env python3
"""Alerta de atividades atrasadas de um projeto do Artia no Microsoft Teams.

Consulta o projeto via API GraphQL do Artia (api.artia.com/graphql), percorre
todas as pastas e subpastas, identifica as atividades atrasadas e publica um
Adaptive Card (v1.4) num fluxo do Power Automate (Workflows) via webhook.

Variáveis de ambiente:
  ARTIA_CLIENT_ID, ARTIA_CLIENT_SECRET  credenciais de API da organização (obrigatórias)
  TEAMS_WEBHOOK_URL                     URL do fluxo do Power Automate (obrigatória, exceto em --dry-run)
  ARTIA_ORGANIZATION_ID, ARTIA_USER_ID  cabeçalhos OrganizationId / UserId (opcionais)
  ARTIA_APP_URL                         base do link do projeto (padrão: https://app2.artia.com)

Somente biblioteca padrão do Python 3.9+.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo

    TZ_SP = ZoneInfo("America/Sao_Paulo")
except Exception:  # sem base tzdata no sistema; Brasil não tem horário de verão desde 2019
    TZ_SP = timezone(timedelta(hours=-3))

ACCOUNT_ID = 4430980
PROJECT_ID = 5272141

API_URL = "https://api.artia.com/graphql"
# A API recusa (403) o User-Agent padrão do urllib.
USER_AGENT = "artia-alerta-atrasadas/1.0"
HTTP_TIMEOUT = 60

REPO_ROOT = Path(__file__).resolve().parent.parent
SEM_RESPONSAVEL = "Sem responsável"
ETAPA_RAIZ = "—"
# Limite prático de payload de cards no Teams (~28 KB); fica uma folga.
MAX_CARD_BYTES = 26_000


# --------------------------------------------------------------------------
# API do Artia
# --------------------------------------------------------------------------

Q_AUTH = """
mutation($clientId: String!, $secret: String!) {
  authenticationByClient(clientId: $clientId, secret: $secret) { token }
}"""

Q_PROJECT = """
query($id: ID!, $accountId: Int) {
  showProject(id: $id, accountId: $accountId) { id name }
}"""

Q_FOLDERS = """
query($accountId: Int, $page: Int, $filter: FolderFilterInput) {
  listingFoldersV2(accountId: $accountId, page: $page, filter: $filter) {
    totalPages
    currentPage
    folders { id name parent { id name } }
  }
}"""

Q_ACTIVITIES = """
query($accountId: Int, $folderId: Int, $page: Int) {
  listingActivitiesV2(accountId: $accountId, folderId: $folderId, page: $page) {
    totalPages
    currentPage
    activities {
      id
      title
      status
      estimatedEnd
      completedPercent
      responsible { id name }
      parent { id name }
    }
  }
}"""


class ArtiaError(RuntimeError):
    pass


class ArtiaClient:
    def __init__(self, client_id, secret, organization_id=None, user_id=None, url=API_URL):
        self.client_id = client_id
        self.secret = secret
        self.organization_id = organization_id
        self.user_id = user_id
        self.url = url
        self.token = None

    def _post(self, query, variables, auth=True):
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
            if self.organization_id:
                headers["OrganizationId"] = str(self.organization_id)
            if self.user_id:
                headers["UserId"] = str(self.user_id)
        body = json.dumps({"query": query, "variables": variables}).encode()
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as e:
            raise ArtiaError(f"HTTP {e.code} da API do Artia: {e.read()[:500]!r}") from e
        if payload.get("errors"):
            msgs = "; ".join(err.get("message", str(err)) for err in payload["errors"])
            raise ArtiaError(f"Erro da API do Artia: {msgs}")
        return payload["data"]

    def authenticate(self):
        data = self._post(Q_AUTH, {"clientId": self.client_id, "secret": self.secret}, auth=False)
        self.token = data["authenticationByClient"]["token"]

    def project_name(self, account_id, project_id):
        data = self._post(Q_PROJECT, {"id": str(project_id), "accountId": account_id})
        project = data["showProject"]
        if not project or not project.get("name"):
            raise ArtiaError(f"Projeto {project_id} não encontrado no grupo de trabalho {account_id}")
        return project["name"]

    def _paginate(self, query, variables, field, items_key):
        page, items = 1, []
        while True:
            result = self._post(query, {**variables, "page": page})[field]
            items.extend(result[items_key])
            if page >= (result["totalPages"] or 0):
                return items
            page += 1

    def child_folders(self, account_id, parent_ids):
        """Filhos diretos (um nível) das pastas/projetos informados."""
        return self._paginate(
            Q_FOLDERS,
            {"accountId": account_id, "filter": {"parentIds": [int(i) for i in parent_ids]}},
            "listingFoldersV2",
            "folders",
        )

    def folder_activities(self, account_id, folder_id):
        """Atividades da pasta informada, sem incluir as subpastas."""
        return self._paginate(
            Q_ACTIVITIES,
            {"accountId": account_id, "folderId": int(folder_id)},
            "listingActivitiesV2",
            "activities",
        )


def collect_activities(client, account_id, project_id):
    """Todas as atividades do projeto, descendo recursivamente pelas pastas."""
    folder_ids = [str(project_id)]
    seen = set(folder_ids)
    level = folder_ids[:]
    while level:
        children = [str(f["id"]) for f in client.child_folders(account_id, level)]
        level = [fid for fid in children if fid not in seen]
        seen.update(level)
        folder_ids.extend(level)

    activities = []
    for fid in folder_ids:
        activities.extend(client.folder_activities(account_id, fid))
    return activities


# --------------------------------------------------------------------------
# Regras de negócio
# --------------------------------------------------------------------------


def today_sp():
    return datetime.now(TZ_SP).date()


def parse_date(value):
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def overdue_activities(activities, ref_date, project_id):
    """Atrasada = não encerrada (status false) e término estimado anterior à data de referência."""
    result = []
    for act in activities:
        if act.get("status"):
            continue
        end = parse_date(act.get("estimatedEnd"))
        if end is None or end >= ref_date:
            continue
        parent = act.get("parent") or {}
        on_root = str(parent.get("id")) == str(project_id)
        responsible = ((act.get("responsible") or {}).get("name") or "").strip()
        result.append(
            {
                "id": str(act["id"]),
                "titulo": (act.get("title") or "").strip() or "(sem título)",
                "etapa": ETAPA_RAIZ if on_root or not parent.get("name") else parent["name"],
                "responsavel": responsible or SEM_RESPONSAVEL,
                "termino": end,
                "dias_atraso": (ref_date - end).days,
                "percentual": act.get("completedPercent") or 0,
            }
        )
    return result


def baseline_ids(state, ref_date):
    """IDs atrasados na execução anterior, ou None se não houver execução anterior.

    Se o snapshot salvo é do próprio dia (reexecução), compara com a execução
    anterior a ele, para que as "novas" não sumam numa segunda rodada no mesmo dia.
    """
    if not state:
        return None
    if state.get("data") == ref_date.isoformat():
        previous = state.get("execucao_anterior")
        return set(previous["atrasadas"]) if previous else None
    return set(state.get("atrasadas", []))


def mark_new(overdue, baseline, ref_date):
    """Sem snapshot anterior, são novas as que tinham término previsto no dia anterior."""
    yesterday = ref_date - timedelta(days=1)
    for item in overdue:
        if baseline is None:
            item["nova"] = item["termino"] == yesterday
        else:
            item["nova"] = item["id"] not in baseline
    return overdue


def group_by_responsible(overdue):
    groups = {}
    for item in overdue:
        groups.setdefault(item["responsavel"], []).append(item)
    for items in groups.values():
        items.sort(key=lambda i: (-i["dias_atraso"], i["titulo"].lower()))
    return sorted(groups.items(), key=lambda g: (-len(g[1]), g[0].lower()))


def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def build_state(previous, project_id, ref_date, overdue):
    if previous and previous.get("data") == ref_date.isoformat():
        anterior = previous.get("execucao_anterior")
    elif previous:
        anterior = {"data": previous.get("data"), "atrasadas": previous.get("atrasadas", [])}
    else:
        anterior = None
    return {
        "projeto_id": project_id,
        "data": ref_date.isoformat(),
        "atrasadas": sorted(item["id"] for item in overdue),
        "execucao_anterior": anterior,
    }


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Adaptive Card
# --------------------------------------------------------------------------


def fmt_date(d):
    return d.strftime("%d/%m/%Y")


def fmt_percent(value):
    value = float(value)
    text = f"{value:.0f}" if value == int(value) else f"{value:.1f}".replace(".", ",")
    return f"{text}%"


def fmt_days(n):
    return f"{n} dia" if n == 1 else f"{n} dias"


def _text(text, **props):
    return {"type": "TextBlock", "text": text, "wrap": True, **props}


def _header(project_name, ref_date):
    return [
        _text(project_name, size="Large", weight="Bolder"),
        _text(f"Posição em {fmt_date(ref_date)}", isSubtle=True, spacing="None"),
    ]


def _footer(project_url):
    return _text(f"🔗 [Abrir projeto no Artia]({project_url})", separator=True, spacing="Medium", size="Small")


def _indicator(label, value, color="Default"):
    return {
        "type": "Column",
        "width": "stretch",
        "items": [
            _text(str(value), size="ExtraLarge", weight="Bolder", color=color, horizontalAlignment="Center"),
            _text(label, isSubtle=True, spacing="None", horizontalAlignment="Center"),
        ],
    }


def _activity(item):
    title = f"🆕 {item['titulo']}" if item["nova"] else item["titulo"]
    details = (
        f"Etapa: {item['etapa']} · Término previsto: {fmt_date(item['termino'])} · "
        f"**{fmt_days(item['dias_atraso'])} de atraso** · {fmt_percent(item['percentual'])} concluído"
    )
    return {
        "type": "Container",
        "spacing": "Small",
        "items": [_text(title, weight="Bolder"), _text(details, size="Small", spacing="None")],
    }


def _card(body):
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }


def build_card(project_name, ref_date, overdue, project_url, max_per_responsible=None):
    if not overdue:
        return _card(
            _header(project_name, ref_date)
            + [_text("✅ O projeto está sem atividades atrasadas.", color="Good", weight="Bolder"), _footer(project_url)]
        )

    groups = group_by_responsible(overdue)
    total_new = sum(1 for i in overdue if i["nova"])
    body = _header(project_name, ref_date)
    body.append(
        {
            "type": "ColumnSet",
            "spacing": "Medium",
            "columns": [
                _indicator("Atividades atrasadas", len(overdue), "Attention"),
                _indicator("Novas atrasadas", total_new, "Warning" if total_new else "Default"),
            ],
        }
    )
    body.append(_text("Total por responsável", weight="Bolder", spacing="Medium"))
    body.append({"type": "FactSet", "facts": [{"title": name, "value": str(len(items))} for name, items in groups]})

    for name, items in groups:
        shown = items if max_per_responsible is None else items[:max_per_responsible]
        section = [_text(f"👤 {name} ({len(items)})", size="Medium", weight="Bolder")]
        section.extend(_activity(i) for i in shown)
        hidden = len(items) - len(shown)
        if hidden:
            section.append(_text(f"… e mais {hidden} atividade(s) — veja no Artia.", isSubtle=True, size="Small"))
        body.append({"type": "Container", "separator": True, "spacing": "Medium", "items": section})

    body.append(_footer(project_url))
    return _card(body)


def build_message(card):
    return {
        "type": "message",
        "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}],
    }


def build_message_within_limit(project_name, ref_date, overdue, project_url):
    """Monta a mensagem; se passar do limite do Teams, encurta as listas por responsável."""
    for cap in (None, 20, 10, 5, 3, 1, 0):
        message = build_message(build_card(project_name, ref_date, overdue, project_url, cap))
        if len(json.dumps(message, ensure_ascii=False).encode()) <= MAX_CARD_BYTES:
            return message
    return message


# --------------------------------------------------------------------------
# Teams
# --------------------------------------------------------------------------


def send_to_teams(webhook_url, message):
    body = json.dumps(message, ensure_ascii=False).encode()
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Webhook do Teams respondeu HTTP {e.code}: {e.read()[:500]!r}") from e


# --------------------------------------------------------------------------
# Execução
# --------------------------------------------------------------------------


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--account-id", type=int, default=ACCOUNT_ID)
    p.add_argument("--project-id", type=int, default=PROJECT_ID)
    p.add_argument("--state-file", help="padrão: state/atrasadas_<projeto>.json")
    p.add_argument("--dry-run", action="store_true", help="imprime o payload; não envia nem grava o snapshot")
    p.add_argument("--fixture", help="lê projeto e atividades de um JSON local em vez da API (testes)")
    p.add_argument("--data-referencia", type=date.fromisoformat, help="AAAA-MM-DD; padrão: hoje em America/Sao_Paulo")
    return p.parse_args(argv)


def fetch(args):
    if args.fixture:
        data = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        return data["projeto"]["name"], data["atividades"]

    client_id = os.environ.get("ARTIA_CLIENT_ID")
    secret = os.environ.get("ARTIA_CLIENT_SECRET")
    if not client_id or not secret:
        sys.exit("Defina ARTIA_CLIENT_ID e ARTIA_CLIENT_SECRET.")
    client = ArtiaClient(
        client_id,
        secret,
        organization_id=os.environ.get("ARTIA_ORGANIZATION_ID"),
        user_id=os.environ.get("ARTIA_USER_ID"),
    )
    client.authenticate()
    name = client.project_name(args.account_id, args.project_id)
    return name, collect_activities(client, args.account_id, args.project_id)


def main(argv=None):
    args = parse_args(argv)
    ref_date = args.data_referencia or today_sp()
    state_path = Path(args.state_file or REPO_ROOT / "state" / f"atrasadas_{args.project_id}.json")
    app_url = os.environ.get("ARTIA_APP_URL", "https://app2.artia.com").rstrip("/")
    project_url = f"{app_url}/a/{args.account_id}/p/{args.project_id}"

    project_name, activities = fetch(args)
    previous = load_state(state_path)
    overdue = overdue_activities(activities, ref_date, args.project_id)
    mark_new(overdue, baseline_ids(previous, ref_date), ref_date)
    message = build_message_within_limit(project_name, ref_date, overdue, project_url)

    total_new = sum(1 for i in overdue if i["nova"])
    print(
        f"{project_name}: {len(activities)} atividades lidas, {len(overdue)} atrasadas, "
        f"{total_new} novas (referência {fmt_date(ref_date)})",
        file=sys.stderr,
    )

    if args.dry_run:
        print(json.dumps(message, ensure_ascii=False, indent=2))
        return 0

    webhook = os.environ.get("TEAMS_WEBHOOK_URL")
    if not webhook:
        sys.exit("Defina TEAMS_WEBHOOK_URL.")
    status = send_to_teams(webhook, message)
    print(f"Mensagem enviada ao Teams (HTTP {status}).", file=sys.stderr)

    # Só grava o snapshot depois do envio: se o envio falhar, a próxima
    # execução ainda compara com o último estado efetivamente notificado.
    save_state(state_path, build_state(previous, args.project_id, ref_date, overdue))
    return 0


if __name__ == "__main__":
    sys.exit(main())
