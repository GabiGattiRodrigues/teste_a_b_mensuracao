"""
Michelangelo — assistente de MEDIÇÃO de teste A/B (v1)
Companheiro do DaVinci (que cuida do desenho/dimensionamento do teste).

Rodar com:
    pip install -r requirements.txt
    streamlit run app.py
"""

import base64
import json
from datetime import datetime
from html import escape as _esc
from pathlib import Path

import streamlit as st
import plotly.graph_objects as go

import ab_measure as m
import davinci_bridge as db

ASSETS_DIR = Path(__file__).parent / "assets"
LOGO_PATH = ASSETS_DIR / "michelangelo_mascote.png"
DAVI_ICON_PATH = ASSETS_DIR / "davi_icon.png"
_page_icon = str(LOGO_PATH) if LOGO_PATH.exists() else "🗿"


def _img_b64(path: Path) -> str:
    """Codifica uma imagem em base64 pra embutir como <img> dentro de HTML do markdown."""
    return base64.b64encode(path.read_bytes()).decode("ascii")


_DAVI_ICON_TAG = (
    f'<img src="data:image/png;base64,{_img_b64(DAVI_ICON_PATH)}" '
    f'style="height:52px; vertical-align:-16px; margin-left:6px;" alt="Davi, a escultura que o Michelangelo fez">'
    if DAVI_ICON_PATH.exists() else "🗿"
)

HISTORICO_PATH = Path(__file__).parent / "historico_medicoes.json"
USUARIOS_LOG_PATH = Path(__file__).parent / "usuarios_log.json"

# Mesmas senhas do DaVinci -- pra quem já tem acesso lá, não precisa decorar
# uma segunda senha. Não é autenticação de verdade (senhas em texto puro no
# código), é só uma trava simples pra separar "uso normal" de "modo admin"
# num protótipo local -- igual ao DaVinci.
SENHA_PADRAO = "teste_a_b_produto"
SENHA_ADMIN = "teste_a_b_gabi"

COR_CONTROLE = "#0E8A74"
COR_VARIANTE = "#DD7024"

st.set_page_config(page_title="Michelangelo — Medição de Teste A/B", page_icon=_page_icon, layout="wide")


