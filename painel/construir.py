# -*- coding: utf-8 -*-
"""
Monta o painel: CSVs do ETL -> painel/index.html.

Mesmo padrao do Midiometro: `modelo.html` e o arquivo editavel, `index.html` e
gerado e nunca editado a mao. O dado vai embutido no HTML, entao a pagina abre
sozinha, sem servidor e sem rede.

O QUE ENTRA, E O QUE NAO ENTRA
------------------------------
So as materias que citam algum candidato vao para o drill-down, 1.9 mil de
15.973. Embutir as 16 mil inteiras custaria 4 MB de pagina para mostrar linhas
que o drill-down nunca alcanca, ja que ele comeca em veiculo e desce por
candidato. Os agregados (share, equilibrio, tom) sao calculados sobre o recorte
INTEIRO antes do corte, entao nenhum numero depende de quem ficou de fora.

    python construir.py
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

AQUI = Path(__file__).parent
SAIDA_ETL = AQUI.parent / "dados" / "saida"

#a fatia e a faixa moram no etl.py, que e quem as aplica. Importar em vez de
#repetir impede que o painel e o ETL divirjam em silencio.
sys.path.insert(0, str(AQUI.parent))
from etl import FATIA_CANDIDATO, FATIA_FAIXA  # noqa: E402
MODELO = AQUI / "modelo.html"
DESTINO = AQUI / "index.html"

CHAVES = ["lula", "flavio", "eleicoes", "tse"]
CANDIDATOS = ["lula", "flavio"]          #os dois do 2o turno

#A data da votacao e fato do mundo, nao sai do banco. Ela parte o recorte em
#tres regimes que NAO podem ser lidos como um so: antes do resultado a disputa
#estava em aberto, depois dele existe um vencedor, e cobrir mais o vencedor e
#decisao editorial comum. Misturar os tres faz o equilibrio medio esconder os
#dois comportamentos.
DIA_VOTACAO = "2026-10-25"

#a mesma do etl.py: a de campanha, sem os picos dos dois turnos
JANELA_CANONICA = "campanha"
ROTULOS = {"lula": "Lula", "flavio": "Flávio Bolsonaro",
           "eleicoes": "Eleições", "tse": "TSE"}


def ler(nome: str) -> list[dict]:
    caminho = SAIDA_ETL / nome
    if not caminho.exists():
        sys.exit(f"{nome} nao existe, rode `python etl.py` antes")
    with caminho.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def base(materias: list[dict], veiculos: list[dict]) -> sqlite3.Connection:
    """Carrega os CSVs do ETL numa base SQLite em memoria.

    O painel precisa de contagem cruzada: por veiculo, por tema, por tom, e do
    cruzamento do tom do dicionario com o da IA. Isso e GROUP BY, e escrito em
    laco de Python cada contagem vira uma linha diferente que ninguem consegue
    comparar com a outra. Em memoria porque o arquivo do Midiometro so e lido
    pelo etl.py, e porque o insumo aqui sao os CSVs ja validados.

    A coluna `ordem` guarda a posicao da linha no CSV. Ela existe para o
    desempate ficar igual ao do `Counter.most_common`, que em empate mantem a
    ordem de chegada. Sem ela, dois temas com a mesma contagem trocariam de
    lugar entre execucoes e o painel mudaria sozinho.
    """
    con = sqlite3.connect(":memory:")
    inteiras = {"entra_nos_indicadores", "dia_completo", "na_home",
                "observacoes", "cita_lula", "cita_flavio", "cita_eleicoes",
                "cita_tse", "materia_id", "posicao_home", "posicoes_home"}

    def tabela(nome: str, linhas: list[dict], extra: str = "") -> None:
        campos = list(linhas[0])
        tipos = ", ".join(f"{c} {'INTEGER' if c in inteiras else 'TEXT'}"
                          for c in campos)
        con.execute(f"CREATE TABLE {nome} (ordem INTEGER, {tipos}{extra})")
        con.executemany(
            f"INSERT INTO {nome} (ordem, {', '.join(campos)}) "
            f"VALUES ({', '.join('?' * (len(campos) + 1))})",
            [(i, *[int(l[c]) if c in inteiras and l[c] != "" else (l[c] or None)
                   for c in campos]) for i, l in enumerate(linhas)])

    tabela("materias", materias)
    tabela("veiculos", veiculos)
    con.execute("CREATE INDEX i_mat ON materias(veiculo, dia)")
    return con


#O recorte que alimenta tema, tom e papel: factual e citando candidato. Fica
#numa constante porque os tres indicadores TEM de olhar o mesmo conjunto; ja
#houve divergencia por um deles esquecer o filtro de factual.
ALVO = "entra_nos_indicadores = 1 AND (cita_lula = 1 OR cita_flavio = 1)"


def participacao(valores: dict[str, float]) -> dict[str, float] | None:
    total = sum(valores.values())
    return {k: v / total for k, v in valores.items()} if total else None


def _antes_de(dia: str) -> str:
    d = datetime.fromisoformat(dia)
    return (d - timedelta(days=1)).date().isoformat()


def _depois_de(dia: str) -> str:
    d = datetime.fromisoformat(dia)
    return (d + timedelta(days=1)).date().isoformat()


def _br(dia: str) -> str:
    """2026-10-25 -> 25/10"""
    return dia[8:10] + "/" + dia[5:7]


def correlacao(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 4:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** .5
    return round(num / den, 3) if den else None


def montar_episodios() -> list[dict]:
    """
    Cada episodio com as duas curvas e quem puxou quem.

    A defasagem e o que distingue a imprensa criando o assunto de a imprensa
    respondendo a um interesse que ja existia. Compara a correlacao no mesmo
    dia com as duas defasagens de um dia e fica com a maior.
    """
    linhas = ler("episodios_diario.csv")
    por_ep = {}
    for l in linhas:
        por_ep.setdefault(l["episodio"], {})[l["dia"]] = l
    saida = []
    for episodio, dias_ep in por_ep.items():
        dias = sorted(dias_ep)
        serie = [{"dia": d,
                  "materias": int(dias_ep[d]["materias"]),
                  "busca": float(dias_ep[d]["indice_busca"]) if dias_ep[d]["indice_busca"] else None}
                 for d in dias]
        com_busca = [p for p in serie if p["busca"] is not None]
        imp = [p["materias"] for p in com_busca]
        bus = [p["busca"] for p in com_busca]
        r_igual = correlacao(imp, bus) if com_busca else None
        r_imprensa = correlacao(imp[:-1], bus[1:]) if len(imp) > 4 else None
        r_publico = correlacao(bus[:-1], imp[1:]) if len(imp) > 4 else None
        quem = None
        if None not in (r_igual, r_imprensa, r_publico):
            melhor = max((r_igual, "mesmo dia"), (r_imprensa, "imprensa"), (r_publico, "público"))
            #se NENHUMA das tres correlacoes chega a 0,3, nao ha relacao a
            #descrever, e dizer "o publico puxa" sobre r=0,13 seria inventar
            #uma. Foi o caso do Processo dos Bolsonaro, que tem r entre -0,26
            #e +0,13: a cobertura e a busca simplesmente nao andam juntas.
            if melhor[0] < .3:
                quem = "sem relação"
            else:
                #so vale chamar de defasagem se ela ganhar com folga
                quem = melhor[1] if melhor[0] - r_igual > .1 or melhor[1] == "mesmo dia" else "mesmo dia"
        pico_imp = max(serie, key=lambda p: p["materias"])["dia"]
        pico_bus = max(com_busca, key=lambda p: p["busca"])["dia"] if com_busca else None
        saida.append({
            "episodio": episodio,
            "materias": sum(p["materias"] for p in serie),
            "busca": round(sum(bus), 1) if bus else None,
            "tem_busca": bool(com_busca),
            #o indice do Trends e normalizado DENTRO de cada consulta: os cinco
            #episodios da campanha vieram de uma so, entao sao comparaveis entre
            #si; este veio de outra e NAO e comparavel com eles
            "escala_propria": episodio == "Processo dos Bolsonaro",
            "r_mesmo_dia": r_igual, "r_imprensa_puxa": r_imprensa, "r_publico_puxa": r_publico,
            "quem_puxa": quem,
            "pico_imprensa": pico_imp, "pico_busca": pico_bus,
            "serie": serie,
        })
    return sorted(saida, key=lambda e: -e["materias"])


def montar_resultado() -> dict | None:
    """
    O resultado oficial do TSE e o erro das pesquisas contra ele.

    Esta e a unica medida do projeto com gabarito: tudo o mais e atencao e
    opiniao, que nao tem resposta certa. Por isso o erro das pesquisas e
    calculado contra o resultado, e nao o contrario.
    """
    linhas = ler("resultado_oficial.csv")
    if not linhas:
        return None
    segundo = sorted((l for l in linhas if l["turno"] == "2"),
                     key=lambda l: -float(l["votos"]))
    if len(segundo) < 2:
        return None
    vencedor, perdedor = segundo[0], segundo[1]
    pct_v, pct_p = float(vencedor["pct_validos"]), float(perdedor["pct_validos"])

    #a ultima pesquisa de cada instituto, em votos validos
    pesquisas = [l for l in ler("pesquisas.csv")
                 if l["metrica"] == "intencao" and l["base"] == "válidos"]
    por_inst = {}
    for l in pesquisas:
        por_inst.setdefault(l["instituto"], {}).setdefault(l["dia"], {})[l["candidato"]] = float(l["valor"])
    ultimas = []
    for instituto, dias in por_inst.items():
        dia = max(dias)
        v = dias[dia]
        if len(v) == 2:
            #margem prevista para Flávio, contra a margem real
            prevista = v["Flávio Bolsonaro"] - v["Lula"]
            ultimas.append({"instituto": instituto, "dia": dia,
                            "flavio": v["Flávio Bolsonaro"], "lula": v["Lula"],
                            "margem_prevista": round(prevista, 2)})
    real = (pct_p if perdedor["candidato"].startswith("FLAVIO") else pct_v) -            (pct_v if vencedor["candidato"].startswith("LULA") else pct_p)
    for u in ultimas:
        u["erro"] = round(u["margem_prevista"] - real, 2)

    return {
        "vencedor": vencedor["candidato"], "pct_vencedor": pct_v,
        "perdedor": perdedor["candidato"], "pct_perdedor": pct_p,
        "votos_vencedor": int(vencedor["votos"]), "votos_perdedor": int(perdedor["votos"]),
        "margem_pontos": round(pct_v - pct_p, 2),
        "margem_votos": int(vencedor["votos"]) - int(perdedor["votos"]),
        "total_validos": int(vencedor["total_validos"]),
        "margem_real_flavio": round(real, 2),
        "gerado_pelo_tse": vencedor["gerado_pelo_tse"],
        "ultimas_pesquisas": sorted(ultimas, key=lambda u: -abs(u["erro"])),
    }


def montar_pesquisas() -> dict:
    """As series de pesquisa, por metrica. O valor vem da manchete, auditado."""
    linhas = ler("pesquisas.csv")
    por_metrica = {}
    for l in linhas:
        chave = l["metrica"]
        ponto = por_metrica.setdefault(chave, {}).setdefault((l["dia"], l["instituto"], l["base"]), {})
        ponto[l["candidato"]] = float(l["valor"])
    saida = {}
    for metrica, pontos in por_metrica.items():
        saida[metrica] = [{"dia": d, "instituto": i, "base": b,
                           "flavio": v.get("Flávio Bolsonaro"), "lula": v.get("Lula")}
                          for (d, i, b), v in sorted(pontos.items())]
    return saida


def montar() -> dict:
    materias = ler("materias.csv")
    share = ler("share_diario.csv")
    veiculos = ler("veiculos.csv")
    con = base(materias, veiculos)

    factuais = [m for m in materias if m["entra_nos_indicadores"] == "1"]
    dias = sorted({m["dia"] for m in materias})
    ultimo_completo = max((m["dia"] for m in materias if m["dia_completo"] == "1"),
                          default=dias[-1])

    #---- por veiculo: share of voice, equilibrio, tom -------------------
    #O LEFT JOIN mantem na tabela o veiculo que nao tiver nenhuma noticia
    #factual no recorte, que com INNER JOIN sumiria sem deixar sinal.
    por_veiculo = [{
        "slug": slug,
        "nome": nome,
        "noticias": noticias,
        "lula": lula,
        "flavio": flavio,
        "ambos": ambos,
        "algum": algum,
        #1 = cobertura identica entre os dois; 0 = so um candidato aparece
        "equilibrio": round(1 - abs(lula - flavio) / (lula + flavio), 3)
                      if lula + flavio else None,
        "neutro": round(100 * neutras / com_tom, 1) if com_tom else None,
        #a profundidade da capa, sem a qual a posicao nao e comparavel
        "posicoes_home": posicoes,
    } for (slug, nome, posicoes, noticias, lula, flavio, ambos, algum,
           com_tom, neutras) in con.execute("""
        SELECT v.slug, v.nome, v.posicoes_home,
               COUNT(m.materia_id),
               COALESCE(SUM(m.cita_lula), 0),
               COALESCE(SUM(m.cita_flavio), 0),
               COALESCE(SUM(m.cita_lula * m.cita_flavio), 0),
               COALESCE(SUM(CASE WHEN m.cita_lula = 1 OR m.cita_flavio = 1
                                 THEN 1 ELSE 0 END), 0),
               COUNT(m.tom_lexico),
               COALESCE(SUM(CASE WHEN m.tom_lexico = 'neutro'
                                 THEN 1 ELSE 0 END), 0)
          FROM veiculos v
          LEFT JOIN materias m
            ON m.veiculo = v.slug AND m.entra_nos_indicadores = 1
         GROUP BY v.slug, v.nome, v.posicoes_home
         ORDER BY v.ordem""")]

    #---- serie diaria, por janela do Explore ----------------------------
    #cada janela tem a sua propria normalizacao, entao elas nunca se somam:
    #ficam lado a lado e o painel diz qual e a canonica.
    por_janela = {}
    for linha in share:
        por_janela.setdefault(linha["janela"], {}) \
                  .setdefault(linha["dia"], {})[linha["termo"]] = linha

    def montar_serie(dias_da_janela: dict) -> list[dict]:
        saida = []
        for dia in sorted(dias_da_janela):
            linhas = dias_da_janela[dia]
            ponto = {
                "dia": dia,
                "completo": linhas[CHAVES[0]]["dia_completo"] == "1",
                "imprensa": {c: float(linhas[c]["share_imprensa"] or 0) for c in CHAVES},
                "mencoes": {c: int(linhas[c]["mencoes"]) for c in CHAVES},
            }
            if all(linhas[c]["share_publico"] for c in CHAVES):
                ponto["publico"] = {c: float(linhas[c]["share_publico"]) for c in CHAVES}
                #o INDICE bruto fica junto do share: o total do periodo tem de
                #ser somado sobre ele, nao sobre os share ja normalizados.
                #somar share e renormalizar da media simples entre dias e pesa
                #um domingo fraco igual a uma quinta cheia. Medido, as duas
                #contas davam 0,266 e 0,275 para a mesma janela.
                ponto["indice"] = {c: float(linhas[c]["indice_publico"]) for c in CHAVES}
                ponto["dvt"] = round(
                    0.5 * sum(abs(ponto["imprensa"][c] - ponto["publico"][c]) for c in CHAVES), 4)
            saida.append(ponto)
        return saida

    def resumir(pontos: list[dict]) -> dict | None:
        usaveis = [p for p in pontos if p["completo"] and "publico" in p]
        if not usaveis:
            return None
        imprensa = {c: sum(p["mencoes"][c] for p in usaveis) for c in CHAVES}
        publico = {c: sum(p["indice"][c] for p in usaveis) for c in CHAVES}
        si, sp = participacao(imprensa), participacao(publico)
        if not si or not sp:
            return None

        #A FAIXA, e nao so o ponto. O indice do candidato ja chega multiplicado
        #pela fatia de trabalho; reescalonar para os dois extremos da faixa
        #mostra quanto do indicador depende dessa escolha, que e a maior
        #limitacao do metodo, e nao uma casa decimal.
        def com_fatia(fatia: float) -> float:
            alt = dict(publico)
            alt["flavio"] = publico["flavio"] * fatia / FATIA_CANDIDATO
            s = participacao(alt)
            return 0.5 * sum(abs(si[c] - s[c]) for c in CHAVES)

        extremos = sorted(com_fatia(f) for f in FATIA_FAIXA)
        return {
            "de": usaveis[0]["dia"], "ate": usaveis[-1]["dia"], "dias": len(usaveis),
            "termos": [{
                "chave": c, "rotulo": ROTULOS[c], "mencoes": imprensa[c],
                "imprensa": round(si[c], 4), "publico": round(sp[c], 4),
                "descompasso": round(si[c] - sp[c], 4),
            } for c in CHAVES],
            "dvt": round(0.5 * sum(abs(si[c] - sp[c]) for c in CHAVES), 4),
            "dvt_faixa": [round(extremos[0], 4), round(extremos[1], 4)],
        }

    serie = montar_serie(por_janela.get(JANELA_CANONICA, {}))
    periodo = resumir(serie)
    arco = resumir(montar_serie(por_janela.get("arco", {})))

    #---- as tres fases: antes, durante e depois do resultado ------------
    #A mesma consulta serve a fase inteira e a fase de um veiculo so: o que
    #muda e um pedaco do WHERE. Antes eram tres listas filtradas em memoria e
    #mais uma por veiculo, quatro recortes do mesmo dado com quatro chances de
    #divergir no filtro de factual.
    def equilibrio(de: str, ate: str, veiculo: str | None = None) -> dict:
        onde = "entra_nos_indicadores = 1 AND dia BETWEEN ? AND ?"
        args: list = [de, ate]
        if veiculo is not None:
            onde += " AND veiculo = ?"
            args.append(veiculo)
        n, nl, nf = con.execute(
            f"""SELECT COUNT(*), COALESCE(SUM(cita_lula), 0),
                       COALESCE(SUM(cita_flavio), 0)
                  FROM materias WHERE {onde}""", args).fetchone()
        return {
            "noticias": n, "lula": nl, "flavio": nf,
            "razao": round(nl / nf, 2) if nf else None,
            "equilibrio": round(1 - abs(nl - nf) / (nl + nf), 3) if nl + nf else None,
        }

    fim_antes = min(dias[-1], _antes_de(DIA_VOTACAO))
    inicio_depois = _depois_de(DIA_VOTACAO)
    antes = equilibrio("2026-10-07", fim_antes)
    depois = equilibrio(inicio_depois, dias[-1])
    fases = None
    if antes["noticias"] and depois["noticias"]:
        fases = {
            "dia_votacao": DIA_VOTACAO,
            "blocos": [
                {"nome": "Entre turnos", "janela": f"07/10 a {_br(_antes_de(DIA_VOTACAO))}",
                 **antes},
                {"nome": "Dia da votação", "janela": _br(DIA_VOTACAO),
                 **equilibrio(DIA_VOTACAO, DIA_VOTACAO)},
                {"nome": "Repercussão", "janela": f"{_br(_depois_de(DIA_VOTACAO))} a {_br(dias[-1])}",
                 **depois},
            ],
            "por_veiculo": [{
                "slug": v["slug"], "nome": v["nome"],
                "antes": equilibrio("2026-10-07", fim_antes, v["slug"])["equilibrio"],
                "depois": equilibrio(inicio_depois, dias[-1], v["slug"])["equilibrio"],
            } for v in veiculos],
            #a repercussao costuma terminar num dia ainda aberto
            "ultimo_parcial": dias[-1] != ultimo_completo,
        }

    #---- tom geral (so o lexico existe no recorte) ----------------------
    tons = [m["tom_lexico"] for m in factuais if m["tom_lexico"]]
    tom = {v: round(100 * tons.count(v) / len(tons), 1)
           for v in ("positivo", "neutro", "negativo")} if tons else None

    #A trava de cobertura, usada pelo tom da IA e pela distribuicao por tema.
    #duas condicoes, e a segunda e a que pega o caso real: em 06/11/2026 a
    #cobertura era de 55% e os sete primeiros dias do recorte tinham ZERO.
    #A media nao avisa nada disso; quem avisa e o pior dia.
    COBERTURA_MINIMA = 0.80

    #---- o tom da IA, e o confronto com o dicionario ---------------------
    #
    #nao e "a versao boa do mesmo numero": sao dois metodos que respondem a
    #mesma pergunta e discordam em metade dos casos. Medido nas MESMAS 1.295
    #materias, concordam em 50,1%. O dicionario le 76,8% de neutro e a IA le
    #55,4%, e 442 materias que o dicionario chamou de neutras a IA leu como
    #carregadas.
    #
    #por isso o painel mostra os dois e a matriz, em vez de trocar um pelo
    #outro em silencio. Trocar sem mostrar faria o cartao de tom mudar de
    #numero entre duas visitas sem explicacao nenhuma.
    COM_IA = f"{ALVO} AND tom_ia IS NOT NULL"
    AMBOS = f"{COM_IA} AND tom_lexico IS NOT NULL"
    conta = lambda onde: con.execute(
        f"SELECT COUNT(*) FROM materias WHERE {onde}").fetchone()[0]

    n_alvo, n_com_ia, n_ambos = conta(ALVO), conta(COM_IA), conta(AMBOS)

    VALORES = ("negativo", "neutro", "positivo")

    def reparte(campo, onde):
        linhas = dict(con.execute(
            f"SELECT {campo}, COUNT(*) FROM materias WHERE {onde} GROUP BY {campo}"))
        n = sum(linhas.values())
        return {v: round(100 * linhas.get(v, 0) / n, 1) for v in VALORES} if n else None

    #HAVING no lugar da diferenca entre dois conjuntos de dias: o dia que tem
    #materia do alvo e nenhuma leitura da IA e exatamente COUNT(tom_ia) = 0.
    dias_sem_ia = [d for (d,) in con.execute(
        f"""SELECT dia FROM materias WHERE {ALVO}
             GROUP BY dia HAVING COUNT(tom_ia) = 0 ORDER BY dia""")]
    cobertura_tom = n_com_ia / n_alvo if n_alvo else 0.0

    cruzado = {(l, i): n for l, i, n in con.execute(
        f"""SELECT tom_lexico, tom_ia, COUNT(*) FROM materias
             WHERE {AMBOS} GROUP BY tom_lexico, tom_ia""")}

    tom_ia = {
        "pronto": cobertura_tom >= COBERTURA_MINIMA and not dias_sem_ia,
        "cobertura": round(100 * cobertura_tom, 1),
        "analisadas": n_com_ia,
        "alvo": n_alvo,
        "minimo": round(100 * COBERTURA_MINIMA),
        "dias_vazios": dias_sem_ia,
        "reparticao": reparte("tom_ia", COM_IA),
        #o confronto so pode sair das materias que tem OS DOIS tons, senao
        #compara universos diferentes e a divergencia vira artefato
        "confronto": {
            "n": n_ambos,
            "acordo": round(100 * conta(f"{AMBOS} AND tom_ia = tom_lexico") / n_ambos, 1),
            "lexico": reparte("tom_lexico", AMBOS),
            "ia": reparte("tom_ia", AMBOS),
            #a matriz e um GROUP BY de duas colunas; o Python so arruma o
            #resultado em linhas e colunas na ordem dos VALORES
            "matriz": [[cruzado.get((L, I), 0) for I in VALORES] for L in VALORES],
            "valores": list(VALORES),
        } if n_ambos else None,
    } if n_com_ia else None

    #---- distribuicao por tema (o quinto indicador) ----------------------
    #
    #O indicador so acende quando a analise de IA cobre o recorte de um jeito
    #que admita ser lido como "o recorte". Duas travas, e as duas vieram de
    #medicao, nao de cautela generica:
    #
    #- COBERTURA MINIMA. Com metade das materias analisadas, metade da
    #frase e chute.
    #- NENHUM DIA VAZIO. Esta e a que pega o caso real: em 06/11/2026 a
    #cobertura era de 55% e os sete primeiros dias do recorte, 04 a
    #10/10, tinham ZERO. A IA processa em ordem decrescente de dia e a
    #cota do dia acabou antes de chegar neles. Uma distribuicao por tema
    #calculada ali diria 80,9% de politica e seria uma frase sobre
    #12-26/10 vendida como frase sobre o recorte.
    #
    #A media nao protege contra isso: 55% de cobertura "no geral" conviveu com
    #0% em sete dias e 83% no ultimo. Por isso a trava olha o PIOR dia.
    #o mesmo alvo do resto do painel: factual E citando candidato
    COM_TEMA = f"{ALVO} AND tema_ia IS NOT NULL"
    n_com_tema = conta(COM_TEMA)

    #cobertura por dia numa consulta so: o total do dia e quantos desses a IA
    #leu. E daqui que sai o PIOR dia, que e o numero que a media esconde.
    por_dia_tema = {dia: (total, lidas) for dia, total, lidas in con.execute(
        f"""SELECT dia, COUNT(*), COUNT(tema_ia) FROM materias
             WHERE {ALVO} GROUP BY dia ORDER BY dia""")}

    cobertura = n_com_tema / n_alvo if n_alvo else 0.0
    pior_dia = min(((f / t, dia) for dia, (t, f) in por_dia_tema.items() if t),
                   default=(0.0, ""))
    dias_vazios = [dia for dia, (t, f) in por_dia_tema.items() if t and not f]

    #ORDER BY com desempate por MIN(ordem) para reproduzir exatamente o que o
    #counter.most_common fazia: contagem decrescente e, no empate, quem
    #apareceu primeiro. Sem o desempate, dois temas com a mesma contagem
    #trocariam de posicao entre execucoes.
    contagem = list(con.execute(
        f"""SELECT tema_ia, COUNT(*) FROM materias
             WHERE {COM_TEMA}
             GROUP BY tema_ia
             ORDER BY COUNT(*) DESC, MIN(ordem)"""))
    pronto = cobertura >= COBERTURA_MINIMA and not dias_vazios

    tema_dist = {
        "pronto": pronto,
        "cobertura": round(100 * cobertura, 1),
        "analisadas": n_com_tema,
        "alvo": n_alvo,
        "pior_dia": {"dia": pior_dia[1], "pct": round(100 * pior_dia[0], 1)},
        "dias_vazios": dias_vazios,
        "minimo": round(100 * COBERTURA_MINIMA),
        #a fatia vai mesmo quando `pronto` e falso: o painel a usa para dizer
        #o que JA se sabe, rotulada como parcial, em vez de mostrar vazio.
        "fatias": [{"tema": t, "n": n, "pct": round(100 * n / n_com_tema, 1)}
                   for t, n in contagem] if n_com_tema else [],
    }

    #---- materias do drill-down ------------------------------------------
    citam = [m for m in factuais if m["cita_lula"] == "1" or m["cita_flavio"] == "1"]
    citam.sort(key=lambda m: m["dia"], reverse=True)
    drill = [{
        "v": m["veiculo"], "d": m["dia"], "t": m["titulo"], "u": m["url"],
        "s": m["secao"], "tom": m["tom_lexico"],
        "c": [c for c in CANDIDATOS if m[f"cita_{c}"] == "1"],
        "p": int(m["posicao_home"]) if m["posicao_home"] else None,
    } for m in citam]

    return {
        "gerado_em": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "recorte": {
            "inicio": dias[0], "fim": dias[-1], "ultimo_completo": ultimo_completo,
            "noticias": len(factuais), "exportadas": len(materias),
            "citam_candidato": len(citam),
            "sem_candidato": len(factuais) - len(citam),
        },
        "veiculos": por_veiculo,
        "serie": serie,
        "periodo": periodo,
        "arco": arco,
        "tom": tom,
        "tema": tema_dist,
        "tom_ia": tom_ia,
        "fases": fases,
        "episodios": montar_episodios(),
        "pesquisas": montar_pesquisas(),
        "resultado": montar_resultado(),
        "fatia_candidato": FATIA_CANDIDATO,
        "fatia_faixa": list(FATIA_FAIXA),
        "rotulos": ROTULOS,
        "materias": drill,
    }


def main() -> int:
    if not MODELO.exists():
        sys.exit(f"modelo nao encontrado em {MODELO}")
    dados = montar()
    #substituicao simples por marcador: o modelo tem chaves de CSS por todo
    #lado, entao format() ou f-string quebrariam nele
    html = MODELO.read_text(encoding="utf-8").replace(
        "__DADOS__", json.dumps(dados, ensure_ascii=False, separators=(",", ":")))
    DESTINO.write_text(html, encoding="utf-8")

    r = dados["recorte"]
    print(f"painel gerado: {DESTINO}")
    print(f"  recorte          {r['inicio']} a {r['fim']}  (completo ate {r['ultimo_completo']})")
    print(f"  noticias         {r['noticias']:,}")
    print(f"  no drill-down    {r['citam_candidato']:,}  "
          f"({r['sem_candidato']:,} nao citam candidato e ficam de fora)")
    if dados["periodo"]:
        p = dados["periodo"]
        print(f"  descompasso      {p['dvt']:.3f}  ({p['de']} a {p['ate']}, {p['dias']} dias)")
    print(f"  tamanho          {DESTINO.stat().st_size/1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
