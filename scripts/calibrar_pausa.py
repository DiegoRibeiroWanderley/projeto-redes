"""Calibra a pausa entre blocos do UDP no cenário de arquivo (RNF03).

Com o servidor UDP já rodando e a rede SEM regras netem, roda o cliente várias
vezes para cada pausa e mostra a perda. Use a menor pausa com perda 0% em
todas as repetições, com alguma folga, como valor de --pausa-us.

    python src/servidor_udp.py &
    python scripts/calibrar_pausa.py --pausas 0 10 20 30 50 --repeticoes 10
"""

import argparse
import csv
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def main() -> None:
    """Roda o cliente UDP para cada pausa e imprime a tabela de perda e tempo médio."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pausas", type=float, nargs="+", default=[0, 10, 20, 30, 50, 100])
    p.add_argument("--repeticoes", type=int, default=10)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--porta", type=int, default=5001)
    args = p.parse_args()

    with tempfile.TemporaryDirectory() as d:
        saida = Path(d) / "calibracao.csv"
        print(f"{'pausa (µs)':>10}  {'perda média':>11}  {'perda máx.':>10}  {'tempo médio':>11}")
        for pausa in args.pausas:
            for i in range(1, args.repeticoes + 1):
                subprocess.run(
                    [sys.executable, str(RAIZ / "src" / "cliente.py"), "--protocolo", "udp",
                     "--cenario", "arquivo", "--host", args.host, "--porta", str(args.porta),
                     "--pausa-us", str(pausa), "--condicao", f"pausa{pausa:g}",
                     "--repeticao", str(i), "--saida", str(saida)],
                    stdout=subprocess.DEVNULL, check=False,
                )
            with saida.open(newline="") as f:
                linhas = [r for r in csv.DictReader(f) if r["condicao"] == f"pausa{pausa:g}"]
            ok = [r for r in linhas if r["integro"] != "falha"]
            if not ok:
                print(f"{pausa:>10g}  todas as execuções falharam (o servidor está rodando?)")
                continue
            perdas = [float(r["perda_pct"]) for r in ok]
            tempos = [float(r["tempo_s"]) for r in ok]
            print(f"{pausa:>10g}  {statistics.mean(perdas):>10.2f}%  {max(perdas):>9.2f}%  "
                  f"{statistics.mean(tempos):>10.3f}s")


if __name__ == "__main__":
    main()