def _carregar_usuarios_log() -> list:
    if USUARIOS_LOG_PATH.exists():
        try:
            return json.loads(USUARIOS_LOG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _registrar_usuario(nome: str, admin: bool) -> None:
    log = _carregar_usuarios_log()
    log.insert(0, {"nome": nome, "quando": datetime.now().strftime("%d/%m/%Y %H:%M"), "admin": admin})
    USUARIOS_LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# Boas-vindas — igual ao DaVinci: mascote + nome + senha, antes de qualquer
# outra coisa aparecer na tela.
# --------------------------------------------------------------------------

if not st.session_state.get("usuario_nome"):
    st.markdown("<div style='height: 48px;'></div>", unsafe_allow_html=True)
    col_boas_a, col_boas_b, col_boas_c = st.columns([1, 2, 1])
    with col_boas_b:
        if LOGO_PATH.exists():
            img_col_a, img_col_b, img_col_c = st.columns([1, 1, 1])
            with img_col_b:
                st.image(str(LOGO_PATH), width=140)
        st.markdown(
            f"<h2 style='text-align:center; margin-bottom:4px;'>Oi! Eu sou o Michelangelo {_DAVI_ICON_TAG}</h2>"
            "<p style='text-align:center; color:#7A6752; font-size:15px; margin-top:0;'>"
            "Vou te ajudar a medir o seu teste A/B que já está rodando, todo explicado em "
            "português simples — sem precisar saber estatística de antemão.</p>",
            unsafe_allow_html=True,
        )
        nome_input = st.text_input(
            "Antes da gente começar, qual é o seu nome? *",
            key="input_boas_vindas_nome", placeholder="Seu nome",
        )
        senha_input = st.text_input(
            "Senha de acesso *", key="input_boas_vindas_senha", type="password", placeholder="Senha",
        )
        st.caption("Campos com \\* são obrigatórios. Mesma senha que você usa no DaVinci.")
        if st.button("Vamos começar →", use_container_width=True, type="primary"):
            if not nome_input.strip():
                st.warning("Preciso do seu nome pra continuar 🙂")
            elif senha_input == SENHA_ADMIN:
                st.session_state.usuario_nome = nome_input.strip()
                st.session_state.is_admin = True
                _registrar_usuario(nome_input.strip(), True)
                st.rerun()
            elif senha_input == SENHA_PADRAO:
                st.session_state.usuario_nome = nome_input.strip()
                st.session_state.is_admin = False
                _registrar_usuario(nome_input.strip(), False)
                st.rerun()
            else:
                st.error("Senha incorreta — confere com quem te passou o acesso.")
    st.stop()

_CSS = """
<style>
.chip { display:inline-block; background:#FBF0D2; color:#6B4E00; border-radius:999px;
        padding:5px 11px; font-size:12.5px; margin:3px 6px 0 0; }
.ficha { background:#FFF1E3; border:1px solid #F3D9BC; border-radius:10px; padding:16px 18px 8px; }
.ficha-header { display:flex; justify-content:space-between; align-items:center;
                font-size:12.5px; font-weight:600; color:#7A6752; margin-bottom:10px;
                padding-bottom:10px; border-bottom:1px dashed #EAD9C6; }
.ficha-id { font-family: "SFMono-Regular", Menlo, monospace; color:#B8430A; background:#fff;
            border:1px solid #EAD9C6; border-radius:6px; padding:2px 8px; }
.banner { border-radius:10px; padding:12px 16px; font-size:14px; margin:10px 0; }
.banner.good { background:#E9F5E1; color:#2C6B1F; }
.banner.warn { background:#FBF0D2; color:#6B4E00; }
.banner.bad  { background:#FBE3DE; color:#9A2A17; }
.banner.info { background:#EAF1FB; color:#1F4E8C; }
.verdict { border-radius:12px; padding:18px 20px; display:flex; gap:14px; align-items:flex-start; }
.verdict.good { background:#E9F5E1; }
.verdict.bad  { background:#FBE3DE; }
.verdict.mid  { background:#FBF0D2; }
.verdict.none { background:#F1ECE4; }
.verdict .icon { font-size:28px; line-height:1; }
.verdict .title { font-size:17px; font-weight:700; color:#241708; margin-bottom:4px; }
.verdict .sub { font-size:13.5px; color:#4A3B2C; line-height:1.5; }
.pg-wrap { display:grid; grid-template-columns: repeat(20, 1fr); gap:3px; max-width:340px; }
.pg-dot { width:100%; aspect-ratio:1; border-radius:50%; }
.pg-dot.win  { background: __VARIANT_COLOR__; }
.pg-dot.lose { background: #D9CBB8; }
.decision-card { border:1px solid #EAD9C6; border-radius:10px; padding:14px 16px; margin-bottom:10px; }
.decision-card.recommended { border-color:#E85C0D; background:#FFF6EC; }
.decision-tag { display:inline-block; background:#E85C0D; color:#fff; font-size:10.5px;
                font-weight:700; border-radius:999px; padding:2px 9px; margin-left:8px; }
.check-hint { font-size:12.5px; color:#7A6752; margin:2px 0 10px 26px; line-height:1.5; }
</style>
""".replace("__VARIANT_COLOR__", COR_VARIANTE)

st.markdown(_CSS, unsafe_allow_html=True)

# --------------------------------------------------------------------------
# Cabeçalho
# --------------------------------------------------------------------------

col_logo, col_title, col_usuario = st.columns([1, 6, 2], gap="small")
with col_logo:
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=76)
with col_title:
    st.title("Michelangelo")
    st.caption(
        "Mede um teste A/B que já está rodando, com inferência bayesiana explicada em português simples. "
        "Cuida só da medição — objetivo, público e meta de amostra são desenhados no "
        "**DaVinci**, e aqui chegam prontos, só para leitura."
    )
with col_usuario:
    _selo_admin = (
        " <span style='background:#FBF0D2; color:#6B4E00; border-radius:999px; padding:2px 9px; "
        "font-size:11px; font-weight:600; margin-left:4px;'>admin</span>"
        if st.session_state.get("is_admin") else ""
    )
    st.markdown(
        f"<div style='text-align:right; padding-top:20px;'>"
        f"<span style='font-size:14px; color:#7A6752;'>👋 Olá, <b>{_esc(st.session_state.usuario_nome)}</b></span>"
        f"{_selo_admin}</div>",
        unsafe_allow_html=True,
    )
    if st.button("trocar", key="btn_trocar_usuario", help="Trocar o nome de quem está usando"):
        st.session_state.usuario_nome = None
        st.session_state.is_admin = False
        st.rerun()

with st.expander("📖 Como usar o Michelangelo (passo a passo)", expanded=False):
    st.markdown(
        "**1. Escolha o teste.** No menu \"Qual teste você quer medir?\" logo abaixo, escolha o "
        "teste que já está rodando de verdade. A ficha dele (BU, métrica, campanha, meta de amostra, "
        "prazo) vem travada do DaVinci, só pra conferência — não dá pra editar por aqui de propósito.\n\n"
        "**2. Diga há quantos dias ele está rodando.** No campo \"Período\", digite quantos dias já "
        "se passaram desde que o teste foi ao ar de verdade (não precisa ser 7, 14 ou 30 — pode ser "
        "qualquer número).\n\n"
        "**3. Escolha qual variante comparar.** Se o teste tiver mais de dois grupos, o Michelangelo "
        "sempre compara o Controle contra **um** desafiante por vez — escolha qual.\n\n"
        "**4. Role a tela, na ordem que ela já vem:** primeiro se a amostra planejada foi atingida "
        "(e o que fazer se ainda não foi), depois quem está ganhando e com que confiança, depois um "
        "checklist de coisas pra conferir manualmente, e por fim os gráficos que mostram esse resultado "
        "visualmente.\n\n"
        "**5. Antes de aplicar a mudança pra todo mundo, confira o checklist.** A ferramenta calcula a "
        "estatística sozinha, mas coisas como \"a divisão entre os grupos ficou mesmo igual?\" ou "
        "\"alguém viu as duas versões sem querer?\" só uma olhada manual consegue confirmar."
    )

