"""Arquivo de 10 MB usado no cenário de transferência (RF05).

O arquivo é gerado uma única vez com ``os.urandom`` (bytes aleatórios, que
não se comprimem) e reutilizado em todos os testes, para que todos os
protocolos transfiram exatamente o mesmo conteúdo:

    python src/arquivo_teste.py            # gera dados/arquivo_10mb.bin
    python src/arquivo_teste.py --forcar   # gera de novo (muda o hash!)

Os servidores carregam o arquivo com ``ArquivoTeste.carregar`` e o enviam
bloco a bloco.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

from metricas import MB, sha256
from protocolo import TAM_BLOCO

RAIZ = Path(__file__).resolve().parent.parent  # pasta raiz do projeto
CAMINHO_PADRAO = RAIZ / "dados" / "arquivo_10mb.bin"
TAMANHO_PADRAO = 10 * MB  # 10.485.760 bytes = 10240 blocos de 1024 bytes


def gerar(caminho: Path = CAMINHO_PADRAO, tamanho: int = TAMANHO_PADRAO, forcar: bool = False) -> Path:
    """Grava ``tamanho`` bytes aleatórios em ``caminho`` e devolve o caminho.

    Recusa sobrescrever um arquivo existente (lança ``FileExistsError``),
    a menos que ``forcar`` seja verdadeiro: um arquivo novo tem outro hash
    e deixaria de bater com execuções anteriores.
    """
    if caminho.exists() and not forcar:
        raise FileExistsError(f"{caminho} já existe (use --forcar para gerar de novo)")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(os.urandom(tamanho))
    return caminho


class ArquivoTeste:
    """Conteúdo do arquivo em memória, já dividido em blocos de 1024 bytes.

    Atributos:
        conteudo: todos os bytes do arquivo.
        tam_bloco: tamanho de cada bloco (o último pode ser menor).
        tamanho: tamanho total em bytes.
        num_blocos: quantidade de blocos (arredondada para cima).
        hash: SHA-256 do arquivo, enviado ao cliente para conferir a integridade.
    """

    def __init__(self, conteudo: bytes, tam_bloco: int = TAM_BLOCO) -> None:
        """Divide ``conteudo`` em blocos de ``tam_bloco`` bytes e calcula o hash."""
        self.conteudo = conteudo
        self.tam_bloco = tam_bloco
        self.tamanho = len(conteudo)
        self.num_blocos = math.ceil(self.tamanho / tam_bloco)
        self.hash = sha256(conteudo)

    @classmethod
    def carregar(cls, caminho: str | os.PathLike = CAMINHO_PADRAO) -> "ArquivoTeste":
        """Lê o arquivo do disco. Se ele não existir, explica como gerá-lo."""
        caminho = Path(caminho)
        if not caminho.exists():
            raise FileNotFoundError(
                f"{caminho} não existe. Gere-o uma vez com: python src/arquivo_teste.py"
            )
        return cls(caminho.read_bytes())

    def bloco(self, i: int) -> bytes:
        """Devolve o bloco de índice ``i`` (de 0 a num_blocos-1)."""
        return self.conteudo[i * self.tam_bloco : (i + 1) * self.tam_bloco]

    def metadados(self) -> dict:
        """Informações que o servidor manda ao cliente antes e depois dos dados.

        Com elas o cliente sabe quantos blocos esperar (para calcular a
        perda) e qual hash o arquivo remontado deve ter.
        """
        return {
            "tamanho": self.tamanho,
            "blocos": self.num_blocos,
            "tam_bloco": self.tam_bloco,
            "sha256": self.hash,
        }


def main() -> None:
    """Linha de comando: gera o arquivo e mostra tamanho, blocos e hash."""
    p = argparse.ArgumentParser(description="Gera o arquivo de teste de 10 MB.")
    p.add_argument("--caminho", type=Path, default=CAMINHO_PADRAO)
    p.add_argument("--tamanho", type=int, default=TAMANHO_PADRAO, help="em bytes")
    p.add_argument("--forcar", action="store_true", help="sobrescreve se já existir")
    args = p.parse_args()
    try:
        caminho = gerar(args.caminho, args.tamanho, args.forcar)
    except FileExistsError as e:
        sys.exit(str(e))
    arq = ArquivoTeste.carregar(caminho)
    print(f"{caminho}: {arq.tamanho} bytes, {arq.num_blocos} blocos, sha256={arq.hash}")


if __name__ == "__main__":
    main()
