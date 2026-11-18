"""Cálculo das métricas (RF09) e registro em CSV (RF10).

As mesmas funções são usadas pelos três protocolos, para que as definições
sejam sempre idênticas e a comparação seja justa:

    tempo total     do primeiro envio ao último dado recebido ou confirmado (s)
    vazão           bytes úteis entregues / tempo total (MB/s, com 1 MB = 2**20 bytes)
    latência        média dos RTTs (ms)
    jitter          média das diferenças absolutas entre RTTs consecutivos (ms)
    perda           (enviados - recebidos) / enviados * 100 (%)
    fora de ordem   pacotes que chegaram depois de um de número maior
    duplicados      pacotes recebidos mais de uma vez
    retransmissões  reenvios feitos pelo híbrido
    integridade     SHA-256 do recebido igual ao do original (sim/nao)

Métricas que não se aplicam a um cenário ficam vazias no CSV (o pandas lê
como NaN). Uma execução que falha é gravada com ``integro = "falha"``.
"""

from __future__ import annotations

import csv
import hashlib
import os
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

MB = 1024 * 1024  # 1 MB = 2**20 bytes em todo o projeto

# Ordem exata das colunas do resultados.csv (RF10)
COLUNAS = [
    "data_hora",
    "protocolo",
    "cenario",
    "condicao",
    "repeticao",
    "tempo_s",
    "vazao_mbs",
    "latencia_ms",
    "jitter_ms",
    "perda_pct",
    "fora_ordem",
    "duplicados",
    "retransmissoes",
    "integro",
]


# ------------------------------------------------------------------ fórmulas

def vazao_mbs(bytes_uteis: int, tempo_s: float) -> float:
    """Vazão em MB/s: bytes úteis entregues dividido pelo tempo total.

    "Úteis" quer dizer só os dados da aplicação, sem cabeçalhos, ACKs nem
    cópias duplicadas. Lança ``ValueError`` se o tempo não for positivo.
    """
    if tempo_s <= 0:
        raise ValueError("tempo total precisa ser positivo")
    return bytes_uteis / MB / tempo_s


def latencia_media_ms(rtts_s: list[float]) -> float | None:
    """Latência: média dos tempos de ida e volta (RTT), convertida para ms.

    Recebe os RTTs em segundos. Devolve ``None`` (vazio no CSV) se não houver
    nenhum RTT medido.
    """
    if not rtts_s:
        return None
    return sum(rtts_s) / len(rtts_s) * 1000


def jitter_ms(rtts_s: list[float]) -> float | None:
    """Jitter: média das diferenças absolutas entre RTTs consecutivos, em ms.

    Mede o quanto a latência varia de um pacote para o seguinte. Exemplo:
    RTTs de 10, 12 e 9 ms dão diferenças 2 e 3, logo jitter de 2,5 ms.
    Devolve ``None`` com menos de dois RTTs.
    """
    if len(rtts_s) < 2:
        return None
    difs = [abs(b - a) for a, b in zip(rtts_s, rtts_s[1:])]
    return sum(difs) / len(difs) * 1000


def perda_pct(enviados: int, recebidos: int) -> float:
    """Perda em %: (enviados - recebidos) / enviados * 100.

    ``recebidos`` deve contar cada pacote uma vez só (sem duplicados).
    """
    if enviados <= 0:
        raise ValueError("número de enviados precisa ser positivo")
    return (enviados - recebidos) / enviados * 100


def sha256(dados: bytes) -> str:
    """Hash SHA-256 em hexadecimal, usado para conferir a integridade do arquivo."""
    return hashlib.sha256(dados).hexdigest()


# --------------------------------------------------------------- resultado

@dataclass
class Resultado:
    """Resultado de uma execução: corresponde a uma linha do CSV.

    Os quatro primeiros campos identificam a execução; os demais são as
    métricas. Uma métrica que fica ``None`` vira célula vazia no CSV.
    ``integro`` vale "sim", "nao" ou "falha" (a execução não terminou).
    """

    protocolo: str
    cenario: str
    condicao: str
    repeticao: int
    tempo_s: float | None = None
    vazao_mbs: float | None = None
    latencia_ms: float | None = None
    jitter_ms: float | None = None
    perda_pct: float | None = None
    fora_ordem: int | None = None
    duplicados: int | None = None
    retransmissoes: int | None = None
    integro: str | None = None
    data_hora: str = ""  # preenchida automaticamente ao gravar

    def linha_csv(self) -> dict:
        """Converte o resultado num dicionário pronto para o ``csv.DictWriter``.

        Preenche a data e hora atual se ela não tiver sido dada, troca
        ``None`` por vazio e escreve números com 6 casas decimais.
        """
        d = asdict(self)
        if not d["data_hora"]:
            d["data_hora"] = datetime.now().isoformat(timespec="seconds")
        for chave, valor in d.items():
            if valor is None:
                d[chave] = ""
            elif isinstance(valor, float):
                d[chave] = f"{valor:.6f}"
        return {c: d[c] for c in COLUNAS}

    def resumo(self) -> str:
        """Linha curta e legível com as métricas, impressa pelo cliente no terminal."""
        partes = [f"{self.protocolo}/{self.cenario}/{self.condicao} #{self.repeticao}"]
        for nome, fmt in [
            ("tempo_s", "tempo={:.3f}s"),
            ("vazao_mbs", "vazão={:.2f}MB/s"),
            ("latencia_ms", "latência={:.3f}ms"),
            ("jitter_ms", "jitter={:.3f}ms"),
            ("perda_pct", "perda={:.2f}%"),
            ("fora_ordem", "fora_ordem={}"),
            ("duplicados", "duplicados={}"),
            ("retransmissoes", "retx={}"),
            ("integro", "íntegro={}"),
        ]:
            valor = getattr(self, nome)
            if valor is not None:
                partes.append(fmt.format(valor))
        return "  ".join(partes)


# garante que a classe e a lista de colunas não saiam de sincronia
assert set(COLUNAS) == {f.name for f in fields(Resultado)}


# ------------------------------------------------------------------- CSV

def gravar_csv(caminho: str | os.PathLike, resultado: Resultado) -> None:
    """Acrescenta uma linha ao CSV, criando o arquivo com cabeçalho se preciso.

    O arquivo é aberto em modo de acréscimo ("a"), então execuções
    anteriores nunca são apagadas. A pasta é criada se não existir.
    """
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    novo = not caminho.exists() or caminho.stat().st_size == 0
    with caminho.open("a", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUNAS)
        if novo:
            escritor.writeheader()
        escritor.writerow(resultado.linha_csv())
