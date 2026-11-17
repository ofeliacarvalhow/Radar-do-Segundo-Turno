# -*- coding: utf-8 -*-
"""
Confere a saida do ETL recontando tudo a partir dos CSVs.

POR QUE EXISTE
--------------
O `etl.py` calcula o share e o descompasso no mesmo lugar em que le o banco. Se
a conta estiver errada la, ela estaria errada nos dois. Este arquivo refaz as
contas a partir dos CSVs ja escritos, sem abrir o banco: e um segundo caminho
ate o mesmo numero. Rodar depois de cada `etl.py`.

    python validar.py

POR QUE EM SQL, E POR QUE O CODIGO NAO E COMPARTILHADO
------------------------------------------------------
As conferencias sao join e GROUP BY, e escreve-las em SQL faz o caminho ser
diferente de verdade, nao a mesma soma escrita outra vez. A carga dos CSVs esta
duplicada de proposito: importar o carregador do `painel/construir.py` faria o
conferente depender da peca que ele confere, e um erro comum aos dois passaria
despercebido nos dois.
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

SAIDA = Path(__file__).parent / "dados" / "saida"
CHAVES = ["lula", "flavio", "eleicoes", "tse"]

#como cada coluna entra na base. O que nao esta aqui entra como texto, e
#string vazia vira NULL, que e o que permite usar COUNT e AVG sem filtrar.
TIPOS = {
    "dia_completo": "INTEGER", "entra_nos_indicadores": "INTEGER",
    "mencoes": "INTEGER", "mencoes_total": "INTEGER",
    "share_imprensa": "REAL", "share_publico": "REAL",
    "indice_publico": "REAL", "descompasso_pp": "REAL",
    "distancia_variacao_total": "REAL",
    **{f"cita_{c}": "INTEGER" for c in CHAVES},
}


def ler(nome: str) -> list[dict]:
    caminho = SAIDA / nome
    if not caminho.exists():
        sys.exit(f"{nome} nao existe, rode `python etl.py` antes")
    #utf-8-sig porque o ETL grava com BOM, para o Power BI ler sem configurar
    with caminho.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def carregar(con: sqlite3.Connection, tabela: str, nome_csv: str) -> int:
    """Poe um CSV do ETL numa tabela da base em memoria."""
    linhas = ler(nome_csv)
    campos = list(linhas[0])
    con.execute(f"CREATE TABLE {tabela} ("
                + ", ".join(f"{c} {TIPOS.get(c, 'TEXT')}" for c in campos) + ")")

    def valor(coluna: str, cru: str):
        if cru == "":
            return None
        tipo = TIPOS.get(coluna, "TEXT")
        return int(cru) if tipo == "INTEGER" else float(cru) if tipo == "REAL" else cru

    con.executemany(
        f"INSERT INTO {tabela} VALUES ({', '.join('?' * len(campos))})",
        [tuple(valor(c, l[c]) for c in campos) for l in linhas])
    return len(linhas)


def um(con: sqlite3.Connection, sql: str, *args):
    return con.execute(sql, args).fetchone()[0]


def main() -> int:
    con = sqlite3.connect(":memory:")
    n_materias = carregar(con, "materias", "materias.csv")
    n_share = carregar(con, "share", "share_diario.csv")
    n_desc = carregar(con, "descompasso", "descompasso_diario.csv")
    falhas = 0

    #A contagem por termo em formato longo, que e o formato do share_diario.
    #UNION ALL em vez de quatro consultas: assim a comparacao com o CSV vira
    #um join so, e um termo que sumisse da agregacao apareceria como NULL.
    contagem = " UNION ALL ".join(
        f"""SELECT dia, '{c}' AS termo, SUM(cita_{c}) AS n
              FROM materias WHERE entra_nos_indicadores = 1 GROUP BY dia"""
        for c in CHAVES)

    #1. a contagem agregada tem de bater com a contagem materia a materia.
    #vale para toda janela: a imprensa e a mesma, so o lado do publico muda.
    #`IS NOT` no lugar de `<>` porque comparar com NULL por `<>` nunca e
    #verdadeiro, e a linha sem par passaria batido como se estivesse certa.
    erros = um(con, f"""
        SELECT COUNT(*) FROM share s
          LEFT JOIN ({contagem}) c ON c.dia = s.dia AND c.termo = s.termo
         WHERE s.mencoes IS NOT c.n""")
    print(f"1. mencoes batem com as materias .... {n_share} linhas, {erros} erros")
    falhas += erros

    #2. share e participacao: tem de somar 1 em cada dia DE CADA JANELA.
    #A chave inclui a janela porque cada uma tem a sua propria normalizacao;
    #somar as duas no mesmo dia daria 2 e nao significaria nada.
    for lado in ("share_imprensa", "share_publico"):
        grupos, ruins = con.execute(f"""
            SELECT COUNT(*), COALESCE(SUM(ABS(soma - 1) > 1e-5), 0) FROM (
                SELECT janela, dia, SUM({lado}) AS soma
                  FROM share WHERE {lado} IS NOT NULL
                 GROUP BY janela, dia)""").fetchone()
        print(f"2. {lado:14s} soma 1 ....... {grupos} dias-janela, {ruins} erros")
        falhas += ruins

    #3. a distancia de variacao total, recalculada do zero
    erros = um(con, """
        SELECT COUNT(*) FROM descompasso d
          LEFT JOIN (
                SELECT janela, dia,
                       0.5 * SUM(ABS(share_imprensa - share_publico)) AS dvt
                  FROM share
                 WHERE share_imprensa IS NOT NULL AND share_publico IS NOT NULL
                 GROUP BY janela, dia) r
            ON r.janela = d.janela AND r.dia = d.dia
         WHERE ABS(COALESCE(r.dvt, 0) - d.distancia_variacao_total) > 1e-5""")
    print(f"3. distancia recalculada ............ {n_desc} dias-janela, {erros} erros")
    falhas += erros

    #4. a marca de dia completo tem de ser coerente
    #
    #antes esta regra exigia que o ULTIMO dia fosse sempre incompleto, porque
    #o recorte acompanhava a coleta e o dia mais recente estava sempre pela
    #metade. Com RECORTE_FIM fixo isso deixou de valer: se a coleta ja passou
    #do fim do recorte, o ultimo dia esta fechado de verdade. O que continua
    #tendo de valer e a coerencia, um dia e completo para todas as suas
    #materias ou para nenhuma, nunca metade a metade. Em SQL isso e um
    #HAVING COUNT(DISTINCT), que pega o dia misto sem varrer a lista.
    mistos = [d for (d,) in con.execute("""
        SELECT dia FROM materias
         GROUP BY dia HAVING COUNT(DISTINCT dia_completo) > 1 ORDER BY dia""")]
    ok = not mistos
    ultimo_dia = um(con, "SELECT MAX(dia) FROM materias")
    ultimo = um(con, "SELECT MIN(dia_completo) FROM materias WHERE dia = ?",
                ultimo_dia) == 1
    print(f"4. marca de dia completo coerente ... {'sim' if ok else 'NAO'}"
          f"   (último dia {ultimo_dia}: {'fechado' if ultimo else 'parcial'})")
    if mistos:
        print(f"   dias com marca contraditória: {', '.join(mistos[:5])}")
    falhas += 0 if ok else 1

    #5. o resultado de cada janela, so com dias completos
    janelas = [j for (j,) in con.execute(
        "SELECT DISTINCT janela FROM share WHERE janela <> '' ORDER BY janela")]
    for janela in janelas:
        linhas = {termo: (mencoes, indice) for termo, mencoes, indice in con.execute("""
            SELECT termo, SUM(mencoes), SUM(indice_publico)
              FROM share
             WHERE janela = ? AND dia_completo = 1 AND share_publico IS NOT NULL
             GROUP BY termo""", (janela,))}
        if not linhas or not sum(i for _, i in linhas.values()):
            continue
        dias, primeiro, ultimo_da_janela = con.execute("""
            SELECT COUNT(DISTINCT dia), MIN(dia), MAX(dia)
              FROM share
             WHERE janela = ? AND dia_completo = 1 AND share_publico IS NOT NULL""",
            (janela,)).fetchone()

        imprensa = {c: linhas.get(c, (0, 0.0))[0] for c in CHAVES}
        publico = {c: linhas.get(c, (0, 0.0))[1] for c in CHAVES}
        si = {c: imprensa[c] / sum(imprensa.values()) for c in CHAVES}
        sp = {c: publico[c] / sum(publico.values()) for c in CHAVES}
        print(f"\n   janela '{janela}', {dias} dias completos "
              f"({primeiro} a {ultimo_da_janela}):")
        for c in CHAVES:
            print(f"     {c:9s} imprensa {100*si[c]:5.1f}%  publico {100*sp[c]:5.1f}%"
                  f"  descompasso {100*(si[c]-sp[c]):+6.1f} p.p.")
        print(f"     distancia de variacao total: "
              f"{0.5*sum(abs(si[c]-sp[c]) for c in CHAVES):.3f}")

    print(f"\n{'TUDO CONFERE' if not falhas else f'{falhas} FALHAS'}")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
