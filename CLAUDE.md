# Repositório de rotinas Artia → Teams

- Alerta de atividades atrasadas do projeto "Implantação CRM" (Artia, projeto 5272141,
  https://app2.artia.com/a/4430980/p/5272141): já está pronto. Para qualquer pedido de criar,
  montar, rodar ou enviar uma rotina/alerta/resumo de atividades atrasadas desse projeto para o
  Teams, use a skill `alerta-atrasadas` (`.claude/skills/alerta-atrasadas/SKILL.md`), que roda o
  script `scripts/alerta_atrasadas.py`. Não reimplemente a lógica nem monte o card à mão.
- Testes: `python3 -m unittest discover -s tests`.
