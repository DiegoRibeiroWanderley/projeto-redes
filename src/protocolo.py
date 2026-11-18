"""Formato das mensagens trocadas entre cliente e servidor (RF04).

Este módulo é a "língua" comum dos programas: todo o resto do projeto monta e
lê mensagens só pelas funções daqui, para que TCP, UDP e híbrido usem
exatamente o mesmo formato.

Cabeçalho
---------
Toda mensagem começa com um cabeçalho fixo de 13 bytes, no formato ``!BId``
do módulo ``struct``:

    !         ordem de bytes da rede (big-endian), sem bytes de alinhamento
    tipo      B  1 byte   DADOS, ACK, FIM, INICIO ou ERRO
    seq       I  4 bytes  número de sequência, inteiro sem sinal (0 a 2**32-1)
    horario   d  8 bytes  time.perf_counter() no momento do envio

Depois do cabeçalho vem a carga útil (os dados de verdade), de tamanho
variável.

Enquadramento no TCP
--------------------
O TCP entrega um *fluxo* de bytes: duas mensagens enviadas separadamente
podem chegar grudadas num único ``recv``, ou uma mensagem pode chegar
partida em dois. Por isso, no TCP, cada mensagem é precedida por 2 bytes
(``!H``) com o seu tamanho; o receptor lê esses 2 bytes e depois exatamente
aquela quantidade. No UDP isso não é necessário, porque cada datagrama já
chega inteiro e separado dos outros.

Tipos de mensagem
-----------------
Além de DADOS, ACK e FIM, pedidos no RF04, há dois tipos de controle:
INICIO (primeira mensagem do cliente, indica o cenário) e ERRO (o servidor
recusa o pedido). As mensagens de controle levam a carga em JSON.
"""

from __future__ import annotations

import json
import socket
import struct
import time
from dataclasses import dataclass

# ---------------------------------------------------------------- constantes

FORMATO_CABECALHO = "!BId"
TAM_CABECALHO = struct.calcsize(FORMATO_CABECALHO)  # 1 + 4 + 8 = 13 bytes
assert TAM_CABECALHO == 13

FORMATO_TAMANHO_TCP = "!H"  # inteiro sem sinal de 2 bytes: mensagens de até 65535 bytes
TAM_PREFIXO_TCP = struct.calcsize(FORMATO_TAMANHO_TCP)

TAM_BLOCO = 1024  # bytes úteis por bloco no cenário de arquivo (RF05)

PORTA_TCP = 5000  # porta padrão do servidor TCP (RF01)
PORTA_UDP = 5001  # porta padrão do servidor UDP e do híbrido (RF02)

MAX_SEQ = 2**32 - 1  # maior valor que cabe nos 4 bytes do campo seq

# Tipos de mensagem (valor do primeiro byte do cabeçalho)
DADOS = 1   # bloco de dados úteis
ACK = 2     # confirmação; também a resposta do servidor ao INICIO
FIM = 3     # fim da transmissão
INICIO = 4  # pedido do cliente: qual cenário rodar e com quais parâmetros
ERRO = 5    # o servidor não pode atender o pedido

NOMES_TIPOS = {DADOS: "DADOS", ACK: "ACK", FIM: "FIM", INICIO: "INICIO", ERRO: "ERRO"}

CENARIOS = ("arquivo", "tempo-real", "req-resp")
PROTOCOLOS = ("tcp", "udp", "hibrido")


# ------------------------------------------------------ empacotar/desempacotar

@dataclass(frozen=True)
class Mensagem:
    """Uma mensagem já desempacotada.

    Atributos:
        tipo: um dos tipos DADOS, ACK, FIM, INICIO ou ERRO.
        seq: número de sequência. Nos DADOS é o índice do bloco; no INICIO e
            na resposta a ele é o número de sessão sorteado pelo cliente.
        horario: ``time.perf_counter()`` do emissor no momento do envio. Só
            serve para comparar horários da mesma máquina (RNF04).
        carga: bytes que vêm depois do cabeçalho.
    """

    tipo: int
    seq: int
    horario: float
    carga: bytes = b""

    @property
    def nome_tipo(self) -> str:
        """Nome legível do tipo (para mensagens de log e de erro)."""
        return NOMES_TIPOS.get(self.tipo, f"?{self.tipo}")

    def json(self) -> dict:
        """Interpreta a carga útil como JSON (usado nas mensagens de controle).

        Retorna um dicionário vazio se não houver carga.
        """
        return json.loads(self.carga.decode("utf-8")) if self.carga else {}


def empacotar(tipo: int, seq: int, carga: bytes = b"", horario: float | None = None) -> bytes:
    """Monta uma mensagem: cabeçalho de 13 bytes + carga útil.

    Parâmetros:
        tipo: tipo da mensagem (DADOS, ACK, FIM, INICIO ou ERRO).
        seq: número de sequência, de 0 a 2**32-1.
        carga: dados que vão depois do cabeçalho.
        horario: horário de envio a gravar no cabeçalho. Se omitido, usa
            ``time.perf_counter()`` no momento da chamada.

    Retorna os bytes prontos para ``send``/``sendto`` (no TCP, use
    ``enviar_tcp``, que acrescenta o prefixo de tamanho).

    Lança ``ValueError`` se o tipo ou o número de sequência forem inválidos.
    """
    if tipo not in NOMES_TIPOS:
        raise ValueError(f"tipo de mensagem inválido: {tipo}")
    if not 0 <= seq <= MAX_SEQ:
        raise ValueError(f"número de sequência fora do intervalo: {seq}")
    if horario is None:
        horario = time.perf_counter()
    return struct.pack(FORMATO_CABECALHO, tipo, seq, horario) + carga


