# Comparação Prática entre TCP, UDP e um Protocolo Híbrido

Nov 8, 2026 · @Diego

## Visão geral

O projeto compara TCP, UDP e um protocolo híbrido usando programas cliente e servidor escritos em Python pelo próprio grupo, medidos nas mesmas condições de rede.

Em vez de observar aplicações prontas, cujo funcionamento interno não se controla, cada protocolo transmite exatamente o mesmo conteúdo em três cenários: transferência de arquivo, fluxo em tempo real (voz ou jogo) e requisição e resposta (chat). Os testes rodam em Linux, e a rede é degradada de forma controlada com o tc netem; os próprios programas calculam as métricas. O resultado são gráficos que respondem com dados quando cada protocolo é a melhor escolha.

### Objetivo

Determinar, com medições próprias, em quais situações a confiabilidade do TCP compensa seu custo em tempo e em quais a rapidez do UDP é preferível, e mostrar como um mecanismo simples de confirmação e retransmissão muda o comportamento do UDP.

### Escopo

- **Dentro do escopo:** cliente e servidor em TCP, UDP e híbrido; três cenários de tráfego; degradação controlada da rede com `tc netem`; coleta automática de métricas; tabelas, gráficos e relatório.
- **Fora do escopo:** controle de congestionamento próprio, criptografia, interface gráfica e testes com tráfego real de terceiros pela internet pública.

### Como ler os requisitos

Os requisitos estão divididos em três grupos: **obrigatórios (RF)**, sem os quais o projeto não responde à sua pergunta; **desejáveis (RD)**, que só entram se sobrar tempo; e **não funcionais (RNF)**, que dizem como o sistema deve ser construído e executado. Cada requisito funcional traz um critério de aceitação, que diz como verificar que ele foi cumprido.

## Requisitos funcionais obrigatórios

Sem estes requisitos o projeto não responde à sua pergunta central. Todos devem estar prontos até o fim da semana 3.

### RF01 — Servidor TCP

O servidor abre um socket `SOCK_STREAM` numa porta configurável (padrão 5000), aceita uma conexão por execução e atende o cenário que o cliente indicar na primeira mensagem. Ao terminar, fecha a conexão e fica pronto para a próxima execução, sem precisar ser reiniciado. Parte do primeiro laboratório do livro (cliente e servidor simples).

**Critério de aceitação:** atende dez execuções seguidas de cada cenário sem reinício e sem erro.

### RF02 — Servidor UDP

O servidor abre um socket `SOCK_DGRAM` numa porta configurável (padrão 5001) e responde a cada datagrama conforme o cenário. Como não há conexão, cada execução é identificada pelo endereço e pela porta de origem do cliente. Parte do laboratório UDP Pinger.

**Critério de aceitação:** atende dez execuções seguidas de cada cenário sem reinício e sem erro.

### RF03 — Cliente configurável

O cliente recebe todos os parâmetros pela linha de comando, com `argparse`: `--protocolo` (`tcp`, `udp` ou `hibrido`), `--cenario` (`arquivo`, `tempo-real` ou `req-resp`), `--host`, `--porta`, `--condicao` (rótulo da condição de rede, gravado no CSV) e `--repeticao`. Assim, todos os testes usam o mesmo programa, sem editar código.

**Critério de aceitação:** `python cliente.py --protocolo udp --cenario arquivo --host 127.0.0.1` executa sem alterações no código, e um parâmetro inválido mostra a mensagem de uso.

### RF04 — Formato das mensagens

Toda mensagem começa com um cabeçalho fixo de 13 bytes, empacotado com `struct` no formato `!BId` (ordem de bytes da rede): tipo da mensagem (1 byte: dados, ACK ou fim), número de sequência (4 bytes, sem sinal) e horário de envio (8 bytes, obtido com `time.perf_counter`). No TCP, que entrega um fluxo contínuo de bytes sem fronteiras entre mensagens, cada mensagem é precedida ainda por 2 bytes com o seu tamanho, para que o receptor saiba onde uma termina e a próxima começa.

**Critério de aceitação:** um teste empacota e desempacota mensagens e recupera os mesmos valores; numa sequência de teste com números faltando, repetidos e trocados, o receptor detecta cada caso corretamente.

### RF05 — Cenário 1: transferência de arquivo