with st.expander("❓ Como interpretar os resultados"):
    st.markdown(
        "**Por que a probabilidade é sobre \"quantas vezes eu ganharia\", e não um número seco?**\n\n"
        "Cada grupo (Controle e Variante) não tem um resultado fixo — tem uma **nuvem de valores "
        "prováveis** (a \"posterior\"), porque medimos só uma amostra, não todo mundo. Simulamos essa "
        "nuvem **3.000 vezes** (Monte Carlo) e contamos em quantas a Variante ganhou. Se ganhou em "
        "2.850 de 3.000, a probabilidade é 95% — é como jogar uma moeda viciada 3.000 vezes e ver "
        "quantas vezes deu cara."
    )
    st.markdown(
        "**O que é o \"lift esperado\"?**\n\n"
        "É o tamanho médio da diferença entre a Variante e o Controle, em porcentagem — não só "
        "\"quem ganha\", mas \"por quanto\". Um lift de +8% quer dizer que, na média das simulações, "
        "a Variante veio 8% acima do Controle. Repare que dá pra ter uma probabilidade alta de "
        "vitória (ex.: 96%) com um lift pequeno (ex.: +2%) — a Variante ganha quase sempre, mas por "
        "pouco."
    )
    st.markdown(
        "**O que é o \"intervalo de credibilidade (90%)\"?**\n\n"
        "É a faixa onde o efeito real provavelmente está — em vez de um número seco, um \"tenho quase "
        "certeza que está entre X e Y\". Quanto mais estreita, mais preciso é o palpite (e ela só "
        "fica mais estreita com mais gente medida). Se essa faixa cruzar o zero (ex.: -2% a +9%), "
        "isso é sinal de que ainda não dá pra ter certeza da direção do efeito — a conta completa de "
        "onde ele vem está no \"Ver a conta\" logo abaixo do resultado."
    )
    st.markdown(
        "**Pra que serve o checklist e os guardrails?**\n\n"
        "São verificações que a estatística sozinha não consegue fazer. O checklist olha coisas como "
        "divisão desigual entre os grupos (SRM), gente vendo as duas versões sem querer (crossover) "
        "ou o efeito sumir depois dos primeiros dias (novidade). Os guardrails são métricas que **não "
        "podem piorar** mesmo se a métrica principal melhorar (ex.: custo por cliente, tempo de "
        "checkout) — servem pra pegar um \"efeito colateral\" ruim escondido atrás de um bom resultado."
    )
    st.markdown(
        "**Por que por usuário único, e não por sessão?**\n\n"
        "Se um usuário voltar 3 vezes, contar por sessão faz ele valer como 3 pessoas — isso infla "
        "a amostra e faz o intervalo de confiança parecer mais estreito (mais preciso) do que "
        "realmente é. Medir por usuário único evita esse engano."
    )
    st.markdown(
        "**De onde vem a meta de amostra e o prazo que aparecem na ficha do teste?**\n\n"
        "Vêm prontos do **DaVinci** — é lá que se decide o objetivo, a métrica, o público e quantas "
        "pessoas por grupo o teste precisa. O Michelangelo só lê isso (nunca deixa editar) e "
        "acompanha se a medição de hoje já bateu essa meta."
    )
    st.markdown(
        "**E se a amostra planejada ainda não foi atingida?**\n\n"
        "O Michelangelo calcula três caminhos possíveis — prorrogar o teste, aceitar menos confiança "
        "e decidir com o que já tem, ou parar sem decidir — e marca qual costuma fazer mais sentido "
        "pro seu caso, na seção \"Amostra planejada × observada\"."
    )

if st.session_state.get("is_admin"):
    st.divider()
    st.subheader("👥 Usuários que já entraram no Michelangelo")
    _usuarios = _carregar_usuarios_log()
    if not _usuarios:
        st.caption("Ninguém entrou ainda.")
    else:
        st.caption(f"{len(_usuarios)} entrada(s) de login registrada(s) neste computador.")
        st.table([
            {"Nome": u.get("nome", ""), "Quando": u.get("quando", ""), "Tipo": "Administradora" if u.get("admin") else "Usuário"}
            for u in _usuarios
        ])

st.divider()

# --------------------------------------------------------------------------
# Seleção do teste — vem do DaVinci, cadastro 100% somente-leitura
# --------------------------------------------------------------------------

