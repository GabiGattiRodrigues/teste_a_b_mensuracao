"""
ab_measure.py
=============

Motor de MEDICAO bayesiana de teste A/B -- Michelangelo v1.

Escopo:
  - le a "ficha" de um teste ja desenhado no DaVinci (BU, metrica, publico,
    meta de amostra, prazo) -- este modulo nunca decide nem edita cadastro,
    so mede o que ja esta rodando;
  - duas familias de metrica, espelhando o DaVinci:
      * "proporcao" -- Beta-Binomial (conjugado): posterior = Beta(1+conversoes, 1+nao-conversoes);
      * "media"     -- Normal (aproximacao via erro padrao da media diaria).
  - Monte Carlo (3000 simulacoes) para estimar P(variante > controle), lift
    esperado, intervalo de credibilidade 90% e PERDA ESPERADA (quanto se
    perde, em media, se a escolha estiver errada);
  - testes com 3 grupos ou mais (A/B/n): chance de cada grupo ser o melhor
    de todos, com o mesmo controle em todas as comparacoes;
  - SRM com teste qui-quadrado de verdade (p-valor), nao so o % de cada grupo;
  - checklist de contaminacao (SRM, crossover, duplicidade/nulos, efeito
    novidade, guardrails) e apoio a decisao quando a amostra planejada no
    DaVinci ainda nao foi atingida (prorrogar / aceitar menos confianca /
    parar inconclusivo).

Fora de escopo por decisao (fica no DaVinci): desenho/dimensionamento do
teste -- objetivo, publico, meta de amostra e prazo sao cadastrados la e
tratados aqui como SOMENTE LEITURA.

Depende apenas da biblioteca padrao do Python (random, math, statistics,
hashlib, dataclasses).
"""

from __future__ import annotations

import hashlib
import math
import random
import threading
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Lingua dos textos (veredito, checklist, opcoes de decisao, numeros)
# ---------------------------------------------------------------------------
# Portugues por padrao. O app chama definir_idioma("en") no comeco de cada
# execucao; a lingua fica por thread. As contas nao mudam.

_LINGUA = threading.local()


def definir_idioma(idioma: str) -> None:
    _LINGUA.valor = "en" if str(idioma).lower().startswith("en") else "pt"


def idioma() -> str:
    return getattr(_LINGUA, "valor", "pt")


def _L(pt: str, en: str) -> str:
    """A frase na lingua ativa."""
    return en if idioma() == "en" else pt


def _dec(s: str) -> str:
    """Troca o ponto decimal pela virgula em portugues."""
    return s if idioma() == "en" else s.replace(".", ",")


def _milhar(s: str) -> str:
    """'1,234.50' (formato Python) -> '1.234,50' em portugues."""
    if idioma() == "en":
        return s
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


# ---------------------------------------------------------------------------
# 0. Config fictícia de métricas por BU (mesmo papel do BU_CONFIG do Michelangelo
#    em HTML) -- no mundo real, isso viria de um catálogo de métricas de negócio.
# ---------------------------------------------------------------------------

BU_CONFIG = {
    "varejo": {
        "label": "Varejo (e-commerce)", "label_en": "Retail (e-commerce)",
        "metrics": {
            "conv_checkout": {"label": "Taxa de conversão do checkout", "type": "proporcao", "direction": "up", "base": 0.045, "jitter": 0.015},
            "rev_per_session": {"label": "Receita por usuário", "type": "media", "direction": "up", "unit": "R$", "base": 3.2, "jitter": 0.9, "sd": 6.5},
            "ticket_medio": {"label": "Ticket médio", "type": "media", "direction": "up", "unit": "R$", "base": 148.0, "jitter": 30.0, "sd": 95.0},
            "margem": {"label": "Margem", "type": "media", "direction": "up", "unit": "%", "base": 27.5, "jitter": 4.0, "sd": 6.5},
        },
    },
    "clube": {
        "label": "Assinatura / Clube", "label_en": "Subscription / Club",
        "metrics": {
            "conv_assinatura": {"label": "Taxa de conversão da assinatura", "type": "proporcao", "direction": "up", "base": 0.081, "jitter": 0.02},
            "churn": {"label": "Churn mensal", "type": "proporcao", "direction": "down", "base": 0.052, "jitter": 0.012},
            "repets": {"label": "Taxa de recompra (repets)", "type": "proporcao", "direction": "up", "base": 0.34, "jitter": 0.05},
        },
    },
    "saude": {
        "label": "Seguro Pet", "label_en": "Pet Insurance",
        "metrics": {
            "conv_cotacao": {"label": "Taxa de conversão da cotação", "type": "proporcao", "direction": "up", "base": 0.061, "jitter": 0.018},
            "churn_apolice": {"label": "Churn de apólices", "type": "proporcao", "direction": "down", "base": 0.033, "jitter": 0.009},
            "vidas": {"label": "Vidas seguradas por usuário", "type": "media", "direction": "up", "unit": "", "base": 1.35, "jitter": 0.2, "sd": 0.7},
        },
    },
    "servicos": {
        "label": "Serviços (banho, tosa, vet)", "label_en": "Services (bath, grooming, vet)",
        "metrics": {
            "conv_agendamento": {"label": "Taxa de conversão do agendamento", "type": "proporcao", "direction": "up", "base": 0.091, "jitter": 0.02},
            "ticket_servico": {"label": "Ticket médio do serviço", "type": "media", "direction": "up", "unit": "R$", "base": 92.0, "jitter": 16.0, "sd": 30.0},
        },
    },
}

