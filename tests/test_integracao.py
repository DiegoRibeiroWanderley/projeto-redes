"""Testes de ponta a ponta no loopback, sem tc netem.

Cobrem os critérios de aceitação de RF01, RF02, RF03, RF05, RF09 e RF10:
servidores atendendo dez execuções seguidas sem reinício, arquivo íntegro em
rede estável e perda simulada no código (1 bloco a cada 10) com métricas
batendo com o valor esperado.
"""

import csv
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

import cliente  # noqa: E402
from arquivo_teste import ArquivoTeste  # noqa: E402
from metricas import MB  # noqa: E402

EXECUCOES = 10


def porta_livre(tipo):
    """Pede ao sistema uma porta livre (bind na porta 0) para os servidores de teste."""
    with socket.socket(socket.AF_INET, tipo) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestPontaAPonta(unittest.TestCase):
    """Os servidores rodam em processos separados, como no uso real: no mesmo
    processo o GIL do Python faz o receptor UDP perder pacotes."""

    @classmethod
    def setUpClass(cls):
        """Gera um arquivo de 10 MB temporário e sobe os dois servidores em processos próprios."""
        cls.dir = tempfile.TemporaryDirectory()
        caminho_arq = Path(cls.dir.name) / "arquivo.bin"
        caminho_arq.write_bytes(os.urandom(10 * MB))
        cls.arq = ArquivoTeste.carregar(caminho_arq)
        cls.csv = Path(cls.dir.name) / "resultados.csv"
        cls.portas = {"tcp": porta_livre(socket.SOCK_STREAM), "udp": porta_livre(socket.SOCK_DGRAM)}
        cls.servidores = [
            subprocess.Popen([sys.executable, str(RAIZ / "src" / f"servidor_{p}.py"), "--host", "127.0.0.1",
                              "--porta", str(porta), "--arquivo", str(caminho_arq)],
                             stderr=subprocess.DEVNULL)
            for p, porta in cls.portas.items()
        ]
        time.sleep(1.0)  # tempo para carregar o arquivo e abrir os sockets

    @classmethod
    def tearDownClass(cls):
        """Encerra os servidores e apaga os arquivos temporários."""
        for s in cls.servidores:
            s.terminate()
            s.wait(timeout=5)
        cls.dir.cleanup()

    def rodar(self, protocolo, *extras, repeticao=1):
        """Executa o cliente no cenário de arquivo, como se fosse pela linha de comando."""
        porta = self.portas[protocolo]
        argv = ["--protocolo", protocolo, "--cenario", "arquivo", "--porta", str(porta),
                "--repeticao", str(repeticao), "--saida", str(self.csv), *extras]
        args = cliente.criar_parser().parse_args(argv)
        return cliente.executar(args)

    def test_dez_execucoes_seguidas_sem_reinicio(self):
        """RF01/RF02/RF05: dez execuções seguidas de cada servidor, todas íntegras e sem perda."""
        for protocolo in ("tcp", "udp"):
            for i in range(1, EXECUCOES + 1):
                with self.subTest(protocolo=protocolo, execucao=i):
                    res = self.rodar(protocolo, repeticao=i)
                    self.assertEqual(res.integro, "sim")
                    self.assertEqual(res.perda_pct, 0.0)
                    self.assertGreater(res.vazao_mbs, 0)

    def test_perda_simulada_um_a_cada_dez(self):
        """RF09: descartando 1 bloco a cada 10, a perda tem de ser 10% e a vazão bater com a conta."""
        for protocolo in ("tcp", "udp"):
            with self.subTest(protocolo=protocolo):
                res = self.rodar(protocolo, "--descartar-a-cada", "10")
                self.assertAlmostEqual(res.perda_pct, 10.0)  # 1024 de 10240 blocos
                self.assertEqual(res.integro, "nao")
                esperado = (self.arq.num_blocos - self.arq.num_blocos // 10) * 1024 / MB / res.tempo_s
                self.assertAlmostEqual(res.vazao_mbs, esperado, places=6)

    def test_servidor_ausente_vira_falha_sem_travar(self):
        """RNF06: sem servidor, a execução vira 'falha' em vez de travar ou quebrar."""
        args = cliente.criar_parser().parse_args(
            ["--protocolo", "tcp", "--cenario", "arquivo", "--porta", "1"])
        self.assertEqual(cliente.executar(args).integro, "falha")

    def test_linha_de_comando(self):
        """RF03/RF10: parâmetro inválido mostra o uso; execução real pela linha de comando grava o CSV."""
        # parâmetro inválido -> mensagem de uso e código 2
        r = subprocess.run([sys.executable, str(RAIZ / "src" / "cliente.py"), "--protocolo", "sctp",
                            "--cenario", "arquivo"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage:", r.stderr)
        # execução real pela linha de comando grava no CSV
        saida = Path(self.dir.name) / "cli.csv"
        r = subprocess.run([sys.executable, str(RAIZ / "src" / "cliente.py"), "--protocolo", "udp",
                            "--cenario", "arquivo", "--host", "127.0.0.1", "--porta", str(self.portas["udp"]),
                            "--saida", str(saida)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        with saida.open(newline="") as f:
            linhas = list(csv.DictReader(f))
        self.assertEqual(linhas[0]["integro"], "sim")


if __name__ == "__main__":
    unittest.main()
