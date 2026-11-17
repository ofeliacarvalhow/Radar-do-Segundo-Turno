# -*- coding: utf-8 -*-
"""
Coletor do feed oficial de buscas em alta do Google (Brasil).

POR QUE ESTE ARQUIVO EXISTE
---------------------------
O descompasso de agenda compara o que a imprensa cobre com o que o publico
busca. A metade da imprensa ja esta no banco do Midiometro. A metade do publico
nao existe em lugar nenhum, e o feed do Google e um RETRATO DO AGORA, sem
historico: o que nao for coletado hoje nao volta. Por isso este coletor precisa
rodar varias vezes por dia, e precisa comecar a rodar antes de estar bonito.

O QUE ELE NAO FAZ, DE PROPOSITO
-------------------------------
O feed traz, para cada termo, 2 ou 3 MATERIAS que o Google associou aquela
busca, dos mesmos portais que o Midiometro ja coleta tres vezes por dia.
Guardar isso seria duplicar a base e extrair de novo o que ja existe. Entao
`news_item`, `picture` e `description` sao DESCARTADOS na leitura. Deste feed
sai so o que o Midiometro nao tem: o termo, o tamanho da busca e a hora.

Custo medido em 20/10/2026: a resposta tem 20,7 KB e o que sobra dela sao
0,3 KB. A 3 coletas por dia, o arquivo cresce ~80 KB por mes.

COMO RODAR
----------
    python coleta_trends.py              # coleta e grava
    python coleta_trends.py --ver        # so mostra o feed, nao grava nada
"""
from __future__ import annotations

import argparse
import csv
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

#o feed oficial. NAO e o pytrends: aquele raspa um endpoint interno, quebra
#quando o Google mexe e devolve so interesse relativo. Este e RSS publico.
URL_FEED = "https://trends.google.com/trending/rss?geo=BR"

#o Google recusa a chamada sem navegador declarado
CABECALHOS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

#namespace das extensoes do proprio Google dentro do RSS
NS = {"ht": "https://trends.google.com/trending/rss"}

PASTA_DADOS = Path(__file__).parent / "dados"
ARQUIVO = PASTA_DADOS / "trends_br.csv"

#a base do Midiometro usa dia em Brasilia, nao em UTC (armadilha 1 do README).
#O feed carimba em -0700 (Pacifico), entao converter e obrigatorio, nao enfeite.
FUSO_BRASILIA = timezone(timedelta(hours=-3))

COLUNAS = [
    "coletado_em",      #quando ESTA execucao rodou, em UTC
    "termo",            #a busca em alta, como o Google escreve
    "trafego_texto",    #como veio: "200+", "5000+", "1 mi+"
    "trafego_min",      #o mesmo, como numero: o piso da faixa
    "subiu_em",         #pubDate do termo, convertido para Brasilia
    "dia",              #o dia em Brasilia, para casar com a base do Midiometro
    "posicao_no_feed",  #1 = a busca mais em alta daquele momento
]


def _buscar_feed(url: str = URL_FEED) -> bytes:
    """Baixa o feed. O conteudo fica so na memoria, e o XML cru nunca e gravado."""
    requisicao = urllib.request.Request(url, headers=CABECALHOS)
    with urllib.request.urlopen(requisicao, timeout=30) as resposta:
        return resposta.read()


def converter_trafego(texto: str) -> int:
    """
    "200+" -> 200 | "5000+" -> 5000 | "1 mi+" -> 1000000

    O Google nao da o numero de buscas, da uma FAIXA. Guardamos o piso dela,
    que e o unico numero que o feed de fato afirma. Qualquer media calculada
    sobre isto e um piso, e a nota metodologica tem de dizer isso.
    """
    if not texto:
        return 0
    limpo = texto.strip().lower().replace("+", "").replace(".", "").replace(",", "")
    multiplicador = 1
    for sufixo, fator in (("mi", 1_000_000), ("mil", 1_000), ("k", 1_000)):
        if limpo.endswith(sufixo):
            limpo = limpo[: -len(sufixo)].strip()
            multiplicador = fator
            break
    try:
        return int(float(limpo) * multiplicador)
    except ValueError:
        return 0