def desempacotar(dados: bytes) -> Mensagem:
    """Separa cabeçalho e carga útil de uma mensagem recebida.

    Lança ``ValueError`` se a mensagem for menor que o cabeçalho ou tiver um
    tipo desconhecido (por exemplo, lixo vindo de outro programa).
    """
    if len(dados) < TAM_CABECALHO:
        raise ValueError(f"mensagem curta demais: {len(dados)} bytes")
    tipo, seq, horario = struct.unpack_from(FORMATO_CABECALHO, dados)
    if tipo not in NOMES_TIPOS:
        raise ValueError(f"tipo de mensagem desconhecido: {tipo}")
    return Mensagem(tipo, seq, horario, bytes(dados[TAM_CABECALHO:]))


def carga_json(obj: dict) -> bytes:
    """Converte um dicionário em JSON compacto (carga das mensagens de controle)."""
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


# --------------------------------------------------------- enquadramento TCP

def enviar_tcp(sock: socket.socket, mensagem: bytes) -> None:
    """Envia uma mensagem pelo TCP com o prefixo de 2 bytes de tamanho.

    ``sendall`` repete o envio até todos os bytes entrarem no buffer do
    kernel, porque um ``send`` simples pode enviar só parte deles.
    """
    if len(mensagem) > 0xFFFF:
        raise ValueError("mensagem grande demais para o prefixo de 2 bytes")
    sock.sendall(struct.pack(FORMATO_TAMANHO_TCP, len(mensagem)) + mensagem)


def _receber_exato(sock: socket.socket, n: int) -> bytes:
    """Lê exatamente ``n`` bytes do socket TCP.

    Um ``recv(n)`` pode devolver menos que ``n`` bytes (o resto ainda está a
    caminho), então lemos em laço até completar. ``recv`` devolvendo ``b""``
    significa que o outro lado fechou a conexão.
    """
    partes = bytearray()
    while len(partes) < n:
        pedaco = sock.recv(n - len(partes))
        if not pedaco:
            raise ConnectionError("conexão fechada pelo outro lado")
        partes += pedaco
    return bytes(partes)


def receber_tcp(sock: socket.socket) -> Mensagem:
    """Lê exatamente uma mensagem do fluxo TCP.

    Primeiro lê o prefixo de 2 bytes com o tamanho e depois a mensagem.
    Respeita o timeout configurado no socket: se nada chegar a tempo, lança
    ``socket.timeout`` (RNF06, nenhuma espera infinita).
    """
    (tamanho,) = struct.unpack(FORMATO_TAMANHO_TCP, _receber_exato(sock, TAM_PREFIXO_TCP))
    return desempacotar(_receber_exato(sock, tamanho))


# ------------------------------------------------- análise da sequência (RF04)

# Possíveis classificações devolvidas por AnalisadorSequencia.registrar
NOVO = "novo"
DUPLICADO = "duplicado"
FORA_DE_ORDEM = "fora_de_ordem"


class AnalisadorSequencia:
    """Classifica cada número de sequência que chega no receptor.

    É daqui que saem as métricas de duplicados, fora de ordem e perda (RF09):

    - duplicado: o número já tinha sido recebido antes;
    - fora de ordem: chegou depois de um número maior (e não é duplicado);
    - faltando: números que nunca chegaram (consultados no fim com ``faltando``).

    Exemplo: a chegada 0, 2, 1, 2 dá NOVO, NOVO, FORA_DE_ORDEM, DUPLICADO.
    """

    def __init__(self) -> None:
        """Começa sem nenhum número recebido."""
        self.recebidos: set[int] = set()  # números já vistos (sem repetição)
        self.maior = -1                   # maior número visto até agora
        self.chegadas = 0                 # total de chegadas, contando repetidas
        self.duplicados = 0
        self.fora_ordem = 0

    def registrar(self, seq: int) -> str:
        """Registra a chegada de ``seq`` e devolve NOVO, DUPLICADO ou FORA_DE_ORDEM."""
        self.chegadas += 1
        if seq in self.recebidos:
            self.duplicados += 1
            return DUPLICADO
        self.recebidos.add(seq)
        if seq < self.maior:
            self.fora_ordem += 1
            return FORA_DE_ORDEM
        self.maior = seq
        return NOVO

    @property
    def unicos(self) -> int:
        """Quantos números diferentes chegaram (base do cálculo de perda)."""
        return len(self.recebidos)

    def faltando(self, total: int) -> list[int]:
        """Números de 0 a ``total``-1 que não chegaram."""
        return [s for s in range(total) if s not in self.recebidos]