GUARDRAILS_BY_BU = {
    "varejo": ["Custo por cliente", "Tempo médio de checkout"],
    "clube": ["Custo por assinante", "NPS do onboarding"],
    "saude": ["Custo por apólice emitida", "Tempo médio de cotação"],
    "servicos": ["Custo por agendamento", "Taxa de no-show"],
}

REGIAO_FACTOR = {"Nacional": 1.0, "Sudeste": 0.42, "Sul": 0.16, "Nordeste": 0.2, "Centro-Oeste": 0.1, "Norte": 0.08,
                 # o mesmo cadastro digitado no DaVinci em ingles
                 "Nationwide": 1.0, "National": 1.0, "Southeast": 0.42, "South": 0.16, "Northeast": 0.2,
                 "Center-West": 0.1, "Midwest": 0.1, "North": 0.08}

# Nomes dos guardrails na tela em ingles (o dado continua em portugues)
GUARDRAILS_EN = {
    "Custo por cliente": "Cost per customer", "Tempo médio de checkout": "Average checkout time",
    "Custo por assinante": "Cost per subscriber", "NPS do onboarding": "Onboarding NPS",
    "Custo por apólice emitida": "Cost per policy issued", "Tempo médio de cotação": "Average quote time",
    "Custo por agendamento": "Cost per booking", "Taxa de no-show": "No-show rate",
    "Custo por conversão": "Cost per conversion", "Tempo médio da etapa": "Average step time",
}


def nome_guardrail(nome: str) -> str:
    return GUARDRAILS_EN.get(nome, nome) if idioma() == "en" else nome
PLATAFORMA_TRAFFIC = {"App": 1250.0, "Web mobile": 980.0, "Web desktop": 480.0}  # usuários únicos/dia (não sessões)

NSIM = 3000


def metric_config(bu: str, metric_id: str) -> dict:
    return BU_CONFIG[bu]["metrics"][metric_id]


# ---------------------------------------------------------------------------
# 1. RNG determinístico -- mesma combinação de filtros sempre gera os
#    mesmos "dados". Usamos random.Random (stdlib) com uma seed derivada
#    de hash estável da chave -- nada de numpy.
# ---------------------------------------------------------------------------

def seed_key(test_id: str, periodo: int, variante: str) -> str:
    return f"{test_id}|{periodo}|{variante}"


def make_rng(key: str) -> random.Random:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def beta_pdf(x: float, a: float, b: float) -> float:
    """Densidade da Beta(a, b) -- só pra desenhar a curva posterior."""
    if x <= 0 or x >= 1:
        return 0.0
    log_b = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    return math.exp((a - 1) * math.log(x) + (b - 1) * math.log(1 - x) - log_b)


def normal_pdf(x: float, mean: float, sd: float) -> float:
    if sd <= 0:
        return 0.0
    return math.exp(-0.5 * ((x - mean) / sd) ** 2) / (sd * math.sqrt(2 * math.pi))


# ---------------------------------------------------------------------------
# 2. Geração de dados sintéticos por dia (controle x variante) -- fica
#    determinística pra mesma chave de filtro; simula um cenário plausível
#    (tráfego por plataforma/região, viés levemente positivo, chance de
#    SRM proposital pra alimentar o checklist).
# ---------------------------------------------------------------------------

@dataclass
class DiaMedicao:
    dia: int
    valor_controle: float
    sessoes_controle: int
    valor_variante: float
    sessoes_variante: int


@dataclass
class DadosGerados:
    dias: list  # list[DiaMedicao]
    sess_c: int
    sess_v: int
    conv_c: int
    conv_v: int
    soma_c: float
    soma_v: float
    soma_sq_c: float
    soma_sq_v: float


