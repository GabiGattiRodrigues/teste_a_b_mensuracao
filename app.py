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

# O app é aberto: qualquer pessoa entra só com o nome. A senha abaixo serve
# unicamente pra desbloquear o painel de admin ("quem já entrou") -- igual ao
# DaVinci. Não é autenticação de verdade (fica em texto puro no código), é só
# uma trava simples pra separar "uso normal" de "modo admin".
SENHA_ADMIN = "teste_a_b_gabi"

COR_CONTROLE = "#9AA9BF"
COR_VARIANTE = "#2A78D6"

st.set_page_config(page_title="Michelangelo — Medição de Teste A/B · A/B test measurement",
                   page_icon=_page_icon, layout="wide")

# --------------------------------------------------------------------------
# Português e inglês. O texto em inglês mora ao lado do português
# (L("Olá", "Hi")); a língua fica no session_state e espelha na URL
# (?lang=en). As contas não mudam de língua.
# --------------------------------------------------------------------------

if "idioma" not in st.session_state:
    _pedido = str(st.query_params.get("lang", "pt")).lower()
    st.session_state["idioma"] = "en" if _pedido.startswith("en") else "pt"


def _trocar_idioma() -> None:
    novo = st.session_state.get("_seletor_idioma")
    if novo in ("pt", "en"):          # clicar de novo na língua ativa desmarca: ignora
        st.session_state["idioma"] = novo
        st.query_params["lang"] = novo


EN = st.session_state["idioma"] == "en"
m.definir_idioma(st.session_state["idioma"])


def L(pt: str, en: str) -> str:
    """A frase na língua ativa."""
    return en if EN else pt


def seletor_idioma() -> None:
    st.session_state["_seletor_idioma"] = st.session_state["idioma"]
    st.segmented_control(
        "Idioma · Language", options=["pt", "en"],
        format_func=lambda k: {"pt": "🇧🇷 Português", "en": "🇺🇸 English"}[k],
        key="_seletor_idioma", on_change=_trocar_idioma,
        label_visibility="collapsed", selection_mode="single")


# --------------------------------------------------------------------------
# Log de "quem já entrou" — por padrão fica só num arquivo local (não
# aparece pra quem acessa de outro computador/celular/instância). Se uma
# planilha Google for configurada nos secrets (veja o README), o log passa
# a ser escrito e lido dali, e aí sim fica igual pra todo mundo, em
# qualquer device. Se a planilha não estiver configurada ou der erro, o
# app volta sozinho a usar o arquivo local — nunca quebra por causa disso.
# (Mesmo mecanismo do DaVinci.)
# --------------------------------------------------------------------------

_SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# _planilha_usuarios() devolve (aba, motivo_do_erro) -- os DOIS presos
# juntos no mesmo cache (@st.cache_resource), em vez de guardar o motivo
# numa variável global à parte. É de propósito: o Streamlit reexecuta o
# script inteiro a cada interação, então uma variável global reatribuída
# no topo do arquivo (ex.: "_ERRO_PLANILHA = None") volta a zero em toda
# rodada nova -- e como a função em si só roda de fato uma vez (o resto
# são acertos de cache), o motivo do erro se perdia assim que a rodada
# seguinte começava (o que acontece sempre que o login dá um st.rerun()).
# Devolvendo os dois juntos, o valor certo vem sempre, cache ou não.
@st.cache_resource(show_spinner=False)
def _planilha_usuarios():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as e:
        return None, f"biblioteca não instalada ({e}) — rode 'pip install -r requirements.txt' de novo."
    try:
        if "gcp_service_account" not in st.secrets or "gsheets_log_url" not in st.secrets:
            return None, "secrets não configurados (faltando gcp_service_account e/ou gsheets_log_url)."
        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]), scopes=_SHEETS_SCOPES
        )
        cliente = gspread.authorize(creds)
        aba = cliente.open_by_url(st.secrets["gsheets_log_url"]).sheet1
        return aba, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _carregar_usuarios_log_local() -> list:
    if USUARIOS_LOG_PATH.exists():
        try:
            return json.loads(USUARIOS_LOG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _carregar_usuarios_log() -> list:
    aba, _ = _planilha_usuarios()
    if aba is not None:
        try:
            return aba.get_all_records()
        except Exception:
            pass
    return _carregar_usuarios_log_local()


def _registrar_usuario(nome: str, admin: bool) -> None:
    quando = datetime.now().strftime("%d/%m/%Y %H:%M")
    tipo = "Administradora" if admin else "Usuário"
    aba, _ = _planilha_usuarios()
    if aba is not None:
        try:
            aba.append_row([nome, quando, tipo])
            return
        except Exception:
            pass
    log = _carregar_usuarios_log_local()
    log.insert(0, {"nome": nome, "quando": quando, "tipo": tipo})
    USUARIOS_LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# Boas-vindas — igual ao DaVinci: mascote + nome + senha, antes de qualquer
# outra coisa aparecer na tela.
# --------------------------------------------------------------------------

if not st.session_state.get("usuario_nome"):
    st.markdown("<div style='height: 48px;'></div>", unsafe_allow_html=True)
    col_boas_a, col_boas_b, col_boas_c = st.columns([1, 2, 1])
    with col_boas_c:
        seletor_idioma()
    with col_boas_b:
        if LOGO_PATH.exists():
            img_col_a, img_col_b, img_col_c = st.columns([1, 1, 1])
            with img_col_b:
                st.image(str(LOGO_PATH), width=140)
        st.markdown(
            f"<h2 style='text-align:center; margin-bottom:4px;'>"
            f"{L('Oi! Eu sou o Michelangelo', 'Hi! I am Michelangelo')} {_DAVI_ICON_TAG}</h2>"
            "<p style='text-align:center; color:#5B6B82; font-size:15px; margin-top:0;'>"
            + L("Vou te ajudar a medir o seu teste A/B que já está rodando, todo explicado em "
                "português simples — sem precisar saber estatística de antemão.",
                "I'll help you measure your A/B test that's already running, all explained in "
                "plain English — no need to know statistics beforehand.") + "</p>",
            unsafe_allow_html=True,
        )
        nome_input = st.text_input(
            L("Antes da gente começar, qual é o seu nome? *", "Before we start, what's your name? *"),
            key="input_boas_vindas_nome", placeholder=L("Seu nome", "Your name"),
        )
        with st.expander(L("Acesso da criadora (opcional)", "Creator access (optional)")):
            senha_input = st.text_input(
                L("Senha de admin", "Admin password"), key="input_boas_vindas_senha", type="password",
                placeholder=L("Só quem cuida do app precisa disso", "Only whoever runs the app needs this"),
            )
        st.caption(L("Campos com \\* são obrigatórios.", "Fields with \\* are required."))
        if st.button(L("Vamos começar →", "Let's start →"), width="stretch", type="primary"):
            if not nome_input.strip():
                st.warning(L("Preciso do seu nome pra continuar 🙂", "I need your name to continue 🙂"))
            else:
                eh_admin = senha_input == SENHA_ADMIN
                st.session_state.usuario_nome = nome_input.strip()
                st.session_state.is_admin = eh_admin
                _registrar_usuario(nome_input.strip(), eh_admin)
                st.rerun()
    st.stop()

_CSS = """
<style>
.chip { display:inline-block; background:#E3EDFB; color:#1F4E8C; border-radius:999px;
        padding:5px 11px; font-size:12.5px; margin:3px 6px 0 0; }
.ficha { background:#EEF4FC; border:1px solid #C9DAF1; border-radius:10px; padding:16px 18px 8px; }
.ficha-header { display:flex; justify-content:space-between; align-items:center;
                font-size:12.5px; font-weight:600; color:#5B6B82; margin-bottom:10px;
                padding-bottom:10px; border-bottom:1px dashed #D6E2F2; }
.ficha-id { font-family: "SFMono-Regular", Menlo, monospace; color:#1B5DB0; background:#fff;
            border:1px solid #D6E2F2; border-radius:6px; padding:2px 8px; }
.banner { border-radius:10px; padding:12px 16px; font-size:14px; margin:10px 0; }
.banner.good { background:#E9F5E1; color:#2C6B1F; }
.banner.warn { background:#FBF0D2; color:#6B4E00; }
.banner.bad  { background:#FBE3DE; color:#9A2A17; }
.banner.info { background:#EAF1FB; color:#1F4E8C; }
.verdict { border-radius:12px; padding:18px 20px; display:flex; gap:14px; align-items:flex-start; }
.verdict.good { background:#E9F5E1; }
.verdict.bad  { background:#FBE3DE; }
.verdict.mid  { background:#FBF0D2; }
.verdict.none { background:#EDF1F7; }
.verdict .icon { font-size:28px; line-height:1; }
.verdict .title { font-size:17px; font-weight:700; color:#0F1E36; margin-bottom:4px; }
.verdict .sub { font-size:13.5px; color:#34445C; line-height:1.5; }
.pg-wrap { display:grid; grid-template-columns: repeat(20, 1fr); gap:3px; max-width:340px; }
.pg-dot { width:100%; aspect-ratio:1; border-radius:50%; }
.pg-dot.win  { background: __VARIANT_COLOR__; }
.pg-dot.lose { background: #C8D3E3; }
.decision-card { border:1px solid #D6E2F2; border-radius:10px; padding:14px 16px; margin-bottom:10px; }
.decision-card.recommended { border-color:#2A78D6; background:#F3F7FD; }
.decision-tag { display:inline-block; background:#2A78D6; color:#fff; font-size:10.5px;
                font-weight:700; border-radius:999px; padding:2px 9px; margin-left:8px; }
.check-hint { font-size:12.5px; color:#5B6B82; margin:2px 0 10px 26px; line-height:1.5; }
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
    st.caption(L(
        "Mede um teste A/B que já está rodando, com inferência bayesiana explicada em português simples. "
        "Cuida só da medição — objetivo, público e meta de amostra são desenhados no "
        "**DaVinci**, e aqui chegam prontos, só para leitura.",
        "Measures an A/B test that's already running, with Bayesian inference explained in plain English. "
        "It only handles measurement — goal, audience and sample target are designed in "
        "**DaVinci**, and arrive here ready, read-only."
    ))
with col_usuario:
    seletor_idioma()
    _selo_admin = (
        " <span style='background:#E3EDFB; color:#1F4E8C; border-radius:999px; padding:2px 9px; "
        "font-size:11px; font-weight:600; margin-left:4px;'>admin</span>"
        if st.session_state.get("is_admin") else ""
    )
    st.markdown(
        f"<div style='text-align:right; padding-top:20px;'>"
        f"<span style='font-size:14px; color:#5B6B82;'>👋 {L('Olá', 'Hi')}, <b>{_esc(st.session_state.usuario_nome)}</b></span>"
        f"{_selo_admin}</div>",
        unsafe_allow_html=True,
    )
    if st.button(L("trocar", "switch"), key="btn_trocar_usuario",
                 help=L("Trocar o nome de quem está usando", "Change the name of who's using it")):
        st.session_state.usuario_nome = None
        st.session_state.is_admin = False
        st.rerun()

with st.expander(L("📖 Como usar o Michelangelo (passo a passo)", "📖 How to use Michelangelo (step by step)"),
                 expanded=False):
    st.markdown(L(
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
        "\"alguém viu as duas versões sem querer?\" só uma olhada manual consegue confirmar.",
        "**1. Pick the test.** In the \"Which test do you want to measure?\" menu just below, pick the "
        "test that's actually running. Its record (BU, metric, campaign, sample target, duration) comes "
        "locked from DaVinci, just for checking — it can't be edited here, on purpose.\n\n"
        "**2. Say how many days it's been running.** In the \"Period\" field, type how many days have "
        "passed since the test actually went live (it doesn't have to be 7, 14 or 30 — any number "
        "works).\n\n"
        "**3. Pick which variant to compare.** If the test has more than two groups, Michelangelo "
        "always compares the Control against **one** challenger at a time — pick which.\n\n"
        "**4. Scroll down, in the order the screen already comes in:** first whether the planned sample "
        "was reached (and what to do if not yet), then who's winning and with what confidence, then a "
        "checklist of things to check by hand, and finally the charts that show this result "
        "visually.\n\n"
        "**5. Before rolling the change out to everyone, go through the checklist.** The tool computes the "
        "statistics on its own, but things like \"was the split between the groups really even?\" or "
        "\"did anyone see both versions by accident?\" only a manual look can confirm."
    ))

with st.expander(L("❓ Como interpretar os resultados", "❓ How to read the results")):
    st.markdown(L(
        "**Por que a probabilidade é sobre \"quantas vezes eu ganharia\", e não um número seco?**\n\n"
        "Cada grupo (Controle e Variante) não tem um resultado fixo — tem uma **nuvem de valores "
        "prováveis** (a \"posterior\"), porque medimos só uma amostra, não todo mundo. Simulamos essa "
        "nuvem **3.000 vezes** (Monte Carlo) e contamos em quantas a Variante ganhou. Se ganhou em "
        "2.850 de 3.000, a probabilidade é 95% — é como jogar uma moeda viciada 3.000 vezes e ver "
        "quantas vezes deu cara.",
        "**Why is the probability about \"how many times I'd win\", and not a single number?**\n\n"
        "Each group (Control and Variant) doesn't have a fixed result — it has a **cloud of likely "
        "values** (the \"posterior\"), because we only measured a sample, not everyone. We simulate that "
        "cloud **3,000 times** (Monte Carlo) and count how many times the Variant won. If it won 2,850 "
        "out of 3,000, the probability is 95% — like flipping a loaded coin 3,000 times and seeing how "
        "many times it came up heads."
    ))
    st.markdown(L(
        "**O que é o \"lift esperado\"?**\n\n"
        "É o tamanho médio da diferença entre a Variante e o Controle, em porcentagem — não só "
        "\"quem ganha\", mas \"por quanto\". Um lift de +8% quer dizer que, na média das simulações, "
        "a Variante veio 8% acima do Controle. Repare que dá pra ter uma probabilidade alta de "
        "vitória (ex.: 96%) com um lift pequeno (ex.: +2%) — a Variante ganha quase sempre, mas por "
        "pouco.",
        "**What is the \"expected lift\"?**\n\n"
        "It's the average size of the difference between the Variant and the Control, in percent — not "
        "just \"who wins\", but \"by how much\". A +8% lift means that, on average across the "
        "simulations, the Variant came in 8% above the Control. Note that you can have a high "
        "probability of winning (e.g. 96%) with a small lift (e.g. +2%) — the Variant almost always "
        "wins, but by a little."
    ))
    st.markdown(L(
        "**O que é o \"intervalo de credibilidade (90%)\"?**\n\n"
        "É a faixa onde o efeito real provavelmente está — em vez de um número seco, um \"tenho quase "
        "certeza que está entre X e Y\". Quanto mais estreita, mais preciso é o palpite (e ela só "
        "fica mais estreita com mais gente medida). Se essa faixa cruzar o zero (ex.: -2% a +9%), "
        "isso é sinal de que ainda não dá pra ter certeza da direção do efeito — a conta completa de "
        "onde ele vem está no \"Ver a conta\" logo abaixo do resultado.",
        "**What is the \"credible interval (90%)\"?**\n\n"
        "It's the range where the real effect most likely is — instead of a single number, an \"I'm "
        "almost sure it's between X and Y\". The narrower it is, the more precise the guess (and it only "
        "gets narrower with more people measured). If that range crosses zero (e.g. -2% to +9%), it's a "
        "sign that you can't be sure of the effect's direction yet — the full math behind it is in "
        "\"See the math\" just below the result."
    ))
    st.markdown(L(
        "**Pra que serve o checklist e os guardrails?**\n\n"
        "São verificações que a estatística sozinha não consegue fazer. O checklist olha coisas como "
        "divisão desigual entre os grupos (SRM), gente vendo as duas versões sem querer (crossover) "
        "ou o efeito sumir depois dos primeiros dias (novidade). Os guardrails são métricas que **não "
        "podem piorar** mesmo se a métrica principal melhorar (ex.: custo por cliente, tempo de "
        "checkout) — servem pra pegar um \"efeito colateral\" ruim escondido atrás de um bom resultado.",
        "**What are the checklist and the guardrails for?**\n\n"
        "They're checks that statistics alone can't do. The checklist looks at things like an uneven "
        "split between groups (SRM), people seeing both versions by accident (crossover) or the effect "
        "fading after the first days (novelty). Guardrails are metrics that **must not get worse** even "
        "if the main metric improves (e.g. cost per customer, checkout time) — they catch a bad "
        "\"side effect\" hidden behind a good result."
    ))
    st.markdown(L(
        "**Por que por usuário único, e não por sessão?**\n\n"
        "Se um usuário voltar 3 vezes, contar por sessão faz ele valer como 3 pessoas — isso infla "
        "a amostra e faz o intervalo de confiança parecer mais estreito (mais preciso) do que "
        "realmente é. Medir por usuário único evita esse engano.",
        "**Why by unique user, and not by session?**\n\n"
        "If a user comes back 3 times, counting by session makes them count as 3 people — that inflates "
        "the sample and makes the interval look narrower (more precise) than it really is. Measuring by "
        "unique user avoids that mistake."
    ))
    st.markdown(L(
        "**De onde vem a meta de amostra e o prazo que aparecem na ficha do teste?**\n\n"
        "Vêm prontos do **DaVinci** — é lá que se decide o objetivo, a métrica, o público e quantas "
        "pessoas por grupo o teste precisa. O Michelangelo só lê isso (nunca deixa editar) e "
        "acompanha se a medição de hoje já bateu essa meta.",
        "**Where do the sample target and the duration on the test record come from?**\n\n"
        "They come ready from **DaVinci** — that's where the goal, the metric, the audience and how many "
        "people per group the test needs are decided. Michelangelo only reads them (never lets you edit) "
        "and tracks whether today's measurement has hit that target."
    ))
    st.markdown(L(
        "**E se a amostra planejada ainda não foi atingida?**\n\n"
        "O Michelangelo calcula três caminhos possíveis — prorrogar o teste, aceitar menos confiança "
        "e decidir com o que já tem, ou parar sem decidir — e marca qual costuma fazer mais sentido "
        "pro seu caso, na seção \"Amostra planejada × observada\".",
        "**And if the planned sample hasn't been reached yet?**\n\n"
        "Michelangelo works out three possible paths — extend the test, accept less confidence and "
        "decide with what you have, or stop without deciding — and marks which one usually makes the "
        "most sense for your case, in the \"Planned × observed sample\" section."
    ))

if st.session_state.get("is_admin"):
    st.divider()
    st.subheader(L("👥 Usuários que já entraram no Michelangelo", "👥 Users who have entered Michelangelo"))
    _aba_usuarios, _erro_planilha = _planilha_usuarios()
    _usando_planilha = _aba_usuarios is not None
    _usuarios = _carregar_usuarios_log()

    if not _usando_planilha:
        if _erro_planilha:
            st.caption(L(
                "⚠️ Não consegui usar a planilha Google — caiu pro arquivo local (só desta instância). "
                "Motivo:",
                "⚠️ I couldn't use the Google Sheet — fell back to the local file (this instance only). "
                "Reason:"
            ))
            st.code(_erro_planilha, language=None)
        else:
            st.caption(L(
                "Planilha Google não configurada — usando o arquivo local (só desta instância). "
                "Veja o README, seção \"Ver quem usou o Michelangelo em qualquer dispositivo\".",
                "Google Sheet not configured — using the local file (this instance only). "
                "See the README, section \"Ver quem usou o Michelangelo em qualquer dispositivo\"."
            ))

    if not _usuarios:
        st.caption(L("Ninguém entrou ainda.", "Nobody has entered yet."))
    else:
        if _usando_planilha:
            st.caption(L(
                f"{len(_usuarios)} entrada(s) — vindas da planilha compartilhada "
                "(conta quem entrou em qualquer computador, celular ou instância que usa essa planilha).",
                f"{len(_usuarios)} entry(ies) — from the shared sheet "
                "(counts whoever entered from any computer, phone or instance that uses this sheet)."
            ))
        else:
            st.caption(L(f"{len(_usuarios)} entrada(s) registrada(s) só nesta instância.",
                         f"{len(_usuarios)} entry(ies) recorded on this instance only."))
        _tipos_en = {"Administradora": "Admin", "Usuário": "User"}
        st.table([
            {
                L("Nome", "Name"): u.get("nome", ""),
                L("Quando", "When"): u.get("quando", ""),
                L("Tipo", "Type"): (lambda t: _tipos_en.get(t, t) if EN else t)(
                    u.get("tipo") or ("Administradora" if u.get("admin") else "Usuário")),
            }
            for u in _usuarios
        ])

st.divider()

# --------------------------------------------------------------------------
# Seleção do teste — vem do DaVinci, cadastro 100% somente-leitura
# --------------------------------------------------------------------------

st.subheader(L("Qual teste você quer medir?", "Which test do you want to measure?"))

testes, usando_exemplo = db.carregar_testes_registrados(st.session_state["idioma"])

if usando_exemplo:
    st.info(L(
        "📋 Nenhum teste salvo ainda no histórico do DaVinci — mostrando **exemplos** no formato real "
        "que o DaVinci gera (mesmo ID, mesma ficha). Assim que você salvar um teste de verdade no "
        "DaVinci (botão \"💾 Salvar este teste no histórico\"), ele aparece aqui automaticamente.",
        "📋 No test saved in DaVinci's history yet — showing **examples** in the real format "
        "DaVinci generates (same ID, same record). As soon as you save a real test in DaVinci "
        "(the \"💾 Save this test to the history\" button), it shows up here automatically.",
    ))

# A opção é o ID (não muda com a língua); o rótulo mostra ID · nome.
opcoes = {t["id"]: t for t in testes}
escolha = st.selectbox(L("Teste cadastrado no DaVinci", "Test registered in DaVinci"), list(opcoes.keys()),
                       key="sel_teste_id", format_func=lambda i: f"{i} · {opcoes[i]['nome']}")
test = opcoes[escolha]

metric_cfg = db.derive_metric_cfg(test)

# Ficha 100% somente-leitura -- nada aqui é um widget editável de propósito:
# BU, métrica, região, campanha, plataforma, dispositivo, objetivo, meta de
# amostra e prazo são cadastro do DaVinci, não algo que se mude por aqui.
chips = "".join(
    f'<span class="chip">{_esc(rotulo)}: {_esc(str(valor))}</span>'
    for rotulo, valor in [
        ("BU", test["bu"]), (L("Métrica", "Metric"), test["metrica_nome"]), (L("Região", "Region"), test["regiao"]),
        (L("Campanha", "Campaign"), test["campanha"]), (L("Plataforma", "Platform"), test["plataforma"]),
        (L("Dispositivo", "Device"), test["dispositivo"]),
    ]
)
meta_fmt = m.fmt_int(test["target_per_group"])
prazo_fmt = f"{test['planned_days']} {L('dias', 'days')}"
st.markdown(
    f'<div class="ficha">'
    f'<div class="ficha-header"><span>{L("🔒 Cadastrado no DaVinci — não editável aqui", "🔒 Registered in DaVinci — not editable here")}</span>'
    f'<span class="ficha-id">{_esc(test["id"])}</span></div>'
    f'<div style="font-size:13.5px; color:#34445C; margin-bottom:10px;">'
    f'<strong>{_esc(test.get("objetivo") or L("Objetivo não preenchido no DaVinci", "Goal not filled in on DaVinci"))}</strong>'
    f'{" · " + _esc(test["area"]) if test.get("area") else ""}</div>'
    f'{chips}'
    f'<div style="margin-top:12px; display:flex; gap:24px; font-size:13px; color:#5B6B82;">'
    f'<span>🎯 {L("Meta de amostra", "Sample target")}: <strong style="color:#0F1E36;">{meta_fmt} {L("por grupo", "per group")}</strong></span>'
    f'<span>🗓️ {L("Prazo planejado", "Planned duration")}: <strong style="color:#0F1E36;">{prazo_fmt}</strong></span>'
    f'</div></div>',
    unsafe_allow_html=True,
)

st.caption(L(
    "Michelangelo compara duas variantes por vez. Com 3 grupos ou mais cadastrados no DaVinci, "
    "ele ajusta a confiança sozinho (correção de Bonferroni) — aqui, sempre controle × 1 desafiante.",
    "Michelangelo compares two variants at a time. With 3 or more groups registered in DaVinci, "
    "it adjusts the confidence on its own (Bonferroni correction) — here, always control × 1 challenger."
))

col_periodo, col_variante = st.columns(2)
with col_periodo:
    periodo = st.number_input(
        L("Período (rodando há)", "Period (running for)"), min_value=1, max_value=180, value=14, step=1,
        key="periodo",
        help=L("Quantos dias esse teste já está rodando — digite o número, não precisa ser um valor redondo.",
               "How many days this test has been running — type the number, it doesn't need to be round."),
    )
    st.caption(f"{periodo} {L('dia(s)', 'day(s)')}")
with col_variante:
    variante = st.radio(
        L("Comparando controle (A) com", "Comparing control (A) with"), test["variantes"], index=0,
        horizontal=True, key="variante",
        format_func=lambda v: f"{L('Variante', 'Variant')} {v}",
    )

st.divider()

# --------------------------------------------------------------------------
# Cálculo
# --------------------------------------------------------------------------

r = m.computar(test, metric_cfg, periodo, variante)
guard_names = db.guardrails_para_bu(test["bu"])
guard_itens, guard_risco, guard_total = m.guardrails_status(guard_names, r.rng)
r.guardrail_risco, r.guardrail_total = guard_risco, guard_total
variante_label = f"{L('Variante', 'Variant')} {variante}"
_controle = L("Controle", "Control")

# --------------------------------------------------------------------------
# Amostra planejada (DaVinci) x observada (Michelangelo)
# --------------------------------------------------------------------------

st.subheader(L("Amostra planejada × observada", "Planned × observed sample"))

status = m.status_amostra(test, r)
c1, c2, c3 = st.columns(3)
c1.metric(L("Usuários por grupo (o menor dos 2)", "Users per group (the smaller of the 2)"),
          m.fmt_int(status["observed"]),
          f"{L('de', 'of')} {m.fmt_int(status['target'])} {L('planejados', 'planned')}")
c2.metric(L("Dias rodando", "Days running"), f"{r.dias}",
          f"{L('de', 'of')} {status['planned_days']} {L('planejados', 'planned')}")
c3.metric(L("% da meta atingida", "% of target reached"), f"{min(100, status['pct_sample']*100):.0f}%")
st.progress(min(1.0, status["pct_sample"]))

if status["reached_sample"]:
    st.markdown('<div class="banner good">' + L(
        f'✅ Meta de amostra atingida — {m.fmt_int(status["observed"])} usuários por grupo. Dá pra confiar no resultado abaixo e decidir agora.',
        f'✅ Sample target reached — {m.fmt_int(status["observed"])} users per group. You can trust the result below and decide now.')
        + '</div>', unsafe_allow_html=True)
elif not status["past_deadline"]:
    faltam = m.fmt_int(status["remaining"])
    dias_txt = (f"{status['extra_days_needed']} {L('dia(s)', 'day(s)')}" if status["extra_days_needed"] is not None
                else L("alguns dias", "a few days"))
    st.markdown('<div class="banner warn">' + L(
        f'⏳ Ainda faltam cerca de {faltam} usuários por grupo pra bater a meta — no ritmo atual, isso é mais ou menos {dias_txt}. Como ainda está dentro do prazo planejado ({status["planned_days"]} dias), o recomendado é continuar rodando antes de decidir.',
        f'⏳ About {faltam} users per group are still missing to hit the target — at the current pace, that\'s roughly {dias_txt}. Since it\'s still within the planned duration ({status["planned_days"]} days), the recommendation is to keep running before deciding.')
        + '</div>', unsafe_allow_html=True)
else:
    faltam = m.fmt_int(status["remaining"])
    st.markdown('<div class="banner warn">' + L(
        f'⚠️ O prazo planejado ({status["planned_days"]} dias) já passou e a amostra ainda não bateu a meta — faltam cerca de {faltam} usuários por grupo. Isso costuma acontecer quando o tráfego real veio mais baixo do que o esperado no desenho do teste.',
        f'⚠️ The planned duration ({status["planned_days"]} days) has passed and the sample still hasn\'t hit the target — about {faltam} users per group are missing. This usually happens when real traffic came in lower than expected in the test design.')
        + '</div>', unsafe_allow_html=True)
    st.markdown(L("**O que fazer agora?**", "**What to do now?**"))
    opcoes_dec = m.opcoes_decisao(r, status)
    cols_dec = st.columns(3)
    for col, o in zip(cols_dec, opcoes_dec):
        tag = (f'<span class="decision-tag">{L("recomendado aqui", "recommended here")}</span>'
               if o["recomendado"] else "")
        col.markdown(
            f'<div class="decision-card{" recommended" if o["recomendado"] else ""}">'
            f'<div style="font-weight:700; margin-bottom:6px;">{o["titulo"]}{tag}</div>'
            f'<div style="font-size:12.5px; color:#34445C;">{o["desc"]}</div></div>',
            unsafe_allow_html=True,
        )

st.divider()

# --------------------------------------------------------------------------
# Resultado bayesiano
# --------------------------------------------------------------------------

st.subheader(L(f"Como o Grupo {variante} está indo contra o Controle — {metric_cfg['label']}",
               f"How Group {variante} is doing against the Control — {metric_cfg['label']}"))

_usuarios_em = L("usuários únicos em", "unique users in")
tile_c, tile_v = st.columns(2)
tile_c.metric(L("Grupo A · Controle", "Group A · Control"), m.fmt_metric(r.post_controle.summary_value, metric_cfg),
              f"{m.fmt_int(r.dados.sess_c)} {_usuarios_em} {r.dias} {L('dias', 'days')}")
tile_v.metric(f"{L('Grupo', 'Group')} {variante} · {L('Variante', 'Variant')}",
              m.fmt_metric(r.post_variante.summary_value, metric_cfg),
              f"{m.fmt_int(r.dados.sess_v)} {_usuarios_em} {r.dias} {L('dias', 'days')}")

v = m.veredito(r.p_melhor, variante_label)
st.markdown(
    f'<div class="verdict {v["classe"]}"><div class="icon">{v["icone"]}</div>'
    f'<div><div class="title">{_esc(v["titulo"])}</div><div class="sub">{_esc(v["sub"])}</div></div></div>',
    unsafe_allow_html=True,
)

st.markdown(L(f"##### Qual é a probabilidade de a **{variante_label}** ser melhor que o Controle?",
              f"##### What's the probability that **{variante_label}** is better than the Control?"))
pct_str = m._dec(f"{r.p_melhor*100:.1f}") + "%"


def _badge(p: float) -> str:
    forca = max(p, 1 - p)
    if forca >= 0.95:
        return L("Evidência forte", "Strong evidence")
    if forca >= 0.80:
        return L("Evidência moderada", "Moderate evidence")
    return L("Sem diferença clara", "No clear difference")


col_fig, col_pg = st.columns([1, 2])
with col_fig:
    st.markdown(f"<div style='font-size:52px; font-weight:800; color:#1B5DB0; font-family:monospace;'>{pct_str}</div>", unsafe_allow_html=True)
    badge_label = _badge(r.p_melhor)
    side = (L(f"{variante_label} à frente", f"{variante_label} ahead") if r.p_melhor >= 0.5
            else L("Controle à frente", "Control ahead"))
    st.caption(f"**{badge_label}** · {side}")
with col_pg:
    wins = round(r.p_melhor * 100)
    dots = "".join(f'<span class="pg-dot {"win" if i < wins else "lose"}"></span>' for i in range(100))
    st.markdown(f'<div class="pg-wrap">{dots}</div>', unsafe_allow_html=True)
    losses = 100 - wins
    if wins <= 1:
        frase = L(f"Se desse pra repetir esse teste 100 vezes do zero, a {variante_label} praticamente **nunca** sairia na frente.",
                  f"If we could repeat this test 100 times from scratch, {variante_label} would practically **never** come out ahead.")
    elif wins >= 99:
        frase = L(f"Se desse pra repetir esse teste 100 vezes do zero, a {variante_label} sairia na frente em **quase todas**.",
                  f"If we could repeat this test 100 times from scratch, {variante_label} would come out ahead in **almost all of them**.")
    else:
        frase = L(f"Se desse pra repetir esse teste **100 vezes do zero**, a {variante_label} sairia na frente em cerca de **{wins} delas**, e o Controle nas outras **{losses}** — como as bolinhas acima.",
                  f"If we could repeat this test **100 times from scratch**, {variante_label} would come out ahead in about **{wins} of them**, and the Control in the other **{losses}** — like the dots above.")
    st.caption(frase)

col_lift, col_ci = st.columns(2)
col_lift.metric(L("Lift esperado", "Expected lift"), m.fmt_pct_signed(r.lift_medio),
                L("menor é melhor", "lower is better") if metric_cfg["direction"] == "down"
                else L("maior é melhor", "higher is better"))
col_ci.metric(L("Intervalo de credibilidade (90%)", "Credible interval (90%)"),
              f"{m.fmt_pct_signed(r.ci_baixo)} {L('a', 'to')} {m.fmt_pct_signed(r.ci_alto)}")
st.caption(L(
    f"🍬 Pense assim: é como adivinhar quantos doces tem num pote sem contar um por um. A gente não crava "
    f"um número único — diz **\"tenho quase certeza que o efeito real está entre {m.fmt_pct_signed(r.ci_baixo)} "
    f"e {m.fmt_pct_signed(r.ci_alto)}\"**. Quanto mais estreita essa faixa, mais preciso é o palpite — e ela só "
    f"fica mais estreita com mais gente medida. Os números de onde ela vem estão em \"Ver a conta\" abaixo.",
    f"🍬 Think of it like guessing how many candies are in a jar without counting them one by one. We don't "
    f"commit to a single number — we say **\"I'm almost sure the real effect is between {m.fmt_pct_signed(r.ci_baixo)} "
    f"and {m.fmt_pct_signed(r.ci_alto)}\"**. The narrower that range, the more precise the guess — and it only "
    f"gets narrower with more people measured. The numbers behind it are in \"See the math\" below."
))

split_total = r.dados.sess_c + r.dados.sess_v
split_c = r.dados.sess_c / split_total
if abs(split_c - 0.5) > 0.035:
    pct_c_fmt = m._dec(f"{split_c*100:.1f}")
    pct_v_fmt = m._dec(f"{(1-split_c)*100:.1f}")
    st.markdown('<div class="banner warn">' + L(
        f'⚠️ Possível desbalanceamento na divisão dos grupos (SRM) — controle recebeu {pct_c_fmt}% dos usuários, variante {pct_v_fmt}%.',
        f'⚠️ Possible imbalance in the group split (SRM) — control got {pct_c_fmt}% of users, variant {pct_v_fmt}%.')
        + '</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="banner good">' + L(
        '✅ Nenhum aviso — a divisão de usuários entre os grupos está dentro do esperado (~50/50).',
        '✅ No warning — the split of users between the groups is within what\'s expected (~50/50).')
        + '</div>', unsafe_allow_html=True)

with st.expander(L(f"🧮 Ver a conta por trás dos {pct_str}", f"🧮 See the math behind the {pct_str}")):
    if metric_cfg["type"] == "proporcao":
        rate_c = r.dados.conv_c / r.dados.sess_c
        rate_v = r.dados.conv_v / r.dados.sess_v
        st.markdown(L("**Passo 1 — o que a gente observou** (usuários únicos, não sessões):",
                      "**Step 1 — what we observed** (unique users, not sessions):"))
        _conv = L("conversões em", "conversions in")
        _usu = L("usuários", "users")
        st.code(
            f"{_controle}: {m.fmt_int(r.dados.conv_c)} {_conv} {m.fmt_int(r.dados.sess_c)} {_usu} → {rate_c*100:.2f}%\n"
            f"{L('Variante', 'Variant')}: {m.fmt_int(r.dados.conv_v)} {_conv} {m.fmt_int(r.dados.sess_v)} {_usu} → {rate_v*100:.2f}%",
            language=None,
        )
        st.markdown(L("**Passo 2 — a \"nuvem de chances\" de cada grupo** (modelo Beta-Binomial, α = 1 + conversões, β = 1 + não-conversões):",
                      "**Step 2 — each group's \"cloud of chances\"** (Beta-Binomial model, α = 1 + conversions, β = 1 + non-conversions):"))
        _centro = L("centro", "center")
        st.code(
            f"{_controle} → α={r.post_controle.alpha:.0f}  β={r.post_controle.beta:.0f}  → {_centro} ≈ {r.post_controle.mean*100:.2f}%\n"
            f"{L('Variante', 'Variant')} → α={r.post_variante.alpha:.0f}  β={r.post_variante.beta:.0f}  → {_centro} ≈ {r.post_variante.mean*100:.2f}%",
            language=None,
        )
    else:
        st.markdown(L("**Passo 1 — média diária observada em cada grupo:**",
                      "**Step 1 — daily average observed in each group:**"))
        _media = L("média", "mean")
        _ep = L("erro padrão", "standard error")
        st.code(
            f"{_controle} → {_media} ≈ {m.fmt_metric(r.post_controle.mean, metric_cfg)}  ({_ep} ≈ {r.post_controle.se:.3f})\n"
            f"{L('Variante', 'Variant')} → {_media} ≈ {m.fmt_metric(r.post_variante.mean, metric_cfg)}  ({_ep} ≈ {r.post_variante.se:.3f})",
            language=None,
        )
    nsim_fmt = m.fmt_int(r.nsim)
    vitorias_fmt = m.fmt_int(round(r.p_melhor * r.nsim))
    st.markdown(L(
        f"**Passo 3 — Monte Carlo:** sorteamos **{nsim_fmt}** valores possíveis de cada nuvem e contamos "
        f"em quantos a Variante venceu o Controle. Nessa simulação, venceu em **{vitorias_fmt}** "
        f"de {nsim_fmt} → **{pct_str}**.",
        f"**Step 3 — Monte Carlo:** we draw **{nsim_fmt}** possible values from each cloud and count "
        f"how many times the Variant beat the Control. In this simulation, it won **{vitorias_fmt}** "
        f"out of {nsim_fmt} → **{pct_str}**."
    ))
    st.markdown(L(
        f"**Passo 4 — lift:** em cada uma das {nsim_fmt} simulações calculamos a diferença relativa "
        f"(Variante − Controle) ÷ Controle. A média dessas {nsim_fmt} diferenças é o lift esperado "
        f"({m.fmt_pct_signed(r.lift_medio)}).",
        f"**Step 4 — lift:** in each of the {nsim_fmt} simulations we compute the relative difference "
        f"(Variant − Control) ÷ Control. The average of those {nsim_fmt} differences is the expected lift "
        f"({m.fmt_pct_signed(r.lift_medio)})."
    ))
    pos_baixo_fmt = m.fmt_int(int(r.nsim * 0.05))
    pos_alto_fmt = m.fmt_int(int(r.nsim * 0.95))
    st.markdown(L(
        f"**Passo 5 — de onde vem o intervalo de credibilidade (90%):** pegamos essas {nsim_fmt} diferenças "
        f"e colocamos em ordem, da menor pra maior — como uma fila. A que fica na posição "
        f"{pos_baixo_fmt} (5% do início da fila) é o limite de baixo: **{m.fmt_pct_signed(r.ci_baixo)}**. A que "
        f"fica na posição {pos_alto_fmt} (95% do início da fila) é o limite de cima: **{m.fmt_pct_signed(r.ci_alto)}**. "
        f"Isso quer dizer: em **90% das {nsim_fmt} simulações**, a diferença caiu entre esses dois valores — "
        f"cortamos os 5% de simulações mais pessimistas e os 5% mais otimistas de cada ponta, e ficamos com "
        f"a faixa \"do meio\", que é onde o efeito real provavelmente está.",
        f"**Step 5 — where the credible interval (90%) comes from:** we take those {nsim_fmt} differences "
        f"and sort them, smallest to largest — like a queue. The one at position {pos_baixo_fmt} (5% from "
        f"the start of the queue) is the lower bound: **{m.fmt_pct_signed(r.ci_baixo)}**. The one at position "
        f"{pos_alto_fmt} (95% from the start) is the upper bound: **{m.fmt_pct_signed(r.ci_alto)}**. "
        f"That means: in **90% of the {nsim_fmt} simulations**, the difference fell between those two values — "
        f"we cut the 5% most pessimistic and 5% most optimistic simulations at each end, and keep the "
        f"\"middle\" range, which is where the real effect most likely is."
    ))

st.divider()

# --------------------------------------------------------------------------
# Checklist de contaminação
# --------------------------------------------------------------------------

st.subheader(L("✅ Antes de aplicar de verdade: checklist", "✅ Before rolling it out for real: checklist"))
st.caption(L("Isso aqui a ferramenta não consegue confirmar sozinha — vale olhar manualmente antes de confiar 100% no resultado.",
             "The tool can't confirm these on its own — worth checking by hand before trusting the result 100%."))

hints = m.checklist_hints(r)
checked = 0
for item in m.CHECKLIST_ITEMS:
    key = f"chk_{item['key']}_{test['id']}_{periodo}_{variante}"
    titulo_item, tecnico = m.item_checklist(item)
    val = st.checkbox(f"{titulo_item}  *(​{tecnico})*", key=key)
    st.markdown(f'<div class="check-hint">{hints[item["key"]]}</div>', unsafe_allow_html=True)
    if val:
        checked += 1

total_itens = len(m.CHECKLIST_ITEMS)
st.caption(L(f"{checked} de {total_itens} confirmados", f"{checked} of {total_itens} confirmed"))
if checked == total_itens:
    st.markdown('<div class="banner good">' + L(
        '✅ Checklist completo — pode confiar nesse resultado e aplicar a mudança.',
        '✅ Checklist complete — you can trust this result and roll out the change.') + '</div>',
        unsafe_allow_html=True)
else:
    st.markdown('<div class="banner warn">' + L(
        f'Faltam {total_itens - checked} item(ns) pra confirmar antes de aplicar essa mudança de verdade.',
        f'{total_itens - checked} item(s) left to confirm before rolling out this change for real.') + '</div>',
        unsafe_allow_html=True)

st.divider()

# --------------------------------------------------------------------------
# Gráficos
# --------------------------------------------------------------------------

st.subheader(L("Distribuições posteriores", "Posterior distributions"))

_media_c_fmt = m.fmt_metric(r.post_controle.mean, metric_cfg)
_media_v_fmt = m.fmt_metric(r.post_variante.mean, metric_cfg)
st.markdown('<div class="banner info">' + L(
    f'🎯 <strong>Como ler esse gráfico, de um jeito bem simples:</strong> imagine que você '
    f'não sabe o valor exato de cada grupo — só tem <em>palpites</em>, uns mais prováveis que outros. Cada curva '
    f'colorida é o "mapa de palpites" de um grupo: onde ela fica mais alta (o topo do montinho), é o valor que a '
    f'gente acha mais provável de ser o valor real; onde ela é baixinha, nas pontas, é um valor bem improvável. '
    f'Nesse caso, o topo do montinho cinza (Controle) fica perto de <strong>{_esc(_media_c_fmt)}</strong>, e o do '
    f'azul (Variante) perto de <strong>{_esc(_media_v_fmt)}</strong>. Quando os dois montinhos ficam bem '
    f'separados um do outro (pouca área onde eles se sobrepõem), é sinal forte de que os grupos são mesmo '
    f'diferentes — quando ficam quase um em cima do outro, ainda não dá pra ter certeza de qual é melhor.',
    f'🎯 <strong>How to read this chart, the simple way:</strong> imagine you don\'t know each group\'s exact '
    f'value — you only have <em>guesses</em>, some more likely than others. Each colored curve is a group\'s '
    f'"map of guesses": where it\'s highest (the top of the hill) is the value we think is most likely to be the '
    f'real one; where it\'s low, at the ends, is a very unlikely value. Here, the top of the grey hill (Control) '
    f'is near <strong>{_esc(_media_c_fmt)}</strong>, and the blue one (Variant) near '
    f'<strong>{_esc(_media_v_fmt)}</strong>. When the two hills are well apart (little overlapping area), it\'s a '
    f'strong sign that the groups really are different — when they sit almost on top of each other, you can\'t '
    f'be sure yet which one is better.') + '</div>',
    unsafe_allow_html=True,
)
st.caption(L(
    "Eixo de baixo: o valor possível da métrica. Eixo da esquerda (\"densidade\"): o quão provável é aquele valor — não é uma quantidade de pessoas, é só a altura do \"mapa de palpites\".",
    "Bottom axis: the metric's possible value. Left axis (\"density\"): how likely that value is — it's not a number of people, just the height of the \"map of guesses\"."
))

_nome_var = f"{L('Variante', 'Variant')} {variante}"
_layout_base = dict(plot_bgcolor="white", paper_bgcolor="white", separators=".," if EN else ",.")
fig_dens = go.Figure()
if metric_cfg["type"] == "proporcao":
    def _beta_sd(post):
        a, b = post.alpha, post.beta
        return ((a * b) / ((a + b) ** 2 * (a + b + 1))) ** 0.5
    sd_c, sd_v = _beta_sd(r.post_controle), _beta_sd(r.post_variante)
    lo = max(0.0001, min(r.post_controle.mean - 5 * sd_c, r.post_variante.mean - 5 * sd_v))
    hi = min(0.9999, max(r.post_controle.mean + 5 * sd_c, r.post_variante.mean + 5 * sd_v))
    xs = [lo + (hi - lo) * i / 999 for i in range(1000)]
    for post, nome, cor in [(r.post_controle, _controle, COR_CONTROLE), (r.post_variante, _nome_var, COR_VARIANTE)]:
        ys = [m.beta_pdf(x, post.alpha, post.beta) for x in xs]
        fig_dens.add_trace(go.Scatter(x=[x * 100 for x in xs], y=ys, mode="lines", name=nome, line=dict(color=cor, width=2.5), fill="tozeroy"))
    fig_dens.update_xaxes(title_text=L("taxa (%)", "rate (%)"))
else:
    lo = min(r.post_controle.mean - 4 * r.post_controle.se, r.post_variante.mean - 4 * r.post_variante.se)
    hi = max(r.post_controle.mean + 4 * r.post_controle.se, r.post_variante.mean + 4 * r.post_variante.se)
    xs = [lo + (hi - lo) * i / 999 for i in range(1000)]
    for post, nome, cor in [(r.post_controle, _controle, COR_CONTROLE), (r.post_variante, _nome_var, COR_VARIANTE)]:
        ys = [m.normal_pdf(x, post.mean, post.se) for x in xs]
        fig_dens.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name=nome, line=dict(color=cor, width=2.5), fill="tozeroy"))
    fig_dens.update_xaxes(title_text=metric_cfg.get("unit") or L("valor", "value"))
fig_dens.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10), yaxis_title=L("densidade", "density"),
                       legend=dict(orientation="h", y=1.1), **_layout_base)
st.plotly_chart(fig_dens, width="stretch")

st.subheader(L("Evolução no período", "Trend over the period"))
dias_x = [d.dia + 1 for d in r.dados.dias]
_escala = 100 if metric_cfg["type"] == "proporcao" else 1
fig_metric = go.Figure()
fig_metric.add_trace(go.Scatter(x=dias_x, y=[d.valor_controle * _escala for d in r.dados.dias], mode="lines+markers", name=_controle, line=dict(color=COR_CONTROLE, width=2.5)))
fig_metric.add_trace(go.Scatter(x=dias_x, y=[d.valor_variante * _escala for d in r.dados.dias], mode="lines+markers", name=_nome_var, line=dict(color=COR_VARIANTE, width=2.5)))
fig_metric.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), xaxis_title=L("dia do teste", "test day"),
                         yaxis_title=metric_cfg["label"], legend=dict(orientation="h", y=1.15), **_layout_base)
st.plotly_chart(fig_metric, width="stretch")

fig_sess = go.Figure()
fig_sess.add_trace(go.Bar(x=dias_x, y=[d.sessoes_controle for d in r.dados.dias], name=_controle, marker_color=COR_CONTROLE))
fig_sess.add_trace(go.Bar(x=dias_x, y=[d.sessoes_variante for d in r.dados.dias], name=_nome_var, marker_color=COR_VARIANTE))
fig_sess.update_layout(barmode="group", height=280, margin=dict(l=10, r=10, t=10, b=10),
                       xaxis_title=L("dia do teste", "test day"), yaxis_title=L("usuários únicos", "unique users"),
                       legend=dict(orientation="h", y=1.15), **_layout_base)
st.plotly_chart(fig_sess, width="stretch")

st.divider()

# --------------------------------------------------------------------------
# Guardrails
# --------------------------------------------------------------------------

st.subheader(L("🚧 O que não pode piorar (guardrails)", "🚧 What must not get worse (guardrails)"))
if guard_itens:
    for g in guard_itens:
        status_txt = L("⚠ atenção", "⚠ watch out") if g["risco"] else L("✓ estável", "✓ stable")
        cor = "#9A2A17" if g["risco"] else "#2C6B1F"
        st.markdown(f'<div style="display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #D6E2F2;"><span>{_esc(m.nome_guardrail(g["nome"]))}</span><span style="color:{cor}; font-weight:600;">{status_txt} · {m.fmt_pct_signed(g["delta_pct"]/100)}</span></div>', unsafe_allow_html=True)
else:
    st.caption(L("Nenhum guardrail definido para essa BU.", "No guardrail defined for this BU."))

st.divider()

# --------------------------------------------------------------------------
# Histórico de medições (próprio do Michelangelo -- separado do
# historico_testes.json do DaVinci, que é sobre DESENHO, não medição)
# --------------------------------------------------------------------------

st.subheader(L("📁 Histórico de medições salvas", "📁 Saved measurement history"))


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
    st.caption(L("Nenhuma medição salva ainda. Use o botão abaixo para guardar o resultado atual.",
                 "No measurement saved yet. Use the button below to keep the current result."))
else:
    for h in historico:
        titulo = f"{h['teste_nome']} · {h['variante_label']} · {h['salvo_em']}"
        with st.expander(titulo):
            st.write(L(f"**{h['pct_melhor']}** de chance de a {h['variante_label']} ser melhor · lift {h['lift']}",
                       f"**{h['pct_melhor']}** chance that {h['variante_label']} is better · lift {h['lift']}"))
            st.caption(L(f"ID do teste: {h['teste_id']} · Período: {h['periodo']} dias · {h['badge_label']}",
                         f"Test ID: {h['teste_id']} · Period: {h['periodo']} days · {h['badge_label']}"))
            if st.button(L("🗑️ Remover do histórico", "🗑️ Remove from history"), key=f"del_{h['id']}"):
                _salvar_historico_medicoes([x for x in _carregar_historico_medicoes() if x["id"] != h["id"]])
                st.rerun()

if st.button(L("💾 Salvar esta medição no histórico", "💾 Save this measurement to the history"), width="stretch"):
    novo = {
        "id": int(datetime.now().timestamp() * 1000),
        "teste_id": test["id"],
        "teste_nome": test["nome"],
        "variante_label": variante_label,
        "periodo": periodo,
        "salvo_em": datetime.now().strftime(L("%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M")),
        "pct_melhor": pct_str,
        "lift": m.fmt_pct_signed(r.lift_medio),
        "badge_label": _badge(r.p_melhor),
    }
    lista = [h for h in _carregar_historico_medicoes() if not (h["teste_id"] == novo["teste_id"] and h["variante_label"] == novo["variante_label"] and h["periodo"] == novo["periodo"])]
    lista.insert(0, novo)
    _salvar_historico_medicoes(lista[:25])
    st.success(L(f"Medição de \"{test['nome']}\" ({variante_label}) salva no histórico.",
                 f"Measurement of \"{test['nome']}\" ({variante_label}) saved to the history."))
    st.rerun()

st.markdown("---")
st.caption(L(
    "Michelangelo · protótipo · inferência bayesiana rodando localmente no seu computador · dados de medição sintéticos pra demonstração — a ficha do teste vem de verdade do DaVinci.",
    "Michelangelo · prototype · Bayesian inference running locally on your computer · synthetic measurement data for demonstration — the test record really comes from DaVinci."
))
