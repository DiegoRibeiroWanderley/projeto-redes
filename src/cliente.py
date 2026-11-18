"""Cliente configurável (RF03).

Todos os parâmetros vêm da linha de comando, para que todos os testes usem o
mesmo programa sem editar código. Ao fim de cada execução o cliente calcula
as métricas (RF09) e acrescenta uma linha ao CSV (RF10).

    python src/cliente.py --protocolo udp --cenario arquivo --host 127.0.0.1

No cenário de arquivo, o cliente é o receptor: pede o arquivo, recebe os
blocos, remonta o conteúdo e calcula as métricas sem depender de nenhum
relatório do servidor.

Organização do código:

- ``ReceptorArquivo``: lógica de recepção comum a TCP e UDP (remontagem,
  contagem de duplicados e fora de ordem, perda simulada, métricas);
- ``arquivo_tcp`` e ``arquivo_udp``: a parte específica de cada protocolo;
- ``EXECUTORES``: liga cada (protocolo, cenário) à função que o executa;
- ``main``: lê os argumentos, executa, grava o CSV e imprime o resumo.

Estado (semana 1): cenário "arquivo" em TCP e UDP. Os cenários "tempo-real"
e "req-resp" entram na semana 2 e o protocolo "hibrido" na semana 3.
"""

from __future__ import annotations

import argparse
import random
import socket
import sys
import time
from pathlib import Path

from metricas import (
    Resultado,
    gravar_csv,
    latencia_media_ms,
    perda_pct,
    sha256,
    vazao_mbs,
)
from protocolo import (
    ACK,
    CENARIOS,
    DADOS,
    ERRO,
    FIM,
    INICIO,
    MAX_SEQ,
    PORTA_TCP,
    PORTA_UDP,
    PROTOCOLOS,
    AnalisadorSequencia,
    Mensagem,
    carga_json,
    desempacotar,
    empacotar,
    enviar_tcp,
    receber_tcp,
)

RAIZ = Path(__file__).resolve().parent.parent  # pasta raiz do projeto
CSV_PADRAO = RAIZ / "resultados" / "resultados.csv"

# Tempos de espera. Toda leitura de socket tem um limite (RNF06).
TIMEOUT_TCP_S = 10.0          # espera máxima por qualquer leitura no TCP
TIMEOUT_INICIO_UDP_S = 1.0    # espera pela resposta ao INICIO antes de reenviá-lo
TENTATIVAS_INICIO_UDP = 10    # quantas vezes o INICIO é enviado antes de desistir
INATIVIDADE_UDP_S = 2.0       # o receptor UDP encerra após 2 s sem receber nada (RF05)
TOLERANCIA_POS_FIM_S = 0.25   # depois do FIM, ainda espera blocos atrasados ou reordenados

BUFFER_SOCKET = 4 * 1024 * 1024  # pedido ao kernel; ele limita ao net.core.rmem_max
PAUSA_UDP_PADRAO_US = 50.0       # RNF03: calibrado para perda 0% em rede estável (ver README)


class FalhaExecucao(Exception):
    """A execução não pôde ser concluída (vai para o CSV com integro = falha)."""


# --------------------------------------------------- receptor do arquivo

class ReceptorArquivo:
    """Remonta o arquivo com os blocos que chegaram e calcula as métricas.

    É usado igualmente por TCP e UDP, o que garante que as métricas tenham
    a mesma definição nos dois protocolos.

    ``descartar_a_cada`` simula perda no próprio código (RF09): a cada N
    blocos que chegam, um é ignorado como se nunca tivesse chegado. Com
    N = 10, a perda calculada tem de dar exatamente 10%.
    """

    def __init__(self, descartar_a_cada: int = 0) -> None:
        """Começa sem nenhum bloco. ``descartar_a_cada = 0`` desliga a perda simulada."""
        self.blocos: dict[int, bytes] = {}  # índice do bloco -> conteúdo
        self.seq = AnalisadorSequencia()
        self.descartar_a_cada = descartar_a_cada
        self.chegadas_dados = 0
        self.t_ultimo: float | None = None  # quando chegou o último bloco novo

    def receber(self, msg: Mensagem, agora: float) -> None:
        """Registra a chegada de uma mensagem DADOS no instante ``agora``.

        Blocos duplicados são contados, mas não substituem o já recebido nem
        atualizam o horário do último bloco.
        """
        self.chegadas_dados += 1
        if self.descartar_a_cada and self.chegadas_dados % self.descartar_a_cada == 0:
            return  # perda simulada
        if self.seq.registrar(msg.seq) != "duplicado":
            self.blocos[msg.seq] = msg.carga
            self.t_ultimo = agora

    def preencher(self, res: Resultado, meta: dict, t0: float, rtts: list[float]) -> Resultado:
        """Calcula as métricas da execução e as grava em ``res``.

        Parâmetros:
            res: resultado com protocolo, cenário, condição e repetição já preenchidos.
            meta: metadados do servidor (quantidade de blocos e hash original).
            t0: instante do primeiro envio do cliente (``time.perf_counter``).
            rtts: RTTs medidos, em segundos (aqui, só o do pedido).

        Lança ``FalhaExecucao`` se nenhum bloco tiver chegado.
        """
        if self.t_ultimo is None:
            raise FalhaExecucao("nenhum bloco recebido")
        enviados = int(meta["blocos"])
        # remonta o arquivo em ordem, só com os blocos que chegaram
        conteudo = b"".join(self.blocos[i] for i in sorted(self.blocos))
        res.tempo_s = self.t_ultimo - t0
        res.vazao_mbs = vazao_mbs(len(conteudo), res.tempo_s)
        res.latencia_ms = latencia_media_ms(rtts)  # RTT do pedido (INICIO -> ACK)
        res.jitter_ms = None                        # não se aplica: só há um RTT
        res.perda_pct = perda_pct(enviados, self.seq.unicos)
        res.fora_ordem = self.seq.fora_ordem
        res.duplicados = self.seq.duplicados
        res.retransmissoes = 0                      # só o híbrido retransmite
        res.integro = "sim" if sha256(conteudo) == meta["sha256"] else "nao"
        return res


