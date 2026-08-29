# Michelangelo — medição de teste A/B

Michelangelo é o companheiro do **DaVinci**: enquanto o DaVinci cuida do
**desenho** de um teste A/B (objetivo, público, tamanho de amostra, prazo),
o Michelangelo cuida da **medição** — acompanhar um teste que já está
rodando e dizer, em português simples, se ele funcionou ou não.

Ele roda 100% no seu computador (nada é enviado pra internet) e explica
cada conceito estatístico como se fosse pra alguém que nunca estudou
estatística: o que significa "97,3% de chance de a Variante ser melhor",
de onde vem esse número, e o que fazer se a amostra planejada ainda não
foi atingida.

## Como rodar

1. Confirme que o Python está instalado (baixe em
   [python.org/downloads](https://www.python.org/downloads/) se não
   estiver — marque "Add Python to PATH" na instalação).
2. Dê duplo clique em **`rodar_michelangelo.bat`**.
3. Na primeira vez, ele instala sozinho o que precisa (`streamlit`,
   `plotly`) — pode demorar um minuto. Depois disso abre direto no
   navegador.
4. Pra fechar, volte na janela preta que abriu e aperte `Ctrl+C`.

## Como ele conversa com o DaVinci

Isso é o coração do Michelangelo: **ele não tem cadastro próprio de
testes**. Ao abrir, ele lê o arquivo `historico_testes.json` que o
DaVinci grava quando alguém clica em "💾 Salvar este teste no histórico"
lá — e usa exatamente essa fonte (nome, ID, meta de amostra, prazo, BU,
métrica, região, campanha, plataforma, dispositivo, objetivo). Você só
escolhe **qual teste** acompanhar, num dropdown por "ID · Nome"; toda a
ficha cadastral aparece **travada, só pra leitura** — não dá pra clicar e
editar nada disso por aqui, de propósito, porque essa parte é
responsabilidade do DaVinci.

Por padrão, o Michelangelo procura o DaVinci numa pasta **irmã**, ou seja:

```
sua_pasta\
  teste_ab_desenho\       <- pasta do DaVinci
  teste_ab_mensuracao\    <- esta pasta (Michelangelo)
```

Se você organizou diferente, crie um arquivo de texto chamado
`davinci_path.txt` dentro desta pasta com o caminho completo da pasta do
DaVinci em uma linha (ex.: `C:\Users\voce\Documents\teste_ab_desenho`).

**Nenhum arquivo do DaVinci é alterado.** O Michelangelo só lê o JSON que
já existe — o app.py e o ab_design.py do DaVinci continuam exatamente
como estavam.

### Se você ainda não salvou nenhum teste no DaVinci

O Michelangelo mostra **3 exemplos** no formato real que o DaVinci gera
(mesma estrutura de ID, mesma ficha) — só pra você ver como vai funcionar.
Um aviso azul deixa isso claro na tela. Assim que o primeiro teste
de verdade for salvo no DaVinci, ele substitui os exemplos automaticamente
— não precisa fazer nada aqui.

## O que é medido, e como

- **Duas famílias de métrica**, espelhando o DaVinci: proporção (taxa de
  conversão, churn — modelo Beta-Binomial) e média (ticket médio, receita
  por usuário — aproximação Normal via erro padrão da média diária).
- **Simulação de Monte Carlo** (3.000 simulações) pra estimar a
  probabilidade de a variante ser melhor que o controle, o lift esperado
  e o intervalo de credibilidade (90%).
- **Sempre por usuário único**, nunca por sessão — contar sessão repetida
  do mesmo usuário infla a amostra e faz o resultado parecer mais preciso
  do que é de verdade.
- **Checklist de contaminação** (randomização/SRM, crossover,
  duplicidade/nulos, efeito novidade, guardrails) — coisas que a
  ferramenta não consegue confirmar sozinha, e que valem uma checagem
  manual antes de aplicar a mudança de verdade.
- **Apoio à decisão quando a amostra planejada não foi atingida**:
  prorrogar o teste, aceitar menos confiança e decidir com o que já tem,
  ou parar sem decidir — com uma recomendação calculada pra cada caso.

Dado que a ficha do teste (BU, métrica, meta de amostra) é real (vem do
DaVinci), mas o teste ainda não está rodando de verdade em produção, os
dados do dia a dia (quantos usuários converteram, quanto variou por dia)
são **sintéticos** — gerados de forma determinística a partir do ID do
teste, do período escolhido e da variante, pra sempre dar o mesmo
resultado pro mesmo filtro. Ligar isso a dados reais (BigQuery, um data
warehouse) é o próximo passo natural, fora do escopo deste protótipo.

## Login

Antes de mostrar qualquer coisa, o Michelangelo pede nome e senha — igual
ao DaVinci, e com **as mesmas senhas** (senha padrão de usuário e senha de
administrador). Quem entra como administrador vê também uma tabelinha com
quem já entrou no app. Esse controle é só uma trava simples (não é
autenticação de verdade), do mesmo jeito que já era no DaVinci.

## Sobre as imagens

A imagem usada no login e no cabeçalho (`assets/michelangelo_mascote.png`)
é a que você mandou — recortei o fundo branco pra deixar transparente, sem
mais nenhum tratamento. Ao lado do "Oi! Eu sou o Michelangelo" na tela de
login tem um segundo ícone (`assets/davi_icon.png`) — o Davi, também a
partir da imagem que você mandou, recortado sem fundo.

## Estrutura da pasta

```
teste_ab_mensuracao\
  app.py                    <- interface (Streamlit)
  ab_measure.py              <- motor de medição bayesiana (só biblioteca padrão do Python)
  davinci_bridge.py          <- leitura (somente leitura) do histórico do DaVinci
  requirements.txt
  rodar_michelangelo.bat
  historico_medicoes.json    <- criado automaticamente ao salvar uma medição
  davinci_path.txt           <- opcional: caminho da pasta do DaVinci, se não for irmã desta
  assets\
    michelangelo_mascote.png
    historico_exemplo.json   <- os 3 exemplos usados quando o DaVinci ainda não tem testes salvos
  .streamlit\
    config.toml              <- mesmo tema visual do DaVinci
```

## Fora de escopo (de propósito)

Assim como o DaVinci deixa de fora o acompanhamento pós-desenho, o
Michelangelo deixa de fora o desenho do teste — objetivo, público-alvo,
tamanho de amostra e prazo são decisões do DaVinci, e aqui chegam prontas,
só para leitura.
