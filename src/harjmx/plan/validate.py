"""Confere o plan.json contra o HAR e resolve as variáveis (nome, valor real, origem).

As mensagens dizem o que corrigir, para o agente conseguir ajustar o plano sozinho.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from harjmx import jsonpath
from harjmx.correlation import Candidato, field_value
from harjmx.models import Entry
from harjmx.plan.schema import Extractor, Plan


@dataclass
class Variavel:
    nome: str
    valor: str
    tipo: Literal["extractor", "csv", "global"]
    origem_har_id: int | None = None          # requisição cujo extractor define a variável
    extractor: Extractor | None = None
    substituir_em: set[int] | None = None

    def em_escopo(self, har_id: int, pos: dict[int, int]) -> bool:
        """A variável pode substituir valores na requisição `har_id`?"""
        if self.substituir_em is not None and har_id not in self.substituir_em:
            return False
        return self.origem_har_id is None or pos[self.origem_har_id] < pos[har_id]


@dataclass
class Resultado:
    plan: Plan | None = None
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    variaveis: list[Variavel] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.erros

    def report(self) -> str:
        lines = [f"ERRO: {e}" for e in self.erros] + [f"AVISO: {a}" for a in self.avisos]
        if self.ok:
            lines.append(f"OK: {len(self.plan.requisicoes)} requisições, "
                         f"{len(self.variaveis)} variáveis.")
        return "\n".join(lines)


def response_headers_text(e: Entry) -> str:
    return "\n".join(f"{k}: {v}" for k, v in e.response.headers)


def run_extractor(ex: Extractor, e: Entry) -> str | None:
    """Executa o extractor na resposta gravada no HAR. ValueError quando não dá para avaliar."""
    idx = max(ex.match, 1) - 1                  # match 0 (aleatório): confere a 1ª ocorrência
    if ex.tipo == "json":
        if not e.response.is_json:
            raise ValueError(f"a resposta de #{e.id} não é JSON ({e.response.mime or 'vazia'})")
        found = jsonpath.find(e.response.json, ex.expressao)
        return jsonpath.as_text(found[idx]) if len(found) > idx else None
    text = response_headers_text(e) if ex.campo == "headers" else (e.response.text or "")
    try:
        matches = list(re.finditer(ex.expressao, text))
    except re.error as err:
        raise ValueError(f"regex inválida: {err}") from err
    if len(matches) <= idx:
        return None
    m = matches[idx]
    return m.group(1) if m.groups() else m.group(0)


def _schema_errors(err: ValidationError) -> list[str]:
    out = []
    for e in err.errors():
        loc = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e["loc"]).lstrip(".")
        out.append(f"{loc or 'plano'}: {e['msg']}")
    return out


def load_plan(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(raw: dict, kept: list[Entry], excluded: dict[int, str],
             correlations: list[Candidato]) -> Resultado:
    res = Resultado()
    try:
        plan = Plan.model_validate(raw)
    except ValidationError as err:
        res.erros = _schema_errors(err)
        return res
    res.plan = plan
    by_id = {e.id: e for e in kept}
    _check_structure(plan, res)
    _check_requests(plan, by_id, excluded, res)
    if res.erros:
        return res                          # sem requisições válidas não dá para resolver variáveis
    pos = {r.har_id: i for i, r in enumerate(plan.ordered())}
    _resolve_correlations(plan, correlations, pos, res)
    _resolve_extractors(plan, by_id, res)
    _resolve_parametros(plan, by_id, pos, res)
    _check_names(res)
    _check_usage(plan, by_id, pos, res)
    return res


def _check_structure(plan: Plan, res: Resultado) -> None:
    numeros = [e.numero for e in plan.etapas]
    if len(set(numeros)) != len(numeros):
        res.erros.append("etapas: número de etapa repetido")
    if sorted(numeros) != list(range(1, len(numeros) + 1)):
        res.erros.append(f"etapas: os números devem ser 1..{len(numeros)} sem buracos (veio {sorted(numeros)})")
    passos: dict[int, list[int]] = {}
    for r in plan.requisicoes:
        if r.etapa not in numeros:
            res.erros.append(f"requisição #{r.har_id}: etapa {r.etapa} não existe em `etapas`")
        passos.setdefault(r.etapa, []).append(r.passo)
    for n in numeros:
        p = sorted(passos.get(n, []))
        if not p:
            res.erros.append(f"etapa {n}: nenhuma requisição")
        elif p != list(range(len(p))):
            res.erros.append(f"etapa {n}: passos devem ser 0..{len(p) - 1} sem repetição (veio {p})")
    used: set[int] = set()
    for g in plan.grupos:
        if g.de >= g.ate:
            res.erros.append(f"grupo '{g.nome}': `de` ({g.de}) deve ser menor que `ate` ({g.ate}); "
                             "uma etapa sozinha não precisa de grupo")
            continue
        span = set(range(g.de, g.ate + 1))
        if not span <= set(numeros):
            res.erros.append(f"grupo '{g.nome}': etapas {g.de}-{g.ate} não existem todas")
        if span & used:
            res.erros.append(f"grupo '{g.nome}': sobrepõe outro grupo")
        used |= span


def _check_requests(plan: Plan, by_id: dict[int, Entry], excluded: dict[int, str],
                    res: Resultado) -> None:
    seen: set[int] = set()
    for r in plan.requisicoes:
        if r.har_id in seen:
            res.erros.append(f"requisição #{r.har_id} aparece duas vezes no plano")
        seen.add(r.har_id)
        if r.har_id not in by_id:
            why = excluded.get(r.har_id)
            hint = {"terceiro": " (rode o prepare com --host para incluir o domínio)",
                    "estatico": " (rode o prepare com --include-static)"}.get(why or "", "")
            res.erros.append(f"requisição #{r.har_id} não está no catálogo"
                             + (f": excluída como '{why}'{hint}" if why else ": id inexistente"))
        if r.endpoint_nome is not None and (not r.endpoint_nome.startswith("/") or "?" in r.endpoint_nome):
            res.erros.append(f"requisição #{r.har_id}: endpoint_nome deve começar com '/' e não ter query")
        for a in r.assertions:
            if a.tipo == "status" and not a.esperado.isdigit():
                res.erros.append(f"requisição #{r.har_id}: assertion de status espera um código (ex: '200')")
    har_order = [r.har_id for r in sorted(plan.requisicoes, key=lambda r: r.har_id)]
    plan_order = [r.har_id for r in plan.ordered()]
    if har_order != plan_order:
        res.avisos.append("a ordem do plano difere da ordem do HAR: "
                          + " → ".join(f"#{i}" for i in plan_order))


def _resolve_correlations(plan: Plan, correlations: list[Candidato], pos: dict[int, int],
                          res: Resultado) -> None:
    cands = {c.id: c for c in correlations}
    for corr in plan.correlacoes:
        c = cands.get(corr.id)
        if c is None:
            res.erros.append(f"correlação {corr.id} não existe no catálogo")
            continue
        if c.origem.har_id not in pos:
            res.erros.append(f"correlação {corr.id}: a origem #{c.origem.har_id} não está no plano; "
                             "inclua a requisição ou remova a correlação")
            continue
        if not any(u.har_id in pos and pos[u.har_id] > pos[c.origem.har_id] for u in c.usos):
            res.avisos.append(f"correlação {corr.id}: nenhum uso fica depois de #{c.origem.har_id} no plano")
        nome = corr.variavel or c.variavel
        expressao = c.origem.expressao
        if corr.aleatorio and c.origem.tipo == "json":
            expressao = re.sub(r"\[\d+\]", "[*]", expressao)
        extractor = Extractor(variavel=nome, tipo=c.origem.tipo, expressao=expressao,
                              campo=c.origem.campo, match=0 if corr.aleatorio else c.origem.match)
        res.variaveis.append(Variavel(nome, c.valor, "extractor", c.origem.har_id, extractor,
                                      set(corr.substituir_em) if corr.substituir_em else None))


def _valor_de(ref: str, by_id: dict[int, Entry]) -> tuple[str | None, str | None]:
    """'<har_id>:<local>' → (valor, erro)."""
    har_id, local = ref.split(":", 1)
    e = by_id.get(int(har_id))
    if e is None:
        return None, f"valor_de: requisição #{har_id} não está no catálogo"
    try:
        valor = field_value(e, local)
    except ValueError as err:
        return None, f"valor_de: {err}"
    return (valor, None) if valor is not None else (None, f"valor_de: '{local}' não existe em #{har_id}")


def _resolve_extractors(plan: Plan, by_id: dict[int, Entry], res: Resultado) -> None:
    for r in plan.requisicoes:
        e = by_id[r.har_id]
        for ex in r.extractors:
            prefix = f"#{r.har_id} extractor '{ex.variavel}'"
            ref_valor = None
            if ex.valor_de:
                ref_valor, err = _valor_de(ex.valor_de, by_id)
                if err:
                    res.erros.append(f"{prefix}: {err}")
                    continue
            if e.response.text is None:
                if ref_valor is None:
                    res.erros.append(f"{prefix}: a resposta de #{r.har_id} não foi gravada no HAR; informe "
                                     "`valor_de: '<har_id>:<local>'` de uma requisição que usa o valor")
                    continue
                res.avisos.append(f"{prefix}: não verificado (resposta não gravada no HAR)")
                res.variaveis.append(Variavel(ex.variavel, ref_valor, "extractor", r.har_id, ex))
                continue
            try:
                valor = run_extractor(ex, e)
            except ValueError as err:
                res.erros.append(f"{prefix}: {err}")
                continue
            if valor is None:
                res.erros.append(f"{prefix}: `{ex.expressao}` não encontra nada na resposta gravada "
                                 "(use `harjmx show` para ver a resposta)")
                continue
            if ref_valor is not None and ref_valor != valor:
                res.avisos.append(f"{prefix}: o valor extraído do HAR difere de valor_de; usando valor_de")
                valor = ref_valor
            res.variaveis.append(Variavel(ex.variavel, valor, "extractor", r.har_id, ex))


def _resolve_parametros(plan: Plan, by_id: dict[int, Entry], pos: dict[int, int],
                        res: Resultado) -> None:
    for p in plan.parametros:
        valor = p.valor
        if valor is None:
            if p.har_id not in by_id:
                res.erros.append(f"parametro '{p.variavel}': requisição #{p.har_id} não está no catálogo")
                continue
            try:
                valor = field_value(by_id[p.har_id], p.local)
            except ValueError as err:
                res.erros.append(f"parametro '{p.variavel}': {err}")
                continue
            if valor is None:
                res.erros.append(f"parametro '{p.variavel}': '{p.local}' não existe em #{p.har_id} "
                                 "(formatos: query.<k>, form.<k>, header.<k>, body.<jsonpath>, path.<n>)")
                continue
        if len(valor) < 4 and not p.substituir_em:
            res.avisos.append(f"parametro '{p.variavel}': valor curto ('{valor}') pode ser trocado em "
                              "lugares errados; considere `substituir_em`")
        res.variaveis.append(Variavel(p.variavel, valor, p.destino,
                                      substituir_em=set(p.substituir_em) if p.substituir_em else None))


def _check_names(res: Resultado) -> None:
    seen: dict[str, str] = {}
    for v in res.variaveis:
        if v.nome in seen:
            res.erros.append(f"variável '{v.nome}' definida duas vezes ({seen[v.nome]} e {v.tipo}); "
                             "renomeie uma delas")
        seen[v.nome] = v.tipo
    if any(not v.valor for v in res.variaveis):
        res.erros += [f"variável '{v.nome}' tem valor vazio no HAR; não há o que substituir"
                      for v in res.variaveis if not v.valor]


def _check_usage(plan: Plan, by_id: dict[int, Entry], pos: dict[int, int], res: Resultado) -> None:
    from harjmx.jmx.hydrate import request_texts      # evita import circular

    for v in res.variaveis:
        if not v.valor:
            continue
        hits = [r.har_id for r in plan.requisicoes
                if v.em_escopo(r.har_id, pos) and any(v.valor in t for t in request_texts(by_id[r.har_id]))]
        if not hits:
            onde = f" depois de #{v.origem_har_id}" if v.origem_har_id else ""
            res.avisos.append(f"variável '{v.nome}' não é usada em nenhuma requisição{onde}")
