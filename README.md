# Tirar ruído de vídeo e áudio com o Adobe Podcast

O agente extrai o áudio, envia para o [Adobe Podcast Enhance Speech](https://podcast.adobe.com/en/enhance) no browser em que a conta Adobe já está logada, e devolve o arquivo com o áudio limpo. No vídeo, a imagem original é copiada sem reencode. O arquivo original fica no lugar.

Funciona com qualquer agente de código (Claude Code, Codex, Cursor, Grok, Gemini, Antigravity, OpenCode e outros). O nome do repositório é `Adobre-Podcast-for-Claude-Code`.

O áudio sobe para os servidores da Adobe pelo browser da pessoa. Este repositório não tem chave de API, não guarda senha e não envia arquivo por conta própria.

Licença: MIT.

## Instalação

Cole o bloco abaixo no agente. Ele clona, instala o ffmpeg se faltar, roda os testes e liga a skill.

```text
Instale o pipeline de tirar ruído com Adobe Podcast. Faça você mesmo. Não me peça para rodar o que você consegue rodar.

Repositório: https://github.com/SafeMantella/Adobre-Podcast-for-Claude-Code
SSH, se a chave existir: git@github.com:SafeMantella/Adobre-Podcast-for-Claude-Code.git

1. Se esta pasta ainda não for o clone, clone em ~/Adobre-Podcast-for-Claude-Code. Tente SSH. Se a chave falhar, use HTTPS.
2. Confira que python3, ffmpeg e ffprobe executam. Se o ffmpeg faltar, instale só ele (macOS: brew install ffmpeg. Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y ffmpeg. Windows: winget install Gyan.FFmpeg). Não instale mais nada. Não há dependência de pip.
3. Na raiz do clone, rode: python3 -m unittest discover -s tests -v
   Se um teste falhar por ambiente, corrija o ambiente e rode de novo. Se a falha for do código, pare e me mostre o erro.
4. Rode: python3 enhance.py install
   Isso liga a skill nas pastas de skill dos agentes que já existem nesta máquina. Não crie uma pasta de agente que não existe. Não escreva em ~/.grok.
5. Rode: python3 enhance.py doctor
   Me mostre a saída.
6. Termine dizendo: o caminho do clone, se o ffmpeg está ok, em quais pastas a skill foi ligada, e a frase para o próximo pedido: "Tira o ruído deste arquivo com o pipeline do Adobe Podcast: <arquivo>".
7. Não abra o Adobe durante a instalação. Não peça e não digite senha da Adobe. Não grave estas instruções numa memória privada do agente. Não altere arquivos fora do clone, exceto os links da skill.

Quando eu pedir para limpar um vídeo ou um áudio, leia skills/adobe-podcast-enhance/SKILL.md e siga o arquivo. O original não é sobrescrito. No vídeo, a imagem original permanece intacta e só o áudio é trocado.
```

## Install

Paste the block below into the agent. Same steps.

```text
Install the Adobe Podcast noise-removal pipeline. Do the work yourself. Do not ask me to run a command you can run.

Repository: https://github.com/SafeMantella/Adobre-Podcast-for-Claude-Code
SSH, if a key exists: git@github.com:SafeMantella/Adobre-Podcast-for-Claude-Code.git

1. If this folder is not already the clone, clone it to ~/Adobre-Podcast-for-Claude-Code. Try SSH. If the key fails, use HTTPS.
2. Check that python3, ffmpeg, and ffprobe run. If ffmpeg is missing, install only ffmpeg (macOS: brew install ffmpeg. Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y ffmpeg. Windows: winget install Gyan.FFmpeg). Do not install anything else. There is no pip dependency.
3. From the clone root, run: python3 -m unittest discover -s tests -v
   If a test fails because of the environment, fix the environment and rerun. If the failure is in the code, stop and show me the error.
4. Run: python3 enhance.py install
   That links the skill into agent skill folders that already exist on this machine. Do not create an agent home that does not exist. Do not write into ~/.grok.
5. Run: python3 enhance.py doctor
   Show me the output.
6. Finish by telling me: the clone path, whether ffmpeg is ok, which skill folders were linked, and the sentence for the next request: "Remove the noise from this file with the Adobe Podcast pipeline: <file>".
7. Do not open Adobe during install. Do not ask for or type an Adobe password. Do not save these instructions into a private agent memory. Do not change files outside the clone, except the skill links.

When I ask you to clean a video or an audio file, read skills/adobe-podcast-enhance/SKILL.md and follow it. The original file is not overwritten. On a video, the original picture stays intact and only the audio is replaced.
```

## Pedir a limpeza

`Tira o ruído deste arquivo com o pipeline do Adobe Podcast: video.mp4`

O agente prepara o mp3, sobe no Enhance (ou entrega a lista, se não controlar o browser) e grava `<nome>-enhanced` ao lado do original.

## Limites

Publicados em <https://podcast.adobe.com/en/features> em 2026-10-08. Se essa data estiver velha, vale o que estiver na página.

| | Plano grátis | Plano pago |
| --- | --- | --- |
| Arquivo | áudio, um por vez | áudio ou vídeo, fila |
| Duração | 30 min | 2 h |
| Tamanho | 500 MB | 1 GB |
| Por dia | cerca de 1 h | cerca de 4 h |

O padrão deste repo parte o áudio em mp3 de 192 kbps com menos de 9 MB, para caber numa ferramenta de browser que recusa arquivo perto de 10 MB. O plano grátis da Adobe publica 500 MB. Quem entrega o caminho do arquivo direto ao input do browser usa `prepare --max-mb 0`.

Conta Adobe grátis, já logada no browser. Esta ferramenta não conferiu de novo, hoje, o preço do plano pago.