Um arquivo de 10 MB é gerado uma única vez com `os.urandom` e reutilizado em todos os testes. Ele é enviado em blocos de 1024 bytes (cerca de 10 mil blocos). No UDP, o fim da transferência é avisado por uma mensagem do tipo fim, enviada três vezes por segurança, e o receptor também encerra após 2 s sem receber nada. O receptor remonta o arquivo com os blocos que chegaram e compara seu hash SHA-256 com o do original.

**Critério de aceitação:** em rede estável, TCP e UDP entregam o arquivo com hash idêntico; sob perda, o programa termina normalmente e registra a integridade como "não" no caso do UDP, sem travar.

### RF06 — Cenário 2: fluxo em tempo real

O cliente envia pacotes de 160 bytes de dados a cada 20 ms durante 30 s (1500 pacotes), simulando uma chamada de voz ou as atualizações de um jogo. O servidor devolve cada pacote (eco), e o cliente calcula latência, jitter e perda a partir das respostas. O ritmo é controlado por horários absolutos (o envio n acontece em t0 + n × 20 ms), para que pequenos atrasos não se acumulem.

**Critério de aceitação:** em rede estável, os 1500 pacotes são enviados com intervalo médio de 20 ms, com variação de no máximo 1 ms.

### RF07 — Cenário 3: requisição e resposta

O cliente envia 500 mensagens de 64 bytes, uma de cada vez, e só envia a próxima depois de receber a resposta do servidor, também de 64 bytes, simulando um chat ou consultas a um servidor. No UDP, se a resposta não chegar em 1 s, a troca conta como perdida e o cliente segue para a próxima.

**Critério de aceitação:** as 500 trocas terminam em todas as condições de rede, e o tempo médio por troca é registrado.

### RF08 — Protocolo híbrido (pare e espere)

Sobre UDP, o emissor envia um pacote e espera o ACK com o mesmo número de sequência antes de enviar o próximo. Se o ACK não chegar em 200 ms, o pacote é reenviado, até no máximo 10 tentativas; depois disso a execução é abortada e registrada como falha. O receptor confirma todo pacote que recebe, inclusive repetidos (pois o ACK anterior pode ter se perdido), e descarta os dados duplicados. É obrigatório no cenário de arquivo e opcional nos demais.

**Critério de aceitação:** com perda de 20%, o arquivo chega com hash idêntico ao original, e o número de retransmissões é registrado.

### RF09 — Cálculo das métricas

Ao fim de cada execução, o cliente calcula as métricas abaixo, sempre com as mesmas definições para os três protocolos:

| Métrica | Definição | Unidade |
| --- | --- | --- |
| Tempo total | Do primeiro envio ao último dado recebido ou confirmado | s |
| Vazão | Bytes úteis entregues ÷ tempo total | MB/s |
| Latência | Média dos tempos de ida e volta (RTT) | ms |
| Jitter | Média das diferenças absolutas entre RTTs consecutivos | ms |
| Perda | (enviados − recebidos) ÷ enviados × 100 | % |
| Fora de ordem | Pacotes que chegaram depois de um de número maior | contagem |
| Duplicados | Pacotes recebidos mais de uma vez | contagem |
| Retransmissões | Reenvios feitos pelo híbrido | contagem |
| Integridade | Hash SHA-256 do arquivo recebido igual ao do original | sim ou não |

**Critério de aceitação:** numa execução de teste com perda conhecida, simulada no próprio código (descartando um pacote a cada dez), as métricas calculadas batem com os valores esperados.

### RF10 — Registro em CSV

Cada execução acrescenta uma linha ao arquivo `resultados.csv`, criando-o com cabeçalho se ainda não existir. As colunas são: `data_hora`, `protocolo`, `cenario`, `condicao`, `repeticao`, `tempo_s`, `vazao_mbs`, `latencia_ms`, `jitter_ms`, `perda_pct`, `fora_ordem`, `duplicados`, `retransmissoes` e `integro`.

**Critério de aceitação:** o arquivo abre numa planilha ou no pandas sem nenhum ajuste manual.

### RF11 — Execução automática da bateria de testes

Um script percorre todas as condições de rede: aplica a regra do `tc netem`, roda pelo menos cinco repetições de cada combinação de protocolo e cenário e remove a regra antes de passar à próxima condição. A remoção também acontece se o script for interrompido ou der erro (com `trap` no bash ou `try/finally` em Python).

**Critério de aceitação:** um único comando executa a bateria completa sem intervenção, e ao final `tc qdisc show dev lo` não mostra nenhuma regra netem.

## Requisitos desejáveis

Estes requisitos só entram depois que todos os obrigatórios estiverem prontos e testados; nenhum deles é necessário para as conclusões principais. A ordem da tabela é a ordem sugerida de implementação.