def _converter_data(texto: str) -> tuple[str, str]:
    """pubDate do RSS -> (carimbo em Brasilia, dia em Brasilia)."""
    if not texto:
        return "", ""
    try:
        momento = parsedate_to_datetime(texto).astimezone(FUSO_BRASILIA)
    except (TypeError, ValueError):
        return "", ""
    return momento.isoformat(), momento.date().isoformat()


def ler_feed(bruto: bytes, coletado_em: str) -> list[dict]:
    """
    XML -> lista de linhas.

    Aqui e onde `news_item`, `picture` e `description` sao deixados de fora:
    ficam de fora pela regra de nao duplicar o que o Midiometro ja extrai.
    """
    raiz = ET.fromstring(bruto)
    linhas = []
    for posicao, item in enumerate(raiz.findall(".//item"), start=1):
        termo = (item.findtext("title") or "").strip()
        if not termo:
            continue
        trafego = (item.findtext("ht:approx_traffic", "", NS) or "").strip()
        subiu_em, dia = _converter_data(item.findtext("pubDate") or "")
        linhas.append({
            "coletado_em": coletado_em,
            "termo": termo,
            "trafego_texto": trafego,
            "trafego_min": converter_trafego(trafego),
            "subiu_em": subiu_em,
            "dia": dia,
            "posicao_no_feed": posicao,
        })
    return linhas


def _chave(linha: dict) -> tuple[str, str]:
    """
    O que torna duas linhas a MESMA observacao.

    Nao e a hora da coleta: o termo fica em alta por horas, e as tres coletas do
    dia devolvem o mesmo termo com o mesmo `pubDate`. Gravar as tres inflaria a
    contagem de buscas sem nenhuma busca nova ter acontecido. A identidade e
    (termo, quando ele subiu), e e isso que torna rodar duas vezes de graca.
    """
    return (linha["termo"], linha["subiu_em"])


def _ja_gravadas() -> set[tuple[str, str]]:
    if not ARQUIVO.exists():
        return set()
    with ARQUIVO.open("r", encoding="utf-8", newline="") as f:
        return {_chave(l) for l in csv.DictReader(f)}


def gravar(linhas: list[dict]) -> int:
    """Acrescenta ao CSV so o que ainda nao esta la. Devolve quantas entraram."""
    PASTA_DADOS.mkdir(exist_ok=True)
    conhecidas = _ja_gravadas()
    novas = [l for l in linhas if _chave(l) not in conhecidas]
    if not novas:
        return 0
    arquivo_novo = not ARQUIVO.exists()
    with ARQUIVO.open("a", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUNAS)
        if arquivo_novo:
            escritor.writeheader()
        escritor.writerows(novas)
    return len(novas)


def main(argv=None) -> int:
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument("--ver", action="store_true",
                            help="mostra o feed e nao grava nada")
    args = analisador.parse_args(argv)

    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        bruto = _buscar_feed()
    except (urllib.error.URLError, TimeoutError) as erro:
        #o feed cai de vez em quando. A coleta seguinte cobre o buraco.
        print(f"feed indisponivel: {erro}", file=sys.stderr)
        return 1

    linhas = ler_feed(bruto, agora)
    print(f"feed: {len(bruto)/1024:.1f} KB, {len(linhas)} termos")

    if args.ver:
        for l in linhas:
            print(f"  {l['posicao_no_feed']:>2}. {l['termo']:<40} "
                  f"{l['trafego_texto']:>8} -> {l['trafego_min']:>9,}  {l['subiu_em']}")
        print("\n(--ver: nada gravado)")
        return 0

    novas = gravar(linhas)
    repetidas = len(linhas) - novas
    tamanho = ARQUIVO.stat().st_size / 1024 if ARQUIVO.exists() else 0
    print(f"gravadas {novas} novas, {repetidas} ja conhecidas")
    print(f"{ARQUIVO.name}: {tamanho:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
