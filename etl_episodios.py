# -*- coding: utf-8 -*-
"""
Segunda camada do ETL: episodios e pesquisas.

Fica separado do `etl.py` de proposito. Aquele produz os indicadores do
enunciado e ja esta validado; este acrescenta duas camadas novas, e um erro
aqui nao pode derrubar aquilo.

AS DEFINICOES ABAIXO FORAM AUDITADAS MANCHETE A MANCHETE. Duas versoes
anteriores estavam erradas e os numeros que elas produziram eram artefato:

  - `pec` sozinho mistura TRES PECs: da Seguranca, do 6x1 e da tarifa zero.
    Media 104 materias e a maioria era do 6x1, que e outro episodio. Com o
    qualificador sao 43.
  - `preso` pega plantao policial: "Filho e preso apos esfaquear o pai",
    "PM preso por matar mulher". O "maior episodio da base", com 594
    materias, era crime comum. Com a regra correta sao 18, e a maioria
    trata de Eduardo Bolsonaro, nao de Jair.
  - `master` sozinho casa "patrocinio master". Exige qualificador bancario.

    python etl_episodios.py
    python etl_episodios.py --conferir   # imprime amostra de cada episodio
"""
from __future__ import annotations

import argparse
import collections
import csv
import random
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from etl import (BANCO, CONSULTA, MOTOR_IA, RECORTE_FIM, RECORTE_INICIO,
                 SAIDA, conectar, escrever)  # noqa: E402

TRENDS = Path(__file__).parent / "dados" / "trends_explore"