def novo_pedido(cenario: str, **extras) -> tuple[int, bytes]:
    """Monta a mensagem INICIO com um número de sessão aleatório.

    O número de sessão vai no campo seq e identifica esta execução: o
    servidor o devolve no ACK e o usa para ignorar um INICIO repetido.
    ``extras`` são parâmetros adicionais do cenário (como ``pausa_us``).

    Retorna o par (sessão, mensagem empacotada).
    """
    sessao = random.randint(1, MAX_SEQ)
    return sessao, empacotar(INICIO, sessao, carga_json({"cenario": cenario, **extras}))


# ---------------------------------------------------------- TCP: arquivo

def arquivo_tcp(args, res: Resultado) -> Resultado:
    """Cenário de arquivo pelo TCP.

    Conecta, envia o INICIO, recebe o ACK com os metadados e lê mensagens
    até o FIM. O relógio começa antes do ``connect``, porque o handshake
    do TCP (SYN, SYN-ACK, ACK) faz parte do custo do protocolo.
    """
    receptor = ReceptorArquivo(args.descartar_a_cada)
    t0 = time.perf_counter()  # primeiro envio: o SYN da conexão
    with socket.create_connection((args.host, args.porta), timeout=TIMEOUT_TCP_S) as sock:
        sessao, pedido = novo_pedido("arquivo")
        t_pedido = time.perf_counter()
        enviar_tcp(sock, pedido)
        resposta = receber_tcp(sock)
        rtts = [time.perf_counter() - t_pedido]
        if resposta.tipo == ERRO:
            raise FalhaExecucao(f"servidor recusou: {resposta.carga.decode(errors='replace')}")
        if resposta.tipo != ACK or resposta.seq != sessao:
            raise FalhaExecucao(f"resposta inesperada ao INICIO: {resposta.nome_tipo}")
        meta = resposta.json()
        while True:
            msg = receber_tcp(sock)
            if msg.tipo == DADOS:
                receptor.receber(msg, time.perf_counter())
            elif msg.tipo == FIM:
                break
    return receptor.preencher(res, meta, t0, rtts)


# ---------------------------------------------------------- UDP: arquivo

def arquivo_udp(args, res: Resultado) -> Resultado:
    """Cenário de arquivo pelo UDP.

    Funciona em duas fases:

    1. Pedido: envia o INICIO e espera até 1 s por uma resposta; se nada
       chegar, reenvia (até 10 vezes), pois o próprio pedido pode se perder.
       Se o ACK se perder mas os DADOS começarem a chegar, segue em frente.
    2. Recepção: lê blocos até passar 2 s sem chegar nada, ou até pouco
       depois do FIM (a tolerância pega blocos reordenados que chegam
       depois dele).

    Se o ACK com os metadados se perder, eles são recuperados do FIM.
    """
    receptor = ReceptorArquivo(args.descartar_a_cada)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        # buffer de recepção maior: aguenta rajadas enquanto o Python processa
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, BUFFER_SOCKET)
        # connect num socket UDP não abre conexão: só fixa o destino de send()
        # e faz o kernel descartar datagramas vindos de outros endereços
        sock.connect((args.host, args.porta))
        sessao, pedido = novo_pedido("arquivo", pausa_us=args.pausa_us)
        meta: dict | None = None
        rtts: list[float] = []

        def tratar(msg: Mensagem, agora: float) -> bool:
            """Processa uma mensagem da fase de recepção; devolve True se for o FIM."""
            nonlocal meta
            if msg.tipo == DADOS:
                receptor.receber(msg, agora)
            elif msg.tipo == FIM:
                meta = meta or msg.json()
                return True
            elif msg.tipo == ERRO:
                raise FalhaExecucao(f"servidor recusou: {msg.carga.decode(errors='replace')}")
            return False  # ACKs repetidos (duplicação na rede) são ignorados

        # 1) pedido
        t0 = time.perf_counter()  # primeiro envio: o primeiro INICIO
        sock.settimeout(TIMEOUT_INICIO_UDP_S)
        fim = False
        for _ in range(TENTATIVAS_INICIO_UDP):
            t_pedido = time.perf_counter()
            sock.send(pedido)
            try:
                dados = sock.recv(65535)
            except socket.timeout:
                continue  # nada chegou: reenvia o INICIO
            except ConnectionRefusedError:
                # o kernel recebeu um ICMP "porta inalcançável": não há servidor
                raise FalhaExecucao("servidor UDP não está escutando nessa porta") from None
            agora = time.perf_counter()
            msg = desempacotar(dados)
            if msg.tipo == ACK and msg.seq == sessao:
                meta = msg.json()
                rtts.append(agora - t_pedido)
            else:  # o ACK se perdeu, mas os dados já estão chegando
                fim = tratar(msg, agora)
            break
        else:  # o for terminou sem break: todas as tentativas falharam
            raise FalhaExecucao(f"sem resposta ao INICIO após {TENTATIVAS_INICIO_UDP} tentativas")

        # 2) recepção
        sock.settimeout(TOLERANCIA_POS_FIM_S if fim else INATIVIDADE_UDP_S)
        while True:
            try:
                dados = sock.recv(65535)
            except (socket.timeout, ConnectionRefusedError):
                break  # silêncio por tempo suficiente: a transmissão acabou
            if tratar(desempacotar(dados), time.perf_counter()):
                sock.settimeout(TOLERANCIA_POS_FIM_S)

    if meta is None:
        raise FalhaExecucao("metadados do arquivo não chegaram (ACK e FIM perdidos)")
    return receptor.preencher(res, meta, t0, rtts)