def gerar_dados(test: dict, metric_cfg: dict, periodo: int, rng: random.Random,
                rng_controle: random.Random | None = None) -> DadosGerados:
    """
    Gera os dados diarios de controle x uma variante.

    O CONTROLE sai de um gerador proprio (`rng_controle`), que nao depende de
    qual variante esta sendo comparada -- assim, num teste com 3 grupos ou
    mais, o Grupo A e exatamente o mesmo em todas as comparacoes (como seria
    na vida real). A variante usa o gerador dela (`rng`).

    Trafego: cada dia tem um volume base comum a todos os grupos; cada grupo
    recebe esse volume +- ruido de sorteio (~ raiz do volume, como numa
    randomizacao de verdade). Em ~16% dos cenarios a variante recebe 6-13% a
    mais ou a menos de proposito, pra alimentar o teste de SRM.
    """
    if rng_controle is None:
        rng_controle = make_rng(seed_key(test["id"], periodo, "controle"))
    rc = rng_controle

    regiao_factor = REGIAO_FACTOR.get(test["regiao"], 1.0)
    device_factor = 0.92 + rc.random() * 0.16
    base_traffic = PLATAFORMA_TRAFFIC.get(test["plataforma"], 800.0) * regiao_factor * device_factor

    baseline_jitter = (rc.random() - 0.5) * 2 * metric_cfg["jitter"]
    baseline_value = metric_cfg["base"] + baseline_jitter
    if metric_cfg["type"] == "proporcao":
        baseline_value = min(0.95, max(0.005, baseline_value))
    ruido_dia = [1 + (rc.random() - 0.5) * 0.22 for _ in range(periodo)]

    # desbalanceamento proposital (SRM) -- so na variante
    srm_skew = 1.0
    if rng.random() < 0.16:
        sinal = 1 if rng.random() < 0.5 else -1
        srm_skew = 1 + (0.06 + rng.random() * 0.07) * sinal

    # efeito real embutido no cenario: vies levemente positivo (empresa testando pra melhorar)
    true_rel_lift = rng.random() * 0.30 - 0.08  # -8% a +22%
    if metric_cfg["direction"] == "down":
        variant_true_value = baseline_value * (1 - true_rel_lift)
    else:
        variant_true_value = baseline_value * (1 + true_rel_lift)
    if metric_cfg["type"] == "proporcao":
        variant_true_value = min(0.97, max(0.003, variant_true_value))

    dias = []
    sess_c = sess_v = conv_c = conv_v = 0
    soma_c = soma_v = soma_sq_c = soma_sq_v = 0.0

    for d in range(periodo):
        vol = base_traffic * ruido_dia[d]
        vol_v = vol * srm_skew
        s_c = max(1, round(vol + rc.gauss(0, math.sqrt(vol))))
        s_v = max(1, round(vol_v + rng.gauss(0, math.sqrt(vol_v))))
        sess_c += s_c
        sess_v += s_v

        if metric_cfg["type"] == "proporcao":
            r_c = min(0.99, max(0.001, baseline_value * (1 + (rc.random() - 0.5) * 0.18)))
            r_v = min(0.99, max(0.001, variant_true_value * (1 + (rng.random() - 0.5) * 0.18)))
            c_c = round(s_c * r_c)
            c_v = round(s_v * r_v)
            conv_c += c_c
            conv_v += c_v
            dias.append(DiaMedicao(d, r_c, s_c, r_v, s_v))
        else:
            m_c = baseline_value * (1 + (rc.random() - 0.5) * 0.14)
            m_v = variant_true_value * (1 + (rng.random() - 0.5) * 0.14)
            soma_c += m_c
            soma_v += m_v
            soma_sq_c += m_c * m_c
            soma_sq_v += m_v * m_v
            dias.append(DiaMedicao(d, m_c, s_c, m_v, s_v))

    return DadosGerados(dias, sess_c, sess_v, conv_c, conv_v, soma_c, soma_v, soma_sq_c, soma_sq_v)


# ---------------------------------------------------------------------------
# 3. Bayes: posterior + Monte Carlo
# ---------------------------------------------------------------------------

@dataclass
class Posterior:
    tipo: str  # "proporcao" | "media"
    mean: float
    summary_value: float
    alpha: float | None = None
    beta: float | None = None
    se: float | None = None


@dataclass
class ResultadoMedicao:
    metric_cfg: dict
    dias: int
    dados: DadosGerados
    post_controle: Posterior
    post_variante: Posterior
    p_melhor: float
    lift_medio: float
    ci_baixo: float
    ci_alto: float
    nsim: int
    exemplo_controle: float
    exemplo_variante: float
    exemplo_lift: float
    rng: random.Random
    guardrail_risco: int = 0
    guardrail_total: int = 0
    # perda esperada (relativa ao controle): quanto se perde, em media, se a
    # escolha estiver errada. perda_variante = risco de aplicar a variante;
    # perda_controle = risco de ficar com o controle.
    perda_variante: float = 0.0
    perda_controle: float = 0.0
    sims_controle: list = field(default_factory=list, repr=False)
    sims_variante: list = field(default_factory=list, repr=False)