st.subheader("Qual teste você quer medir?")

testes, usando_exemplo = db.carregar_testes_registrados()

if usando_exemplo:
    st.info(
        "📋 Nenhum teste salvo ainda no histórico do DaVinci — mostrando **exemplos** no formato real "
        "que o DaVinci gera (mesmo ID, mesma ficha). Assim que você salvar um teste de verdade no "
        "DaVinci (botão \"💾 Salvar este teste no histórico\"), ele aparece aqui automaticamente.",
    )

opcoes = {f"{t['id']} · {t['nome']}": t for t in testes}
escolha = st.selectbox("Teste cadastrado no DaVinci", list(opcoes.keys()), key="sel_teste")
test = opcoes[escolha]

metric_cfg = db.derive_metric_cfg(test)

# Ficha 100% somente-leitura -- nada aqui é um widget editável de propósito:
# BU, métrica, região, campanha, plataforma, dispositivo, objetivo, meta de
# amostra e prazo são cadastro do DaVinci, não algo que se mude por aqui.
chips = "".join(
    f'<span class="chip">{_esc(rotulo)}: {_esc(str(valor))}</span>'
    for rotulo, valor in [
        ("BU", test["bu"]), ("Métrica", test["metrica_nome"]), ("Região", test["regiao"]),
        ("Campanha", test["campanha"]), ("Plataforma", test["plataforma"]), ("Dispositivo", test["dispositivo"]),
    ]
)
meta_fmt = m.fmt_int(test["target_per_group"])
prazo_fmt = f"{test['planned_days']} dias"
st.markdown(
    f'<div class="ficha">'
    f'<div class="ficha-header"><span>🔒 Cadastrado no DaVinci — não editável aqui</span>'
    f'<span class="ficha-id">{_esc(test["id"])}</span></div>'
    f'<div style="font-size:13.5px; color:#4A3B2C; margin-bottom:10px;">'
    f'<strong>{_esc(test.get("objetivo") or "Objetivo não preenchido no DaVinci")}</strong>'
    f'{" · " + _esc(test["area"]) if test.get("area") else ""}</div>'
    f'{chips}'
    f'<div style="margin-top:12px; display:flex; gap:24px; font-size:13px; color:#7A6752;">'
    f'<span>🎯 Meta de amostra: <strong style="color:#241708;">{meta_fmt} por grupo</strong></span>'
    f'<span>🗓️ Prazo planejado: <strong style="color:#241708;">{prazo_fmt}</strong></span>'
    f'</div></div>',
    unsafe_allow_html=True,
)

st.caption(
    "Michelangelo compara duas variantes por vez. Com 3 grupos ou mais cadastrados no DaVinci, "
    "ele ajusta a confiança sozinho (correção de Bonferroni) — aqui, sempre controle × 1 desafiante."
)

col_periodo, col_variante = st.columns(2)
with col_periodo:
    periodo = st.number_input(
        "Período (rodando há)", min_value=1, max_value=180, value=14, step=1, key="periodo",
        help="Quantos dias esse teste já está rodando — digite o número, não precisa ser um valor redondo.",
    )
    st.caption(f"{periodo} dia(s)")
with col_variante:
    variante = st.radio(
        "Comparando controle (A) com", test["variantes"], index=0, horizontal=True, key="variante",
        format_func=lambda v: f"Variante {v}",
    )

st.divider()

# --------------------------------------------------------------------------
# Cálculo
# --------------------------------------------------------------------------

r = m.computar(test, metric_cfg, periodo, variante)
guard_names = db.guardrails_para_bu(test["bu"])
guard_itens, guard_risco, guard_total = m.guardrails_status(guard_names, r.rng)
r.guardrail_risco, r.guardrail_total = guard_risco, guard_total
variante_label = f"Variante {variante}"

# --------------------------------------------------------------------------
# Amostra planejada (DaVinci) x observada (Michelangelo)
# --------------------------------------------------------------------------

st.subheader("Amostra planejada × observada")

status = m.status_amostra(test, r)
c1, c2, c3 = st.columns(3)
c1.metric("Usuários por grupo (o menor dos 2)", m.fmt_int(status["observed"]), f"de {m.fmt_int(status['target'])} planejados")
c2.metric("Dias rodando", f"{r.dias}", f"de {status['planned_days']} planejados")
c3.metric("% da meta atingida", f"{min(100, status['pct_sample']*100):.0f}%")
st.progress(min(1.0, status["pct_sample"]))

if status["reached_sample"]:
    st.markdown(f'<div class="banner good">✅ Meta de amostra atingida — {m.fmt_int(status["observed"])} usuários por grupo. Dá pra confiar no resultado abaixo e decidir agora.</div>', unsafe_allow_html=True)
