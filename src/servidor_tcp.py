"""Servidor TCP (RF01).

Abre um socket SOCK_STREAM na porta configurável (padrão 5000), aceita uma
conexão por execução e atende o cenário indicado na primeira mensagem
(INICIO). Ao terminar, fecha a conexão e volta a esperar a próxima, sem
precisar ser reiniciado.

    python src/servidor_tcp.py [--porta 5000] [--arquivo dados/arquivo_10mb.bin]

Fluxo de uma execução:

    cliente                        servidor
    connect()            ──────►   accept()
    INICIO {"cenario": ...} ───►   escolhe o tratador do cenário
                         ◄──────   ACK com os metadados do arquivo
                         ◄──────   DADOS 0, 1, ..., 10239
                         ◄──────   FIM
    close()              ──────►   close(), volta ao accept()

Como o TCP já garante entrega, ordem e ausência de duplicados, o servidor
envia cada bloco uma única vez; quem retransmite, se preciso, é o kernel.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading

from arquivo_teste import CAMINHO_PADRAO, ArquivoTeste
from protocolo import (
    ACK,
    DADOS,
    ERRO,
    FIM,
    INICIO,
    PORTA_TCP,
    carga_json,
    empacotar,
    enviar_tcp,
    receber_tcp,
)

TIMEOUT_S = 10.0  # nenhuma leitura de socket espera para sempre (RNF06)


def log(texto: str) -> None:
    """Escreve uma mensagem de acompanhamento no stderr (não mistura com resultados)."""
    print(f"[tcp] {texto}", file=sys.stderr, flush=True)


# ------------------------------------------------------------------ cenários

def cenario_arquivo(conn: socket.socket, arq: ArquivoTeste, sessao: int) -> None:
    """Cenário 1 (RF05): envia o arquivo em blocos de 1024 bytes.

    Sequência: ACK com os metadados (respondendo ao INICIO), um DADOS por
    bloco com o índice no campo seq, e um FIM no final.

    Parâmetros:
        conn: conexão já aceita com o cliente.
        arq: arquivo a enviar.
        sessao: número de sessão do INICIO, devolvido no ACK.
    """
    enviar_tcp(conn, empacotar(ACK, sessao, carga_json(arq.metadados())))
    for i in range(arq.num_blocos):
        enviar_tcp(conn, empacotar(DADOS, i, arq.bloco(i)))
    fim = {"enviados": arq.num_blocos, **arq.metadados()}
    enviar_tcp(conn, empacotar(FIM, arq.num_blocos, carga_json(fim)))


# Cenários atendidos: nome usado no INICIO -> função que o executa.
# tempo-real e req-resp entram na semana 2.
CENARIOS = {"arquivo": cenario_arquivo}


# ------------------------------------------------------------------ servidor

class ServidorTCP:
    """Servidor que atende uma conexão por vez, indefinidamente.

    Atributos:
        porta: porta em que está escutando (útil quando se passa 0 e o
            sistema escolhe uma porta livre, como nos testes).
        parar: evento que, se acionado, encerra o laço de ``rodar``.
        atendidas: quantas execuções terminaram com sucesso.
    """

    def __init__(self, arq: ArquivoTeste, host: str = "0.0.0.0", porta: int = PORTA_TCP) -> None:
        """Cria o socket, associa à porta e começa a escutar.

        ``SO_REUSEADDR`` permite reabrir a porta logo depois de reiniciar o
        servidor, sem esperar o estado TIME_WAIT das conexões antigas.
        """
        self.arq = arq
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, porta))
        self.sock.listen(1)
        self.sock.settimeout(1.0)  # accept volta a cada 1 s para checar o pedido de parada
        self.porta = self.sock.getsockname()[1]
        self.parar = threading.Event()
        self.atendidas = 0

    def atender(self, conn: socket.socket, origem) -> None:
        """Atende uma execução completa numa conexão já aceita.

        Lê o INICIO, executa o cenário pedido e encerra a conexão de forma
        ordenada. Pedidos inválidos ou de cenários não implementados recebem
        uma mensagem ERRO.
        """
        conn.settimeout(TIMEOUT_S)
        pedido = receber_tcp(conn)
        if pedido.tipo != INICIO:
            enviar_tcp(conn, empacotar(ERRO, 0, b"esperava INICIO"))
            return
        cenario = pedido.json().get("cenario")
        tratador = CENARIOS.get(cenario)
        if tratador is None:
            log(f"{origem}: cenário não implementado: {cenario!r}")
            enviar_tcp(conn, empacotar(ERRO, pedido.seq, f"cenário não implementado: {cenario}".encode()))
            return
        tratador(conn, self.arq, pedido.seq)
        # Fecha só o sentido de escrita (envia FIN) e espera o cliente fechar.
        # Fechar de vez agora poderia descartar dados ainda não lidos pelo cliente.
        conn.shutdown(socket.SHUT_WR)
        while conn.recv(4096):
            pass
        self.atendidas += 1
        log(f"{origem}: {cenario} concluído (execução {self.atendidas})")

    def rodar(self) -> None:
        """Laço principal: aceita conexões e atende uma por vez até ``parar``.

        Um erro numa execução (cliente que caiu, timeout, mensagem inválida)
        é registrado no log e não derruba o servidor.
        """
        log(f"escutando na porta {self.porta}")
        while not self.parar.is_set():
            try:
                conn, origem = self.sock.accept()
            except socket.timeout:
                continue
            with conn:
                try:
                    self.atender(conn, origem)
                except (OSError, ValueError) as e:
                    log(f"{origem}: execução interrompida: {e}")
        self.sock.close()


def main() -> None:
    """Linha de comando: carrega o arquivo de teste e inicia o servidor."""
    p = argparse.ArgumentParser(description="Servidor TCP do projeto.")
    p.add_argument("--host", default="0.0.0.0", help="0.0.0.0 escuta em todas as interfaces")
    p.add_argument("--porta", type=int, default=PORTA_TCP)
    p.add_argument("--arquivo", default=CAMINHO_PADRAO, help="arquivo do cenário de transferência")
    args = p.parse_args()
    try:
        arq = ArquivoTeste.carregar(args.arquivo)
    except FileNotFoundError as e:
        sys.exit(str(e))
    try:
        ServidorTCP(arq, args.host, args.porta).rodar()
    except KeyboardInterrupt:  # Ctrl+C
        log("encerrado")


if __name__ == "__main__":
    main()
