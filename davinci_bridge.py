"""
davinci_bridge.py
==================

Ponte de leitura (somente leitura) entre o Michelangelo e o histórico de
testes salvos no DaVinci.

Como funciona: quando alguém clica em "Salvar este teste no histórico" no
DaVinci (app.py), ele grava uma entrada em `historico_testes.json` com
{id, nome, salvo_em, n_por_braco, n_total, dias_rodar, resumo_html}. O
`resumo_html` já é o relatório visual completo que o próprio DaVinci gera
(ab_design.py -> build_html_summary) e carrega todo o cadastro do teste —
BU, métrica, região, campanha, plataforma, dispositivo, objetivo, área.

Este módulo NÃO modifica nem lê nenhum código do DaVinci: ele só abre o
JSON que o DaVinci já grava no disco (pasta irmã, por padrão) e extrai o
cadastro de dentro do `resumo_html` com um parser leve baseado em regex,
casado com o template usado em build_html_summary(). Se algum campo não
for encontrado (ex.: versão futura do template mudar), a leitura degrada
graciosamente para "—" em vez de quebrar o app.

Se não houver nenhum teste salvo ainda no DaVinci (arquivo ausente ou
vazio), cai automaticamente para os exemplos embutidos em
`assets/historico_exemplo.json` — gerados com o próprio motor do DaVinci
(ab_design.py), então têm exatamente a mesma cara de um teste real salvo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ASSETS_DIR = Path(__file__).parent / "assets"
EXEMPLO_PATH = ASSETS_DIR / "historico_exemplo.json"

# Pasta do DaVinci: por padrão, pasta irmã "teste_ab_desenho" (mesmo nível
# desta pasta do Michelangelo). Se você organizou diferente, edite o
# caminho abaixo ou crie um arquivo texto "davinci_path.txt" nesta pasta
# com o caminho completo em uma linha.
_PATH_OVERRIDE_FILE = Path(__file__).parent / "davinci_path.txt"


def _davinci_dir() -> Path:
    if _PATH_OVERRIDE_FILE.exists():
        custom = _PATH_OVERRIDE_FILE.read_text(encoding="utf-8").strip()
        if custom:
            return Path(custom)
    return Path(__file__).parent.parent / "teste_ab_desenho"


def _historico_path() -> Path:
    return _davinci_dir() / "historico_testes.json"


def _extrair(padrao: str, texto: str, default: str = "") -> str:
    m = re.search(padrao, texto, re.DOTALL)
    if not m:
        return default
    valor = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return valor or default


def _extrair_chips(texto: str, secao_titulo: str) -> dict:
    """Extrai os chips 'Rótulo: Valor' de uma seção <h4>secao_titulo</h4><div>...</div>."""
    bloco = re.search(
        r"<h4>" + re.escape(secao_titulo) + r"</h4>\s*<div>(.*?)</div>", texto, re.DOTALL
    )
    if not bloco:
        return {}
    chips = re.findall(r'<span class="chip">([^<]+)</span>', bloco.group(1))
    out = {}
    for chip in chips:
        if ":" in chip:
            rotulo, valor = chip.split(":", 1)
            out[rotulo.strip()] = valor.strip()
    return out


def _extrair_metric_grid(texto: str, secao_titulo: str) -> dict:
    """Extrai pares rótulo/valor de uma grade <h4>...</h4><div class="grid">...</div>."""
    bloco = re.search(
        r"<h4>" + re.escape(secao_titulo) + r"</h4>\s*<div class=\"grid[^\"]*\">(.*?)</div>\s*(?:<h4>|<div class=\"foot)",
        texto, re.DOTALL,
    )
    if not bloco:
        return {}
    pares = re.findall(
        r'<div class="l">([^<]+)</div><div class="v">([^<]+)</div>', bloco.group(1)
    )
    return {rotulo.strip(): valor.strip() for rotulo, valor in pares}


def _parse_numero_davinci(s: str) -> tuple[float | None, str, str]:
    """
    Recebe um valor formatado pelo DaVinci (ex.: "4.50%", "148,50 R$", "1,35")
    e devolve (valor_numerico, tipo, unidade). tipo em {"proporcao","media"}.
    DaVinci formata proporção com ponto decimal ("4.50%") e média com vírgula
    (padrão BR, "148,50 R$") -- os dois formatos são tratados aqui.
    """
    s = s.strip()
    if not s:
        return None, "media", ""
    if s.endswith("%"):
        try:
            return float(s[:-1].replace(",", ".")) / 100, "proporcao", "%"
        except ValueError:
            return None, "proporcao", "%"
    unidade = ""
    corpo = s
    m = re.match(r"^(R\$)\s*([\d.,]+)$", s)
    if m:
        unidade, corpo = m.group(1), m.group(2)
    else:
        m2 = re.match(r"^([\d.,]+)\s*([A-Za-zÀ-ÿ$]+)?$", s)
        if m2:
            corpo, unidade = m2.group(1), (m2.group(2) or "")
    try:
        valor = float(corpo.replace(".", "").replace(",", "."))
    except ValueError:
        try:
            valor = float(corpo.replace(",", "."))
        except ValueError:
            return None, "media", unidade
    return valor, "media", unidade


def parse_resumo_html(resumo_html: str) -> dict:
    """Extrai o cadastro do teste de dentro do resumo_html gerado pelo DaVinci."""
    nome = _extrair(r'<div class="title">(.*?)</div>', resumo_html, "(sem nome)")
    sub = _extrair(r'<div class="sub">(.*?)</div>', resumo_html, "")
    id_teste = _extrair(r"ID ([^<]+)<br>", resumo_html, "")

    area, objetivo = "", sub
    if " · " in sub:
        area, objetivo = sub.split(" · ", 1)

    contexto = _extrair_chips(resumo_html, "Contexto do experimento")
    testando = _extrair_metric_grid(resumo_html, "O que estamos testando")

    hoje_raw = testando.get("Hoje", "")
    alvo_key = next((k for k in testando if k.startswith("Queremos")), None)
    alvo_raw = testando.get(alvo_key, "") if alvo_key else ""
    direcao = "down" if (alvo_key and "cair" in alvo_key) else "up"

    hoje_val, tipo, unidade = _parse_numero_davinci(hoje_raw)
    alvo_val, _, _ = _parse_numero_davinci(alvo_raw)

    letras_grupo = sorted(set(re.findall(r"Grupo (\w) ·", resumo_html)))
    variantes = [l for l in letras_grupo if l != "A"] or ["B"]

    return {
        "id": id_teste,
        "nome": nome,
        "area": area,
        "objetivo": objetivo,
        "bu": contexto.get("BU", "—"),
        "metrica_nome": contexto.get("Métrica", "—"),
        "experimento": contexto.get("Experimento", nome),
        "regiao": contexto.get("Região", "—"),
        "campanha": contexto.get("Campanha", "—"),
        "plataforma": contexto.get("Plataforma", "—"),
        "dispositivo": contexto.get("Dispositivo", "—"),
        "hoje": hoje_raw or "—",
        "alvo": alvo_raw or "—",
        "hoje_valor": hoje_val,
        "alvo_valor": alvo_val,
        "tipo_metrica": tipo,
        "unidade": unidade,
        "direcao": direcao,
        "variantes": variantes,
    }


def _carregar_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def carregar_testes_registrados() -> tuple[list, bool]:
    """
    Retorna (lista_de_testes, usando_exemplo).

    Cada item: {id, nome, salvo_em, n_por_braco, n_total, dias_rodar,
    bu, metrica_nome, experimento, regiao, campanha, plataforma,
    dispositivo, objetivo, area}.

    `bu` aqui é o rótulo textual do DaVinci (ex.: "Varejo (e-commerce)") --
    quem for casar com ab_measure.BU_CONFIG faz a tradução por rótulo.
    """
    reais = _carregar_json(_historico_path())
    usando_exemplo = not reais
    fonte = reais if reais else _carregar_json(EXEMPLO_PATH)

    testes = []
    for item in fonte:
        cadastro = parse_resumo_html(item.get("resumo_html", ""))
        testes.append({
            "id": item.get("id", cadastro.get("id", "")),
            "nome": item.get("nome") or cadastro.get("nome", "(sem nome)"),
            "salvo_em": item.get("salvo_em", ""),
            "target_per_group": item.get("n_por_braco", 0),
            "n_total": item.get("n_total", 0),
            "planned_days": item.get("dias_rodar", 14),
            "bu": cadastro.get("bu", "—"),
            "metrica_nome": cadastro.get("metrica_nome", "—"),
            "experimento": cadastro.get("experimento", ""),
            "regiao": cadastro.get("regiao", "Nacional"),
            "campanha": cadastro.get("campanha", "—"),
            "plataforma": cadastro.get("plataforma", "App"),
            "dispositivo": cadastro.get("dispositivo", "—"),
            "objetivo": cadastro.get("objetivo", ""),
            "area": cadastro.get("area", ""),
            "hoje": cadastro.get("hoje", "—"),
            "alvo": cadastro.get("alvo", "—"),
            "hoje_valor": cadastro.get("hoje_valor"),
            "alvo_valor": cadastro.get("alvo_valor"),
            "tipo_metrica": cadastro.get("tipo_metrica", "proporcao"),
            "unidade": cadastro.get("unidade", ""),
            "direcao": cadastro.get("direcao", "up"),
            "variantes": cadastro.get("variantes", ["B"]),
        })
    return testes, usando_exemplo


# ---------------------------------------------------------------------------
# Tradução do cadastro do DaVinci (texto livre) para o motor de medição
# ---------------------------------------------------------------------------

_GUARDRAILS_PADRAO = ["Custo por conversão", "Tempo médio da etapa"]
_TRAFEGO_PADRAO = 800.0
_REGIAO_FACTOR_PADRAO = 1.0


def derive_metric_cfg(test: dict) -> dict:
    """
    Monta o metric_cfg que o ab_measure.computar() espera, a partir do
    baseline/alvo REAIS lidos do resumo_html do DaVinci -- não depende de
    bater o rótulo da métrica com um catálogo fixo, então funciona mesmo
    que a BU/métrica tenham sido digitadas livremente no DaVinci.
    """
    hoje = test.get("hoje_valor")
    alvo = test.get("alvo_valor")
    tipo = test.get("tipo_metrica", "proporcao")
    direcao = test.get("direcao", "up")

    if hoje is None:
        hoje = 0.05 if tipo == "proporcao" else 100.0
    if alvo is None:
        alvo = hoje * 1.1

    diferenca = abs(alvo - hoje)
    jitter = max(diferenca * 0.35, hoje * 0.05) if hoje else max(diferenca * 0.35, 0.01)

    cfg = {
        "label": test.get("metrica_nome", "Métrica"),
        "type": tipo,
        "direction": direcao,
        "base": hoje,
        "jitter": jitter,
    }
    if tipo == "media":
        cfg["unit"] = test.get("unidade", "")
        cfg["sd"] = max(diferenca * 1.5, abs(hoje) * 0.25, 1.0)
    return cfg


def guardrails_para_bu(bu_label: str) -> list:
    from ab_measure import GUARDRAILS_BY_BU, BU_CONFIG
    for slug, cfg in BU_CONFIG.items():
        if cfg["label"].strip().lower() == (bu_label or "").strip().lower():
            return GUARDRAILS_BY_BU.get(slug, _GUARDRAILS_PADRAO)
    return _GUARDRAILS_PADRAO


def trafego_diario(test: dict) -> float:
    from ab_measure import PLATAFORMA_TRAFFIC, REGIAO_FACTOR
    base = PLATAFORMA_TRAFFIC.get(test.get("plataforma", ""), _TRAFEGO_PADRAO)
    fator = REGIAO_FACTOR.get(test.get("regiao", ""), _REGIAO_FACTOR_PADRAO)
    return base * fator