def _sortear_posterior(metric_cfg: dict, n_conv: int, n_sess: int, soma: float, soma_sq: float,
                       periodo: int, rng: random.Random) -> tuple[Posterior, list]:
    """Posterior de UM grupo + NSIM sorteios dele."""
    if metric_cfg["type"] == "proporcao":
        a, b = 1 + n_conv, 1 + (n_sess - n_conv)
        sims = [rng.betavariate(a, b) for _ in range(NSIM)]
        return Posterior("proporcao", a / (a + b), n_conv / n_sess, a, b), sims
    mean = soma / periodo
    sd = math.sqrt(max(1e-6, soma_sq / periodo - mean ** 2)) or metric_cfg["sd"]
    se = max(sd / math.sqrt(periodo), mean * 0.01)
    sims = [rng.gauss(mean, se) for _ in range(NSIM)]
    return Posterior("media", mean, mean, se=se), sims


def computar(test: dict, metric_cfg: dict, periodo: int, variante: str) -> ResultadoMedicao:
    rng = make_rng(seed_key(test["id"], periodo, variante))
    rng_c = make_rng(seed_key(test["id"], periodo, "controle"))
    dados = gerar_dados(test, metric_cfg, periodo, rng, rng_c)

    # o controle tem sorteios proprios (mesmos em qualquer comparacao)
    rng_sim_c = make_rng(seed_key(test["id"], periodo, "sim|controle"))
    post_c, sims_c = _sortear_posterior(metric_cfg, dados.conv_c, dados.sess_c, dados.soma_c,
                                        dados.soma_sq_c, periodo, rng_sim_c)
    post_v, sims_v = _sortear_posterior(metric_cfg, dados.conv_v, dados.sess_v, dados.soma_v,
                                        dados.soma_sq_v, periodo, rng)

    desce = metric_cfg["direction"] == "down"
    wins_variant = 0
    lifts = [0.0] * NSIM
    perda_v = perda_c = 0.0
    for i in range(NSIM):
        c, v = sims_c[i], sims_v[i]
        if (v < c) if desce else (v > c):
            wins_variant += 1
        lifts[i] = (v - c) / max(1e-9, c)
        # ganho da variante na direcao "boa" (positivo = variante melhor)
        ganho = -lifts[i] if desce else lifts[i]
        perda_v += max(-ganho, 0.0)
        perda_c += max(ganho, 0.0)
    exemplo_lift = lifts[0]
    lifts_sorted = sorted(lifts)
    p_melhor = wins_variant / NSIM
    lift_medio = sum(lifts) / NSIM
    ci_baixo = lifts_sorted[int(NSIM * 0.05)]
    ci_alto = lifts_sorted[int(NSIM * 0.95)]

    return ResultadoMedicao(
        metric_cfg=metric_cfg, dias=periodo, dados=dados,
        post_controle=post_c, post_variante=post_v,
        p_melhor=p_melhor, lift_medio=lift_medio, ci_baixo=ci_baixo, ci_alto=ci_alto,
        nsim=NSIM, exemplo_controle=sims_c[0], exemplo_variante=sims_v[0], exemplo_lift=exemplo_lift,
        rng=rng, perda_variante=perda_v / NSIM, perda_controle=perda_c / NSIM,
        sims_controle=sims_c, sims_variante=sims_v,
    )


# ---------------------------------------------------------------------------
# 3b. Perda esperada -- regra de decisao
# ---------------------------------------------------------------------------
# "Chance de ser melhor" sozinha nao diz quanto custa errar. A perda esperada
# responde: se eu aplicar a variante e ela for na verdade pior, quanto eu
# perco em media (em % do controle)? Regra comum: aplica quando essa perda
# fica abaixo de um limite que o negocio topa perder (o "limite de
# tolerancia", ex.: 0,1% do resultado).

LIMITE_PERDA_PADRAO = 0.001  # 0,1% do controle


def decisao_por_perda(r: ResultadoMedicao, limite: float = LIMITE_PERDA_PADRAO) -> str:
    """'variante' | 'controle' | 'esperar' pela regra da perda esperada."""
    if r.perda_variante <= limite and r.perda_variante <= r.perda_controle:
        return "variante"
    if r.perda_controle <= limite and r.perda_controle < r.perda_variante:
        return "controle"
    return "esperar"


