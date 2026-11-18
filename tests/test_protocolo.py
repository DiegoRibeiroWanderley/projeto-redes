"""Critério de aceitação do RF04 (formato das mensagens)."""

import socket
import struct
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from protocolo import (  # noqa: E402
    ACK,
    DADOS,
    DUPLICADO,
    FIM,
    FORA_DE_ORDEM,
    NOVO,
    TAM_CABECALHO,
    AnalisadorSequencia,
    desempacotar,
    empacotar,
    enviar_tcp,
    receber_tcp,
)


class TestCabecalho(unittest.TestCase):
    """Cabeçalho de 13 bytes no formato !BId (RF04)."""

    def test_cabecalho_tem_13_bytes(self):
        """Uma mensagem sem carga tem exatamente o tamanho do cabeçalho."""
        self.assertEqual(TAM_CABECALHO, 13)
        self.assertEqual(len(empacotar(ACK, 0)), 13)

    def test_ida_e_volta_recupera_os_mesmos_valores(self):
        """Empacotar e desempacotar devolve tipo, seq, horário e carga intactos."""
        for tipo, seq, carga in [(DADOS, 0, b"x" * 1024), (ACK, 2**32 - 1, b""), (FIM, 12345, b"{}")]:
            horario = time.perf_counter()
            msg = desempacotar(empacotar(tipo, seq, carga, horario))
            self.assertEqual((msg.tipo, msg.seq, msg.horario, msg.carga), (tipo, seq, horario, carga))

    def test_ordem_de_bytes_da_rede(self):
        """Os campos são gravados em big-endian (ordem de bytes da rede)."""
        bruto = empacotar(DADOS, 1, horario=0.0)
        self.assertEqual(bruto[:5], b"\x01\x00\x00\x00\x01")  # big-endian
        self.assertEqual(struct.unpack("!d", bruto[5:13])[0], 0.0)

    def test_valores_invalidos(self):
        """Tipo inexistente, seq fora do intervalo e mensagem curta são recusados."""
        with self.assertRaises(ValueError):
            empacotar(99, 0)
        with self.assertRaises(ValueError):
            empacotar(DADOS, 2**32)
        with self.assertRaises(ValueError):
            desempacotar(b"\x01\x00")


class TestEnquadramentoTCP(unittest.TestCase):
    """Prefixo de tamanho que separa as mensagens no fluxo TCP."""

    def test_mensagens_coladas_no_fluxo_sao_separadas(self):
        """Dez mensagens que chegam grudadas no buffer são lidas separadas e na ordem."""
        a, b = socket.socketpair()
        with a, b:
            b.settimeout(2)
            enviadas = [empacotar(DADOS, i, bytes([i]) * (i * 100)) for i in range(10)]
            for m in enviadas:
                enviar_tcp(a, m)  # chegam todas grudadas no buffer de b
            for i in range(10):
                msg = receber_tcp(b)
                self.assertEqual((msg.seq, len(msg.carga)), (i, i * 100))

    def test_conexao_fechada_no_meio(self):
        """Se a conexão fecha no meio de uma mensagem, a leitura acusa erro em vez de travar."""
        a, b = socket.socketpair()
        with b:
            b.settimeout(2)
            a.sendall(b"\x00\x20" + b"abc")  # anuncia 32 bytes e fecha
            a.close()
            with self.assertRaises(ConnectionError):
                receber_tcp(b)


class TestAnalisadorSequencia(unittest.TestCase):
    """Detecção de pacotes faltando, repetidos e fora de ordem (RF04)."""

    def test_detecta_faltando_repetidos_e_trocados(self):
        """Sequência com um número faltando, um repetido e dois trocados é classificada corretamente."""
        # 0..9 com: 3 faltando, 5 repetido, 7 e 8 trocados, 1 chegando atrasado
        chegada = [0, 2, 1, 4, 5, 5, 6, 8, 7, 9, 9]
        a = AnalisadorSequencia()
        status = [a.registrar(s) for s in chegada]
        self.assertEqual(
            status,
            [NOVO, NOVO, FORA_DE_ORDEM, NOVO, NOVO, DUPLICADO, NOVO, NOVO, FORA_DE_ORDEM, NOVO, DUPLICADO],
        )
        self.assertEqual(a.faltando(10), [3])
        self.assertEqual(a.duplicados, 2)
        self.assertEqual(a.fora_ordem, 2)
        self.assertEqual(a.unicos, 9)
        self.assertEqual(a.chegadas, 11)

    def test_sequencia_perfeita(self):
        """Uma sequência sem problemas não gera nenhuma contagem."""
        a = AnalisadorSequencia()
        for s in range(1000):
            self.assertEqual(a.registrar(s), NOVO)
        self.assertEqual((a.faltando(1000), a.duplicados, a.fora_ordem), ([], 0, 0))


if __name__ == "__main__":
    unittest.main()
