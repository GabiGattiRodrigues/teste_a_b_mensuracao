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
    esperado e intervalo de credibilidade 90%;
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
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 0. Config fictícia de métricas por BU (mesmo papel do BU_CONFIG do Michelangelo
#    em HTML) -- no mundo real, isso viria de um catálogo de métricas de negócio.
# ---------------------------------------------------------------------------

BU_CONFIG = {
    "varejo": {
        "label": "Varejo (e-commerce)",
        "metrics": {
            "conv_checkout": {"label": "Taxa de conversão do checkout", "type": "proporcao", "direction": "up", "base": 0.045, "jitter": 0.015},
            "rev_per_session": {"label": "Receita por usuário", "type": "media", "direction": "up", "unit": "R$", "base": 3.2, "jitter": 0.9, "sd": 6.5},
            "ticket_medio": {"label": "Ticket médio", "type": "media", "direction": "up", "unit": "R$", "base": 148.0, "jitter": 30.0, "sd": 95.0},
            "margem": {"label": "Margem", "type": "media", "direction": "up", "unit": "%", "base": 27.5, "jitter": 4.0, "sd": 6.5},
        },
    },
    "clube": {
        "label": "Assinatura / Clube",
        "metrics": {
            "conv_assinatura": {"label": "Taxa de conversão da assinatura", "type": "proporcao", "direction": "up", "base": 0.081, "jitter": 0.02},
            "churn": {"label": "Churn mensal", "type": "proporcao", "direction": "down", "base": 0.052, "jitter": 0.012},
            "repets": {"label": "Taxa de recompra (repets)", "type": "proporcao", "direction": "up", "base": 0.34, "jitter": 0.05},
        },
    },
    "saude": {
        "label": "Seguro Pet",
        "metrics": {
            "conv_cotacao": {"label": "Taxa de conversão da cotação", "type": "proporcao", "direction": "up", "base": 0.061, "jitter": 0.018},
            "churn_apolice": {"label": "Churn de apólices", "type": "proporcao", "direction": "down", "base": 0.033, "jitter": 0.009},
            "vidas": {"label": "Vidas seguradas por usuário", "type": "media", "direction": "up", "unit": "", "base": 1.35, "jitter": 0.2, "sd": 0.7},
        },
    },
    "servicos": {
        "label": "Serviços (banho, tosa, vet)",
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

REGIAO_FACTOR = {"Nacional": 1.0, "Sudeste": 0.42, "Sul": 0.16, "Nordeste": 0.2, "Centro-Oeste": 0.1, "Norte": 0.08}
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


def gerar_dados(test: dict, metric_cfg: dict, periodo: int, rng: random.Random) -> DadosGerados:
    regiao_factor = REGIAO_FACTOR.get(test["regiao"], 1.0)
    device_factor = 0.92 + rng.random() * 0.16
    base_traffic = PLATAFORMA_TRAFFIC.get(test["plataforma"], 800.0) * regiao_factor * device_factor

    # leve chance de desbalanceamento proposital, pra alimentar o aviso de SRM
    srm_skew = 1.0
    if rng.random() < 0.16:
        sinal = 1 if rng.random() < 0.5 else -1
        srm_skew = 1 + (0.06 + rng.random() * 0.07) * sinal

    baseline_jitter = (rng.random() - 0.5) * 2 * metric_cfg["jitter"]
    baseline_value = metric_cfg["base"] + baseline_jitter
    if metric_cfg["type"] == "proporcao":
        baseline_value = min(0.95, max(0.005, baseline_value))

    # efeito real embutido no cenário: viés levemente positivo (empresa testando pra melhorar)
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
        day_noise_c = 1 + (rng.random() - 0.5) * 0.22
        day_noise_v = 1 + (rng.random() - 0.5) * 0.22
        s_c = round(base_traffic * day_noise_c * (srm_skew if srm_skew > 1 else 1))
        s_v = round(base_traffic * day_noise_v * ((2 - srm_skew) if srm_skew < 1 else 1))
        sess_c += s_c
        sess_v += s_v

        if metric_cfg["type"] == "proporcao":
            r_c = min(0.99, max(0.001, baseline_value * (1 + (rng.random() - 0.5) * 0.18)))
            r_v = min(0.99, max(0.001, variant_true_value * (1 + (rng.random() - 0.5) * 0.18)))
            c_c = round(s_c * r_c)
            c_v = round(s_v * r_v)
            conv_c += c_c
            conv_v += c_v
            dias.append(DiaMedicao(d, r_c, s_c, r_v, s_v))
        else:
            m_c = baseline_value * (1 + (rng.random() - 0.5) * 0.14)
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


def computar(test: dict, metric_cfg: dict, periodo: int, variante: str) -> ResultadoMedicao:
    rng = make_rng(seed_key(test["id"], periodo, variante))
    dados = gerar_dados(test, metric_cfg, periodo, rng)

    sims_c = [0.0] * NSIM
    sims_v = [0.0] * NSIM

    if metric_cfg["type"] == "proporcao":
        alpha_c, beta_c = 1 + dados.conv_c, 1 + (dados.sess_c - dados.conv_c)
        alpha_v, beta_v = 1 + dados.conv_v, 1 + (dados.sess_v - dados.conv_v)
        for i in range(NSIM):
            sims_c[i] = rng.betavariate(alpha_c, beta_c)
            sims_v[i] = rng.betavariate(alpha_v, beta_v)
        post_c = Posterior("proporcao", alpha_c / (alpha_c + beta_c), dados.conv_c / dados.sess_c, alpha_c, beta_c)
        post_v = Posterior("proporcao", alpha_v / (alpha_v + beta_v), dados.conv_v / dados.sess_v, alpha_v, beta_v)
    else:
        mean_c, mean_v = dados.soma_c / periodo, dados.soma_v / periodo
        sd_c = math.sqrt(max(1e-6, dados.soma_sq_c / periodo - mean_c ** 2)) or metric_cfg["sd"]
        sd_v = math.sqrt(max(1e-6, dados.soma_sq_v / periodo - mean_v ** 2)) or metric_cfg["sd"]
        se_c = max(sd_c / math.sqrt(periodo), mean_c * 0.01)
        se_v = max(sd_v / math.sqrt(periodo), mean_v * 0.01)
        for i in range(NSIM):
            sims_c[i] = rng.gauss(mean_c, se_c)
            sims_v[i] = rng.gauss(mean_v, se_v)
        post_c = Posterior("media", mean_c, mean_c, se=se_c)
        post_v = Posterior("media", mean_v, mean_v, se=se_v)

    wins_variant = 0
    lifts = [0.0] * NSIM
    exemplo_lift = None
    for i in range(NSIM):
        melhor = sims_v[i] < sims_c[i] if metric_cfg["direction"] == "down" else sims_v[i] > sims_c[i]
        if melhor:
            wins_variant += 1
        lifts[i] = (sims_v[i] - sims_c[i]) / max(1e-9, sims_c[i])
        if i == 0:
            exemplo_lift = lifts[i]
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
        rng=rng,
    )


# ---------------------------------------------------------------------------
# 4. Veredito em linguagem simples
# ---------------------------------------------------------------------------

def veredito(p_melhor: float, variante_label: str) -> dict:
    pct = f"{p_melhor * 100:.1f}".replace(".", ",")
    pct_inv = f"{(1 - p_melhor) * 100:.1f}".replace(".", ",")

    if p_melhor >= 0.95:
        return {"classe": "good", "icone": "✅",
                "titulo": f"Funcionou! A {variante_label} é melhor que o Controle.",
                "sub": f"De cada 100 vezes que a gente pudesse repetir esse teste, a Variante venceria em cerca de {pct} delas. É uma vitória bem clara — dá pra confiar e aplicar a mudança."}
    if p_melhor <= 0.05:
        return {"classe": "bad", "icone": "❌",
                "titulo": f"Não funcionou — o Controle continua melhor que a {variante_label}.",
                "sub": f"De cada 100 vezes, o Controle venceria em cerca de {pct_inv} delas. A ideia nova não performou bem: melhor não aplicar essa mudança do jeito que está."}
    if p_melhor >= 0.80:
        return {"classe": "mid", "icone": "🙂",
                "titulo": "Parece que sim, mas ainda não é 100% certeza.",
                "sub": f"A {variante_label} está na frente em {pct}% das simulações — um sinal bem positivo, mas ainda dá pra imaginar um cenário em que o Controle vence. Se der, vale rodar por mais alguns dias antes de decidir."}
    if p_melhor <= 0.20:
        return {"classe": "mid", "icone": "🙁",
                "titulo": "Parece que não, mas ainda não é 100% certeza.",
                "sub": f"O Controle está na frente em {pct_inv}% das simulações. É um sinal de que a mudança não ajudou, mas o teste ainda não fechou — rodar mais alguns dias deixaria a resposta mais firme."}
    return {"classe": "none", "icone": "🤔",
            "titulo": "Ainda não dá pra saber quem é melhor.",
            "sub": f"A chance de vitória está bem dividida ({pct}% Variante × {pct_inv}% Controle) — os dados ainda não mostram diferença clara entre os dois. Vale esperar mais dados chegarem antes de decidir."}


# ---------------------------------------------------------------------------
# 5. Checklist de contaminação (SRM, crossover, duplicidade, novidade, guardrails)
# ---------------------------------------------------------------------------

CHECKLIST_ITEMS = [
    {"key": "srm", "title": "A divisão entre os grupos ficou mesmo igual?", "tech": "randomização / SRM"},
    {"key": "crossover", "title": "Alguém viu as duas versões, sem querer?", "tech": "crossover"},
    {"key": "dup", "title": "Tem gente contada duas vezes, ou faltando informação?", "tech": "duplicidade / nulos"},
    {"key": "novelty", "title": "A melhora continuou, ou foi só nos primeiros dias?", "tech": "efeito novidade"},
    {"key": "guard", "title": "As métricas que não podiam piorar continuam OK?", "tech": "guardrails"},
]


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
    s = f"{value * 100:.{casas}f}".replace(".", ",")
    return ("+" if value >= 0 else "") + s + "%"


def checklist_hints(r: ResultadoMedicao) -> dict:
    split_total = r.dados.sess_c + r.dados.sess_v
    split_c_pct = f"{r.dados.sess_c / split_total * 100:.1f}".replace(".", ",")
    split_v_pct = f"{r.dados.sess_v / split_total * 100:.1f}".replace(".", ",")
    halves = calcular_metades(r)
    half1 = fmt_pct_signed(halves["lift1"])
    half2 = fmt_pct_signed(halves["lift2"])
    mesma_direcao = (halves["lift1"] >= 0) == (halves["lift2"] >= 0)
    guard_text = (
        f"{r.guardrail_risco} de {r.guardrail_total} guardrail(s) em atenção nesse recorte — vale olhar antes de aplicar."
        if r.guardrail_risco > 0 else "nenhum guardrail em atenção nesse recorte."
    )
    return {
        "srm": f"Nesse recorte: <strong>{split_c_pct}%</strong> controle × <strong>{split_v_pct}%</strong> variante — em usuários únicos, não em sessões. Numa randomização saudável, isso fica pertinho de 50/50; bem diferente disso é sinal de gente sendo jogada pro grupo errado.",
        "crossover": "Ex.: o cliente trocou de aparelho ou limpou o cookie e caiu no outro grupo no meio do teste. Isso faz um usuário só contar como se fosse dois, e mistura os dois resultados.",
        "dup": "Confira se o mesmo usuário não entrou duas vezes na conta e se não tem usuário sem grupo definido (nulo) entrando sem querer. É por isso que o Michelangelo mede por usuário único, e não por sessão: sessão repetida do mesmo usuário infla a amostra e engana a conta de erro.",
        "novelty": f"Nesse recorte: lift foi de <strong>{half1}</strong> na 1ª metade do período e <strong>{half2}</strong> na 2ª metade" + (" — direção se manteve." if mesma_direcao else " — mudou de direção, vale desconfiar.") + " Efeito que só aparece no começo costuma ser curiosidade, não melhora de verdade.",
        "guard": f"Nesse recorte: {guard_text}",
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
    target_fmt = f"{status['target']:,}".replace(",", ".")
    extra_fmt = f"~{extra:,}".replace(",", ".") + " dia(s)" if extra is not None else "alguns dias"

    return [
        {
            "titulo": "⏳ Prorrogar o teste",
            "desc": f"Continuar rodando por mais {extra_fmt} até bater a meta de {target_fmt} usuários por grupo. Mais seguro, mas atrasa a decisão — e, se a mudança for boa, adia o ganho.",
            "recomendado": (not strong_already) and quick_extension,
        },
        {
            "titulo": "⚠️ Parar e aceitar menos confiança",
            "desc": "Decidir com a amostra de hoje, sabendo que o intervalo de credibilidade fica mais largo e a chance de decisão errada é maior. Só faz sentido se a evidência já estiver bem forte ou a decisão não for crítica.",
            "recomendado": strong_already,
        },
        {
            "titulo": "🛑 Parar sem decidir (inconclusivo)",
            "desc": "Encerrar o teste sem aplicar a mudança agora. Vale quando faltaria muito tempo pra bater a meta — melhor revisar o desenho lá no DaVinci (tráfego esperado, duração) antes de tentar de novo.",
            "recomendado": (not strong_already) and (not quick_extension),
        },
    ]


# ---------------------------------------------------------------------------
# 7. Formatação (padrão pt-BR)
# ---------------------------------------------------------------------------

def fmt_metric(value: float, cfg: dict) -> str:
    if cfg["type"] == "proporcao":
        return f"{value * 100:.2f}".replace(".", ",") + "%"
    if cfg.get("unit") == "%":
        return f"{value:.1f}".replace(".", ",") + "%"
    if cfg.get("unit") == "R$":
        return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_int(value: float) -> str:
    return f"{round(value):,}".replace(",", ".")