# ---------------------------------------------------------------------------
# 3c. SRM -- teste qui-quadrado da divisao entre grupos
# ---------------------------------------------------------------------------
# Se o plano era dividir igual e os usuarios nao chegaram iguais, alguma
# coisa na randomizacao (ou no registro) quebrou. O teste compara o que
# chegou com o que se esperava; p-valor abaixo de 0,001 e o corte usual
# (rigido de proposito: com amostra grande, diferenca pequena ja acende).

LIMIAR_SRM = 0.001


def _gamma_inc_sup_reg(a: float, x: float) -> float:
    """Q(a, x): funcao gama incompleta superior regularizada (Numerical Recipes)."""
    if x <= 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1:  # serie para P, depois 1 - P
        ap, soma, termo = a, 1.0 / a, 1.0 / a
        for _ in range(1000):
            ap += 1
            termo *= x / ap
            soma += termo
            if abs(termo) < abs(soma) * 1e-15:
                break
        return max(0.0, 1.0 - soma * math.exp(-x + a * math.log(x) - gln))
    # fracao continuada para Q (Lentz)
    tiny = 1e-300
    b = x + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def qui2_p_valor(estatistica: float, graus: int) -> float:
    """P(Qui2(graus) >= estatistica)."""
    return _gamma_inc_sup_reg(graus / 2, estatistica / 2)


def srm_teste(contagens: list, proporcoes: list | None = None) -> dict:
    """
    Teste de SRM (sample ratio mismatch) para k grupos.
    contagens: usuarios unicos em cada grupo, controle primeiro.
    proporcoes: divisao planejada (padrao: igual entre os grupos).
    """
    k = len(contagens)
    total = sum(contagens)
    if proporcoes is None:
        proporcoes = [1 / k] * k
    esperados = [total * p for p in proporcoes]
    estat = sum((o - e) ** 2 / e for o, e in zip(contagens, esperados) if e > 0)
    p = qui2_p_valor(estat, k - 1)
    return {
        "qui2": estat, "graus": k - 1, "p_valor": p, "alerta": p < LIMIAR_SRM,
        "pcts": [c / total for c in contagens] if total else [0.0] * k,
    }


def fmt_p_valor(p: float) -> str:
    if p < 0.0001:
        return "< " + _dec("0.0001")
    return _dec(f"{p:.4f}")


# ---------------------------------------------------------------------------
# 3d. Todos os grupos juntos (A/B/n)
# ---------------------------------------------------------------------------

def computar_multibraco(test: dict, metric_cfg: dict, periodo: int) -> dict:
    """
    Com 3 grupos ou mais: em cada uma das NSIM simulacoes, ve qual grupo
    (controle incluido) teve o melhor valor. A fracao de vitorias e a chance
    de cada grupo ser O MELHOR de todos. A perda esperada de cada grupo e a
    distancia media (em % do controle) ate o melhor grupo daquela simulacao.

    O controle e o mesmo em todas as comparacoes (mesmo gerador e mesmos
    sorteios), entao os numeros batem com a tela de comparacao 1 a 1.
    """
    variantes = list(test["variantes"])
    resultados = {v: computar(test, metric_cfg, periodo, v) for v in variantes}
    primeiro = resultados[variantes[0]]
    nomes = ["A"] + variantes
    sims = [primeiro.sims_controle] + [resultados[v].sims_variante for v in variantes]
    desce = metric_cfg["direction"] == "down"

    vitorias = [0] * len(nomes)
    perdas = [0.0] * len(nomes)
    for i in range(NSIM):
        valores = [s[i] for s in sims]
        melhor = min(valores) if desce else max(valores)
        idx = valores.index(melhor)
        vitorias[idx] += 1
        ref = max(1e-9, valores[0])
        for g, val in enumerate(valores):
            perdas[g] += abs(melhor - val) / ref

    grupos = []
    for g, nome in enumerate(nomes):
        if g == 0:
            post, n_usu = primeiro.post_controle, primeiro.dados.sess_c
            r_g = None
        else:
            r_g = resultados[nome]
            post, n_usu = r_g.post_variante, r_g.dados.sess_v
        grupos.append({
            "grupo": nome, "controle": g == 0, "usuarios": n_usu,
            "valor": post.summary_value,
            "p_melhor_de_todos": vitorias[g] / NSIM,
            "perda_esperada": perdas[g] / NSIM,
            "p_melhor_que_controle": None if g == 0 else r_g.p_melhor,
            "lift_medio": 0.0 if g == 0 else r_g.lift_medio,
        })

    srm = srm_teste([primeiro.dados.sess_c] + [resultados[v].dados.sess_v for v in variantes])
    return {"grupos": grupos, "srm": srm, "resultados": resultados}


