# -*- coding: utf-8 -*-
"""
Junta os CSV da entrega num unico Excel, uma aba por arquivo.

Os VALORES vao exatamente como estao nos CSV. O que muda e so a aparencia:
nome da aba, nome das colunas, fonte, largura e cabecalho congelado. Nenhuma
celula e calculada e nenhum valor e convertido. O "<1" que o Google escreve
quando a busca e baixa demais continua "<1".

Os CSV do Google Trends tem duas linhas de preambulo antes do cabecalho. Numa
planilha isso vira lixo nas primeiras celulas, entao a aba comeca na linha de
cabecalho de verdade. O arquivo .csv original segue intacto.

    python exportar_excel.py
    python exportar_excel.py --pasta "C:/.../midiometro-datasets-2026-11-03"
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

FONTE = "Arial"

#arquivo -> (nome da aba, {coluna no csv: nome na planilha})
#A ordem aqui e a ordem das abas: primeiro a base, depois as fontes
#externas, depois o que o projeto calculou, e por fim o Google Trends.
PLANO: list[tuple[str, str, dict[str, str]]] = [
    ("noticias-coletadas-g1-folha-cnn-uol.csv", "Notícias coletadas", {
        "materia_id": "ID da matéria", "veiculo": "Veículo", "dia": "Data",
        "dia_completo": "Dia completo?", "tipo": "Tipo",
        "entra_nos_indicadores": "Entra nos indicadores?", "secao": "Seção",
        "titulo": "Título", "url": "Link", "tom_lexico": "Tom da manchete",
        "posicao_home": "Posição na home", "na_home": "Esteve na home?",
        "observacoes": "Vezes observada", "cita_lula": "Cita Lula",
        "cita_flavio": "Cita Flávio", "cita_eleicoes": "Cita eleições",
        "cita_tse": "Cita TSE"}),

    ("resultado-oficial-tse-presidente.csv", "Resultado oficial TSE", {
        "turno": "Turno", "candidato": "Candidato", "votos": "Votos",
        "pct_validos": "% dos votos válidos",
        "total_validos": "Total de votos válidos",
        "gerado_pelo_tse": "Arquivo gerado pelo TSE em"}),

    ("pesquisas-eleitorais-publicadas.csv", "Pesquisas publicadas", {
        "dia": "Data", "instituto": "Instituto", "metrica": "O que mede",
        "base": "Base de cálculo", "candidato": "Candidato",
        "valor": "Valor (%)",
        "veiculos_que_reportaram": "Veículos que publicaram"}),

    ("veiculos-monitorados.csv", "Veículos monitorados", {
        "slug": "Código", "nome": "Nome", "url_base": "Endereço",
        "posicoes_home": "Posições na home"}),

    ("participacao-de-cada-tema-por-dia.csv", "Participação por tema e dia", {
        "janela": "Janela", "dia": "Data", "dia_completo": "Dia completo?",
        "termo": "Termo buscado", "rotulo": "Tema",
        "mencoes": "Menções na imprensa",
        "share_imprensa": "Participação na imprensa",
        "indice_publico": "Índice do Google Trends",
        "share_publico": "Participação na busca",
        "descompasso_pp": "Descompasso (pontos)"}),

    ("diferenca-entre-imprensa-e-busca-por-dia.csv", "Diferença imprensa x busca", {
        "janela": "Janela", "dia": "Data", "dia_completo": "Dia completo?",
        "distancia_variacao_total": "Distância (0 a 1)",
        "mencoes_total": "Menções no dia"}),

    ("episodios-materias-e-busca-por-dia.csv", "Episódios por dia", {
        "episodio": "Episódio", "dia": "Data",
        "materias": "Matérias publicadas", "indice_busca": "Índice de busca"}),
]

#as abas do Trends: arquivo -> nome da aba (<= 31 caracteres, limite do Excel)
TRENDS_ABAS = {
    "google-trends-candidatos-07out-a-24out.csv": "Busca candidatos 07-24out",
    "google-trends-candidatos-04out-a-27out.csv": "Busca candidatos 04-27out",
    "google-trends-candidatos-04out-a-20out.csv": "Busca candidatos 04-20out",
    "google-trends-assuntos-07out-a-24out.csv": "Busca assuntos 07-24out",
    "google-trends-sigilo-17out-a-24out.csv": "Busca sigilo 17-24out",
    "google-trends-stf-e-bolsonaro-07out-a-24out.csv": "Busca STF Bolsonaro 07-24out",
    "google-trends-pai-e-filho-07out-a-24out.csv": "Busca pai e filho 07-24out",
    "google-trends-variacoes-de-nome-04out-a-20out.csv": "Busca nomes 04-20out",
    "google-trends-escala-6x1-06out-a-24out.csv": "Busca escala 6x1 06-24out",
    "google-trends-candidatos-sem-bolsonaro-07out-a-24out.csv":
        "Busca sem Bolsonaro 07-24out",
    "google-trends-candidatos-sem-bolsonaro-04out-a-27out.csv":
        "Busca sem Bolsonaro 04-27out",
}

NUMERO = re.compile(r"^-?\d+(\.\d+)?$")


def valor(texto: str):
    """Numero vira numero, para a planilha poder somar. O resto fica texto."""
    t = (texto or "").strip()
    if NUMERO.match(t):
        return float(t) if "." in t else int(t)
    return t


def ler_csv(caminho: Path) -> tuple[list[str], list[list[str]]]:
    with caminho.open(encoding="utf-8-sig", newline="") as f:
        linhas = list(csv.reader(f))
    return linhas[0], linhas[1:]


def ler_trends(caminho: Path) -> tuple[list[str], list[list[str]]]:
    """Pula o preambulo do Google e limpa o ': (Brasil)' do cabecalho."""
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    i = next(k for k, l in enumerate(linhas) if l.startswith("Dia,"))
    cabecalho = next(csv.reader([linhas[i]]))
    cabecalho = ["Data"] + [c.split(":")[0].strip() for c in cabecalho[1:]]
    corpo = [r for r in csv.reader(linhas[i + 1:]) if r and r[0].strip()]
    return cabecalho, corpo


def escrever_aba(wb: Workbook, titulo: str, cabecalho: list[str],
                 corpo: list[list[str]]) -> int:
    ws = wb.create_sheet(titulo)
    ws.append(cabecalho)
    for linha in corpo:
        ws.append([valor(c) for c in linha])

    cinza = PatternFill("solid", fgColor="EFEFEF")
    for celula in ws[1]:
        celula.font = Font(name=FONTE, bold=True)
        celula.fill = cinza
        celula.alignment = Alignment(vertical="center", wrap_text=True)
    for linha in ws.iter_rows(min_row=2):
        for celula in linha:
            celula.font = Font(name=FONTE)

    #largura pela maior celula da coluna, olhando uma amostra para nao
    #varrer 22 mil linhas em cada uma
    for i, nome in enumerate(cabecalho, start=1):
        maior = len(str(nome))
        for linha in corpo[:400]:
            if i <= len(linha):
                maior = max(maior, len(str(linha[i - 1])))
        ws.column_dimensions[get_column_letter(i)].width = min(max(maior + 2, 10), 52)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return len(corpo)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    hoje = dt.date.today().isoformat()
    ap.add_argument("--pasta",
                    default=str(Path.home() / "Downloads" / f"midiometro-datasets-{hoje}"))
    args = ap.parse_args(argv)
    pasta = Path(args.pasta)
    if not pasta.is_dir():
        print(f"pasta não encontrada: {pasta}")
        return 2

    wb = Workbook()
    wb.remove(wb.active)
    total, faltando = 0, []

    for arquivo, aba, renomear in PLANO:
        caminho = pasta / arquivo
        if not caminho.exists():
            faltando.append(arquivo)
            continue
        cabecalho, corpo = ler_csv(caminho)
        cabecalho = [renomear.get(c, c) for c in cabecalho]
        n = escrever_aba(wb, aba, cabecalho, corpo)
        total += n
        print(f"  {aba:<30} {n:>7,} linhas")

    for arquivo, aba in TRENDS_ABAS.items():
        caminho = pasta / arquivo
        if not caminho.exists():
            faltando.append(arquivo)
            continue
        cabecalho, corpo = ler_trends(caminho)
        n = escrever_aba(wb, aba, cabecalho, corpo)
        total += n
        print(f"  {aba:<30} {n:>7,} linhas")

    destino = pasta.parent / f"midiometro-datasets-{hoje}.xlsx"
    wb.save(destino)
    print(f"\n  {len(wb.sheetnames)} abas · {total:,} linhas de dados")
    print(f"  {destino}  ({destino.stat().st_size/1048576:.1f} MB)")
    if faltando:
        print(f"\n  NÃO ENCONTRADOS: {', '.join(faltando)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
