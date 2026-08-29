@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo ERRO: nao encontrei o Python instalado neste computador.
    echo Baixe em https://www.python.org/downloads/ e marque a opcao "Add Python to PATH" durante a instalacao.
    echo Depois, so clicar de novo neste arquivo.
    pause
    exit /b 1
)

echo Instalando as bibliotecas necessarias (streamlit, plotly e afins)...
echo Isso pode demorar um pouco na primeira vez que voce roda.
echo.
python -m pip install -r requirements.txt --quiet

if errorlevel 1 (
    echo.
    echo ERRO: nao consegui instalar as bibliotecas. Verifique sua conexao com a internet.
    pause
    exit /b 1
)

echo.
echo Abrindo o Michelangelo no navegador...
echo (Para fechar o app depois, volte nesta janela preta e aperte Ctrl+C)
echo.
python -m streamlit run app.py

pause
