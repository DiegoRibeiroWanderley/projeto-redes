"""Critérios de aceitação do RF09 (métricas) e RF10 (CSV)."""

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from metricas import (  # noqa: E402
    COLUNAS,
    MB,
    Resultado,
    gravar_csv,
    jitter_ms,
    latencia_media_ms,
    perda_pct,
    vazao_mbs,
)


class TestFormulas(unittest.TestCase):
    """Fórmulas do RF09 com valores calculados à mão."""

    def test_vazao(self):
        """10 MB em 2 s dão 5 MB/s."""
        self.assertAlmostEqual(vazao_mbs(10 * MB, 2.0), 5.0)

    def test_latencia_e_jitter(self):
        """Latência é a média dos RTTs; jitter, a média das diferenças entre RTTs vizinhos."""
        rtts = [0.010, 0.012, 0.009, 0.013]  # segundos
        self.assertAlmostEqual(latencia_media_ms(rtts), 11.0)
        self.assertAlmostEqual(jitter_ms(rtts), (2 + 3 + 4) / 3)
        self.assertIsNone(latencia_media_ms([]))
        self.assertIsNone(jitter_ms([0.01]))

    def test_perda(self):
        """1024 blocos perdidos de 10240 enviados dão 10%."""
        self.assertAlmostEqual(perda_pct(10240, 9216), 10.0)
        self.assertEqual(perda_pct(500, 500), 0.0)


class TestCSV(unittest.TestCase):
    """Registro em CSV (RF10)."""

    def test_cria_com_cabecalho_uma_vez_e_acrescenta(self):
        """O cabeçalho é escrito só na criação; execuções seguintes acrescentam linhas."""
        with tempfile.TemporaryDirectory() as d:
            caminho = Path(d) / "sub" / "resultados.csv"
            gravar_csv(caminho, Resultado("tcp", "arquivo", "estavel", 1, tempo_s=1.5, integro="sim"))
            gravar_csv(caminho, Resultado("udp", "arquivo", "perda5", 2, perda_pct=5.0, integro="nao"))
            with caminho.open(newline="", encoding="utf-8") as f:
                linhas = list(csv.reader(f))
            self.assertEqual(linhas[0], COLUNAS)
            self.assertEqual(len(linhas), 3)
            primeira = dict(zip(COLUNAS, linhas[1]))
            self.assertEqual(primeira["protocolo"], "tcp")
            self.assertEqual(float(primeira["tempo_s"]), 1.5)
            self.assertEqual(primeira["jitter_ms"], "")  # não se aplica -> vazio (NaN no pandas)

    def test_abre_no_pandas_sem_ajuste(self):
        """Critério do RF10: o pandas lê o CSV sem ajuste (pulado se não estiver instalado)."""
        try:
            import pandas as pd
        except ImportError:
            self.skipTest("pandas não instalado")
        with tempfile.TemporaryDirectory() as d:
            caminho = Path(d) / "r.csv"
            gravar_csv(caminho, Resultado("tcp", "arquivo", "estavel", 1, tempo_s=0.5, vazao_mbs=20.0))
            df = pd.read_csv(caminho)
            self.assertEqual(list(df.columns), COLUNAS)
            self.assertEqual(df["vazao_mbs"].dtype.kind, "f")


if __name__ == "__main__":
    unittest.main()
