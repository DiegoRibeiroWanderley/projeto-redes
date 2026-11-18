# Comparação prática entre TCP, UDP e um protocolo híbrido: como o código funciona

Este guia explica como os arquivos de `src/` se relacionam e o caminho que os dados percorrem numa execução, do arquivo de teste até a linha no CSV. Para instalar, rodar, testar e ver as decisões de implementação, veja o [RELATORIO.md](RELATORIO.md).

## 1. O caminho dos dados

```
 dados/arquivo_10mb.bin          gerado uma vez por arquivo_teste.py
          │
          ▼
 ┌──────────────────┐            carrega o arquivo na memória e o divide
 │ servidor TCP/UDP │            em 10240 blocos de 1024 bytes
 └────────┬─────────┘
          │  mensagens com cabeçalho de 13 bytes (protocolo.py)
          ▼
   rede (loopback lo, com ou sem tc netem)
          │
          ▼
 ┌──────────────────┐            recebe os blocos, conta duplicados e fora
 │     cliente      │            de ordem, remonta o arquivo e confere o hash
 └────────┬─────────┘
          │  métricas calculadas por metricas.py
          ▼
 resultados/resultados.csv       uma linha por execução
          │
          ▼
 graficos/*.png                  (semana 4)
```

O netem entra na etapa da rede: é uma regra do kernel que degrada a interface `lo` (perda, atraso, duplicação ou reordenação) entre o servidor e o cliente. Os programas não sabem que ela existe; só sentem o efeito. Como aplicar cada condição está em [Testando com o netem](RELATORIO.md#testando-com-o-netem), no `RELATORIO.md`.

## 2. Quem usa quem

```
servidor_tcp.py ─┐
                 ├──► arquivo_teste.py ──► protocolo.py, metricas.py
servidor_udp.py ─┤
                 └──► protocolo.py

cliente.py ──────────► protocolo.py, metricas.py
```

A seta quer dizer "importa". O cliente e os servidores não importam uns aos outros: eles só conversam pela rede.

`protocolo.py` e `metricas.py` não dependem de nenhum outro arquivo do projeto. São a base comum: servidores e cliente montam as mensagens e calculam as métricas pelas mesmas funções, o que mantém a comparação entre os protocolos justa.

## 3. O papel de cada arquivo

| Arquivo | Papel | Principais funções e classes |
| --- | --- | --- |
| `protocolo.py` | Define o formato das mensagens e as constantes compartilhadas (portas, tipos, tamanho do bloco). | `empacotar`, `desempacotar`, `enviar_tcp`, `receber_tcp`, `AnalisadorSequencia` |
| `metricas.py` | Fórmulas das métricas e gravação do CSV. | `vazao_mbs`, `latencia_media_ms`, `jitter_ms`, `perda_pct`, `Resultado`, `gravar_csv` |
| `arquivo_teste.py` | Gera o arquivo de 10 MB e o carrega já dividido em blocos. | `gerar`, `ArquivoTeste` |
| `servidor_tcp.py` | Aceita uma conexão por vez e envia o arquivo pelo TCP. | `ServidorTCP`, `cenario_arquivo` |
| `servidor_udp.py` | Responde a pedidos UDP e envia o arquivo com ritmo controlado. | `ServidorUDP`, `cenario_arquivo`, `esperar_ate` |
| `cliente.py` | Lê os parâmetros, pede o arquivo, recebe, mede e grava o CSV. | `main`, `executar`, `arquivo_tcp`, `arquivo_udp`, `ReceptorArquivo` |

Fora de `src/`, `scripts/calibrar_pausa.py` roda o `cliente.py` várias vezes para escolher a pausa do UDP, e os testes em `tests/` sobem os servidores e chamam o cliente.

## 4. Formato de uma mensagem

Toda mensagem, nos dois protocolos, tem o mesmo cabeçalho (`protocolo.py`):

```
 ┌────────┬───────────────┬──────────────────────┬──────────────────┐
 │ tipo   │ seq           │ horario              │ carga útil       │
 │ 1 byte │ 4 bytes       │ 8 bytes              │ 0 a 1024 bytes   │
 └────────┴───────────────┴──────────────────────┴──────────────────┘
   └───────── cabeçalho de 13 bytes (!BId) ──────┘
```

No TCP, cada mensagem vai precedida por mais 2 bytes com o seu tamanho, porque o TCP não separa uma mensagem da outra:

```
 ┌──────────┬───────────────────────────────┐
 │ tamanho  │ mensagem (cabeçalho + carga)  │
 │ 2 bytes  │                               │
 └──────────┴───────────────────────────────┘
```

| Tipo | Quem envia | Para quê |
| --- | --- | --- |
| `INICIO` | cliente | Pede um cenário. O `seq` leva um número de sessão aleatório, e a carga leva um JSON com o cenário e os parâmetros. |
| `ACK` | servidor | Responde ao INICIO com os metadados do arquivo: tamanho, número de blocos e SHA-256. |
| `DADOS` | servidor | Um bloco do arquivo. O `seq` é o índice do bloco (0 a 10239). |
| `FIM` | servidor | Avisa o fim da transmissão e repete os metadados. No UDP vai três vezes. |
| `ERRO` | servidor | Recusa o pedido (por exemplo, um cenário ainda não implementado). |

## 5. Uma execução passo a passo

Ao rodar `python3 src/cliente.py --protocolo udp --cenario arquivo`:

**No cliente (`cliente.py`):**

1. `main` lê os parâmetros com `criar_parser` e escolhe a porta padrão do protocolo.
2. `executar` procura em `EXECUTORES` a função de `("udp", "arquivo")`, que é `arquivo_udp`.
3. `arquivo_udp` cria um `ReceptorArquivo`, abre o socket e monta o INICIO com `novo_pedido`.

**A troca de mensagens:**

```
   cliente.py (arquivo_udp)                     servidor_udp.py
   ────────────────────────                     ───────────────
   t0 = perf_counter()
   INICIO(seq=sessão, {"cenario":"arquivo",
                       "pausa_us":50})   ──────►  rodar() recebe o datagrama
                                                  tratar() vê que é uma sessão nova
                                                  chama cenario_arquivo()
                                          ◄──────  ACK(seq=sessão, metadados)
   guarda os metadados e o RTT
                                          ◄──────  DADOS seq=0
                                          ◄──────  DADOS seq=1      um bloco a cada 50 µs
                                          ◄──────  ...              (esperar_ate)
                                          ◄──────  DADOS seq=10239
   ReceptorArquivo.receber()
   para cada bloco
                                          ◄──────  FIM ×3
   espera mais 0,25 s por blocos atrasados
   e encerra                                       volta a esperar o próximo INICIO
```

**De volta ao cliente:**

4. `ReceptorArquivo.preencher` remonta o arquivo com os blocos que chegaram, calcula as métricas com as funções de `metricas.py` e compara o SHA-256 com o do servidor.
5. `main` grava o `Resultado` em `resultados/resultados.csv` com `gravar_csv` e imprime o resumo.

**No TCP (`arquivo_tcp` e `servidor_tcp.py`), o fluxo é o mesmo, com quatro diferenças:**

- antes do INICIO há o `connect` (o handshake de três vias), e o relógio começa antes dele;
- as mensagens passam por `enviar_tcp` e `receber_tcp`, por causa do prefixo de tamanho;
- não há pausa entre blocos nem FIM repetido, porque o próprio TCP controla o ritmo e garante a entrega;
- no fim, o servidor fecha só o envio (`shutdown`) e espera o cliente fechar a conexão.

## 6. O que acontece quando algo se perde (UDP)

| O que se perde | O que acontece |
| --- | --- |
| O INICIO | O cliente não recebe nada em 1 s e reenvia, até 10 tentativas. |
| O ACK | O cliente segue assim que os DADOS chegam e pega os metadados no FIM. |
| Um INICIO chega repetido | O servidor reconhece o par (endereço, sessão) e ignora, sem enviar o arquivo de novo. |
| Blocos de DADOS | O cliente não pede de novo (é UDP puro). A perda e a integridade "nao" vão para o CSV. |
| Os três FIM | O cliente encerra depois de 2 s sem receber nada. |
| Tudo (servidor desligado) | A execução é gravada com `integro = falha`, e o programa termina normalmente. |

O protocolo híbrido (semana 3) vai acrescentar a retransmissão dos blocos perdidos.

## 7. Onde mexer nas próximas semanas

**Novo cenário (semana 2, `tempo-real` e `req-resp`):**

1. Nos servidores, escreva uma função `cenario_<nome>` e registre-a no dicionário `CENARIOS` de `servidor_tcp.py` e `servidor_udp.py`.
2. No cliente, escreva as funções `<nome>_tcp` e `<nome>_udp` e registre-as em `EXECUTORES`.
3. Use as funções de `metricas.py` já prontas, inclusive `jitter_ms`, que só passa a ser usado nesses cenários.

**Protocolo híbrido (semana 3):** entra como mais uma entrada em `EXECUTORES`, como `("hibrido", "arquivo")`. Do lado do servidor, pode ser um parâmetro no JSON do INICIO que o `servidor_udp.py` usa para escolher o envio com espera por ACK.

O `--cenario` e o `--protocolo` do cliente já aceitam esses valores. Hoje eles respondem "ainda não foi implementado" e passam a funcionar assim que as funções forem registradas.