| ID | Requisito | Descrição | Critério de aceitação |
| --- | --- | --- | --- |
| RD01 | Janela deslizante (Go-Back-N) | Evolução do RF08: o emissor mantém até N pacotes enviados sem confirmação, com um temporizador para o mais antigo. Em caso de timeout, reenvia todos a partir dele. O receptor só aceita pacotes em ordem e usa ACK cumulativo. Baseado no laboratório de RDT e no capítulo 3 do livro. | Arquivo íntegro com perda de 20% e vazão maior que a do pare e espere com atraso alto. |
| RD02 | Ajuste do tamanho da janela | Parâmetro `--janela` para rodar o RD01 com janelas de 1, 4, 16 e 64 pacotes. | Gráfico de vazão por tamanho de janela gerado para a condição de atraso alto. |
| RD03 | Modo parcialmente confiável | Variante do híbrido que só reenvia um pacote enquanto ele tiver menos de um prazo configurável (padrão 100 ms); depois disso, desiste dele. Pensado para o cenário de tempo real. | Nenhum pacote é reenviado depois do prazo, e perda e jitter são registrados no CSV. |
| RD04 | Captura de pacotes | Grava um arquivo `.pcap` por execução com `tcpdump` e conta as retransmissões do TCP no Wireshark com o filtro `tcp.analysis.retransmission`. | Contagem de retransmissões do TCP em pelo menos uma condição de perda incluída no relatório. |
| RD05 | Teste entre duas máquinas | Roda cliente e servidor em computadores diferentes, aplicando o `tc netem` na placa de rede em vez da `lo`. | Bateria reduzida (rede estável e perda de 5%) executada e comparada com os resultados no loopback. |
| RD06 | Geração automática de gráficos | Script com `matplotlib` que lê o CSV, calcula média e desvio padrão das repetições e gera os gráficos em PNG. | Um único comando gera todos os gráficos do relatório. |
| RD07 | Medição de overhead | Compara os bytes úteis com os bytes totais transmitidos, incluindo cabeçalhos, ACKs e retransmissões. | Tabela de overhead por protocolo nas condições de rede estável e perda de 5%. |

## Requisitos não funcionais

Estes requisitos garantem que as comparações sejam justas e que qualquer pessoa consiga reproduzir os resultados.

- **RNF01 — Plataforma:** Linux (todos os testes na mesma distribuição e na mesma máquina) e Python 3.10 ou superior. O núcleo usa só a biblioteca padrão (`socket`, `struct`, `time`, `hashlib`, `csv`, `argparse` e `os`); o `matplotlib` é usado apenas para os gráficos. O `tc` vem do pacote `iproute2` e exige `sudo`.
- **RNF02 — Comparação justa:** os três protocolos enviam o mesmo volume de dados úteis, com o mesmo tamanho de bloco, na mesma máquina e sem outros downloads ativos. A ordem dos protocolos é alternada entre as repetições, para que uma variação momentânea da máquina não favoreça sempre o mesmo.
- **RNF03 — Ritmo do UDP limitado:** no cenário de arquivo, o emissor UDP faz uma pausa configurável entre blocos, calibrada para que a perda em rede estável seja 0%. Sem isso, o buffer do receptor estoura e surgem perdas que não vêm da rede.
- **RNF04 — Medição de tempo:** intervalos são medidos com `time.perf_counter`, que é monotônico e de alta resolução, nunca com `time.time`. A latência é sempre medida como tempo de ida e volta no próprio cliente, para não depender de relógios sincronizados.
- **RNF05 — Repetições:** pelo menos cinco execuções por combinação de protocolo, cenário e condição, e o relatório apresenta média e desvio padrão.
- **RNF06 — Robustez:** nenhuma espera é infinita; toda leitura de socket tem timeout. Uma execução que falha é registrada como falha e a bateria segue para a próxima, em vez de parar.
- **RNF07 — Organização:** código num repositório Git com pastas separadas para código-fonte, scripts, resultados e gráficos, e um README que explica como reproduzir a bateria completa.

## Plano de testes

Cada combinação de protocolo, cenário e condição de rede roda pelo menos 5 vezes, e o relatório usa a média para reduzir o efeito de variações pontuais.

As condições de rede são aplicadas com o `tc netem` na interface de loopback (`lo`), mudando uma variável por vez. Na `lo` o efeito vale nos dois sentidos, por isso os atrasos configurados são metade do valor de ida e volta desejado. Um script percorre todas as condições e protocolos automaticamente:

| Condição | Comando |
| --- | --- |
| Rede estável | `sudo tc qdisc del dev lo root` (sem regras) |
| Perda leve | `sudo tc qdisc add dev lo root netem loss 5%` |
| Perda forte | `sudo tc qdisc add dev lo root netem loss 20%` |
| Atraso moderado (50 ms ida e volta) | `sudo tc qdisc add dev lo root netem delay 25ms` |
| Atraso alto (200 ms ida e volta) | `sudo tc qdisc add dev lo root netem delay 100ms` |
| Duplicação | `sudo tc qdisc add dev lo root netem duplicate 10%` |
| Reordenação | `sudo tc qdisc add dev lo root netem delay 10ms reorder 10%` |

Os resultados de cada cenário entram numa tabela-resumo como esta, depois transformada em gráficos como "tempo de transferência × taxa de perda", com uma linha por protocolo:

| Cenário | Condição | Protocolo | Tempo (s) | Vazão (MB/s) | Latência (ms) | Jitter (ms) | Perda (%) | Íntegro? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Arquivo 10 MB | Perda 5% | TCP |  |  |  |  |  |  |
| Arquivo 10 MB | Perda 5% | UDP |  |  |  |  |  |  |
| Arquivo 10 MB | Perda 5% | Híbrido |  |  |  |  |  |  |

Resultados esperados, a confirmar com os testes: o TCP e o híbrido entregam o arquivo íntegro, mas ficam mais lentos com perda; o UDP mantém o ritmo e a latência baixos no fluxo em tempo real, ao custo de dados perdidos.

## Restrições e limitações

Estes pontos não impedem o projeto, mas limitam o alcance das conclusões e devem ser citados no relatório.

- **Efeito dobrado no loopback:** na `lo`, as regras do netem valem para os dois sentidos, então perdas e atrasos se aplicam na ida e na volta. Por isso os atrasos configurados são metade do RTT desejado, e a perda efetiva de ida e volta é maior que a porcentagem configurada.
- **Valores relativos, não absolutos:** os programas são escritos em Python, mais lento que as implementações reais. Os números servem para comparar os protocolos entre si, não para prever o desempenho de aplicações comerciais.
- **Integridade do UDP:** sob perda, o UDP puro não entrega o arquivo completo. Isso é o comportamento esperado do protocolo, não um defeito do programa.
- **Duas máquinas:** entre computadores diferentes, o netem na placa de rede afeta só os pacotes de saída, e a rede real acrescenta variações próprias.
- **Escopo do híbrido:** ele demonstra o princípio da confiabilidade sobre UDP, mas não tem controle de congestionamento, timeout adaptativo nem criptografia, como o TCP e o QUIC reais.
- **Captura de pacotes:** o Wireshark e o `tcpdump` funcionam normalmente no Linux, mas capturar tudo em todas as execuções gera arquivos grandes; por isso a captura é um requisito desejável (RD04), feita só em execuções escolhidas.

## Plano de trabalho

O prazo de entrega é 7 de dezembro de 2026, o que dá quatro semanas: três para programar e testar, e a última reservada para a escrita.

&#91;embedded content: plano de trabalho · 4 semanas até a entrega em 7 de dezembro\]

- **Semana 1 (9 a 15/11) — Base TCP e UDP:** RF01 a RF05, RF09 e RF10. Cliente e servidor nos dois protocolos, cenário de arquivo e métricas gravadas no CSV.
- **Semana 2 (16 a 22/11) — Cenários e script:** RF06, RF07 e RF11. Cenários de tempo real e de requisição e resposta, mais o script da bateria de testes. Ao fim da semana, fazer uma rodada de testes pequena para detectar números estranhos cedo.
- **Semana 3 (23 a 29/11) — Híbrido e testes:** RF08 e a rodada completa de testes. É a semana mais importante, porque todos os dados do relatório saem daqui.
- **Semana 4 (30/11 a 6/12) — Relatório:** gráficos, relatório e apresentação. Os requisitos desejáveis, a começar pelo RD06 e pelo RD01, só entram se a semana 3 terminar no prazo.
- **7/12 (segunda-feira) — Entrega.**

Se algo atrasar, corta-se uma funcionalidade incremental, nunca o tempo do relatório. Em último caso, o cenário de requisição e resposta pode sair e as condições de rede podem ser reduzidas a estável, perda 5%, perda 20% e atraso alto.
