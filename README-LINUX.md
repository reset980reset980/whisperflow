# WhisperFlow — Linux 헤드리스 웹 포트

원본 [WhisperFlow](./README.md)는 **macOS 전용** 데스크톱 음성 어시스턴트입니다
(메뉴바 rumps 앱, 전역 단축키 pynput, `say`/NSSpeechSynthesizer TTS, macOS `.app` 번들).
이 문서는 그 프로젝트를 **Linux 서버 + 브라우저** 환경으로 이식한 버전을 설명합니다.

원본의 시그니처인 **JARVIS 파티클 UI(HTML5 Canvas + WebSocket)** 와
**음성 → AI → 음성** 파이프라인을 그대로 살리되, macOS에 묶여 있던 부분만 교체했습니다.

## 무엇이 바뀌었나 (macOS → Linux)

| 영역 | 원본 (macOS) | Linux 포트 |
|------|--------------|------------|
| 진입점 | `app.py` (rumps 메뉴바) | `server.py` (헤드리스 웹 서버) |
| 마이크 | 서버 로컬 마이크 (`sounddevice`) | **브라우저 마이크** (`getUserMedia`+`MediaRecorder`) → WS 업로드 |
| STT | 서버 faster-whisper | 서버 faster-whisper (동일, 브라우저 오디오 디코드) |
| AI 응답 | Claude Code CLI 서브프로세스 (`--resume`) | **OpenAI/Claude HTTP API** 스트리밍 (`ai_backend.py`) |
| 대화 기억 | CLI 세션 재개 | 인메모리 메시지 히스토리 재전송 |
| TTS | `say` / NSSpeechSynthesizer (서버 재생) | **OpenAI TTS**(또는 espeak) → `tts_audio`로 브라우저 재생 |
| 말하기 파티클 | 서버가 `audio_level` 스트림 | 동일 — 서버가 wav 엔벨로프를 재생 동기로 스트리밍 |
| 단축키/제스처/Hue/카메라 | macOS 네이티브 | 웹 서버에서는 미사용 (파일은 보존) |

### 🔧 실시간 도구 (function calling)

AI가 필요할 때 스스로 호출하는 실제 도구들 — 전부 **추가 API 키 불필요**:

| 도구 | 데이터 소스 | 예시 질문 |
|------|------------|----------|
| 현재 시각 | 서버 시계 (Asia/Seoul) | "지금 몇 시야?" |
| 날씨 + 예보 | Open-Meteo | "서울 날씨 어때?" |
| 암호화폐 시세 | Upbit 공개 API | "비트코인 얼마야?" |
| 서버 상태 | PM2 (`pm2 jlist`) | "서버 상태 알려줘" |
| 뉴스 검색 | Google News RSS (한국) | "AI 뉴스 찾아줘" |
| 지식 검색 | 한국어 위키백과 | "이순신이 누구야?" |
| 메모 저장 | Obsidian vault `00 Inbox` | "내일 회의 준비 메모해줘" |

> ⚠️ **범위 정직성**: 도구는 OpenAI 프로바이더에서만 동작합니다
> (`AI_PROVIDER=anthropic`은 텍스트 전용). 원본 macOS 버전의 Claude Code CLI
> 도구 실행(파일 읽기/쓰기 전권)은 이식하지 않았습니다.

## 아키텍처

```
브라우저 (jarvis.html, 파티클 UI)
  │  마이크 캡처(MediaRecorder, webm/opus)
  ▼  WebSocket  ws(s)://<host>[:8767]
서버 (whisperflow.server)
  ├─ audio_upload → stt.py (faster-whisper)  → transcript
  ├─ chat_input   → ai_backend.py (OpenAI/Claude 스트리밍, 누적)
  └─ 응답 완료    → tts_engine.py (OpenAI TTS, wav)
                    → tts_audio(base64) + audio_envelope.py 로 audio_level 동기 스트리밍
  (HTTP 정적 파일 + WebSocket 이 같은 포트 8767 공유)
```

## 빠른 시작

```bash
cd whisperflow

# 1) 실행 (최초 실행 시 venv 생성 + 의존성 설치 + .env 생성)
./run-linux.sh

# 또는 수동으로:
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-linux.txt
cp .env.example .env         # 그리고 .env 편집
python -m whisperflow.server
```

`.env`에 최소한 다음을 설정하세요 (자세한 옵션은 `.env.example`):

```
AI_PROVIDER=openai
OPENAI_API_KEY=sk-...        # 실제 OpenAI 키
TTS_BACKEND=openai           # 또는 espeak (오프라인)
WHISPER_MODEL=base
WHISPER_LANGUAGE=ko
```

키가 없으면 AI는 **에코 모드**로, TTS는 **espeak**(설치 시)로 자연스럽게 낮춰집니다.

브라우저에서 `http://127.0.0.1:8767` 접속 → 마이크 버튼을 눌러 말하면
전사 → AI 응답 → 음성 재생 + 파티클이 목소리에 반응합니다.

> 🎙️ 브라우저 마이크는 보안 컨텍스트(`localhost` 또는 **HTTPS**)에서만 동작합니다.
> 도메인으로 접속한다면 아래 Caddy(HTTPS) 구성을 사용하세요.

## Caddy 리버스 프록시 (선택)

이 저장소는 Caddyfile을 수정하지 **않습니다**. 도메인으로 노출하려면
운영자가 직접 다음 블록을 Caddyfile에 추가하세요. HTTP와 WebSocket이 8767
포트를 공유하므로 별도 WS 라우팅이 필요 없습니다:

```caddy
jarvis.example.com {
    reverse_proxy 127.0.0.1:8767
}
```

`jarvis.html`은 HTTPS일 때 자동으로 `wss://<host>`로 WebSocket을 연결합니다.

## 요구사항

- Python 3.10+
- `ffmpeg` (faster-whisper가 브라우저 webm/opus 디코드에 사용)
- (선택) `espeak-ng`/`espeak` — 오프라인 TTS 폴백
- 첫 STT 실행 시 Whisper 모델 자동 다운로드

## 파일 지도 (이 포트에서 추가/변경)

| 파일 | 역할 |
|------|------|
| `whisperflow/server.py` | 헤드리스 진입점, TTS/엔벨로프/STT 배선 |
| `whisperflow/ai_backend.py` | OpenAI/Claude 스트리밍 프로바이더 (+에코 폴백) |
| `whisperflow/assistant_session.py` | 세션/멀티턴 메모리 (CLI → API 로 재작성) |
| `whisperflow/tts_engine.py` | OpenAI/espeak TTS → 오디오 바이트 |
| `whisperflow/audio_envelope.py` | wav → audio_level 엔벨로프(3-band) |
| `whisperflow/stt.py` | 브라우저 오디오 바이트 → faster-whisper 전사 |
| `whisperflow/env_loader.py` | `.env` 로더 (stdlib) |
| `whisperflow/ws_server.py` | (수정) host/port env, `audio_upload` 핸들러 |
| `whisperflow/static/jarvis.html` | (수정) 데스크톱 마이크 = 브라우저 MediaRecorder |
| `requirements-linux.txt`, `run-linux.sh`, `.env.example` | 배포 |