# (protocolo, cenário) -> função que executa. Combinações ausentes ainda não
# foram implementadas: tempo-real e req-resp na semana 2, híbrido na semana 3.
EXECUTORES = {
    ("tcp", "arquivo"): arquivo_tcp,
    ("udp", "arquivo"): arquivo_udp,
}


# ------------------------------------------------------------------ main

def criar_parser() -> argparse.ArgumentParser:
    """Define os parâmetros de linha de comando do cliente.

    ``choices`` faz o argparse recusar valores inválidos e mostrar a
    mensagem de uso automaticamente (critério de aceitação do RF03).
    """
    p = argparse.ArgumentParser(
        description="Cliente de medição: TCP, UDP e híbrido.",
        epilog="exemplo: python src/cliente.py --protocolo udp --cenario arquivo --host 127.0.0.1",
    )
    p.add_argument("--protocolo", required=True, choices=PROTOCOLOS)
    p.add_argument("--cenario", required=True, choices=CENARIOS)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--porta", type=int, help=f"padrão: {PORTA_TCP} (tcp) ou {PORTA_UDP} (udp/hibrido)")
    p.add_argument("--condicao", default="estavel", help="rótulo da condição de rede, gravado no CSV")
    p.add_argument("--repeticao", type=int, default=1)
    p.add_argument("--saida", type=Path, default=CSV_PADRAO, help="CSV de resultados")
    p.add_argument("--pausa-us", type=float, default=PAUSA_UDP_PADRAO_US,
                   help="pausa entre blocos do UDP no cenário de arquivo, em µs (RNF03)")
    p.add_argument("--descartar-a-cada", type=int, default=0, metavar="N",
                   help="teste: descarta 1 a cada N blocos recebidos (perda simulada)")
    return p


def executar(args) -> Resultado:
    """Executa uma medição e devolve o resultado.

    Erros de rede, timeouts e respostas inválidas não interrompem o
    programa: viram um resultado com ``integro = "falha"``, para que a
    bateria de testes possa seguir para a próxima execução (RNF06).

    Lança ``NotImplementedError`` para combinações que ainda não existem.
    """
    res = Resultado(args.protocolo, args.cenario, args.condicao, args.repeticao)
    executor = EXECUTORES.get((args.protocolo, args.cenario))
    if executor is None:
        raise NotImplementedError(f"{args.protocolo}/{args.cenario} ainda não foi implementado")
    try:
        return executor(args, res)
    except (OSError, ValueError, KeyError, FalhaExecucao) as e:
        print(f"falha: {e}", file=sys.stderr)
        return Resultado(args.protocolo, args.cenario, args.condicao, args.repeticao, integro="falha")


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada: lê os argumentos, mede, grava o CSV e imprime o resumo.

    Códigos de saída: 0 sucesso, 1 a execução falhou (mas foi registrada
    no CSV), 2 parâmetros inválidos ou combinação não implementada.
    """
    parser = criar_parser()
    args = parser.parse_args(argv)
    if args.porta is None:
        args.porta = PORTA_TCP if args.protocolo == "tcp" else PORTA_UDP
    if args.repeticao < 1 or args.descartar_a_cada < 0 or args.pausa_us < 0:
        parser.error("--repeticao precisa ser >= 1; --descartar-a-cada e --pausa-us, >= 0")
    try:
        res = executar(args)
    except NotImplementedError as e:
        parser.exit(2, f"{parser.prog}: {e}\n")
    gravar_csv(args.saida, res)
    print(res.resumo())
    return 1 if res.integro == "falha" else 0


if __name__ == "__main__":
    sys.exit(main())
