# Radar da Cobertura do 2º Turno

Mede como G1, Folha de S.Paulo, CNN Brasil e UOL cobriram o segundo turno de
2026, e compara essa cobertura com o que o eleitor procurou no Google.

O recorte vai de 4 a 26 de outubro, do primeiro turno ao dia seguinte à
apuração. São 19.082 notícias factuais, das quais 2.376 citam um candidato e
entram nos indicadores.

Esse número sai de `dados/saida/materias.csv`, e o painel o lê do payload em
vez de trazê-lo escrito. Ele já esteve escrito nos dois lugares: dizia 19.051,
o ETL rodou de novo, e texto e dados discordaram por 31 matérias até alguém
perguntar se o número estava certo.

O painel fica em `painel/index.html`. Abre com duplo clique, sem servidor.

## O que o projeto responde

A pergunta é fácil de enunciar e difícil de responder no olho. A cobertura foi
equilibrada entre os dois candidatos, e ela acompanhou o que as pessoas estavam
procurando?

Quatro indicadores tentam responder:

- **descompasso de agenda**, a distância entre a pauta da imprensa e a do
  público;
- **índice de equilíbrio**, se um veículo cita os dois candidatos na mesma
  medida;
- **share of voice**, que fatia do noticiário cada candidato ocupou;
- **tom das manchetes**, por dicionário de palavras e, ao lado, pela IA;
- **distribuição por tema**, pela leitura da manchete por IA.

O quinto depende de uma análise que ainda está rodando, e por isso ele tem uma
trava. Enquanto a IA não cobrir 80% do recorte sem nenhum dia vazio, a seção
mostra o que já se sabe em barras cinzas, com o aviso de que o número é parcial
e os dias que faltam escritos por extenso. A trava está em `painel/construir.py`
e se abre sozinha quando a cobertura chega lá.

## Como rodar

A análise de IA que alimenta os dois últimos indicadores roda do lado do
Midiômetro, e num período fechado ela precisa de `--ordem antigas`, senão a
cota diária acaba deixando os primeiros dias do recorte sem nenhuma leitura.

```
python etl.py            # lê o banco e escreve os CSV do recorte
python etl_episodios.py  # episódios e pesquisas, camada separada
python validar.py        # confere os números; tem de dizer TUDO CONFERE
python painel/construir.py
```

O `validar.py` recalcula os indicadores a partir dos CSV, sem reaproveitar
nada do `etl.py`. Se os dois discordarem, ele acusa.

Outros comandos, quando precisar:

```
python tse.py                # resultado oficial, por requisição de faixa
python checar_tse.py         # pergunta se o TSE publicou algo novo
python exportar_datasets.py  # os CSV da entrega, em Downloads
python exportar_excel.py     # os mesmos dados num .xlsx, uma aba por arquivo
```

## O banco

O ETL lê o banco do Midiômetro em modo somente leitura, no repositório
`ADOISVERSAOFINALULTRA-main`. Este projeto nunca escreve nele. Quem coleta é o
Midiômetro; aqui só se lê o que ele já guardou.

## Decisões que não são óbvias

**O recorte tem fim declarado.** `RECORTE_FIM` está fixo em 26/10 no `etl.py`.
Sem isso o recorte cresce sozinho a cada coleta nova, e foi o que aconteceu uma
vez: o período pulou para 31/10 numa execução e levou os indicadores junto.

**A data de cada matéria é a de Brasília**, não a do servidor. Filtrar pela
data crua de publicação cortaria um terço da CNN e enviesaria qualquer
comparação entre veículos.

**O termo do candidato pesa mais que qualquer outra escolha do método.** Medir
a busca por "flávio bolsonaro", por "flávio" ou por "bolsonaro" dá descompasso
de 0,276, 0,195 ou 0,082 sobre os mesmos dados. O painel usa "bolsonaro" com
fatia de 90% e declara a faixa de 80% a 96%. Por isso o indicador aparece como
faixa, e nunca como número único.

**O índice do Google Trends é relativo.** O 100 marca o dia de maior busca
daquela consulta. Duas consultas diferentes não estão na mesma escala, não se
somam e não se comparam. Os arquivos em `dados/trends_explore` guardam cada
consulta separada por esse motivo.

**A posição na capa não se compara entre veículos.** O G1 tem 26 posições lidas
contra 142 da CNN, então "chegou ao topo" significa coisas diferentes em cada
um.