def norm(texto: str) -> str:
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def casa(t: str, p: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", t) is not None


# --------------------------------------------------------------------------
#OS EPISODIOS. Cada um e uma funcao sobre o titulo normalizado, e o nome da
#coluna correspondente no CSV do Google Trends.
# --------------------------------------------------------------------------

def _pec_seguranca(t):
    #`pec` sozinho traria a PEC do 6x1 e a da tarifa zero
    return casa(t, "pec") and any(casa(t, p) for p in ("seguranca", "maioridade", "penal"))

def _escala_6x1(t):
    #auditado: o termo e limpo, e no Trends `6x1` vale so 1,3x `escala 6x1`,
    #entao a contaminacao por placar de futebol nao move a curva
    return casa(t, "6x1")

def _vorcaro(t):
    if casa(t, "vorcaro"):
        return True
    return casa(t, "master") and any(casa(t, p) for p in
                                     ("banco", "brb", "caso", "liquidacao", "pf", "bc"))

def _sigilo(t):
    return any(casa(t, p) for p in ("sigilo", "sigilosa", "sigiloso"))

def _bets(t):
    return casa(t, "bets")

def _processo_bolsonaro(t):
    #o sobrenome SEM o primeiro nome do filho (convencao jornalistica: e o
    #pai ou outro parente) MAIS um termo do processo
    if not casa(t, "bolsonaro") or casa(t, "flavio"):
        return False
    return any(casa(t, p) for p in ("soltura", "solto", "prisao", "preso", "condenacao",
                                    "pena", "dosimetria", "anistia", "stf", "julgamento",
                                    "inquerito", "revisao", "habeas", "regime"))

EPISODIOS = {
    #rotulo exibido        regra              coluna no Trends  arquivo do Trends
    "Escala 6x1":          (_escala_6x1,       "escala 6x1",     "episodios_campanha.csv"),
    "Vorcaro / Banco Master": (_vorcaro,       "vorcaro",        "episodios_campanha.csv"),
    "Quebra de sigilo":    (_sigilo,           "sigilo",         "episodios_campanha.csv"),
    "Bets":                (_bets,             "bets",           "episodios_campanha.csv"),
    "PEC da Segurança":    (_pec_seguranca,    "pec da segurança","episodios_campanha.csv"),
    #exportacao propria, na janela exata do recorte (04 a 26/10). Os outros
    #cinco vem de `episodios_campanha.csv`, que cobre 07 a 24/10, por isso
    #este episodio nao cabia naquele arquivo e ficava sem curva de busca.
    "Processo dos Bolsonaro": (_processo_bolsonaro, "jair bolsonaro",
                               "episodio_bolsonaro_recorte.csv"),
}


# --------------------------------------------------------------------------
#AS PESQUISAS, extraidas das proprias manchetes
# --------------------------------------------------------------------------

INSTITUTOS = ["datafolha", "quaest", "ipec", "atlas", "genial",
              "futura", "real time big data", "paranaet"]

#recorte estadual: o painel e nacional
ESTADUAL = re.compile(
    r"\bno (df|es|to|am|rn|ac|acre|amazonas|tocantins|espirito santo|rio grande do norte|"
    r"distrito federal|ceara|para|alagoas|sergipe)\b|para o governo|ao governo|"
    r"governo do|governador")

#o numero vem de uma fatia da pesquisa, nao do total dela
SUBGRUPO = re.compile(
    r"\bentre\b|ausentes|brancos|nulos|faltaram|anularam|votos de|eleitores de|"
    r"quem votou|indecisos|espontanea|migra|decidiram|por ser|evangelic|"
    r"mulheres|homens|jovens|nordeste|sudeste")

NUMERO = re.compile(r"(\d{1,2}(?:[,.]\d{1,2})?)\s*%")


def _metrica(trecho: str) -> str | None:
    #`rejei` e nao `rejeit`: "rejeicao" nao contem "rejeit", so "rejeitado"
    #e "rejeitam" contem. Esse bug classificou rejeicao como intencao.
    if "rejei" in trecho: return "rejeicao"
    if "desaprov" in trecho: return "desaprovacao"
    if "aprov" in trecho: return "aprovacao"
    if "valido" in trecho or "turno" in trecho or "marca" in trecho: return "intencao"
    return None


def _candidato(trecho: str) -> str | None:
    f = re.search(r"(?<![a-z])flavio(?![a-z])", trecho)
    l = re.search(r"(?<![a-z])lula(?![a-z])", trecho)
    if f and not l: return "Flávio Bolsonaro"
    if l and not f: return "Lula"
    return None


def extrair_pesquisas(linhas: list[tuple]) -> list[dict]:
    """
    Le as manchetes de pesquisa clausula a clausula.

    A metrica e o candidato valem ate serem trocados, porque a manchete e
    eliptica: "Flavio e rejeitado por 49%; Lula, por 47%", e a segunda
    clausula nao repete "rejeitado".
    """
    achados = []
    for dia, slug, titulo in linhas:
        tn = norm(titulo)
        instituto = next((i for i in INSTITUTOS if i in tn), None)
        if not instituto or not NUMERO.search(titulo):
            continue
        if ESTADUAL.search(tn) or SUBGRUPO.search(tn):
            continue
        #intencao presidencial nomeia OS DOIS; as outras metricas, nao
        eh_intencao = _metrica(tn) == "intencao"
        if eh_intencao:
            tem_f = re.search(r"(?<![a-z])flavio(?![a-z])", tn)
            tem_l = re.search(r"(?<![a-z])lula(?![a-z])", tn)
            if not (tem_f and tem_l):
                continue
        #votos validos e total sao bases diferentes na mesma manchete
        base = "válidos" if "valido" in tn else "total"
        pedacos = re.split(r"[;,]| e (?=\d|lula|flavio|o lula|o flavio)", tn)
        metrica = candidato = None
        vistos = set()
        for p in pedacos:
            m = _metrica(p)
            if m: metrica = m
            c = _candidato(p)
            if c: candidato = c
            n = NUMERO.search(p)
            if n and metrica and candidato and (metrica, candidato) not in vistos:
                valor = float(n.group(1).replace(",", "."))
                if 20 <= valor <= 70:
                    vistos.add((metrica, candidato))
                    achados.append({"dia": dia, "instituto": instituto,
                                    "metrica": metrica,
                                    "base": base if metrica == "intencao" else "",
                                    "candidato": candidato, "valor": valor,
                                    "veiculo": slug, "titulo": titulo})
    return achados


def consolidar(achados: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Junta o que os quatro veiculos reportaram da MESMA pesquisa.

    Se eles divergem, ha bug de extracao ou ambiguidade na manchete, e essa
    conferencia e o que torna o dado confiavel. As divergencias sao devolvidas
    para o relatorio em vez de escondidas.
    """
    grupos = collections.defaultdict(list)
    for a in achados:
        grupos[(a["dia"], a["instituto"], a["metrica"], a["base"], a["candidato"])].append(a)
    saida, avisos = [], []
    for chave, itens in sorted(grupos.items()):
        valores = {i["valor"] for i in itens}
        dia, instituto, metrica, base, candidato = chave
        if len(valores) > 1:
            #a maioria vence, e o conflito fica registrado
            contagem = collections.Counter(i["valor"] for i in itens)
            escolhido = contagem.most_common(1)[0][0]
            avisos.append(f"{dia} {instituto} {metrica} {candidato}: "
                          f"{sorted(valores)} -> {escolhido}")
        else:
            escolhido = valores.pop()
        saida.append({"dia": dia, "instituto": instituto, "metrica": metrica,
                      "base": base, "candidato": candidato, "valor": escolhido,
                      "veiculos_que_reportaram": len({i["veiculo"] for i in itens})})
    return saida, avisos


# --------------------------------------------------------------------------

def ler_trends(arquivo: str) -> dict:
    caminho = TRENDS / arquivo
    if not caminho.exists():
        return {}
    L = caminho.read_text(encoding="utf-8").splitlines()
    i = next(k for k, l in enumerate(L) if l.startswith("Dia,"))
    cab = [c.split(":")[0].strip() for c in next(csv.reader([L[i]]))[1:]]
    saida = {}
    for r in csv.reader(L[i+1:]):
        if not r or not r[0].strip(): continue
        v = lambda x: 0.5 if x.strip().startswith("<") else float(x.strip() or 0)
        saida[r[0]] = dict(zip(cab, map(v, r[1:])))
    return saida


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--conferir", action="store_true",
                    help="imprime amostra de cada episódio e não escreve nada")
    args = ap.parse_args(argv)

    con = conectar()
    try:
        linhas = [(d, s, t) for (_id, s, d, _tp, _sec, t, *_resto)
                  #a CONSULTA do etl.py tem DOIS parametros nomeados desde
                  #que as colunas da IA entraram. Passar tupla de um
                  #elemento aqui quebrava com 'Incorrect number of
                  #bindings', e o motor precisa ser o mesmo do painel.
                  in con.execute(CONSULTA, {"inicio": RECORTE_INICIO,
                                            "motor_ia": MOTOR_IA})
                  #o mesmo corte do etl.py: sem ele os episodios passavam
                  #do fim do recorte e os cartoes iam ate 31/10
                  if d <= RECORTE_FIM
                  if _tp == "noticia"]
    finally:
        con.close()
    print(f"{len(linhas):,} notícias factuais no recorte\n")

    if args.conferir:
        random.seed(3)
        for rotulo, (regra, _col, _arq) in EPISODIOS.items():
            achadas = [(d, s, t) for d, s, t in linhas if regra(norm(t))]
            print(f"-- {rotulo}: {len(achadas)} matérias --")
            for d, s, t in random.sample(achadas, min(6, len(achadas))):
                print(f"   [{d[8:10]}/{d[5:7]} {s:5s}] {t[:88]}")
            print()
        return 0

    #---- episodios --------------------------------------------------------
    trends = {a: ler_trends(a) for a in {arq for _, _, arq in EPISODIOS.values() if arq}}
    saida_ep = []
    for rotulo, (regra, coluna, arquivo) in EPISODIOS.items():
        por_dia = collections.Counter(d for d, _s, t in linhas if regra(norm(t)))
        busca = trends.get(arquivo, {}) if arquivo else {}
        dias = sorted(set(por_dia) | set(busca))
        for dia in dias:
            saida_ep.append({
                "episodio": rotulo, "dia": dia,
                "materias": por_dia.get(dia, 0),
                "indice_busca": busca.get(dia, {}).get(coluna, "") if coluna else "",
            })
        print(f"  {rotulo:<26} {sum(por_dia.values()):>4} matérias"
              f"{'  (sem termo no Trends)' if not coluna else ''}")

    #---- pesquisas --------------------------------------------------------
    achados = extrair_pesquisas(linhas)
    pesquisas, avisos = consolidar(achados)
    print(f"\n  pesquisas: {len(achados)} extrações -> {len(pesquisas)} pontos")
    por_metrica = collections.Counter(p["metrica"] for p in pesquisas)
    for m, n in por_metrica.most_common():
        print(f"    {m:<14} {n}")
    if avisos:
        print(f"\n  {len(avisos)} divergência(s) entre veículos, resolvidas por maioria:")
        for a in avisos:
            print(f"    {a}")

    escrever("episodios_diario.csv", saida_ep)
    escrever("pesquisas.csv", pesquisas)
    for nome in ("episodios_diario.csv", "pesquisas.csv"):
        print(f"  {nome:<26} {(SAIDA/nome).stat().st_size/1024:>8.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
