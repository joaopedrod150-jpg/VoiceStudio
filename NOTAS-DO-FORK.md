# Notas do fork

Fork de debpalash/VoiceStudio (AGPL-3.0). Branches: `feat/multi-reference-voice`, `fix/pronunciation`, `feat/video-voiceover`, `feat/clean-ui` (empilhadas), todas mescladas em `main` deste fork. Nada foi enviado ao upstream.

## Decisões
- `D:\projetos dev` não existia; foi criada e o fork clonado nela.
- Commits sem trailer de agente: a regra do projeto (CLAUDE.md) proíbe.
- Skills: Superpowers, Caveman e Ponytail existem na sessão; **`frontend-design` não está instalada** (o design foi feito seguindo os componentes e tokens existentes). Não invoquei formalmente cada skill Superpowers; o fluxo foi plano curto, testes junto com o código e verificação por execução.
- Ambiente de teste: sem pesos de modelo; o áudio real de TTS não foi ouvido.

## Tarefa 1 - vários áudios
- Os engines (OmniVoice e demais) são zero-shot com **uma** referência de 3-20 s; nenhum aceita embedding ou várias referências. Estratégia: `backend/services/voice_reference.py` limpa cada gravação (24 kHz mono, DC, normalização RMS, corte de silêncio, rejeita curtas/mudas/saturadas), divide em trechos de fala e escolhe os melhores em rodízio entre as gravações até 15 s, unidos com pausas de 120 ms. O resultado é a referência normal do perfil, então tudo continua compatível.
- Fontes guardadas em `voice_profile_refs` (alembic `0014`). API: `POST /profiles` aceita `extra_ref_audios`; `GET/POST /profiles/{id}/references`, `DELETE /profiles/{id}/references/{ref_id}`. Um perfil antigo ganha a gravação atual como fonte 1 ao receber a primeira extra.
- Ao usar várias gravações, um transcrito digitado é descartado (descrevia só uma); é gerado de novo sobre o clipe combinado.
- Ajuste fino pesado (LoRA): **não implementado**; os engines atuais não expõem isso. Só documentado aqui.
- Gravar pelo microfone usa a mesma tela de gravação existente; os testes não exercitam o microfone real.

## Tarefa 2 - pronúncia
- Causa provável (leitura do código): "Auto" enviava idioma nenhum ao engine, pulava a normalização de números e fazia entradas do dicionário por idioma nunca casarem. Correção e `[lang:xx]` em `backend/services/lang_spans.py` + `generation.py`. O dicionário editável com UI já existia (Configurações → Pronúncia) e foi mantido.
- Não verifiquei a melhora audível (sem modelo). O detector é heurístico (stopwords); recusa quando incerto. Streaming remove as tags e usa um idioma só.
- `[lang:xx]` só vale em `/generate` (não em dublagem/audiobook/OpenAI-compat).

## Tarefa 3 - narração em vídeo
- Tela própria (`/narration`) em vez de enxertar na Dublagem: o pipeline de dublagem depende de jobs com transcrição/tradução. Reaproveitei `fit_planner.plan_fit` (modo só-áudio), `_pitch_preserving_stretch`, ffmpeg e `/generate` (chamado internamente, herdando voz, engine, normalização, dicionário e marca sintética).
- Falas longas demais para o trecho são aceleradas até 1.8x e, se ainda sobrar, cortadas com fade e avisadas. Falas curtas são desaceleradas até 0.85x. Sem lip-sync.
- Jobs ficam em memória (somem ao reiniciar o backend); arquivos em `outputs/narration/<id>`.

## Tarefa 4 - interface
- Acento único (hue 215, croma baixo) em `t3-theme.css`; arco-íris dos ícones removido; sidebar nova `app-sidebar.tsx` (Clonagem, Design, Dublagem, Narração, Transcrição, Audiobook, Modelos, Ajustes + tema). `/` abre Clonagem.
- Removidos do layout padrão: rodapé de patrocínio, atalhos Pro/estrela, coluna da biblioteca. As funções continuam acessíveis (biblioteca na tela de Clonagem e `/personas`; status/engine em Configurações). Os componentes antigos permanecem no código, sem uso.
- Telas conferidas no navegador: Clonagem, Narração, Dublagem (claro e escuro). Não abri o app Electron empacotado.
- Temas escolhíveis em Aparência (Midnight, Catppuccin...) continuam existindo.

## Testes
- Front: `cd electron && bun run test` (2896 + 1495 testes), `bun run typecheck`, `bun run build`.
- Back: `.venv/Scripts/python.exe -m pytest tests` com `HF_HUB_OFFLINE=1`. O `main` original já falha ~60-80 testes no Windows (ffprobe, env, identidade do CI); as falhas novas desta série foram corrigidas. Testes novos: `test_voice_reference.py`, `test_profile_multi_reference.py`, `test_lang_spans.py`, `test_generate_language_runs.py`, `test_narration.py` (este usa ffmpeg real e pode falhar sob pouca memória/paginação).
