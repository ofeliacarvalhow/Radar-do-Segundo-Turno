# -*- coding: utf-8 -*-
"""
Monta a pasta de entrega com os datasets do projeto.

Uma pasta so, sem subpastas, so arquivos .csv. Nada de README, indice ou
dicionario: a pasta e de dados, e o nome de cada arquivo diz o que ele e.

Os arquivos sao copiados sem alteracao. Nenhuma coluna e acrescentada e
nenhum valor e convertido. O que sai do Google Trends vai como o Google
mandou, inclusive o preambulo e o valor "<1".

As datas no nome dos arquivos do Trends sao LIDAS do proprio arquivo, nao
escritas a mao, para nao ficarem desatualizadas se a exportacao for refeita
com outra janela.

Fica de fora `dados/trends_br.csv`: 10 linhas de UMA coleta (20/10 13h33) do
feed de buscas em alta, sem nenhum termo da eleicao brasileira. Nao sustenta
indicador nenhum e nao ha uso previsto.

    python exportar_datasets.py
    python exportar_datasets.py --destino "D:/outra/pasta"
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import shutil
from pathlib import Path

RAIZ = Path(__file__).parent
SAIDA = RAIZ / "dados" / "saida"
TRENDS = RAIZ / "dados" / "trends_explore"

MES = {1: "jan", 2: "fev", 3: "mar", 4: "abr", 5: "mai", 6: "jun",
       7: "jul", 8: "ago", 9: "set", 10: "out", 11: "nov", 12: "dez"}

#google Trends: origem -> assunto da consulta. A data entra depois, lida do
#arquivo. O assunto precisa distinguir consultas de MESMA janela:
#"candidatos" e "variacoes-de-nome" compartilham 04 a 20/10, e
#"candidatos" e "candidatos-sem-bolsonaro" compartilham as outras duas.
TRENDS_ASSUNTO = {
    "campanha.csv": "candidatos",
    "arco.csv": "candidatos",
    "janela_com_1o_turno.csv": "candidatos",
    "episodios_campanha.csv": "assuntos",
    "episodio_sigilo.csv": "sigilo",
    "episodio_jair.csv": "stf-e-bolsonaro",
    "diagnostico_nomes.csv": "variacoes-de-nome",
    "diagnostico_jair.csv": "pai-e-filho",
    "diagnostico_6x1.csv": "escala-6x1",
    #estas duas nao trazem o termo `bolsonaro`, que e a unica diferenca
    #para as de cima na mesma janela
    "antigo_so_frase_campanha.csv": "candidatos-sem-bolsonaro",
    "antigo_so_frase_arco.csv": "candidatos-sem-bolsonaro",
}

#os demais: origem -> nome na entrega
OUTROS = [
    (SAIDA / "materias.csv", "noticias-coletadas-g1-folha-cnn-uol.csv"),
    (SAIDA / "resultado_oficial.csv", "resultado-oficial-tse-presidente.csv"),
    (SAIDA / "pesquisas.csv", "pesquisas-eleitorais-publicadas.csv"),
    (SAIDA / "share_diario.csv", "participacao-de-cada-tema-por-dia.csv"),
    (SAIDA / "descompasso_diario.csv", "diferenca-entre-imprensa-e-busca-por-dia.csv"),
    (SAIDA / "episodios_diario.csv", "episodios-materias-e-busca-por-dia.csv"),
    (SAIDA / "veiculos.csv", "veiculos-monitorados.csv"),
]


def janela(caminho: Path) -> str:
    """Primeira e ultima data do CSV do Trends, como '07out-a-24out'."""
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    i = next(k for k, l in enumerate(linhas) if l.startswith("Dia,"))
    dias = [r[0] for r in csv.reader(linhas[i + 1:]) if r and r[0].strip()]
    def curta(d: str) -> str:
        data = dt.date.fromisoformat(d)
        return f"{data.day:02d}{MES[data.month]}"
    return f"{curta(dias[0])}-a-{curta(dias[-1])}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--destino", default=str(Path.home() / "Downloads"))
    args = ap.parse_args(argv)

    base = Path(args.destino) / f"midiometro-datasets-{dt.date.today().isoformat()}"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)

    faltando, usados = [], {}
    for origem_nome, assunto in TRENDS_ASSUNTO.items():
        origem = TRENDS / origem_nome
        if not origem.exists():
            faltando.append(origem_nome)
            continue
        nome = f"google-trends-{assunto}-{janela(origem)}.csv"
        if nome in usados:
            #duas consultas com mesmo assunto E mesma janela seriam
            #indistinguiveis; melhor falhar alto que sobrescrever calado
            print(f"  NOME REPETIDO: {nome} ({origem_nome} e {usados[nome]})")
            return 2
        usados[nome] = origem_nome
        shutil.copy2(origem, base / nome)

    for origem, nome in OUTROS:
        if not origem.exists():
            faltando.append(origem.name)
            continue
        shutil.copy2(origem, base / nome)

    print(f"\npasta: {base}\n")
    for p in sorted(base.iterdir()):
        with p.open(encoding="utf-8-sig") as f:
            linhas = sum(1 for _ in f)
        print(f"  {p.name:<52} {p.stat().st_size/1024:>8.1f} KB  {linhas:>6,} linhas")

    total = sum(p.stat().st_size for p in base.iterdir())
    print(f"\n  {len(list(base.iterdir()))} arquivos · {total/1048576:.1f} MB")
    if faltando:
        print(f"\n  NÃO ENCONTRADOS: {', '.join(faltando)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
