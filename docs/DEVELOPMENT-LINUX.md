# WhisperFlow Linux 포트 — 개발 문서

> 대상: 이 저장소(`linux-port` 브랜치)를 이어받아 개발할 사람/AI.
> 사용자용 설치·기능 설명은 [README-LINUX.md](../README-LINUX.md) 참고.
> 최종 갱신: 2026-07-02

## 1. 배포 현황 (프로덕션)

| 항목 | 값 |
|------|-----|
| 도메인 | https://jarvis.xsw.kr (Caddy → 127.0.0.1:8767, HTTP+WS 같은 포트) |
| 프로세스 | PM2 `jarvis` (pm_id는 변동, `pm2 describe jarvis`) — `pm2-start.sh` 사용 |
| 코드 위치 | `/home/reset980/projects/whisperflow` (venv: `.venv`, 시크릿: `.env`) |
| 재부팅 지속 | `pm2 save` + `pm2-reset980.service` (systemd enabled) |
| Caddy 블록 | `jarvis.xsw.kr { import xsw_project_security_headers; reverse_proxy localhost:8767 }` |
| 기본 브랜치 | `linux-port` (GitHub 기본). `main` = 원작자(kshicdi) 원본 미러 — 건드리지 말 것 |

**배포 절차**: 코드 수정 → 테스트 → `pm2 restart jarvis` → 라이브 확인 → 커밋/푸시.
`jarvis.html`은 요청마다 다시 읽으므로 **정적 파일만 고쳤으면 재시작 불필요** (브라우저 강력 새로고침만).
파이썬 수정은 재시작 필수.

## 2. 아키텍처

```
브라우저 (jarvis.html — 파티클 UI + 카메라/마이크 캡처, 전 기기 공통)
   │ wss:// (Caddy가 프록시)
   ▼
whisperflow.server (WhisperFlowServer)          ← 헤드리스 진입점
   ├─ ws_server.py   : HTTP 정적 + WS 허브 (포트 8767 공유)
   ├─ stt.py         : faster-whisper (로컬 CPU int8, 브라우저 webm 디코드)
   ├─ assistant_session.py : 멀티세션 + 인메모리 대화 히스토리(20턴 캡)
   ├─ ai_backend.py  : OpenAI 스트리밍 + function calling 루프 (stdlib urllib SSE)
   ├─ tools.py       : 실시간 도구 8종 (아래 §4)
   ├─ tts_engine.py  : OpenAI TTS(wav) / espeak 폴백
   └─ audio_envelope.py : wav → 30fps 3-band 엔벨로프 (파티클 반응용)
```

macOS 원본 모듈(app.py, hotkey_manager, gesture_control, hue_controller 등)은
보존만 되어 있고 Linux 서버에선 **로드되지 않는다** (`__main__.py`가 rumps
임포트 실패 시 server로 폴백).

## 3. WebSocket 메시지 프로토콜 (Linux 포트에서 추가/변경분)

| 방향 | type | 필드 | 설명 |
|------|------|------|------|
| C→S | `chat_input` | `tab_id, text, image?, expect_image?` | 텍스트 질문. `image`=data URL(비전). `expect_image`=라이브 모드에서 프레임이 있어야 함을 명시(클라가 프레임 첨부 실패 시 서버가 정중히 거절) |
| C→S | `audio_upload` | `tab_id, suffix, data, image?` | 브라우저 마이크 녹음(webm base64) → STT → chat 흐름. 라이브 모드면 현재 프레임 동봉 |
| S→C | `transcript` | `value` | STT 결과 |
| S→C | `chat_chunk` / `chat_done` / `chat_error` | | 스트리밍 응답 (chunk는 **누적이 아닌 diff**, ws_server가 누적버퍼에서 diff 계산) |
| S→C | `tts_audio` | `value`(base64 wav) | 문장 단위 청크. UI가 큐(ttsAudioQueue)로 연속 재생 |
| S→C | `audio_level` | `value, low, mid, high` | TTS 재생과 동기화된 30fps 엔벨로프 → 파티클 진동 |
| S→C | `state` | `value` | idle/processing/thinking/recording/tts_playing |
| C→S | `tts_interrupt` | | 재생 중단 → 서버 gen 카운터 증가로 엔벨로프 스트림 취소 |

**중요 규칙**: 서버는 `expect_image=True && image 없음`일 때만 비전 요청을 거절한다.
**키워드 추측으로 차단하지 말 것** — "이거/화면/보이" 등은 일상어라 일반 채팅이 막힌다
(2026-07-02에 이 버그를 실제로 수정함).

## 4. AI 도구 (function calling)

`tools.py`의 `TOOL_SCHEMAS`+`_IMPLS`에 등록. 전부 무키/보유키로 동작:

| 도구 | 소스 | 비고 |
|------|------|------|
| get_current_time | 서버 시계 | Asia/Seoul 고정 |
| get_weather | Open-Meteo | 한글 도시명은 `_KR_CITIES` 맵으로 영문 변환 |
| get_crypto_price | Upbit 공개 ticker | 인증 불필요 |
| get_server_status | `pm2 jlist` | |
| search_news | Google News RSS(ko) | |
| search_wikipedia | ko.wikipedia REST | 404 시 opensearch 폴백 |
| naver_search | Naver OpenAPI | `.env`의 NAVER_CLIENT_ID/SECRET. web/news/blog/local/shop/book |
| save_memo | Obsidian vault `00 Inbox` | `OBSIDIAN_VAULT_PATH` |

- 도구 루프: `ai_backend.OpenAIProvider.stream` — SSE에서 `tool_calls` delta를
  index별로 누적, `finish_reason=tool_calls`면 실행 후 결과를 넣고 재요청 (최대 4라운드).