elif not status["past_deadline"]:
    faltam = m.fmt_int(status["remaining"])
    dias_txt = f"{status['extra_days_needed']} dia(s)" if status["extra_days_needed"] is not None else "alguns dias"
    st.markdown(f'<div class="banner warn">⏳ Ainda faltam cerca de {faltam} usuários por grupo pra bater a meta — no ritmo atual, isso é mais ou menos {dias_txt}. Como ainda está dentro do prazo planejado ({status["planned_days"]} dias), o recomendado é continuar rodando antes de decidir.</div>', unsafe_allow_html=True)
else:
    faltam = m.fmt_int(status["remaining"])
    st.markdown(f'<div class="banner warn">⚠️ O prazo planejado ({status["planned_days"]} dias) já passou e a amostra ainda não bateu a meta — faltam cerca de {faltam} usuários por grupo. Isso costuma acontecer quando o tráfego real veio mais baixo do que o esperado no desenho do teste.</div>', unsafe_allow_html=True)
    st.markdown("**O que fazer agora?**")
    opcoes_dec = m.opcoes_decisao(r, status)
    cols_dec = st.columns(3)
    for col, o in zip(cols_dec, opcoes_dec):
        tag = '<span class="decision-tag">recomendado aqui</span>' if o["recomendado"] else ""
        col.markdown(
            f'<div class="decision-card{" recommended" if o["recomendado"] else ""}">'
            f'<div style="font-weight:700; margin-bottom:6px;">{o["titulo"]}{tag}</div>'
            f'<div style="font-size:12.5px; color:#4A3B2C;">{o["desc"]}</div></div>',
            unsafe_allow_html=True,
        )

st.divider()

# --------------------------------------------------------------------------
# Resultado bayesiano
# --------------------------------------------------------------------------

st.subheader(f"Como o Grupo {variante} está indo contra o Controle — {metric_cfg['label']}")

tile_c, tile_v = st.columns(2)
tile_c.metric("Grupo A · Controle", m.fmt_metric(r.post_controle.summary_value, metric_cfg), f"{m.fmt_int(r.dados.sess_c)} usuários únicos em {r.dias} dias")
tile_v.metric(f"Grupo {variante} · Variante", m.fmt_metric(r.post_variante.summary_value, metric_cfg), f"{m.fmt_int(r.dados.sess_v)} usuários únicos em {r.dias} dias")

v = m.veredito(r.p_melhor, variante_label)
st.markdown(
    f'<div class="verdict {v["classe"]}"><div class="icon">{v["icone"]}</div>'
    f'<div><div class="title">{_esc(v["titulo"])}</div><div class="sub">{_esc(v["sub"])}</div></div></div>',
    unsafe_allow_html=True,
)

st.markdown(f"##### Qual é a probabilidade de a **{variante_label}** ser melhor que o Controle?")
pct_str = f"{r.p_melhor*100:.1f}".replace(".", ",") + "%"

col_fig, col_pg = st.columns([1, 2])
with col_fig:
    st.markdown(f"<div style='font-size:52px; font-weight:800; color:#B8430A; font-family:monospace;'>{pct_str}</div>", unsafe_allow_html=True)
    strength = max(r.p_melhor, 1 - r.p_melhor)
    badge_label = "Evidência forte" if strength >= 0.95 else "Evidência moderada" if strength >= 0.80 else "Sem diferença clara"
    side = f"{variante_label} à frente" if r.p_melhor >= 0.5 else "Controle à frente"
    st.caption(f"**{badge_label}** · {side}")
with col_pg:
    wins = round(r.p_melhor * 100)
    dots = "".join(f'<span class="pg-dot {"win" if i < wins else "lose"}"></span>' for i in range(100))
    st.markdown(f'<div class="pg-wrap">{dots}</div>', unsafe_allow_html=True)
    losses = 100 - wins
    if wins <= 1:
        frase = f"Se desse pra repetir esse teste 100 vezes do zero, a {variante_label} praticamente **nunca** sairia na frente."
    elif wins >= 99:
        frase = f"Se desse pra repetir esse teste 100 vezes do zero, a {variante_label} sairia na frente em **quase todas**."
    else:
        frase = f"Se desse pra repetir esse teste **100 vezes do zero**, a {variante_label} sairia na frente em cerca de **{wins} delas**, e o Controle nas outras **{losses}** — como as bolinhas acima."
    st.caption(frase)

col_lift, col_ci = st.columns(2)
col_lift.metric("Lift esperado", m.fmt_pct_signed(r.lift_medio), "menor é melhor" if metric_cfg["direction"] == "down" else "maior é melhor")
col_ci.metric("Intervalo de credibilidade (90%)", f"{m.fmt_pct_signed(r.ci_baixo)} a {m.fmt_pct_signed(r.ci_alto)}")
st.caption(
    f"🍬 Pense assim: é como adivinhar quantos doces tem num pote sem contar um por um. A gente não crava "
    f"um número único — diz **\"tenho quase certeza que o efeito real está entre {m.fmt_pct_signed(r.ci_baixo)} "
    f"e {m.fmt_pct_signed(r.ci_alto)}\"**. Quanto mais estreita essa faixa, mais preciso é o palpite — e ela só "
    f"fica mais estreita com mais gente medida. Os números de onde ela vem estão em \"Ver a conta\" abaixo."
)

