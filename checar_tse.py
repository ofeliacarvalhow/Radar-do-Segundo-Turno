# -*- coding: utf-8 -*-
"""
Pergunta ao TSE se o resultado oficial do 2o turno ja foi publicado.

POR QUE ESTE ARQUIVO EXISTE
---------------------------
O enunciado do projeto cita os dados abertos do TSE como fonte do resultado.
Em 27/10/2026 esse dado NAO existe ainda, e isso foi verificado nos dois
sistemas que o TSE mantem:

  - resultados.tse.jus.br: a eleicao esta registrada no config (pleito 3221,
    eleicao 6258, "Eleicao Ordinaria Federal - 2026 2o Turno", cargo
    Presidente) e os arquivos de CONFIGURACAO respondem 200, mas todo
    arquivo de resultado, votacao ou estatistica responde 404, nos DOIS
    turnos. O caminho esta certo; o dado e que nao esta la.
  - dadosabertos.tse.jus.br: o dataset "Resultados - 2026" existe, mas os
    arquivos foram modificados pela ultima vez em 25/10 as 16:16 GMT, ou seja
    13:16 em Brasilia, durante a votacao, antes de as urnas fecharem.

ELE NAO BAIXA NADA. Usa uma requisicao HEAD, que le so o cabecalho: o servidor
responde o tamanho e a data de modificacao sem transmitir o arquivo. O maior
deles tem 452 MB e nao caberia no disco, nem precisaria, o que o projeto
quer sao dois numeros.

    python checar_tse.py
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

#as urnas do 2o turno fecharam as 17h de Brasilia = 20h UTC de 25/10/2026.
#arquivo mais antigo que isso nao pode conter o resultado final.
FECHAMENTO = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)

ARQUIVOS = {
    "votação por candidato (município/zona)":
        "https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_candidato_munzona/"
        "votacao_candidato_munzona_2026.zip",
    "detalhe da apuração (município/zona)":
        "https://cdn.tse.jus.br/estatistica/sead/odsele/detalhe_votacao_munzona/"
        "detalhe_votacao_munzona_2026.zip",
}

#o resultado presidencial consolidado, se e quando o TSE publicar nesse padrao
RESULTADO_JSON = ("https://resultados.tse.jus.br/oficial/ele2026/6258/"
                  "dados-simplificados/br/br-c0001-e006258-r.json")


def cabecalho(url: str) -> dict:
    """Le so o cabecalho. Nenhum byte do arquivo trafega."""
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return {"status": r.status,
                    "tamanho": int(r.headers.get("Content-Length") or 0),
                    "modificado": r.headers.get("Last-Modified")}
    except urllib.error.HTTPError as e:
        return {"status": e.code}
    except (urllib.error.URLError, TimeoutError) as e:
        return {"erro": str(e)}


def main() -> int:
    print(f"urnas fecharam em {FECHAMENTO:%d/%m/%Y %H:%M} UTC\n")
    novidade = False

    for nome, url in ARQUIVOS.items():
        h = cabecalho(url)
        if "erro" in h:
            print(f"  {nome}\n    indisponível: {h['erro']}")
            continue
        if h.get("status") != 200:
            print(f"  {nome}\n    HTTP {h['status']}")
            continue
        quando = parsedate_to_datetime(h["modificado"]) if h.get("modificado") else None
        mb = h["tamanho"] / 1048576
        depois = quando and quando > FECHAMENTO
        novidade = novidade or bool(depois)
        marca = "JÁ TEM O RESULTADO" if depois else "ainda é de antes do fechamento"
        print(f"  {nome}")
        print(f"    {mb:,.0f} MB · modificado {quando:%d/%m/%Y %H:%M} UTC · {marca}")

    h = cabecalho(RESULTADO_JSON)
    estado = "PUBLICADO" if h.get("status") == 200 else f"ausente (HTTP {h.get('status','?')})"
    print(f"\n  resultado presidencial consolidado (JSON)\n    {estado}")
    novidade = novidade or h.get("status") == 200

    print("\n" + ("HÁ NOVIDADE: vale buscar o resultado oficial."
                  if novidade else
                  "Nada novo. O resultado oficial continua não publicado."))
    return 0 if novidade else 1


if __name__ == "__main__":
    raise SystemExit(main())