- 도구 구현은 **예외 대신 에러 문자열 반환** — 한 도구 실패가 답변을 죽이면 안 됨.
- Anthropic 프로바이더는 텍스트 전용(도구/비전 미지원). Echo는 키 없을 때 폴백.
- 시스템 프롬프트: 실시간 정보는 반드시 도구 사용 + **숫자 그대로 읽기**
  (모델이 "91,696,000원"을 "91만원"으로 오독한 실사례 때문에 명시).

## 5. 비전 / 라이브 / 제스처 / 얼굴 (프론트, jarvis.html)

- **단일 촬영·첨부**: 이미지 → `shrinkImage`(≤1280px JPEG 0.85) → `chat_input.image`.
  `assistant_session`이 멀티모달 content로 1턴 전송 후 히스토리에서
  `"[사진 첨부됨]"` 마커로 치환(에러 경로 포함, `_strip_image`) — base64 재전송 방지.
- **라이브 모드**(📷 버튼): PiP 프리뷰 상시 표시. 켜져 있는 동안 모든 질문(음성 포함)에
  현재 프레임 자동 첨부. `captureLiveFrameForTurn`이 프레임 준비를 최대 1.8s 대기.
- **제스처**: MediaPipe tasks-vision(CDN, 브라우저 실행) — ✋Open_Palm=녹음 시작,
  ✊Closed_Fist=녹음종료/TTS중단, ✌️Victory=화면 분석. 쿨다운 2.5s, ~5fps.
- **얼굴 등록/인식**: @vladmandic/face-api(CDN) tiny 모델 3종. 등록 시 128-d
  디스크립터를 `localStorage['jarvis_faces']`에 저장(이름당 최대 5샘플).
  유클리드 거리 <0.5 매칭, 인식 시 10분당 1회 자비스가 이름 불러 인사.
- CDN/모델 다운로드는 CSP(`script-src https:`, `connect-src https:`)가 이미 허용.
- 실패는 전부 degrade: 모델 로드 실패 시 PiP 상태줄에 표기하고 나머지 기능 유지.

## 6. 성능 수치 (2026-07-02 실측, 4-core CPU)

| 구간 | 값 | 관련 설정 |
|------|-----|----------|
| STT (base, warm) | ~2.4s | `WHISPER_BEAM_SIZE=1` (5로 올리면 +1s, 정확도 미차) |
| STT 첫 요청 | 페널티 없음 | 서버 시작 시 `stt.preload()` 백그라운드 로드 |
| AI 응답(도구 없음) | 첫 토큰 ~1s, 완료 ~3s | gpt-4o-mini |
| AI 응답(도구 1개) | +2~4s | 왕복 1회 추가 |
| 첫 TTS 오디오 | 텍스트 ~3.5s / 음성 ~5.8s | **문장 파이프라인** — `_split_sentences`로 쪼개 첫 문장 합성 즉시 전송, 이후 문장은 재생 중 백그라운드 합성 |

## 7. 함정 / 하지 말 것

1. **`.env` 커밋 금지** — OpenAI/Naver 키 존재. 커밋 전 `git diff --cached | grep sk-` 습관화.
2. **Caddyfile 임의 수정 금지** — 프로젝트 규칙상 사용자 승인 필요. jarvis 블록은 이미 있음.
3. **PM2 이름 충돌 주의** — `pm2 delete` 전 반드시 대상 확인 (예전 `jarvis`는 텔레그램 봇 로그가 남아있었음).
4. **websockets 13~14 API** — `process_request`/http11 시그니처가 구버전과 다름. `requirements-linux.txt` 핀 유지.
5. **chat_chunk는 diff** — assistant_session은 누적 텍스트를 yield하고, ws_server의 `_streaming_buffers`가 diff를 계산해 보낸다. 형태 바꾸면 UI가 깨짐.
6. **키워드로 메시지 차단 금지** (§3 중요 규칙).
7. mobile도 `jarvis.html`을 받는다 (assistant.html 라우팅은 제거됨, 직접 URL로만 접근).
8. Whisper 모델 캐시는 `~/.cache/huggingface`. 최초 다운로드 시 HF 미인증 경고는 무해.

## 8. 테스트 방법 (헤드리스)

브라우저 없이 전체 파이프라인 검증 패턴 (실제 사용된 스크립트 요지):

```python
# 인프로세스로 서버 띄우고 (WHISPERFLOW_PORT 환경변수로 포트 격리)
# websockets 클라이언트로 chat_input / audio_upload 전송 후
# transcript / chat_chunk / tts_audio / audio_level / state 수신 검증.
# 음성 입력 모사: OpenAI TTS로 wav 생성 → ffmpeg로 webm/opus 변환 → base64.
```

- JS 수정 시: 인라인 `<script>` 블록을 추출해 `node --check`로 구문 검증.
- UI 렌더링·카메라·마이크는 헤드리스로 검증 불가 → 사람이 브라우저에서 확인 필요.

## 9. 남은 일 / 아이디어

- [ ] 제스처·얼굴인식 실기기 검증 (코드 배포됨, 브라우저 테스트 미완)
- [ ] 웨이크워드("자비스") — 브라우저 상시 청취 + 로컬 키워드 감지
- [ ] TTS 보이스/속도 설정 UI (`OPENAI_TTS_VOICE` 노출)
- [ ] 얼굴 DB 서버 저장 (현재 localStorage — 기기별 분리됨)
- [ ] 대화 히스토리 서버 재시작 시 유지 (현재 인메모리)
