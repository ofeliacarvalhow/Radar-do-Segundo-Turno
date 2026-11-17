# -*- coding: utf-8 -*-
"""
ETL do Midiometro Eleitoral: base do Midiometro + Google Trends -> CSVs do painel.

O QUE ESTE ARQUIVO GARANTE
--------------------------
1. **Nunca escreve na base.** Abre o `.db` com `mode=ro`, que torna um acidente
   impossivel em vez de improvavel, e nao chama `banco_remoto --publicar` --
   o unico comando do projeto que alcanca a base compartilhada.
2. **Nunca duplica a base.** Aponta para o `.db` que ja existe (74 MB) e exporta
   so o recorte, em CSV. A saida inteira fica na casa de 3 MB.
3. **Aplica as regras ja auditadas**, em vez de recontar do zero a cada leitura.
   Cada uma delas defende contra uma armadilha medida; os comentarios dizem qual.

COMO RODAR
----------
    python etl.py              # le a base e escreve dados/saida/
    python etl.py --resumo     # so mostra os numeros, nao escreve nada
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------------
#CONFIGURACAO
# --------------------------------------------------------------------------

BANCO = Path(r"C:\Users\Lenovo\Documents\ADOISVERSAOFINALULTRA-main"
             r"\ADOISVERSAOFINALULTRA-main\midiometro\dados\midiometro.db")

AQUI = Path(__file__).parent
SAIDA = AQUI / "dados" / "saida"

#DUAS JANELAS DO EXPLORE, E ELAS NAO PODEM SER SOMADAS.
#O indice do Explore e normalizado DENTRO da consulta: 100 e o pico daquela
#janela. Como o dia da votacao concentra uma busca muito maior que qualquer
#dia de campanha, inclui-lo achata todo o resto. Medido: no arco completo os
#dias de campanha caem para 1 a 7 e `tse` fica censurado ('<1') em 11 dias,
#enquanto na janela de campanha o mesmo termo varia de 1 a 6 e `lula` de 25 a
#100. O mesmo ja tinha acontecido com o pico do 1o turno.
#por isso: a janela de campanha e a CANONICA para o indicador, e o arco serve
#para a pergunta do evento. Cada uma e valida por si; concatena-las nao e.
JANELAS = {
    "campanha": AQUI / "dados" / "trends_explore" / "campanha.csv",
    "arco": AQUI / "dados" / "trends_explore" / "arco.csv",
}
JANELA_CANONICA = "campanha"

#A IMPRENSA E O PUBLICO NAO CHAMAM O CANDIDATO PELO MESMO NOME.
#A convencao jornalistica reserva "Bolsonaro" para o pai e escreve "Flavio"
#para o filho: auditadas uma a uma, as 66 materias com o sobrenome sem o
#primeiro nome eram TODAS sobre Jair, Eduardo, Michelle ou o cla. Quem
#pesquisa faz o oposto, digita "bolsonaro" e quer o filho. Por isso o termo
#nao e a mesma string dos dois lados: do lado da imprensa o candidato e
#`flavio` menos `dino`, e do lado do publico e `bolsonaro`.
#
#QUANTO DE 'bolsonaro' E O CANDIDATO, tres medicoes, janela de campanha:
#- a frase explicita 'jair bolsonaro' vale 23 contra 575 de 'bolsonaro',
#o que poe o PISO do pai em 4,0% (piso, porque quem quer o pai tambem
#digita so o sobrenome);
#- na imprensa, pai e cla somam 53 materias contra 1.154 do candidato: 4,4%;
#- por minimos quadrados o pai sairia em 20,3%, mas esse numero esta
#inflado: a serie 'jair bolsonaro' e praticamente constante em 1 e,
#numa regressao sem intercepto, absorve a linha de base. E teto, nao
#estimativa.
#O teste que decide e o das manchetes: nos dois dias de maior cobertura do pai
#(08/10 com 10 materias, 07/10 com 9: soltura, joias sauditas, intimacao) a
#razao 'bolsonaro'/'flavio bolsonaro' foi 3,2x e 3,0x, ABAIXO da media de 3,3x
#da janela. Se o pai puxasse a busca, esses dias subiriam. Nao sobem.
#
#dai a faixa de 80% a 96%, com 90% como valor de trabalho. ISTO E UMA
#DELIMITACAO DECLARADA, nao uma medida: o indicador deve ser lido como faixa.
FATIA_CANDIDATO = 0.90
FATIA_FAIXA = (0.80, 0.96)

#O recorte do projeto: do dia do 1o turno ao dia seguinte ao 2o turno.
#
#O FIM e deliberado, nao tecnico. Sem ele o recorte CRESCE SOZINHO a cada
#coleta nova, e foi o que aconteceu quando a coleta voltou a rodar: o periodo
#passou de 26/10 para 31/10 numa execucao, sem ninguem decidir isso, e levou
#junto os indicadores. O projeto cobre o periodo eleitoral e a repercussao
#imediata; a semana seguinte e outro assunto.
RECORTE_INICIO = "2026-10-04"
RECORTE_FIM = "2026-10-26"

#so reportagem factual entra nos indicadores. Coluna de opiniao mede julgamento
#do autor, que e outra pergunta. A Folha publica 21% de opiniao e o UOL 29%,
#contra 0% da CNN, e misturar faz esses veiculos parecerem mais negativos por
#artefato do metodo. As materias de opiniao SAO exportadas, com a coluna
#`entra_nos_indicadores` em 0, para o painel poder mostra-las a parte.
TIPO_DOS_INDICADORES = "noticia"

#os termos, com a regra EXATA que foi auditada manchete a manchete.
#`exclui` existe porque casar o termo sozinho traria outra pessoa:
#- 'flavio' sem excluir 'dino' traz Flavio Dino (2 de 441 materias);
#- 'bolsonaro' NAO esta aqui de proposito: 66 materias trazem o sobrenome
#sem 'flavio' e sao sobre Jair, Eduardo, Michelle, Carlos ou o cla.
#por convencao jornalistica, "Bolsonaro" sem primeiro nome e o pai, e
#somar esse termo ao candidato inflaria o share dele em 7,7%.
#O rotulo e de EXIBICAO e nao muda o casamento: o termo precisa ser o primeiro
#nome, porque 51% das materias sobre o candidato nao escrevem o sobrenome.
TERMOS = [
    #chave        rotulo exibido       casa          exclui
    ("lula",     "Lula",               ["lula"],     []),
    ("flavio",   "Fl\u00e1vio Bolsonaro",   ["flavio"],   ["dino"]),
    ("eleicoes", "Elei\u00e7\u00f5es",           ["eleicoes"], []),
    ("tse",      "TSE",                ["tse"],      []),
]

#como cada coluna do CSV do Explore vira termo. `None` = a coluna existe no
#arquivo mas NAO entra no indicador: 'fl\u00e1vio bolsonaro' e subconjunto de
#'bolsonaro' e somar os dois contaria a mesma busca duas vezes. Ela fica no
#arquivo porque e dela que sai a aferi\u00e7\u00e3o da fatia acima.
PUBLICO_PARA_CHAVE = {
    "lula": "lula",
    "bolsonaro": "flavio",
    "fl\u00e1vio bolsonaro": None,
    "jair bolsonaro": None,
    "elei\u00e7\u00f5es": "eleicoes",
    "tse": "tse",
}

#so o termo do candidato leva a fatia; os outros entram inteiros
ESCALA = {"flavio": FATIA_CANDIDATO}


# --------------------------------------------------------------------------
#LEITURA DA BASE
# --------------------------------------------------------------------------

#A data efetiva, que e obrigatoria e nao opcional:
#- `data_publicacao` e nula de forma DESIGUAL (CNN 32,5%, Folha 4,9%, G1 e
#UOL 0%), entao filtrar por ela corta um terco da CNN e enviesa qualquer
#comparacao entre veiculos. A primeira observacao cobre os 100% restantes.
#- o dia e o dia em BRASILIA: o RSS carimba em UTC, e 21h em Brasilia ja e o
#dia seguinte la. 6,9% das materias caem no dia errado sem o deslocamento.
#- o CASE existe porque `data_publicacao` guarda duas coisas: carimbo UTC do
#RSS (com hora, desloca) e data local vinda da URL (sem hora, nao pode).
CONSULTA = """
WITH primeira_obs AS (
    SELECT materia_id, MIN(data_coleta) AS vista_em
      FROM observacoes GROUP BY materia_id
),
mat AS (
    SELECT m.id, m.veiculo_id, m.titulo, m.secao, m.tipo, m.url_canonica,
           DATE(
               COALESCE(m.data_publicacao, p.vista_em),
               CASE WHEN length(COALESCE(m.data_publicacao, p.vista_em)) > 10
                    THEN '-3 hours' ELSE '+0 hours' END
           ) AS dia
      FROM materias m
      LEFT JOIN primeira_obs p ON p.materia_id = m.id
)
SELECT mat.id, v.slug, mat.dia, mat.tipo, mat.secao, mat.titulo, mat.url_canonica,
       (SELECT a.valor FROM analises a
         WHERE a.materia_id = mat.id AND a.tipo = 'sentimento'
           AND a.modelo = 'lexico-pt' AND a.versao = '1.1')            AS tom_lexico,
       (SELECT a.valor FROM analises a
         WHERE a.materia_id = mat.id AND a.tipo = 'topico'
           AND a.modelo = :motor_ia AND a.versao = '2.0')              AS topico_ia,
       (SELECT a.valor FROM analises a
         WHERE a.materia_id = mat.id AND a.tipo = 'papel'
           AND a.modelo = :motor_ia AND a.versao = '2.0')              AS papel_ia,
       -- o tom da IA fica gravado sob o tipo 'sentimento', igual ao do lexico.
       -- O que separa os dois e o MODELO, nao o tipo.
       (SELECT a.valor FROM analises a
         WHERE a.materia_id = mat.id AND a.tipo = 'sentimento'
           AND a.modelo = :motor_ia AND a.versao = '2.0')              AS tom_ia,
       (SELECT MIN(o.posicao) FROM observacoes o
         WHERE o.materia_id = mat.id AND o.origem = 'home')            AS posicao_home,
       (SELECT COUNT(*) FROM observacoes o
         WHERE o.materia_id = mat.id)                                  AS observacoes
  FROM mat
  JOIN veiculos v ON v.id = mat.veiculo_id
 WHERE mat.dia >= :inicio
 ORDER BY mat.dia, v.slug