split_total = r.dados.sess_c + r.dados.sess_v
split_c = r.dados.sess_c / split_total
if abs(split_c - 0.5) > 0.035:
    pct_c_fmt = f"{split_c*100:.1f}".replace(".", ",")
    pct_v_fmt = f"{(1-split_c)*100:.1f}".replace(".", ",")
    st.markdown(f'<div class="banner warn">⚠️ Possível desbalanceamento na divisão dos grupos (SRM) — controle recebeu {pct_c_fmt}% dos usuários, variante {pct_v_fmt}%.</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="banner good">✅ Nenhum aviso — a divisão de usuários entre os grupos está dentro do esperado (~50/50).</div>', unsafe_allow_html=True)

with st.expander(f"🧮 Ver a conta por trás dos {pct_str}"):
    if metric_cfg["type"] == "proporcao":
        rate_c = r.dados.conv_c / r.dados.sess_c
        rate_v = r.dados.conv_v / r.dados.sess_v
        st.markdown("**Passo 1 — o que a gente observou** (usuários únicos, não sessões):")
        st.code(
            f"Controle: {m.fmt_int(r.dados.conv_c)} conversões em {m.fmt_int(r.dados.sess_c)} usuários → {rate_c*100:.2f}%\n"
            f"Variante: {m.fmt_int(r.dados.conv_v)} conversões em {m.fmt_int(r.dados.sess_v)} usuários → {rate_v*100:.2f}%",
            language=None,
        )
        st.markdown("**Passo 2 — a \"nuvem de chances\" de cada grupo** (modelo Beta-Binomial, α = 1 + conversões, β = 1 + não-conversões):")
        st.code(
            f"Controle → α={r.post_controle.alpha:.0f}  β={r.post_controle.beta:.0f}  → centro ≈ {r.post_controle.mean*100:.2f}%\n"
            f"Variante → α={r.post_variante.alpha:.0f}  β={r.post_variante.beta:.0f}  → centro ≈ {r.post_variante.mean*100:.2f}%",
            language=None,
        )
    else:
        st.markdown("**Passo 1 — média diária observada em cada grupo:**")
        st.code(
            f"Controle → média ≈ {m.fmt_metric(r.post_controle.mean, metric_cfg)}  (erro padrão ≈ {r.post_controle.se:.3f})\n"
            f"Variante → média ≈ {m.fmt_metric(r.post_variante.mean, metric_cfg)}  (erro padrão ≈ {r.post_variante.se:.3f})",
            language=None,
        )
    nsim_fmt = m.fmt_int(r.nsim)
    vitorias_fmt = m.fmt_int(round(r.p_melhor * r.nsim))
    st.markdown(
        f"**Passo 3 — Monte Carlo:** sorteamos **{nsim_fmt}** valores possíveis de cada nuvem e contamos "
        f"em quantos a Variante venceu o Controle. Nessa simulação, venceu em **{vitorias_fmt}** "
        f"de {nsim_fmt} → **{pct_str}**."
    )
    st.markdown(
        f"**Passo 4 — lift:** em cada uma das {nsim_fmt} simulações calculamos a diferença relativa "
        f"(Variante − Controle) ÷ Controle. A média dessas {nsim_fmt} diferenças é o lift esperado "
        f"({m.fmt_pct_signed(r.lift_medio)})."
    )
    pos_baixo_fmt = m.fmt_int(int(r.nsim * 0.05))
    pos_alto_fmt = m.fmt_int(int(r.nsim * 0.95))
    st.markdown(
        f"**Passo 5 — de onde vem o intervalo de credibilidade (90%):** pegamos essas {nsim_fmt} diferenças "
        f"e colocamos em ordem, da menor pra maior — como uma fila. A que fica na posição "
        f"{pos_baixo_fmt} (5% do início da fila) é o limite de baixo: **{m.fmt_pct_signed(r.ci_baixo)}**. A que "
        f"fica na posição {pos_alto_fmt} (95% do início da fila) é o limite de cima: **{m.fmt_pct_signed(r.ci_alto)}**. "
        f"Isso quer dizer: em **90% das {nsim_fmt} simulações**, a diferença caiu entre esses dois valores — "
        f"cortamos os 5% de simulações mais pessimistas e os 5% mais otimistas de cada ponta, e ficamos com "
        f"a faixa \"do meio\", que é onde o efeito real provavelmente está."
    )

st.divider()

# --------------------------------------------------------------------------
# Checklist de contaminação
# --------------------------------------------------------------------------

st.subheader("✅ Antes de aplicar de verdade: checklist")
st.caption("Isso aqui a ferramenta não consegue confirmar sozinha — vale olhar manualmente antes de confiar 100% no resultado.")

