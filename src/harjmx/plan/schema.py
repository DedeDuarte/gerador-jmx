"""Contrato do plan.json: o que o Claude Code escreve e o Python executa.

A IA referencia requisições pelo `har_id` do catálogo. Headers, bodies e valores reais
nunca passam pelo plano: o build busca tudo no HAR.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

VarName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_\-]*$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Etapa(_Strict):
    numero: int = Field(ge=1)
    nome: str = Field(min_length=1, description="Substantivo curto: 'Login', 'Sacola'")


class Grupo(_Strict):
    """Simple Controller agrupado: etapas `de` até `ate` viram '<de>-<ate> - <nome>'."""
    de: int = Field(ge=1)
    ate: int = Field(ge=1)
    nome: str = Field(min_length=1)


class Extractor(_Strict):
    variavel: VarName
    tipo: Literal["json", "regex"]
    expressao: str = Field(min_length=1)
    campo: Literal["body", "headers"] = "body"
    match: int = Field(1, ge=0, description="1 = primeira ocorrência; 0 = aleatória (JMeter)")
    valor_de: str | None = Field(None, pattern=r"^\d+:\S+$", description=(
        "'<har_id>:<local>' de uma requisição que usa o valor. Obrigatório quando a resposta "
        "não foi gravada no HAR; é o valor que será trocado por ${variavel}."))


class Assertion(_Strict):
    tipo: Literal["status", "texto", "json_existe"]
    esperado: str = Field(min_length=1, description="status: '200'; texto: trecho; json_existe: JSONPath")


class Requisicao(_Strict):
    har_id: int
    etapa: int = Field(ge=1)
    passo: int = Field(ge=0)
    descricao: str = Field(min_length=1, description="Verbo no infinitivo: 'Autenticar Usuário'")
    endpoint_nome: str | None = Field(None, description="Path sem query, variáveis como {idProduto}")
    extractors: list[Extractor] = []
    assertions: list[Assertion] = []
    seguir_redirect: bool | None = None


class Correlacao(_Strict):
    """Aplica um candidato do catálogo (C1, C2...): extractor na origem + ${var} nos usos."""
    id: str = Field(pattern=r"^C\d+$")
    variavel: VarName | None = None
    aleatorio: bool = Field(False, description="Escolhe um item aleatório da lista: índices viram [*], match 0")
    substituir_em: list[int] | None = None


class Parametro(_Strict):
    """Valor de entrada (login, senha, produto) que vira variável de CSV ou User Defined Variables.

    Indique de onde ler o valor real: `har_id` + `local` (mesmo formato dos usos no catálogo:
    query.<k>, form.<k>, header.<k>, body.<jsonpath>, path.<n>) ou o próprio `valor`.
    """
    variavel: VarName
    destino: Literal["csv", "global"] = "csv"
    har_id: int | None = None
    local: str | None = None
    valor: str | None = None
    substituir_em: list[int] | None = None

    @model_validator(mode="after")
    def _source(self):
        has_loc = self.har_id is not None and self.local is not None
        if has_loc == (self.valor is not None):
            raise ValueError(f"parametro '{self.variavel}': informe har_id+local OU valor")
        return self


class Plan(_Strict):
    nome_teste: str = Field(pattern=r"^[A-Za-z0-9_\-]+$")
    etapas: list[Etapa]
    grupos: list[Grupo] = []
    requisicoes: list[Requisicao] = Field(min_length=1)
    correlacoes: list[Correlacao] = []
    parametros: list[Parametro] = []
    csv_arquivo: str = "massa.csv"

    def ordered(self) -> list[Requisicao]:
        return sorted(self.requisicoes, key=lambda r: (r.etapa, r.passo))
