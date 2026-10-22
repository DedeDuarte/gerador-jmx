"""ETAPA 2 — O plano.

Contrato do plan.json (o que o Claude Code escreve) e a validação dele contra o HAR.
O plano só diz **quais requisições** formam cada passo da jornada, na ordem pedida:

    {"nome_teste": "renner_sacola",
     "etapas": [{"nome": "Ver Jaquetas", "requisicoes": [50]},
                {"nome": "Adicionar à Sacola", "requisicoes": [186]}]}

Todo o resto sai do HAR, sem a IA: correlações, dependências, login, massa de CSV e
asserções (ver correlations.py e jmx_builder.py).

`validate(raw, kept, excluded)` → `Resultado` com erros, avisos e a `Resolucao`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harjmx import correlations
from harjmx.correlations import Resolucao
from harjmx.har_reader import Entry

# --- Contrato do plan.json ----------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Etapa(_Strict):
    nome: str = Field(min_length=1, description="Passo da jornada: 'Ver Jaquetas', 'Adicionar à Sacola'")
    requisicoes: list[int] = Field(min_length=1, description="IDs do catálogo, na ordem")


class Plan(_Strict):
    nome_teste: str = Field(pattern=r"^[A-Za-z0-9_\-]+$")
    etapas: list[Etapa] = Field(min_length=1)


# --- Validação ----------------------------------------------------------------------------
# As mensagens dizem o que corrigir, para o agente conseguir ajustar o plano sozinho.

_SIDE_EFFECT = re.compile(r"add|new|create|claim|apply|insert|save|register|cadastr|adicion|novo", re.I)


@dataclass
class Resultado:
    plan: Plan | None = None
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    resolucao: Resolucao | None = None

    @property
    def ok(self) -> bool:
        return not self.erros

    def report(self, by_id: dict[int, Entry]) -> str:
        """Relatório para o agente repassar ao usuário."""
        lines = [f"ERRO: {e}" for e in self.erros]
        if not self.ok:
            return "\n".join(lines)
        r = self.resolucao
        seq = r.sequencia()
        auto = r.automaticas()
        corr = [v for v in r.variaveis if v.tipo == "extractor"]
        csv = [v for v in r.variaveis if v.tipo == "csv"]
        lines.append(f"OK: {len(seq)} requisições ({len(seq) - len(auto)} pedidas + {len(auto)} automáticas), "
                     f"{len(corr)} correlações, CSV: {', '.join(v.nome for v in csv) or 'nenhum'}")
        if auto:
            lines.append("Automáticas:")
            lines += [f"  #{p.har_id} {by_id[p.har_id].method} {by_id[p.har_id].path} — {p.motivo}" for p in auto]
        if corr:
            lines.append("Correlações:")
            lines += [f"  {v.nome} ← #{v.origem_har_id} {v.extractor.tipo} `{_short(v.extractor.expressao)}`"
                      for v in corr]
        riscos = list(self.avisos)
        riscos += [f"valor fixo em #{f.har_id} {f.local} ({f.motivo})" for f in r.fixos[:10]]
        if len(r.fixos) > 10:
            riscos.append(f"... e mais {len(r.fixos) - 10} valores fixos")
        riscos += [f"#{p.har_id} {by_id[p.har_id].method} {by_id[p.har_id].path} muda estado a cada "
                   "iteração (sacola, endereço, cupom acumulam)"
                   for p in seq if by_id[p.har_id].method != "GET" and _SIDE_EFFECT.search(by_id[p.har_id].path)]
        if riscos:
            lines.append("Riscos:")
            lines += [f"  - {x}" for x in riscos]
        return "\n".join(lines)


def _short(expr: str, limit: int = 70) -> str:
    return expr if len(expr) <= limit else expr[:30] + "…" + expr[-(limit - 31):]


def load_plan(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(raw: dict, kept: list[Entry], excluded: dict[int, str]) -> Resultado:
    res = Resultado()
    try:
        plan = Plan.model_validate(raw)
    except ValidationError as err:
        res.erros = _schema_errors(err)
        return res
    res.plan = plan
    by_id = {e.id: e for e in kept}
    seen: set[int] = set()
    for et in plan.etapas:
        for har_id in et.requisicoes:
            if har_id in seen:
                res.erros.append(f"requisição #{har_id} aparece duas vezes no plano")
            seen.add(har_id)
            if har_id not in by_id:
                why = excluded.get(har_id)
                hint = {"terceiro": " (rode o prepare com --host para incluir o domínio)",
                        "estatico": " (rode o prepare com --include-static)"}.get(why or "", "")
                res.erros.append(f"etapa '{et.nome}': #{har_id} não está no catálogo"
                                 + (f": excluída como '{why}'{hint}" if why else ": id inexistente"))
    if res.erros:
        return res
    pedidos = [i for et in plan.etapas for i in et.requisicoes]
    if pedidos != sorted(pedidos):
        res.avisos.append("a ordem do plano difere da do HAR; confira se os valores dinâmicos ainda têm origem")
    res.resolucao = correlations.resolve([(et.nome, et.requisicoes) for et in plan.etapas], kept)
    return res


def _schema_errors(err: ValidationError) -> list[str]:
    out = []
    for e in err.errors():
        loc = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e["loc"]).lstrip(".")
        out.append(f"{loc or 'plano'}: {e['msg']}")
    return out
