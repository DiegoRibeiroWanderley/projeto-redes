# Comparação prática entre TCP, UDP e um protocolo híbrido

Cliente e servidor em Python (só biblioteca padrão) para medir TCP, UDP e um protocolo híbrido nas mesmas condições de rede. A especificação completa está em `Comparação Prática entre TCP, UDP e um Protocolo Híbrido.md`.

## Estado atual: semana 1 concluída

| Requisito | O que foi feito | Onde |
| --- | --- | --- |
| RF01 Servidor TCP | `SOCK_STREAM`, porta 5000, uma conexão por execução, não precisa reiniciar | `src/servidor_tcp.py` |
| RF02 Servidor UDP | `SOCK_DGRAM`, porta 5001, execução identificada por endereço + porta + nº de sessão | `src/servidor_udp.py` |
| RF03 Cliente configurável | `argparse` com `--protocolo --cenario --host --porta --condicao --repeticao` | `src/cliente.py` |
| RF04 Formato das mensagens | cabeçalho `!BId` de 13 bytes; no TCP, prefixo `!H` de 2 bytes com o tamanho | `src/protocolo.py` |
| RF05 Cenário de arquivo | 10 MB (`os.urandom`), blocos de 1024 B, FIM 3×, fim por inatividade de 2 s, SHA-256 | `src/arquivo_teste.py`, servidores, cliente |
| RF09 Métricas | mesmas fórmulas para todos os protocolos | `src/metricas.py` |
| RF10 CSV | `resultados/resultados.csv`, cabeçalho criado na primeira execução | `src/metricas.py` |

Ainda não implementados: cenários `tempo-real` e `req-resp` (semana 2) e o protocolo `hibrido` (semana 3). O cliente já aceita esses valores e responde que eles ainda não foram implementados (código de saída 2).

## Estrutura

```
src/        código-fonte (protocolo, métricas, servidores, cliente)
scripts/    automação (calibração da pausa; bateria de testes na semana 2)
tests/      testes automáticos dos critérios de aceitação
dados/      arquivo de teste de 10 MB (gerado, fora do Git)
resultados/ resultados.csv
graficos/   PNGs gerados a partir do CSV
```

Para entender como os arquivos se conectam e o que acontece em cada execução, veja o [README.md](README.md).

## Como usar

Requer Linux e Python 3.10 ou superior.

```bash
# 1. gerar o arquivo de teste (uma única vez; gerar de novo muda o hash)
python3 src/arquivo_teste.py

# 2. subir os servidores (em terminais separados ou em segundo plano)
python3 src/servidor_tcp.py
python3 src/servidor_udp.py

# 3. rodar o cliente
python3 src/cliente.py --protocolo tcp --cenario arquivo --host 127.0.0.1
python3 src/cliente.py --protocolo udp --cenario arquivo --host 127.0.0.1 --condicao perda5 --repeticao 3
```

Cada execução imprime um resumo e acrescenta uma linha em `resultados/resultados.csv`.

Outras opções do cliente:

- `--saida CAMINHO`: grava em outro CSV.
- `--pausa-us N`: pausa entre blocos do UDP no cenário de arquivo (padrão 50 µs, ver abaixo).
- `--descartar-a-cada N`: perda simulada no próprio código (ignora 1 a cada N blocos recebidos), usada para verificar as métricas.

## Testando com o netem

O **netem** é um recurso do kernel Linux, configurado pelo comando `tc` (pacote `iproute2`), que piora uma interface de rede de propósito. Ele pode descartar uma porcentagem dos pacotes, atrasá-los, duplicá-los ou trocar a ordem deles. Assim os protocolos são testados numa "rede ruim" controlada e repetível. A regra é aplicada na interface de loopback (`lo`), por onde passa a conversa entre cliente e servidor na mesma máquina (`127.0.0.1`).

Por enquanto o netem é aplicado à mão. O cliente **não** aplica nenhuma regra sozinho: o `--condicao` só escreve um rótulo no CSV. O script que automatiza a bateria (RF11) é da semana 2.

### Passo a passo

Com os dois servidores rodando:

```bash
# 1. aplicar a condição (exige sudo)
sudo tc qdisc add dev lo root netem loss 5%

# 2. conferir se a regra está ativa
tc qdisc show dev lo

# 3. rodar o cliente com o rótulo da condição
python3 src/cliente.py --protocolo tcp --cenario arquivo --condicao perda5
python3 src/cliente.py --protocolo udp --cenario arquivo --condicao perda5

# 4. remover a regra (sempre!)
sudo tc qdisc del dev lo root
```

Para mudar de condição, remova a regra antes de adicionar outra, ou troque `add` por `change`.

### Condições do plano de testes

Todos os comandos começam com `sudo tc qdisc add dev lo root`.

| Condição | Regra | Rótulo (`--condicao`) | O que esperar |
| --- | --- | --- | --- |
| Estável | (sem regra) | `estavel` | TCP e UDP íntegros, perda 0% |
| Perda leve | `netem loss 5%` | `perda5` | TCP íntegro, porém mais lento; UDP com cerca de 5% de perda e `integro = nao` |
| Perda forte | `netem loss 20%` | `perda20` | O mesmo efeito, mais acentuado |
| Atraso moderado | `netem delay 25ms limit 100000` | `atraso50` | Latência de cerca de 50 ms (ida e volta) |
| Atraso alto | `netem delay 100ms limit 100000` | `atraso200` | Latência de cerca de 200 ms |
| Duplicação | `netem duplicate 10%` | `duplicacao` | Coluna `duplicados` com valores maiores que 0 no UDP |
| Reordenação | `netem delay 10ms reorder 10% limit 100000` | `reordenacao` | Coluna `fora_ordem` com valores maiores que 0 no UDP |