# ---------------------------------------------------------------------------
# 4. Veredito em linguagem simples
# ---------------------------------------------------------------------------

def veredito(p_melhor: float, variante_label: str) -> dict:
    pct = _dec(f"{p_melhor * 100:.1f}")
    pct_inv = _dec(f"{(1 - p_melhor) * 100:.1f}")
    L = _L

    if p_melhor >= 0.95:
        return {"classe": "good", "icone": "✅",
                "titulo": L(f"Funcionou! A {variante_label} é melhor que o Controle.",
                            f"It worked! {variante_label} is better than the Control."),
                "sub": L(f"De cada 100 vezes que a gente pudesse repetir esse teste, a Variante venceria em cerca de {pct} delas. É uma vitória bem clara — dá pra confiar e aplicar a mudança.",
                         f"Out of every 100 times we could repeat this test, the Variant would win about {pct} of them. It's a very clear win — you can trust it and roll out the change.")}
    if p_melhor <= 0.05:
        return {"classe": "bad", "icone": "❌",
                "titulo": L(f"Não funcionou — o Controle continua melhor que a {variante_label}.",
                            f"It didn't work — the Control is still better than {variante_label}."),
                "sub": L(f"De cada 100 vezes, o Controle venceria em cerca de {pct_inv} delas. A ideia nova não performou bem: melhor não aplicar essa mudança do jeito que está.",
                         f"Out of every 100 times, the Control would win about {pct_inv} of them. The new idea didn't perform well: better not to roll out this change as it is.")}
    if p_melhor >= 0.80:
        return {"classe": "mid", "icone": "🙂",
                "titulo": L("Parece que sim, mas ainda não é 100% certeza.",
                            "Looks like yes, but it's not 100% certain yet."),
                "sub": L(f"A {variante_label} está na frente em {pct}% das simulações — um sinal bem positivo, mas ainda dá pra imaginar um cenário em que o Controle vence. Se der, vale rodar por mais alguns dias antes de decidir.",
                         f"{variante_label} is ahead in {pct}% of the simulations — a very positive sign, but you can still imagine a scenario where the Control wins. If you can, run it a few more days before deciding.")}
    if p_melhor <= 0.20:
        return {"classe": "mid", "icone": "🙁",
                "titulo": L("Parece que não, mas ainda não é 100% certeza.",
                            "Looks like no, but it's not 100% certain yet."),
                "sub": L(f"O Controle está na frente em {pct_inv}% das simulações. É um sinal de que a mudança não ajudou, mas o teste ainda não fechou — rodar mais alguns dias deixaria a resposta mais firme.",
                         f"The Control is ahead in {pct_inv}% of the simulations. It's a sign the change didn't help, but the test isn't closed yet — running a few more days would make the answer firmer.")}
    return {"classe": "none", "icone": "🤔",
            "titulo": L("Ainda não dá pra saber quem é melhor.", "It's not possible to tell which is better yet."),
            "sub": L(f"A chance de vitória está bem dividida ({pct}% Variante × {pct_inv}% Controle) — os dados ainda não mostram diferença clara entre os dois. Vale esperar mais dados chegarem antes de decidir.",
                     f"The chance of winning is evenly split ({pct}% Variant × {pct_inv}% Control) — the data doesn't show a clear difference between the two yet. It's worth waiting for more data before deciding.")}


# ---------------------------------------------------------------------------
# 5. Checklist de contaminação (SRM, crossover, duplicidade, novidade, guardrails)
# ---------------------------------------------------------------------------

CHECKLIST_ITEMS = [
    {"key": "srm", "title": "A divisão entre os grupos ficou mesmo igual?", "tech": "randomização / SRM",
     "title_en": "Was the split between the groups really even?", "tech_en": "randomization / SRM"},
    {"key": "crossover", "title": "Alguém viu as duas versões, sem querer?", "tech": "crossover",
     "title_en": "Did anyone see both versions by accident?", "tech_en": "crossover"},
    {"key": "dup", "title": "Tem gente contada duas vezes, ou faltando informação?", "tech": "duplicidade / nulos",
     "title_en": "Is anyone counted twice, or is information missing?", "tech_en": "duplicates / nulls"},
    {"key": "novelty", "title": "A melhora continuou, ou foi só nos primeiros dias?", "tech": "efeito novidade",
     "title_en": "Did the improvement last, or was it only in the first days?", "tech_en": "novelty effect"},
    {"key": "guard", "title": "As métricas que não podiam piorar continuam OK?", "tech": "guardrails",
     "title_en": "Are the metrics that couldn't get worse still OK?", "tech_en": "guardrails"},
]


