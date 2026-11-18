"""Servidor UDP (RF02).

Abre um socket SOCK_DGRAM na porta configurável (padrão 5001) e responde a
cada datagrama conforme o cenário. Como o UDP não tem conexão, cada
execução é identificada pelo endereço e pela porta de origem do cliente,
junto com o número de sessão que o cliente sorteia e manda no campo seq do
INICIO. Assim um INICIO repetido (reenvio do cliente ou duplicação na rede)
não dispara uma segunda transmissão.

    python src/servidor_udp.py [--porta 5001] [--arquivo dados/arquivo_10mb.bin]

Diferenças em relação ao TCP:

- nada garante que um datagrama chegue, chegue uma vez só ou chegue em
  ordem; o cliente mede tudo isso;
- o FIM é enviado três vezes, porque pode se perder (RF05);
- o servidor controla o ritmo de envio (``pausa_us``). Sem isso, ele envia
  mais rápido do que o cliente consegue ler, o buffer de recepção enche e
  aparecem perdas que não vêm da rede (RNF03).
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from collections import deque

from arquivo_teste import CAMINHO_PADRAO, ArquivoTeste
from protocolo import (
    ACK,
    DADOS,
    ERRO,
    FIM,
    INICIO,
    PORTA_UDP,
    carga_json,
    desempacotar,
    empacotar,
)

REPETICOES_FIM = 3  # o FIM vai três vezes, por segurança (RF05)
BUFFER_SOCKET = 4 * 1024 * 1024  # pedido ao kernel; ele limita ao net.core.wmem_max


def log(texto: str) -> None:
    """Escreve uma mensagem de acompanhamento no stderr (não mistura com resultados)."""
    print(f"[udp] {texto}", file=sys.stderr, flush=True)


def esperar_ate(alvo: float) -> None:
    """Espera até o instante ``alvo`` (na escala de ``time.perf_counter``).

    ``time.sleep`` sozinho não serve para pausas de microssegundos: ele
    costuma dormir bem mais do que o pedido. Por isso dorme enquanto falta
    mais de 2 ms e completa o restante em espera ativa (laço), que é precisa.
    """
    while True:
        falta = alvo - time.perf_counter()
        if falta <= 0:
            return
        if falta > 0.002:
            time.sleep(falta - 0.001)


# ------------------------------------------------------------------ cenários

def cenario_arquivo(sock: socket.socket, destino, arq: ArquivoTeste, sessao: int, pedido: dict) -> None:
    """Cenário 1 (RF05): envia ACK com os metadados, os blocos e o FIM (3x).

    O ritmo é limitado por ``pausa_us`` (RNF03), que vem no pedido do
    cliente. O bloco i sai no instante t0 + i * pausa: com horários
    absolutos, um atraso num envio não empurra todos os seguintes.

    Parâmetros:
        sock: socket UDP do servidor.
        destino: endereço (ip, porta) do cliente.
        arq: arquivo a enviar.
        sessao: número de sessão do INICIO, devolvido no ACK.
        pedido: carga JSON do INICIO (com ``pausa_us``).
    """
    pausa = float(pedido.get("pausa_us", 0)) / 1e6  # µs -> s
    sock.sendto(empacotar(ACK, sessao, carga_json(arq.metadados())), destino)
    t0 = time.perf_counter()
    for i in range(arq.num_blocos):
        if pausa > 0:
            esperar_ate(t0 + i * pausa)
        sock.sendto(empacotar(DADOS, i, arq.bloco(i)), destino)
    fim = carga_json({"enviados": arq.num_blocos, **arq.metadados()})
    for _ in range(REPETICOES_FIM):
        sock.sendto(empacotar(FIM, arq.num_blocos, fim), destino)


# Cenários atendidos: nome usado no INICIO -> função que o executa.
# tempo-real e req-resp entram na semana 2.
CENARIOS = {"arquivo": cenario_arquivo}


# ------------------------------------------------------------------ servidor

class ServidorUDP:
    """Servidor que atende um pedido por vez, indefinidamente.

    Atributos:
        porta: porta em que está escutando.
        parar: evento que, se acionado, encerra o laço de ``rodar``.
        sessoes_vistas: últimas (origem, sessão) atendidas, para ignorar
            um INICIO repetido.
        atendidas: quantas execuções terminaram.
    """

    def __init__(self, arq: ArquivoTeste, host: str = "0.0.0.0", porta: int = PORTA_UDP) -> None:
        """Cria o socket UDP, aumenta o buffer de envio e associa à porta."""
        self.arq = arq
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, BUFFER_SOCKET)
        self.sock.bind((host, porta))
        self.sock.settimeout(1.0)  # nenhuma leitura infinita; permite checar a parada
        self.porta = self.sock.getsockname()[1]
        self.parar = threading.Event()
        self.sessoes_vistas: deque = deque(maxlen=256)
        self.atendidas = 0

    def tratar(self, dados: bytes, origem) -> None:
        """Processa um datagrama recebido.

        Só um INICIO de uma sessão nova dispara um cenário. Qualquer outra
        coisa (ACKs ou DADOS atrasados de uma execução que já acabou, ou um
        INICIO repetido) é ignorada.
        """
        msg = desempacotar(dados)
        if msg.tipo != INICIO:
            return
        chave = (origem, msg.seq)
        if chave in self.sessoes_vistas:
            return
        self.sessoes_vistas.append(chave)
        pedido = msg.json()
        cenario = pedido.get("cenario")
        tratador = CENARIOS.get(cenario)
        if tratador is None:
            log(f"{origem}: cenário não implementado: {cenario!r}")
            self.sock.sendto(empacotar(ERRO, msg.seq, f"cenário não implementado: {cenario}".encode()), origem)
            return
        tratador(self.sock, origem, self.arq, msg.seq, pedido)
        self.atendidas += 1
        log(f"{origem}: {cenario} concluído (execução {self.atendidas})")

    def rodar(self) -> None:
        """Laço principal: recebe datagramas e os trata até ``parar``.

        Um erro numa execução é registrado no log e não derruba o servidor.
        """
        log(f"escutando na porta {self.porta}")
        while not self.parar.is_set():
            try:
                dados, origem = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except ConnectionRefusedError:
                # aviso ICMP de "porta fechada" de um cliente que já saiu
                continue
            try:
                self.tratar(dados, origem)
            except (OSError, ValueError) as e:
                log(f"{origem}: execução interrompida: {e}")
        self.sock.close()


def main() -> None:
    """Linha de comando: carrega o arquivo de teste e inicia o servidor."""
    p = argparse.ArgumentParser(description="Servidor UDP do projeto.")
    p.add_argument("--host", default="0.0.0.0", help="0.0.0.0 escuta em todas as interfaces")
    p.add_argument("--porta", type=int, default=PORTA_UDP)
    p.add_argument("--arquivo", default=CAMINHO_PADRAO, help="arquivo do cenário de transferência")
    args = p.parse_args()
    try:
        arq = ArquivoTeste.carregar(args.arquivo)
    except FileNotFoundError as e:
        sys.exit(str(e))
    try:
        ServidorUDP(arq, args.host, args.porta).rodar()
    except KeyboardInterrupt:  # Ctrl+C
        log("encerrado")


if __name__ == "__main__":
    main()