hints = m.checklist_hints(r)
checked = 0
for item in m.CHECKLIST_ITEMS:
    key = f"chk_{item['key']}_{test['id']}_{periodo}_{variante}"
    val = st.checkbox(f"{item['title']}  *(​{item['tech']})*", key=key)
    st.markdown(f'<div class="check-hint">{hints[item["key"]]}</div>', unsafe_allow_html=True)
    if val:
        checked += 1

total_itens = len(m.CHECKLIST_ITEMS)
st.caption(f"{checked} de {total_itens} confirmados")
if checked == total_itens:
    st.markdown('<div class="banner good">✅ Checklist completo — pode confiar nesse resultado e aplicar a mudança.</div>', unsafe_allow_html=True)
else:
    st.markdown(f'<div class="banner warn">Faltam {total_itens - checked} item(ns) pra confirmar antes de aplicar essa mudança de verdade.</div>', unsafe_allow_html=True)

st.divider()

# --------------------------------------------------------------------------
# Gráficos
# --------------------------------------------------------------------------

st.subheader("Distribuições posteriores")

_media_c_fmt = m.fmt_metric(r.post_controle.mean, metric_cfg)
_media_v_fmt = m.fmt_metric(r.post_variante.mean, metric_cfg)
st.markdown(
    f'<div class="banner info">🎯 <strong>Como ler esse gráfico, de um jeito bem simples:</strong> imagine que você '
    f'não sabe o valor exato de cada grupo — só tem <em>palpites</em>, uns mais prováveis que outros. Cada curva '
    f'colorida é o "mapa de palpites" de um grupo: onde ela fica mais alta (o topo do montinho), é o valor que a '
    f'gente acha mais provável de ser o valor real; onde ela é baixinha, nas pontas, é um valor bem improvável. '
    f'Nesse caso, o topo do montinho verde (Controle) fica perto de <strong>{_esc(_media_c_fmt)}</strong>, e o do '
    f'laranja (Variante) perto de <strong>{_esc(_media_v_fmt)}</strong>. Quando os dois montinhos ficam bem '
    f'separados um do outro (pouca área onde eles se sobrepõem), é sinal forte de que os grupos são mesmo '
    f'diferentes — quando ficam quase um em cima do outro, ainda não dá pra ter certeza de qual é melhor.</div>',
    unsafe_allow_html=True,
)
st.caption("Eixo de baixo: o valor possível da métrica. Eixo da esquerda (\"densidade\"): o quão provável é aquele valor — não é uma quantidade de pessoas, é só a altura do \"mapa de palpites\".")

fig_dens = go.Figure()
if metric_cfg["type"] == "proporcao":
    def _beta_sd(post):
        a, b = post.alpha, post.beta
        return ((a * b) / ((a + b) ** 2 * (a + b + 1))) ** 0.5
    sd_c, sd_v = _beta_sd(r.post_controle), _beta_sd(r.post_variante)
    lo = max(0.0001, min(r.post_controle.mean - 5 * sd_c, r.post_variante.mean - 5 * sd_v))
    hi = min(0.9999, max(r.post_controle.mean + 5 * sd_c, r.post_variante.mean + 5 * sd_v))
    xs = [lo + (hi - lo) * i / 999 for i in range(1000)]
    for post, nome, cor in [(r.post_controle, "Controle", COR_CONTROLE), (r.post_variante, f"Variante {variante}", COR_VARIANTE)]:
        ys = [m.beta_pdf(x, post.alpha, post.beta) for x in xs]
        fig_dens.add_trace(go.Scatter(x=[x * 100 for x in xs], y=ys, mode="lines", name=nome, line=dict(color=cor, width=2.5), fill="tozeroy"))
    fig_dens.update_xaxes(title_text="taxa (%)")
else:
    lo = min(r.post_controle.mean - 4 * r.post_controle.se, r.post_variante.mean - 4 * r.post_variante.se)
    hi = max(r.post_controle.mean + 4 * r.post_controle.se, r.post_variante.mean + 4 * r.post_variante.se)
    xs = [lo + (hi - lo) * i / 999 for i in range(1000)]
    for post, nome, cor in [(r.post_controle, "Controle", COR_CONTROLE), (r.post_variante, f"Variante {variante}", COR_VARIANTE)]:
        ys = [m.normal_pdf(x, post.mean, post.se) for x in xs]
        fig_dens.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=nome, line=dict(color=cor, width=2.5), fill="tozeroy"))
    fig_dens.update_xaxes(title_text=metric_cfg.get("unit", "valor"))
fig_dens.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10), plot_bgcolor="white", paper_bgcolor="white", yaxis_title="densidade", legend=dict(orientation="h", y=1.1))
st.plotly_chart(fig_dens, use_container_width=True)