**Os dois métodos de tom discordam em metade dos casos, e o painel mostra os
dois.** Nas mesmas 1.370 manchetes, dicionário e IA chegam ao mesmo rótulo em
50,3% das vezes. O dicionário chama 76,9% de neutras e a IA chama 55,4%. A
diferença se concentra numa célula: 284 manchetes sem palavra carregada o
bastante para o dicionário, que a IA leu como tomando partido. Trocar um pelo
outro em silêncio faria o cartão de tom mudar de número entre duas visitas sem
explicação, então a seção mostra a matriz inteira. Nenhum dos dois é gabarito:
o dicionário é transparente e não entende ironia, a IA entende contexto e não
mostra a conta.

**A média de cobertura esconde o buraco; quem mostra é o pior dia.** Em 6 de
novembro a análise de IA cobria 55% do recorte, o que soa como meia amostra. Os
sete primeiros dias, de 4 a 10 de outubro, tinham zero. A IA processa do dia
mais novo para o mais velho e a cota diária acabou antes de chegar neles. A
distribuição calculada ali dava 80,9% de política, e seria uma frase sobre 12 a
26 de outubro vendida como frase sobre o recorte. Por isso a trava olha o dia
com menos cobertura, e não a média.

**O tema da IA não entra cru no gráfico.** A metodologia pede uma etiqueta entre
onze, e o modelo devolveu 32 valores. Parte é grafia, como `politica` sem acento
ao lado de `política`, e parte é subconjunto, como `clima` dentro de meio
ambiente. Essas entram na categoria. Mas `energia`, `trabalho` e `justiça` não
cabem em nenhuma das onze sem que alguém decida por elas, então viram `outros`,
que aparece no gráfico em vez de sumir. São 1,2% das matérias lidas. A tabela
está em `TEMA_EQUIVALE`, no `etl.py`.

**O índice de equilíbrio precisa de volume diário.** Na campanha são cerca de
120 menções por candidato por dia e uma diferença de vinte matérias mal move o
indicador. Fora dela o volume cai e a mesma diferença passa a movê-lo meio
ponto. Por isso ele é reportado por fase e por veículo, nunca por dia isolado.

## Erros que já custaram caro

Três achados deste projeto foram publicados e depois derrubados por medição
nova. Vale saber quais foram, porque o mesmo erro tem três formas.

O primeiro foi o termo do candidato. A primeira versão media a busca por
"flávio bolsonaro" e encontrava descompasso de 0,276. O público digita o
sobrenome, não o nome completo, e com o termo certo o número cai para 0,08.

O segundo foram as definições de episódio. `pec` sozinho misturava três PECs
diferentes, e `preso` pegava plantão policial inteiro: o que parecia o maior
episódio da base, com 594 matérias, era notícia de crime comum.

O terceiro foi a janela. A fase de repercussão tinha um dia só, dava equilíbrio
de 0,621, e isso virou a leitura de que a simetria da imprensa durava só
enquanto a disputa estivesse em aberto. Um dia não estabelece tendência.
Declarar o número como provisório não protege a conclusão escrita em cima dele.

## Estrutura

```
etl.py                 recorte, termos, indicadores
etl_episodios.py       episódios e pesquisas, separado para não derrubar o ETL
validar.py             recalcula tudo por outro caminho
tse.py                 resultado oficial sem baixar 380 MB
checar_tse.py          só pergunta se há novidade, não baixa nada
exportar_datasets.py   a pasta de entrega
exportar_excel.py      o mesmo em planilha
painel/modelo.html     o painel, onde se edita
painel/construir.py    injeta os dados e gera o index.html
dados/saida/           os CSV
dados/trends_explore/  as exportações do Google Trends
```

O `index.html` é gerado. Para mudar o painel, edite o `modelo.html` e rode o
`construir.py` de novo.

## Como o painel vai ao ar

O site é uma página estática. O `vercel.json` diz isso à Vercel: sem instalação,
sem build, e a pasta publicada é `painel`, de modo que o `index.html` responde
na raiz do domínio.

O `.vercelignore` deixa de fora o que não é o site: os `.py`, os dados, e dentro
da própria pasta `painel` o `modelo.html` e o `construir.py`, que são a cozinha
da página. Vão ao ar quatro arquivos e cerca de 850 KB.

Para atualizar o que está publicado, o caminho é o mesmo de sempre, mais um
empurrão: rodar `etl.py`, `etl_episodios.py`, `validar.py` e
`painel/construir.py`, e subir o `painel/index.html` novo para o
repositório. A Vercel republica sozinha a cada envio.

## Reproduzir os números

Os CSV de `dados/saida` já vêm prontos, então o painel pode ser gerado sem
acesso ao banco. Para refazer tudo do zero é preciso a base do Midiômetro, que
mora em outro repositório e é lida em modo somente leitura:

```
python etl.py              # lê o banco, escreve os CSV
python etl_episodios.py    # episódios e pesquisas
python validar.py          # tem de terminar com TUDO CONFERE
python painel/construir.py # gera painel/index.html
```
