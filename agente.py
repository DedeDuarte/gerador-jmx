import argparse
import json
import os
import sys

from dotenv import load_dotenv

# Carrega variáveis do .env (ex: KILO_API_KEY) antes de qualquer outro import
load_dotenv()

# Módulo responsável por ler e transformar o .har em uma lista de requests limpa
from src import har_parser


def main() -> None:
    # Define os argumentos aceitos pelo script na linha de comando
    parser = argparse.ArgumentParser(description="Parse a HAR file for JMeter generation.")
    parser.add_argument("har", nargs="?", default="test/renner_full.har", help="Path to the .har file")

    # Por padrão filtra tudo — essas flags abrem o filtro quando necessário
    parser.add_argument("--include-third-party", action="store_true", help="Include requests to third-party domains")
    parser.add_argument("--include-static", action="store_true", help="Include static assets (images, css, js, fonts, etc.)")

    # Chama a IA para analisar as requests e gerar o JSON JMeter-ready
    parser.add_argument("--analyze", action="store_true", help="Send parsed entries to GPT-4o for JMeter analysis")
    parser.add_argument("-o", "--output", help="Output file path (default: reports/resultado.json com --analyze, reports/resultado.jmx com --to-jmx)")

    # Objetivo em linguagem natural: guia a IA a manter só as requests relevantes
    parser.add_argument("-g", "--goal", help="Describe what you want to test (e.g. 'login e validação de perfil'). Filters out unrelated requests.")

    # Gera um plano JMeter (.jmx) a partir de um JSON já produzido por --analyze
    parser.add_argument("--to-jmx", metavar="INPUT_JSON", help="Path to a resultado.json (from --analyze) to convert into a JMeter .jmx plan")

    # Encadeia a geração do .jmx logo após o --analyze, sem precisar rodar --to-jmx separado
    parser.add_argument("--jmx", action="store_true", help="Usado com --analyze: gera o .jmx automaticamente após salvar o JSON")
    parser.add_argument("--jmx-output", help="Caminho do .jmx gerado com --jmx (default: mesmo nome de -o/--output com extensão .jmx)")

    parser.add_argument("--threads", type=int, default=1, help="Thread Group: número de usuários virtuais (default: 1)")
    parser.add_argument("--loops", type=int, default=1, help="Thread Group: número de loops por usuário (default: 1)")
    parser.add_argument("--ramp-up", type=int, default=1, help="Thread Group: ramp-up em segundos (default: 1)")

    args = parser.parse_args()

    if args.to_jmx and args.analyze:
        parser.error("--to-jmx não pode ser usado junto com --analyze.")

    if args.jmx and not args.analyze:
        parser.error("--jmx só pode ser usado junto com --analyze.")

    # Defaults de saída centralizados em reports/ (ver reorganização de pastas).
    if args.analyze and not args.output:
        args.output = "reports/resultado.json"

    if args.to_jmx and not args.output:
        args.output = "reports/resultado.jmx"

    if args.to_jmx:
        with open(args.to_jmx, encoding="utf-8") as f:
            data = json.load(f)

        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

        from src import jmx_generator

        jmx_generator.generate(
            data,
            args.output,
            threads=args.threads,
            loops=args.loops,
            ramp_up=args.ramp_up,
        )
        print(f"Plano JMeter salvo em {args.output}", file=sys.stderr)
        return

    if args.analyze:
        # Faz o parse do .har já obtendo host/protocol/port na mesma leitura
        # (evita abrir e parsear o arquivo duas vezes).
        entries, site_info = har_parser.parse_with_site_info(
            args.har,
            include_third_party=args.include_third_party,
            include_static=args.include_static,
        )

        # Importa aqui para não exigir OPENAI_API_KEY em execuções sem --analyze
        from src import ai_analyzer

        goal_msg = f" (objetivo: {args.goal})" if args.goal else ""
        print(f"Analisando {len(entries)} requests via DRYVN Gateway (Claude 4.6){goal_msg}...", file=sys.stderr)
        analysis = ai_analyzer.analyze(entries, goal=args.goal)
        requests_result = analysis["requests"]

        # Host/protocol/port derivados diretamente do .har (não pedimos pra IA
        # inferir, pra evitar alucinação).
        result = {
            "host": site_info["host"],
            "protocol": site_info["protocol"],
            "port": site_info["port"],
            "auth": analysis["auth"],
            # Metadados com os valores usados para preencher as variáveis do
            # fluxo (login, senha, email, IDs, etc.), capturados diretamente
            # do .har. Campos marcados "sensitive": true são dados pessoais
            # (não são mascarados: refletem exatamente o que foi usado ao
            # gravar o fluxo, para reproduzi-lo fielmente). Esses mesmos
            # valores são repassados para a linha de dados do .csv final
            # (ver src/jmx_generator.py).
            "test_data_metadata": {
                "description": (
                    "Valores de teste para variaveis do CSV, capturados do "
                    ".har. Campos sensitive=true sao dados pessoais (login, "
                    "senha, email, etc.) - marcados apenas para sinalizacao, "
                    "sem alteracao do valor real usado no fluxo."
                ),
                "variables": analysis["test_data"],
            },
            "requests": requests_result,
        }

        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"Salvo em {args.output} ({len(requests_result)} requests)", file=sys.stderr)

        if args.jmx:
            jmx_output = args.jmx_output or f"{os.path.splitext(args.output)[0]}.jmx"

            from src import jmx_generator

            jmx_generator.generate(
                result,
                jmx_output,
                threads=args.threads,
                loops=args.loops,
                ramp_up=args.ramp_up,
            )
            print(f"Plano JMeter salvo em {jmx_output}", file=sys.stderr)
    else:
        # Faz o parse do .har aplicando os filtros definidos
        entries = har_parser.parse(
            args.har,
            include_third_party=args.include_third_party,
            include_static=args.include_static,
        )

        # Comportamento padrão: imprime o JSON do parse no stdout
        # (escreve direto no buffer para evitar erro de encoding no terminal Windows)
        out = json.dumps(entries, ensure_ascii=False, indent=2)
        sys.stdout.buffer.write(out.encode("utf-8"))
        sys.stdout.buffer.write(b"\n")


if __name__ == "__main__":
    main()