### Cuidados

- **O efeito vale nos dois sentidos.** Na `lo`, a regra vale na ida e na volta. Por isso `delay 25ms` dá 50 ms de ida e volta, e a perda atinge também o pedido e o ACK, não só os dados.
- **Afeta tudo que usa a `lo`.** Qualquer outro programa local que fale com `127.0.0.1` também fica lento ou perde pacotes. Remova a regra sempre que terminar.
- **Use `limit 100000` nas regras com atraso.** A fila padrão do netem guarda só 1000 pacotes. Com atraso, o UDP enche essa fila e o excesso é descartado, o que vira uma perda que não faz parte da condição testada.
- **TCP com atraso alto** demora mais, mas os timeouts são de 10 s, então o cliente não trava.
- **O `tc` precisa estar instalado.** Confira com `which tc || ls /sbin/tc /usr/sbin/tc`. Se não existir, instale com `sudo apt install iproute2`.

## Testes

```bash
python3 -m unittest discover -s tests -v
```

São 17 testes, que cobrem os critérios de aceitação da semana 1: ida e volta do cabeçalho; detecção de pacotes faltando, repetidos e trocados; dez execuções seguidas de cada servidor sem reinício; arquivo íntegro em rede estável; perda simulada de 1 em 10 dando exatamente 10%; linha de comando com parâmetro inválido; e CSV que abre no pandas.

## Decisões de implementação

**Sentido da transferência.** No cenário de arquivo, o cliente pede e o servidor envia. Assim o cliente é o receptor e calcula todas as métricas localmente, como pede o RF09, sem depender de um relatório do servidor que também poderia se perder no UDP.

**Troca de mensagens no cenário de arquivo:**

```
cliente                          servidor
INICIO(seq=sessão, {"cenario":"arquivo", "pausa_us":50})  →
                   ←  ACK(seq=sessão, {tamanho, blocos, sha256})
                   ←  DADOS(seq=0..10239, 1024 bytes cada)
                   ←  FIM ×3 (UDP) / ×1 (TCP), com os metadados de novo
```

No UDP, o cliente reenvia o INICIO se nada chegar em 1 s (até 10 tentativas). Se o ACK se perder, os metadados vêm de novo no FIM. O servidor ignora um INICIO repetido com o mesmo número de sessão, para não transmitir o arquivo duas vezes.

**Tipos de mensagem.** Além de DADOS, ACK e FIM (RF04), há INICIO (o pedido do cliente com o cenário) e ERRO (o servidor recusa o pedido).

**Definições das métricas no cenário de arquivo:**

- O tempo total vai do primeiro envio do cliente (o SYN, no TCP; o primeiro INICIO, no UDP) até a chegada do último bloco novo.
- 1 MB = 2²⁰ bytes, e a vazão conta só os bytes úteis únicos entregues.
- A latência é o RTT do pedido (INICIO → ACK). O jitter fica vazio, porque nesse cenário só há um RTT; ele será medido no cenário de tempo real.
- A perda é calculada como (blocos enviados − blocos únicos recebidos) ÷ enviados.
- Métricas que não se aplicam ficam vazias no CSV (o pandas lê como NaN), e uma execução que falha é gravada com `integro = falha`.

**Pausa do UDP (RNF03).** Calibrada nesta máquina, em loopback e sem netem, com 10 execuções por valor:

| Pausa (µs) | Execuções com perda | Tempo médio |
| --- | --- | --- |
| 0 | 3 de 5 (até 18%) | 0,05 s |
| 10 | 2 de 5 (até 1,1%) | 0,10 s |
| 20 | 1 de 5 (0,3%) | 0,21 s |
| 30 | 0 de 10 | 0,31 s |
| 50 | 0 de 10 | 0,51 s |

O padrão ficou em 50 µs, o que dá margem de segurança. **Recalibre na máquina onde a bateria final vai rodar**, com o servidor UDP no ar:

```bash
python3 scripts/calibrar_pausa.py --pausas 20 30 50 100 --repeticoes 10
```

O tamanho máximo do buffer de recepção do kernel (`net.core.rmem_max`, 208 KB por padrão) limita o buffer do cliente. Aumentá-lo (`sudo sysctl -w net.core.rmem_max=8388608`) reduz as perdas locais, mas a configuração precisa ser a mesma em todas as execuções.

## Pendências e alertas para a semana 2

- **Verificar com o netem.** Falta rodar com `sudo tc qdisc add dev lo root netem loss 20%` e confirmar o critério do RF05 sob perda real: o UDP termina sem travar e registra `integro = nao`. A perda simulada no código já é verificada nos testes.
- **Limite da fila do netem.** Com `delay 100ms` e o UDP enviando a ~20 mil pacotes/s, a fila chega a cerca de 2000 pacotes, o dobro do padrão de 1000. O script da bateria (RF11) deve usar `limit 100000` nas regras de atraso, como na tabela de [Testando com o netem](#testando-com-o-netem).
- **Comparação de tempo no arquivo.** Por causa da pausa, o UDP leva cerca de 0,5 s no arquivo, enquanto o TCP leva cerca de 0,07 s. O relatório precisa explicar que o tempo do UDP nesse cenário reflete a pausa calibrada, não o protocolo em si.