def item_checklist(item: dict) -> tuple[str, str]:
    """(titulo, nome tecnico) do item na lingua ativa."""
    if idioma() == "en":
        return item["title_en"], item["tech_en"]
    return item["title"], item["tech"]


def calcular_metades(r: ResultadoMedicao) -> dict:
    mid = max(1, r.dias // 2)

    def media(desde, ate, campo):
        vals = [getattr(d, campo) for d in r.dados.dias[desde:ate]]
        return sum(vals) / len(vals) if vals else 0.0

    c1 = media(0, mid, "valor_controle")
    v1 = media(0, mid, "valor_variante")
    c2 = media(mid, r.dias, "valor_controle")
    v2 = media(mid, r.dias, "valor_variante")
    lift1 = (v1 - c1) / max(1e-9, c1)
    lift2 = (v2 - c2) / max(1e-9, c2)
    return {"lift1": lift1, "lift2": lift2}


def fmt_pct_signed(value: float, casas: int = 1) -> str:
    s = _dec(f"{value * 100:.{casas}f}")
    return ("+" if value >= 0 else "") + s + "%"


def checklist_hints(r: ResultadoMedicao) -> dict:
    split_total = r.dados.sess_c + r.dados.sess_v
    split_c_pct = _dec(f"{r.dados.sess_c / split_total * 100:.1f}")
    split_v_pct = _dec(f"{r.dados.sess_v / split_total * 100:.1f}")
    srm = srm_teste([r.dados.sess_c, r.dados.sess_v])
    p_srm = fmt_p_valor(srm["p_valor"])
    halves = calcular_metades(r)
    half1 = fmt_pct_signed(halves["lift1"])
    half2 = fmt_pct_signed(halves["lift2"])
    mesma_direcao = (halves["lift1"] >= 0) == (halves["lift2"] >= 0)
    L = _L
    guard_text = (
        L(f"{r.guardrail_risco} de {r.guardrail_total} guardrail(s) em atenção nesse recorte — vale olhar antes de aplicar.",
          f"{r.guardrail_risco} of {r.guardrail_total} guardrail(s) flagged in this cut — worth a look before rolling out.")
        if r.guardrail_risco > 0 else L("nenhum guardrail em atenção nesse recorte.", "no guardrail flagged in this cut.")
    )
    return {
        "srm": L(f"Nesse recorte: <strong>{split_c_pct}%</strong> controle × <strong>{split_v_pct}%</strong> variante — em usuários únicos, não em sessões. "
                 f"Teste qui-quadrado: p-valor <strong>{p_srm}</strong> "
                 + ("(abaixo de 0,001 → a divisão quebrou; não confie no resultado antes de achar a causa)." if srm["alerta"]
                    else "(acima de 0,001 → diferença dentro do que o sorteio explica).")
                 + " Numa randomização saudável, isso fica pertinho de 50/50; bem diferente disso é sinal de gente sendo jogada pro grupo errado.",
                 f"In this cut: <strong>{split_c_pct}%</strong> control × <strong>{split_v_pct}%</strong> variant — in unique users, not sessions. "
                 f"Chi-square test: p-value <strong>{p_srm}</strong> "
                 + ("(below 0.001 → the split broke; don't trust the result before finding the cause)." if srm["alerta"]
                    else "(above 0.001 → a difference that chance alone explains).")
                 + " In a healthy randomization this stays very close to 50/50; far from that is a sign of people being sent to the wrong group."),
        "crossover": L("Ex.: o cliente trocou de aparelho ou limpou o cookie e caiu no outro grupo no meio do teste. Isso faz um usuário só contar como se fosse dois, e mistura os dois resultados.",
                       "E.g.: the customer switched devices or cleared cookies and landed in the other group mid-test. That makes one user count as two, and mixes the two results."),
        "dup": L("Confira se o mesmo usuário não entrou duas vezes na conta e se não tem usuário sem grupo definido (nulo) entrando sem querer. É por isso que o Michelangelo mede por usuário único, e não por sessão: sessão repetida do mesmo usuário infla a amostra e engana a conta de erro.",
                 "Check that the same user wasn't counted twice and that no user without an assigned group (null) slipped in. That's why Michelangelo measures by unique user, not by session: repeat sessions from the same user inflate the sample and fool the error calculation."),
        "novelty": (L(f"Nesse recorte: lift foi de <strong>{half1}</strong> na 1ª metade do período e <strong>{half2}</strong> na 2ª metade",
                      f"In this cut: lift was <strong>{half1}</strong> in the 1st half of the period and <strong>{half2}</strong> in the 2nd half")
                    + (L(" — direção se manteve.", " — the direction held.") if mesma_direcao
                       else L(" — mudou de direção, vale desconfiar.", " — it changed direction, worth being suspicious."))
                    + L(" Efeito que só aparece no começo costuma ser curiosidade, não melhora de verdade.",
                        " An effect that only shows up at the start is usually curiosity, not a real improvement.")),
        "guard": L(f"Nesse recorte: {guard_text}", f"In this cut: {guard_text}"),
    }


def guardrails_status(nomes: list, rng: random.Random) -> list:
    """Continua o MESMO stream de rng do resultado (determinístico p/ o filtro atual)."""
    itens = []
    risco_count = 0
    for nome in nomes:
        risco = rng.random() < 0.15
        if risco:
            risco_count += 1
        sinal = 1 if risco else (1 if rng.random() < 0.5 else -1)
        delta = (rng.random() * 2.2) * sinal
        itens.append({"nome": nome, "risco": risco, "delta_pct": delta})
    return itens, risco_count, len(nomes)


# ---------------------------------------------------------------------------
# 6. Amostra planejada (meta do DaVinci) x observado, e apoio a decisão
# ---------------------------------------------------------------------------

def status_amostra(test: dict, r: ResultadoMedicao) -> dict:
    target = test["target_per_group"]
    planned_days = test["planned_days"]
    observed = min(r.dados.sess_c, r.dados.sess_v)
    avg_daily = observed / r.dias if r.dias else 0
    pct_sample = observed / target if target else 0
    remaining = max(0, target - observed)
    extra_days_needed = math.ceil(remaining / avg_daily) if avg_daily > 0 else None
    reached_sample = observed >= target
    past_deadline = r.dias >= planned_days
    return {
        "target": target, "planned_days": planned_days, "observed": observed,
        "pct_sample": pct_sample, "remaining": remaining, "extra_days_needed": extra_days_needed,
        "reached_sample": reached_sample, "past_deadline": past_deadline,
    }


def opcoes_decisao(r: ResultadoMedicao, status: dict) -> list:
    strength = max(r.p_melhor, 1 - r.p_melhor)
    strong_already = strength >= 0.95
    extra = status["extra_days_needed"]
    quick_extension = extra is not None and extra <= 7
    target_fmt = fmt_int(status['target'])
    L = _L
    extra_fmt = (f"~{fmt_int(extra)} {L('dia(s)', 'day(s)')}" if extra is not None
                 else L("alguns dias", "a few days"))

    return [
        {
            "titulo": L("⏳ Prorrogar o teste", "⏳ Extend the test"),
            "desc": L(f"Continuar rodando por mais {extra_fmt} até bater a meta de {target_fmt} usuários por grupo. Mais seguro, mas atrasa a decisão — e, se a mudança for boa, adia o ganho.",
                      f"Keep running for another {extra_fmt} until hitting the target of {target_fmt} users per group. Safer, but it delays the decision — and, if the change is good, it postpones the gain."),
            "recomendado": (not strong_already) and quick_extension,
        },
        {
            "titulo": L("⚠️ Parar e aceitar menos confiança", "⚠️ Stop and accept less confidence"),
            "desc": L("Decidir com a amostra de hoje, sabendo que o intervalo de credibilidade fica mais largo e a chance de decisão errada é maior. Só faz sentido se a evidência já estiver bem forte ou a decisão não for crítica.",
                      "Decide with today's sample, knowing the credible interval is wider and the chance of a wrong decision is higher. It only makes sense if the evidence is already very strong or the decision isn't critical."),
            "recomendado": strong_already,
        },
        {
            "titulo": L("🛑 Parar sem decidir (inconclusivo)", "🛑 Stop without deciding (inconclusive)"),
            "desc": L("Encerrar o teste sem aplicar a mudança agora. Vale quando faltaria muito tempo pra bater a meta — melhor revisar o desenho lá no DaVinci (tráfego esperado, duração) antes de tentar de novo.",
                      "End the test without rolling out the change now. Worth it when hitting the target would take too long — better to review the design in DaVinci (expected traffic, duration) before trying again."),
            "recomendado": (not strong_already) and (not quick_extension),
        },
    ]


# ---------------------------------------------------------------------------
# 7. Formatação (1.234,5 em portugues · 1,234.5 em ingles)
# ---------------------------------------------------------------------------

def fmt_metric(value: float, cfg: dict) -> str:
    if cfg["type"] == "proporcao":
        return _dec(f"{value * 100:.2f}") + "%"
    if cfg.get("unit") == "%":
        return _dec(f"{value:.1f}") + "%"
    if cfg.get("unit") == "R$":
        return "R$ " + _milhar(f"{value:,.2f}")
    return _milhar(f"{value:,.2f}")


def fmt_int(value: float) -> str:
    return _milhar(f"{round(value):,}")
