"""Monta a árvore do Thread Group conforme as boas práticas.

- etapa com um só passo (x.0)  → requisição solta
- etapa com vários passos      → Simple Controller "N - Nome"
- etapas dentro de um grupo    → Simple Controller agrupado "de-ate - Nome" envolvendo as etapas
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harjmx.jmx import naming
from harjmx.plan.schema import Plan


@dataclass
class Controller:
    nome: str
    filhos: list[Any] = field(default_factory=list)   # Controller | sampler


def arrange(plan: Plan, samplers: dict[int, Any]) -> list[Any]:
    """`samplers`: {har_id: sampler}. Retorna a lista de nós de topo, na ordem do plano."""
    por_etapa: dict[int, list] = {}
    for r in plan.ordered():
        por_etapa.setdefault(r.etapa, []).append(samplers[r.har_id])
    grupo_de = {n: g for g in plan.grupos for n in range(g.de, g.ate + 1)}

    top: list[Any] = []
    abertos: dict[int, Controller] = {}
    for etapa in sorted(plan.etapas, key=lambda e: e.numero):
        reqs = por_etapa.get(etapa.numero, [])
        nos = reqs if len(reqs) == 1 else [Controller(naming.controller(etapa.numero, etapa.nome), reqs)]
        g = grupo_de.get(etapa.numero)
        if g is None:
            top += nos
            continue
        if g.de not in abertos:
            abertos[g.de] = Controller(naming.group(g.de, g.ate, g.nome))
            top.append(abertos[g.de])
        abertos[g.de].filhos += nos
    return top