"""


# ---------------------------------------------------------------------------
#A analise de IA
# ---------------------------------------------------------------------------

#qual motor conta como "a IA" aqui. Fixo de proposito: o Midiometro guarda
#varias series lado a lado (lexico, Groq 2.0, Groq 3.0) e escolher por "a mais
#recente" trocaria os numeros do painel sem ninguem pedir.
MOTOR_IA = "openai/gpt-oss-120b"

#os temas que a metodologia PEDE. Onze, e so.
TEMAS = ["politica", "economia", "internacional", "seguranca", "saude",
         "educacao", "meio ambiente", "ciencia e tecnologia", "esportes",
         "cultura", "cotidiano"]

#de onde vem o resto.
#
#O modelo recebeu a lista de onze e devolveu 32 valores. Nem todos sao erro:
#"politica" sem acento e a mesma coisa que "politica", e "clima" e meio
#ambiente por qualquer leitura. Mas "energia", "trabalho" ou "justica" nao
#cabem em nenhuma das onze sem que alguem decida por elas, e decidir por elas
#inventaria precisao que a medicao nao tem.
#
#entao: variante de grafia e subconjunto claro entram na categoria; o resto
#vai para "outros", que APARECE no grafico. Medido na primeira metade do
#recorte: 142 linhas de grafia, 59 de subconjunto, 36 de outros (1,2%).
#
#A normalizacao tira o acento antes de consultar, entao a tabela so precisa
#tratar o que sobra depois disso.
TEMA_EQUIVALE = {
    "tecnologia": "ciencia e tecnologia",
    "ciencia": "ciencia e tecnologia",
    "clima": "meio ambiente",
    "politica social": "politica",
    "esporte": "esportes",
}

TEMA_OUTROS = "outros"


def tema(bruto: str | None) -> str:
    """O topico da IA reduzido aos onze temas da metodologia, mais 'outros'.

    Devolve "" quando a materia nao tem analise, que e diferente de "outros":
    "" e "nao medido" e "outros" e "medido e nao cabe na lista". O painel conta
    as duas coisas separado, senao cobertura parcial viraria tema.
    """
    if not bruto:
        return ""
    chave = normalizar(bruto).strip()
    chave = TEMA_EQUIVALE.get(chave, chave)
    return chave if chave in TEMAS else TEMA_OUTROS


def conectar() -> sqlite3.Connection:
    """Somente leitura. Um acidente aqui e impossivel, nao apenas improvavel."""
    if not BANCO.exists():
        sys.exit(f"banco nao encontrado em {BANCO}")
    return sqlite3.connect(f"file:{BANCO}?mode=ro", uri=True)


def normalizar(texto: str) -> str:
    """Minuscula e sem acento, para o casamento de termo nao depender de grafia."""
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def casa_palavra(texto: str, termo: str) -> bool:
    """
    Palavra INTEIRA, nunca substring.

    Medido: por substring "rio" dava 109 materias e por palavra inteira 13;
    "acao" contava "apresentacao".
    """
    return re.search(rf"(?<![a-z0-9]){re.escape(termo)}(?![a-z0-9])", texto) is not None


def classificar(titulo: str) -> dict[str, int]:
    """Aplica as regras de TERMOS a um titulo. Devolve {chave: 0 ou 1}."""
    t = normalizar(titulo)
    marcas = {}
    for chave, _rotulo, casa, exclui in TERMOS:
        achou = any(casa_palavra(t, p) for p in casa)
        if achou and any(casa_palavra(t, p) for p in exclui):
            achou = False
        marcas[chave] = int(achou)
    return marcas


def ultimo_dia_completo(con: sqlite3.Connection) -> str:
    """
    Qual o ultimo dia em Brasilia que a coleta cobriu INTEIRO.

    O ultimo dia esta sempre pela metade: a coleta mais recente aconteceu em
    algum momento do dia, e tudo que os portais publicarem depois dela so sera
    visto amanha. Do lado do publico o dia ja esta fechado, e comparar os dois
    enviesaria o ultimo ponto da serie, todo dia, sempre no mesmo sentido.
    Um dia D so esta completo se alguma coleta rodou depois de D 23:59 em
    Brasilia, ou seja a partir de D+1 03:00 UTC.
    """
    bruto = con.execute("SELECT MAX(data_coleta) FROM observacoes").fetchone()[0]
    #o carimbo vem em UTC; Brasilia e UTC-3
    momento = datetime.fromisoformat(bruto.replace("Z", "+00:00"))
    em_brasilia = momento.replace(tzinfo=None) + timedelta(hours=-3)
    #o dia em que a ultima coleta rodou esta incompleto, porque o que os
    #portais publicarem depois dela so sera visto amanha; o anterior fechou
    fechado = (em_brasilia.date() - timedelta(days=1)).isoformat()
    #...mas nunca alem do fim do recorte: se a coleta avancou para novembro,
    #o ultimo dia do recorte ja esta fechado ha muito, e nao e "parcial"
    return min(fechado, RECORTE_FIM)


def ler_publico(caminho: Path) -> tuple[dict, int]:
    """
    Le o CSV do Google Trends Explore.

    O valor e um indice 0-100 normalizado DENTRO da consulta: 100 e o pico
    daquela serie, nunca um volume absoluto, e trocar a janela recalibra tudo.
    Por isso o share (e nao o valor) e o que entra na comparacao. Ao dividir
    cada termo pela soma do dia, o fator de escala desconhecido se cancela.

    `<1` e dado CENSURADO, nao zero. Usamos 0,5, o meio da faixa; testado com
    0, 0,5 e 1, o indicador do periodo nao se move (0,190 / 0,191 / 0,191).
    """
    if not caminho.exists():
        return {}, 0
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    inicio = next(i for i, l in enumerate(linhas) if l.startswith("Dia,"))
    cabecalho = next(csv.reader([linhas[inicio]]))[1:]
    chaves = [PUBLICO_PARA_CHAVE.get(c.split(":")[0].strip().lower()) for c in cabecalho]
    publico, censurados = {}, 0
    for linha in csv.reader(linhas[inicio + 1:]):
        if not linha or not linha[0].strip():
            continue
        valores = {}
        for chave, bruto in zip(chaves, linha[1:]):
            if chave is None:
                continue
            bruto = bruto.strip()
            if bruto.startswith("<"):
                valor = 0.5
                censurados += 1
            else:
                valor = float(bruto or 0)
            #a fatia do candidato entra aqui, uma vez so, no ponto em que o
            #indice bruto vira o indice do termo
            valores[chave] = valor * ESCALA.get(chave, 1.0)
        publico[linha[0]] = valores
    return publico, censurados


# --------------------------------------------------------------------------
#TRANSFORMACAO
# --------------------------------------------------------------------------

def share(valores: dict[str, float]) -> dict[str, float] | None:
    """Converte contagens ou indices em participacao. Soma 1, ou None se vazio."""
    total = sum(valores.values())
    if not total:
        return None
    return {k: v / total for k, v in valores.items()}


#A tabela TEMP e o que permite agregar em SQL sem quebrar a separacao do
#projeto: no SQLite o banco `temp` e um arquivo a parte, entao a base do
#midiometro continua aberta em modo somente leitura e intocada. O que entra
#aqui ja passou pela regra de termo, que e a unica parte que o SQL nao faz
#sozinho, porque `casa_palavra` compara por palavra inteira e sem acento e o
#LIKE nao tem fronteira de palavra.
COLUNAS_RECORTE = [
    ("materia_id", "INTEGER"), ("veiculo", "TEXT"), ("dia", "TEXT"),
    ("dia_completo", "INTEGER"), ("tipo", "TEXT"),
    ("entra_nos_indicadores", "INTEGER"), ("secao", "TEXT"),
    ("tom_lexico", "TEXT"), ("tom_ia", "TEXT"), ("papel_ia", "TEXT"),
    ("tema_ia", "TEXT"), ("posicao_home", "INTEGER"), ("na_home", "INTEGER"),
    ("observacoes", "INTEGER"),
]


def materializar(con: sqlite3.Connection, materias: list[dict],
                 chaves: list[str]) -> None:
    """Copia o recorte ja classificado para a tabela TEMP `recorte`."""
    colunas = COLUNAS_RECORTE + [(f"cita_{c}", "INTEGER") for c in chaves]
    nomes = [n for n, _ in colunas]
    con.execute("DROP TABLE IF EXISTS temp.recorte")
    con.execute("CREATE TEMP TABLE recorte ("
                + ", ".join(f"{n} {t}" for n, t in colunas) + ")")
    con.executemany(
        f"INSERT INTO recorte ({', '.join(nomes)}) "
        f"VALUES ({', '.join('?' * len(nomes))})",
        [tuple(m[n] if m[n] != "" else None for n in nomes) for m in materias])
    con.execute("CREATE INDEX temp.i_recorte_dia ON recorte(dia)")


def contar_por_dia(con: sqlite3.Connection, chaves: list[str]) -> dict[str, dict]:
    """Mencoes de cada termo por dia, so nas factuais. Um GROUP BY no lugar do
    dicionario que era somado a mao dentro do laco de leitura."""
    somas = ", ".join(f"SUM(cita_{c}) AS {c}" for c in chaves)
    return {dia: dict(zip(chaves, resto)) for dia, *resto in con.execute(
        f"""SELECT dia, {somas}
              FROM recorte
             WHERE entra_nos_indicadores = 1
             GROUP BY dia
             ORDER BY dia""")}


def contar_por_termo(con: sqlite3.Connection, chaves: list[str]) -> tuple[int, dict]:
    """Total de factuais e quantas citam cada termo, em uma consulta so."""
    somas = ", ".join(f"SUM(cita_{c})" for c in chaves)
    linha = con.execute(
        f"SELECT COUNT(*), {somas} FROM recorte WHERE entra_nos_indicadores = 1"
    ).fetchone()
    return linha[0], dict(zip(chaves, linha[1:]))


def montar(con: sqlite3.Connection):
    """Le a base, aplica as regras e devolve as tres tabelas do painel."""
    chaves = [c for c, *_ in TERMOS]
    fechado_ate = ultimo_dia_completo(con)

    materias = []
    for (mid, slug, dia, tipo, secao, titulo, url,
         tom, topico_ia, papel_ia, tom_ia,
         posicao, obs) in con.execute(
            CONSULTA, {"inicio": RECORTE_INICIO, "motor_ia": MOTOR_IA}):
        if dia > RECORTE_FIM:
            continue
        marcas = classificar(titulo or "")
        factual = int(tipo == TIPO_DOS_INDICADORES)
        materias.append({
            "materia_id": mid,
            "veiculo": slug,
            "dia": dia,
            "dia_completo": int(dia <= fechado_ate),
            "tipo": tipo,
            "entra_nos_indicadores": factual,
            "secao": secao or "",
            "titulo": titulo or "",
            "url": url or "",
            "tom_lexico": tom or "",
            "tom_ia": tom_ia or "",
            "papel_ia": papel_ia or "",
            "tema_ia": tema(topico_ia),
            #posicao na home: destaque editorial, mas NAO comparavel entre
            #veiculos, o G1 le 26 posicoes e a CNN 139. Normalizar e tarefa
            #do painel; aqui vai o dado cru mais a profundidade do veiculo.
            "posicao_home": posicao if posicao is not None else "",
            "na_home": int(posicao is not None),
            "observacoes": obs,
            **{f"cita_{c}": marcas[c] for c in chaves},
        })

    #A partir daqui quem conta e o SQL, sobre a tabela TEMP. O laco acima so
    #le, classifica e guarda; somar dentro dele misturava duas tarefas e era
    #onde um filtro esquecido passava despercebido.
    materializar(con, materias, chaves)
    por_dia = contar_por_dia(con, chaves)

    rotulos = {c: r for c, r, *_ in TERMOS}
    share_diario, descompasso_diario, censurados = [], [], {}

    #uma passada por janela: cada uma tem a sua propria normalizacao, entao os
    #numeros de janelas diferentes nunca se misturam na mesma linha
    for janela, caminho in JANELAS.items():
        publico, censuras = ler_publico(caminho)
        censurados[janela] = censuras
        for dia in sorted(por_dia):
            if dia not in publico:
                continue
            imprensa = por_dia[dia]
            si, sp = share(imprensa), share(publico[dia])
            if not si or not sp:
                continue
            completo = int(dia <= fechado_ate)
            for c in chaves:
                share_diario.append({
                    "janela": janela,
                    "dia": dia,
                    "dia_completo": completo,
                    "termo": c,
                    "rotulo": rotulos[c],
                    "mencoes": imprensa[c],
                    "share_imprensa": round(si[c], 6),
                    "indice_publico": publico[dia][c],
                    "share_publico": round(sp[c], 6),
                    "descompasso_pp": round(100 * (si[c] - sp[c]), 3),
                })
            #distancia de variacao total: a fracao da cobertura que teria de
            #ser realocada para a imprensa espelhar o interesse do publico
            descompasso_diario.append({
                "janela": janela,
                "dia": dia,
                "dia_completo": completo,
                "distancia_variacao_total": round(
                    0.5 * sum(abs(si[c] - sp[c]) for c in chaves), 6),
                "mencoes_total": sum(imprensa.values()),
            })

    #os dias que a imprensa cobre e nenhuma janela do publico alcanca ainda
    #entram, com o lado do publico vazio, porque o painel precisa saber que existem
    cobertos = {l["dia"] for l in share_diario}
    for dia in sorted(set(por_dia) - cobertos):
        si = share(por_dia[dia])
        for c in chaves:
            share_diario.append({
                "janela": "", "dia": dia, "dia_completo": int(dia <= fechado_ate),
                "termo": c, "rotulo": rotulos[c], "mencoes": por_dia[dia][c],
                "share_imprensa": round(si[c], 6) if si else "",
                "indice_publico": "", "share_publico": "", "descompasso_pp": "",
            })
    share_diario.sort(key=lambda l: (l["janela"], l["dia"]))

    return materias, share_diario, descompasso_diario, fechado_ate, censurados


# --------------------------------------------------------------------------
#ESCRITA
# --------------------------------------------------------------------------

def escrever(nome: str, linhas: list[dict]) -> Path:
    """Grava um CSV em UTF-8 com BOM, que e o que o Power BI le sem configurar."""
    SAIDA.mkdir(parents=True, exist_ok=True)
    caminho = SAIDA / nome
    with caminho.open("w", encoding="utf-8-sig", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=list(linhas[0]))
        escritor.writeheader()
        escritor.writerows(linhas)
    return caminho


def main(argv=None) -> int:
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument("--resumo", action="store_true",
                            help="mostra os numeros e nao escreve nada")
    args = analisador.parse_args(argv)

    con = conectar()
    try:
        materias, share_diario, desc, fechado_ate, censurados = montar(con)
        #uma consulta no lugar de quatro: a profundidade de capa sai no mesmo
        #GROUP BY. O CASE WHEN dentro do COUNT DISTINCT e o que restringe a
        #contagem as observacoes de home sem perder o veiculo que nao tem
        #nenhuma, que o LEFT JOIN garante que aparece com zero.
        veiculos = [{"slug": s, "nome": n, "url_base": u, "posicoes_home": pos}
                    for s, n, u, pos in con.execute("""
            SELECT v.slug, v.nome, v.url_base,
                   COUNT(DISTINCT CASE WHEN o.origem = 'home' THEN o.posicao END)
              FROM veiculos v
              LEFT JOIN materias m ON m.veiculo_id = v.id
              LEFT JOIN observacoes o ON o.materia_id = m.id
             GROUP BY v.id, v.slug, v.nome, v.url_base
             ORDER BY v.slug""")]

        #os totais do resumo, tambem em SQL, enquanto a conexao esta aberta
        n_factuais, por_termo = contar_por_termo(con, [c for c, *_ in TERMOS])
    finally:
        con.close()

    print(f"recorte de {RECORTE_INICIO} em diante")
    print(f"  materias exportadas   {len(materias):>7,}")
    print(f"  entram nos indicadores{n_factuais:>7,}  (tipo={TIPO_DOS_INDICADORES})")
    print(f"  ultimo dia completo   {fechado_ate}")
    for janela in JANELAS:
        dias = sorted({l['dia'] for l in desc if l['janela'] == janela})
        marca = "  (canonica)" if janela == JANELA_CANONICA else ""
        if dias:
            print(f"  janela {janela:<9} {len(dias):>3} dias, {dias[0]} a {dias[-1]}"
                  f"  censurados: {censurados.get(janela, 0)}{marca}")
        else:
            print(f"  janela {janela:<9} sem dias em comum com a imprensa{marca}")

    for chave, rotulo, *_ in TERMOS:
        n = por_termo[chave]
        print(f"  {rotulo:<18} {n:>6,} materias  ({100*n/n_factuais:.1f}%)")

    if args.resumo:
        print("\n(--resumo: nada escrito)")
        return 0

    total = 0
    for nome, linhas in (("materias.csv", materias),
                         ("share_diario.csv", share_diario),
                         ("descompasso_diario.csv", desc),
                         ("veiculos.csv", veiculos)):
        if not linhas:
            print(f"  {nome}: vazio, nao escrito")
            continue
        caminho = escrever(nome, linhas)
        tamanho = caminho.stat().st_size
        total += tamanho
        print(f"  {nome:<24} {len(linhas):>6,} linhas  {tamanho/1024:>8.1f} KB")
    print(f"  {'TOTAL':<24} {'':>6}         {total/1024/1024:>8.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