st.subheader("Evolução no período")
dias_x = [d.dia + 1 for d in r.dados.dias]
fig_metric = go.Figure()
fig_metric.add_trace(go.Scatter(x=dias_x, y=[d.valor_controle * (100 if metric_cfg["type"] == "proporcao" else 1) for d in r.dados.dias], mode="lines+markers", name="Controle", line=dict(color=COR_CONTROLE, width=2.5)))
fig_metric.add_trace(go.Scatter(x=dias_x, y=[d.valor_variante * (100 if metric_cfg["type"] == "proporcao" else 1) for d in r.dados.dias], mode="lines+markers", name=f"Variante {variante}", line=dict(color=COR_VARIANTE, width=2.5)))
fig_metric.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), plot_bgcolor="white", paper_bgcolor="white", xaxis_title="dia do teste", yaxis_title=metric_cfg["label"], legend=dict(orientation="h", y=1.15))
st.plotly_chart(fig_metric, use_container_width=True)

fig_sess = go.Figure()
fig_sess.add_trace(go.Bar(x=dias_x, y=[d.sessoes_controle for d in r.dados.dias], name="Controle", marker_color=COR_CONTROLE))
fig_sess.add_trace(go.Bar(x=dias_x, y=[d.sessoes_variante for d in r.dados.dias], name=f"Variante {variante}", marker_color=COR_VARIANTE))
fig_sess.update_layout(barmode="group", height=280, margin=dict(l=10, r=10, t=10, b=10), plot_bgcolor="white", paper_bgcolor="white", xaxis_title="dia do teste", yaxis_title="usuários únicos", legend=dict(orientation="h", y=1.15))
st.plotly_chart(fig_sess, use_container_width=True)

st.divider()

# --------------------------------------------------------------------------
# Guardrails
# --------------------------------------------------------------------------

st.subheader("🚧 O que não pode piorar (guardrails)")
if guard_itens:
    for g in guard_itens:
        status_txt = "⚠ atenção" if g["risco"] else "✓ estável"
        cor = "#9A2A17" if g["risco"] else "#2C6B1F"
        st.markdown(f'<div style="display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #EAD9C6;"><span>{_esc(g["nome"])}</span><span style="color:{cor}; font-weight:600;">{status_txt} · {m.fmt_pct_signed(g["delta_pct"]/100)}</span></div>', unsafe_allow_html=True)
else:
    st.caption("Nenhum guardrail definido para essa BU.")

st.divider()

# --------------------------------------------------------------------------
# Histórico de medições (próprio do Michelangelo -- separado do
# historico_testes.json do DaVinci, que é sobre DESENHO, não medição)
# --------------------------------------------------------------------------

st.subheader("📁 Histórico de medições salvas")


def _carregar_historico_medicoes() -> list:
    if HISTORICO_PATH.exists():
        try:
            return json.loads(HISTORICO_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _salvar_historico_medicoes(lista: list) -> None:
    HISTORICO_PATH.write_text(json.dumps(lista, ensure_ascii=False, indent=2), encoding="utf-8")


historico = _carregar_historico_medicoes()
if not historico:
    st.caption("Nenhuma medição salva ainda. Use o botão abaixo para guardar o resultado atual.")
else:
    for h in historico:
        titulo = f"{h['teste_nome']} · {h['variante_label']} · {h['salvo_em']}"
        with st.expander(titulo):
            st.write(f"**{h['pct_melhor']}** de chance de a {h['variante_label']} ser melhor · lift {h['lift']}")
            st.caption(f"ID do teste: {h['teste_id']} · Período: {h['periodo']} dias · {h['badge_label']}")
            if st.button("🗑️ Remover do histórico", key=f"del_{h['id']}"):
                _salvar_historico_medicoes([x for x in _carregar_historico_medicoes() if x["id"] != h["id"]])
                st.rerun()

if st.button("💾 Salvar esta medição no histórico", use_container_width=True):
    strength = max(r.p_melhor, 1 - r.p_melhor)
    badge_label = "Evidência forte" if strength >= 0.95 else "Evidência moderada" if strength >= 0.80 else "Sem diferença clara"
    novo = {
        "id": int(datetime.now().timestamp() * 1000),
        "teste_id": test["id"],
        "teste_nome": test["nome"],
        "variante_label": variante_label,
        "periodo": periodo,
        "salvo_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "pct_melhor": pct_str,
        "lift": m.fmt_pct_signed(r.lift_medio),
        "badge_label": badge_label,
    }
    lista = [h for h in _carregar_historico_medicoes() if not (h["teste_id"] == novo["teste_id"] and h["variante_label"] == novo["variante_label"] and h["periodo"] == novo["periodo"])]
    lista.insert(0, novo)
    _salvar_historico_medicoes(lista[:25])
    st.success(f"Medição de \"{test['nome']}\" ({variante_label}) salva no histórico.")
    st.rerun()

st.markdown("---")
st.caption("Michelangelo · protótipo · inferência bayesiana rodando localmente no seu computador · dados de medição sintéticos pra demonstração — a ficha do teste vem de verdade do DaVinci.")
