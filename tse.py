# -*- coding: utf-8 -*-
"""
Resultado oficial do TSE, sem baixar o arquivo inteiro.

O PROBLEMA
----------
O TSE publica a votacao por candidato num ZIP de 380 MB. O painel precisa de
dois numeros. Baixar 380 MB (3 GB descomprimidos) para somar duas colunas
seria desproporcional, e o disco desta maquina nao tem folga para isso.

A SOLUCAO
---------
Um ZIP guarda o indice no FIM do arquivo, e o servidor do TSE aceita
requisicoes por faixa (`Accept-Ranges: bytes`). Entao da para:

  1. ler so os ultimos bytes  -> o indice, com a lista de membros   (~3 KB)
  2. baixar so o membro util  -> `..._BR.csv`, a corrida presidencial (~2,3 MB)

Total transferido: 2,3 MB de 380 MB, 0,6% do arquivo.

O membro `_BR.csv` tem a eleicao presidencial inteira (os dois turnos, todas
as UFs, por municipio e zona). Os arquivos por UF e o `_BRASIL.csv`, de 3 GB,
trazem os outros cargos e nao servem aqui.

CONFERENCIA
-----------
A soma do 1o turno tem de dar Flavio 47,03% e Lula 45,16%, que foi o numero
publicado pela imprensa em 25/10. Se nao der, a agregacao esta errada e o
script avisa.

    python tse.py
"""
from __future__ import annotations

import collections
import csv
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

SAIDA = Path(__file__).parent / "dados" / "saida"
URL = ("https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_candidato_munzona/"
       "votacao_candidato_munzona_2026.zip")
MEMBRO = "votacao_candidato_munzona_2026_BR.csv"

#o 1o turno foi noticiado com estes numeros; servem de prova da agregacao
CONFERENCIA_1O_TURNO = {"FLAVIO BOLSONARO": 47.03, "LULA": 45.16}


class ArquivoRemoto(io.RawIOBase):
    """
    Objeto tipo-arquivo que busca so os trechos lidos, via header `Range`.

    O `zipfile` precisa de `seek` e `read`; como esta classe herda de
    RawIOBase, o `read` e servido por `readinto`, e sem implementar os dois,
    o zipfile levanta NotImplementedError.
    """

    def __init__(self, url: str):
        self.url, self.pos, self.baixado = url, 0, 0
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=40) as r:
            self.tamanho = int(r.headers["Content-Length"])
            self.aceita_faixa = r.headers.get("Accept-Ranges", "none") == "bytes"

    def seek(self, n, de_onde=0):
        self.pos = (n if de_onde == 0 else self.pos + n if de_onde == 1
                    else self.tamanho + n)
        return self.pos

    def tell(self): return self.pos
    def seekable(self): return True
    def readable(self): return True

    def _buscar(self, n: int) -> bytes:
        if n < 0:
            n = self.tamanho - self.pos
        if n == 0 or self.pos >= self.tamanho:
            return b""
        fim = min(self.pos + n - 1, self.tamanho - 1)
        req = urllib.request.Request(
            self.url, headers={"Range": f"bytes={self.pos}-{fim}",
                               "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            dados = r.read()
        self.pos += len(dados)
        self.baixado += len(dados)
        return dados

    def read(self, n=-1): return self._buscar(n)

    def readinto(self, buf):
        dados = self._buscar(len(buf))
        buf[:len(dados)] = dados
        return len(dados)


def apurar(linhas: list[dict], turno: str) -> tuple[dict[str, int], int]:
    votos = collections.Counter()
    for l in linhas:
        if l["NR_TURNO"] == turno:
            votos[l["NM_URNA_CANDIDATO"]] += int(l["QT_VOTOS_NOMINAIS_VALIDOS"] or 0)
    return dict(votos), sum(votos.values())


def main() -> int:
    remoto = ArquivoRemoto(URL)
    if not remoto.aceita_faixa:
        print("o servidor não aceita requisições por faixa; "
              "seria preciso baixar os 380 MB", file=sys.stderr)
        return 2

    z = zipfile.ZipFile(io.BufferedReader(remoto, 262144))
    if MEMBRO not in z.namelist():
        print(f"o membro {MEMBRO} não está no ZIP; o TSE mudou o formato",
              file=sys.stderr)
        return 2
    linhas = list(csv.DictReader(
        io.StringIO(z.read(MEMBRO).decode("latin-1")), delimiter=";"))
    gerado = f"{linhas[0]['DT_GERACAO']} {linhas[0]['HH_GERACAO']}"
    print(f"TSE · arquivo gerado em {gerado}")
    print(f"transferido: {remoto.baixado/1048576:.1f} MB de "
          f"{remoto.tamanho/1048576:,.0f} MB ({100*remoto.baixado/remoto.tamanho:.1f}%)\n")

    #conferência contra o número publicado pela imprensa
    v1, t1 = apurar(linhas, "1")
    for nome, esperado in CONFERENCIA_1O_TURNO.items():
        obtido = 100 * v1.get(nome, 0) / t1
        if abs(obtido - esperado) > .05:
            print(f"CONFERÊNCIA FALHOU: {nome} no 1º turno deu {obtido:.2f}%, "
                  f"esperado {esperado}%", file=sys.stderr)
            return 3
    print(f"conferência do 1º turno: ok "
          f"({', '.join(f'{k} {v}%' for k, v in CONFERENCIA_1O_TURNO.items())})\n")

    v2, t2 = apurar(linhas, "2")
    ordem = sorted(v2.items(), key=lambda kv: -kv[1])
    print(f"2º TURNO · {t2:,} votos nominais válidos")
    for nome, v in ordem:
        print(f"  {nome:<26} {v:>13,}   {100*v/t2:6.2f}%")
    margem = ordem[0][1] - ordem[1][1]
    print(f"\n  margem: {margem:,} votos = {100*margem/t2:.2f} pontos")

    SAIDA.mkdir(parents=True, exist_ok=True)
    destino = SAIDA / "resultado_oficial.csv"
    with destino.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["turno", "candidato", "votos", "pct_validos",
                    "total_validos", "gerado_pelo_tse"])
        for turno, (votos, total) in (("1", (v1, t1)), ("2", (v2, t2))):
            for nome, v in sorted(votos.items(), key=lambda kv: -kv[1]):
                w.writerow([turno, nome, v, round(100 * v / total, 4), total, gerado])
    print(f"\n  {destino.name}  {destino.stat().st_size/1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
