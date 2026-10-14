import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import alerta_atrasadas as aa  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "projeto_5272141.json"
PROJECT_ID = 5272141
REF = date(2026, 10, 14)
URL = "https://app2.artia.com/a/4430980/p/5272141"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["atividades"]


def act(id_, end, status=False, parent=("100", "Pasta"), responsible="Fulano", pct=0):
    return {
        "id": id_,
        "title": f"Atividade {id_}",
        "status": status,
        "estimatedEnd": end,
        "completedPercent": pct,
        "responsible": {"id": "1", "name": responsible} if responsible else None,
        "parent": {"id": parent[0], "name": parent[1]},
    }


class OverdueTest(unittest.TestCase):
    def test_fixture_real(self):
        overdue = aa.overdue_activities(load_fixture(), REF, PROJECT_ID)
        self.assertEqual(len(overdue), 10)
        by_id = {i["id"]: i for i in overdue}
        item = by_id["27650145"]
        self.assertEqual(item["dias_atraso"], 56)
        self.assertEqual(item["etapa"], "02-Configuração do CRM")
        self.assertEqual(item["responsavel"], "Igor de Paula Quirino")
        self.assertNotIn("27650146", by_id)  # encerrada

    def test_regras(self):
        acts = [
            act("1", "2026-10-13"),
            act("2", "2026-10-14"),  # vence hoje: não está atrasada
            act("3", "2026-10-01", status=True),  # encerrada
            act("4", None),  # sem término estimado
            act("5", "2026-10-10", parent=(str(PROJECT_ID), "Projeto"), responsible=None),
            act("6", "2026-10-10T00:00:00-03:00"),
        ]
        overdue = {i["id"]: i for i in aa.overdue_activities(acts, REF, PROJECT_ID)}
        self.assertEqual(sorted(overdue), ["1", "5", "6"])
        self.assertEqual(overdue["1"]["dias_atraso"], 1)
        self.assertEqual(overdue["5"]["etapa"], "—")
        self.assertEqual(overdue["5"]["responsavel"], "Sem responsável")
        self.assertEqual(overdue["6"]["dias_atraso"], 4)


class NewTest(unittest.TestCase):
    def setUp(self):
        self.overdue = aa.overdue_activities([act("1", "2026-10-13"), act("2", "2026-10-01")], REF, PROJECT_ID)

    def test_primeira_execucao_usa_termino_de_ontem(self):
        aa.mark_new(self.overdue, aa.baseline_ids(None, REF), REF)
        self.assertEqual({i["id"]: i["nova"] for i in self.overdue}, {"1": True, "2": False})

    def test_compara_com_snapshot_anterior(self):
        state = {"data": "2026-10-13", "atrasadas": ["1"]}
        aa.mark_new(self.overdue, aa.baseline_ids(state, REF), REF)
        self.assertEqual({i["id"]: i["nova"] for i in self.overdue}, {"1": False, "2": True})

    def test_reexecucao_no_mesmo_dia_mantem_as_novas(self):
        first = aa.build_state({"data": "2026-10-13", "atrasadas": ["2"]}, PROJECT_ID, REF, self.overdue)
        self.assertEqual(first["execucao_anterior"], {"data": "2026-10-13", "atrasadas": ["2"]})
        again = aa.build_state(first, PROJECT_ID, REF, self.overdue)
        self.assertEqual(again["execucao_anterior"], first["execucao_anterior"])
        aa.mark_new(self.overdue, aa.baseline_ids(first, REF), REF)
        self.assertEqual({i["id"]: i["nova"] for i in self.overdue}, {"1": True, "2": False})


class CardTest(unittest.TestCase):
    def test_card_completo(self):
        overdue = aa.overdue_activities(load_fixture(), REF, PROJECT_ID)
        aa.mark_new(overdue, {"27650145"}, REF)
        msg = aa.build_message_within_limit("Implantação CRM", REF, overdue, URL)
        self.assertEqual(msg["type"], "message")
        att = msg["attachments"][0]
        self.assertEqual(att["contentType"], "application/vnd.microsoft.card.adaptive")
        card = att["content"]
        self.assertEqual(card["version"], "1.4")
        body = card["body"]
        self.assertEqual(body[0]["text"], "Implantação CRM")
        self.assertEqual(body[1]["text"], "Posição em 14/10/2026")
        totals = [c["items"][0]["text"] for c in body[2]["columns"]]
        self.assertEqual(totals, ["10", "9"])
        facts = body[4]["facts"]
        counts = [int(f["value"]) for f in facts]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(facts[0], {"title": "Karol", "value": "4"})
        # Ordenação por dias de atraso dentro do responsável.
        igor = next(b["items"] for b in body if b["type"] == "Container" and "Igor" in b["items"][0]["text"])
        self.assertEqual(igor[0]["text"], "👤 Igor de Paula Quirino (3)")
        titles = [c["items"][0]["text"] for c in igor[1:]]
        self.assertEqual(titles[0], "Configurar campos personalizados, layouts de tela e permissões de usuário.")
        self.assertTrue(titles[-1].startswith("🆕 "))
        self.assertIn(URL, body[-1]["text"])
        self.assertIn("56 dias de atraso", igor[1]["items"][1]["text"])

    def test_sem_atrasos(self):
        msg = aa.build_message_within_limit("Projeto X", REF, [], URL)
        texts = [b["text"] for b in msg["attachments"][0]["content"]["body"]]
        self.assertIn("✅ O projeto está sem atividades atrasadas.", texts)
        self.assertIn(URL, texts[-1])

    def test_card_grande_respeita_limite(self):
        acts = [act(str(i), "2026-09-01", responsible=f"Pessoa {i % 3}") for i in range(400)]
        overdue = aa.mark_new(aa.overdue_activities(acts, REF, PROJECT_ID), set(), REF)
        msg = aa.build_message_within_limit("Projeto", REF, overdue, URL)
        self.assertLessEqual(len(json.dumps(msg, ensure_ascii=False).encode()), aa.MAX_CARD_BYTES)
        self.assertIn("400", json.dumps(msg))

    def test_formatos(self):
        self.assertEqual(aa.fmt_percent(40), "40%")
        self.assertEqual(aa.fmt_percent(73.3333), "73,3%")
        self.assertEqual(aa.fmt_days(1), "1 dia")


class FakeClient:
    """Árvore: projeto 1 -> pastas 2, 3; pasta 2 -> subpasta 4."""

    tree = {"1": ["2", "3"], "2": ["4"], "3": [], "4": []}

    def __init__(self):
        self.activity_calls = []

    def child_folders(self, account_id, parent_ids):
        return [{"id": c} for p in parent_ids for c in self.tree[str(p)]]

    def folder_activities(self, account_id, folder_id):
        self.activity_calls.append(folder_id)
        return [act(f"a{folder_id}", "2026-10-01", parent=(folder_id, f"Pasta {folder_id}"))]


class CollectTest(unittest.TestCase):
    def test_busca_recursiva(self):
        client = FakeClient()
        acts = aa.collect_activities(client, 99, 1)
        self.assertEqual(sorted(client.activity_calls), ["1", "2", "3", "4"])
        self.assertEqual(len(acts), 4)

    def test_paginacao(self):
        client = aa.ArtiaClient("id", "secret")
        pages = {1: ["a", "b"], 2: ["c"]}
        client._post = lambda q, v: {"f": {"totalPages": 2, "currentPage": v["page"], "items": pages[v["page"]]}}
        self.assertEqual(client._paginate("q", {}, "f", "items"), ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
