# Coldframe 보안 스펙

> 로컬 우선·소유 가능한 메모리의 *신뢰 근거*. 하드닝 감사(wf_9cebead0)의 보안/프라이버시 cross-cutting findings(C2·H7·H8·H9·H13·M6·M8) 통합. SPEC §16에서 포인터.

## 1. Purge Invariant (C2 / D16·D21-B2) — "secret 제거" 보증
- **1차 방어 = pre-write BLOCK (D15/D-T3)**: secret/credential은 admission에서 차단, **디스크 미접촉이 원칙** (가장 강한 보증).
- **2차 = crypto-shredding**: 모든 fact 변경은 `events` 로그에 **per-event 키로 암호화** 저장. "forget+cascade(`derived_from`)" = 키 파기 → append-only 불변(B1=A) 유지하며 평문 복구 불가. (append-only ↔ hard-purge 모순 동시 해소)
- **purge 대상 전수 열거**(키 파기 + 추가 스크럽): `notes`, `note_fts`+FTS5 shadow(명시적 delete+optimize), `note_vec`(임베딩 역추론 방지), `note_history.snapshot`, `sources`, `edges`, `events.payload`, `jobs.payload`, export 번들.
- **전제**: `PRAGMA secure_delete=ON` + WAL checkpoint(TRUNCATE).
- **honest scope**: "삭제 증명"은 **live DB 파일** 범위만. OS 스냅샷/Time Machine/백업/free-list는 보증 못 함 → 명시 disclaim + at-rest 암호화 권장. ("증명"이 허위보증 되지 않게.)

## 2. Encryption at rest (H13 / D16)
- opt-in **SQLCipher**(AES-256 full-DB), `cold-frame init --encrypt`. **기본은 평문**(grep 가능·"한 파일 소유" 서사 보존).
- 키: OS 키체인(macOS Keychain/Secure Enclave 우선; Linux libsecret / Windows DPAPI). **`.db` 옆 저장 금지.**
- **recovery code 필수**(키체인 분실 대비 passphrase fallback) — 없으면 데이터 영구 손실.
- cross-process 키 획득 + WAL semantics 명세(워커/UI/MCP가 같은 키).

## 3. 로컬 UI 보안 계약 (H8) — UI는 실제로 *write API*다
ux의 pin/forget/revive/edit/merge/resolve/import는 mutating → "read-mostly"가 아니라 **write API**. 무방비 시 drive-by(사용자가 다른 사이트 브라우징)로 corpus wipe/exfiltrate 가능 → "소유 메모리" 브랜드 종말.
- **127.0.0.1 명시 bind** (0.0.0.0 금지).
- **DNS-rebinding 방어**: `Host` 헤더 allowlist(`localhost`/`127.0.0.1:port`만).
- **CSRF**: 모든 mutating 요청에 per-session 토큰(서버 시작 시 생성→페이지 주입) + `Origin`/`Referer` 검사.
- 정적 SPA + JSON API 동일 origin. 무인증이되 무방비 아님.
- **포트**: 기본 `branding.UI_PORT=27182`(흔하지 않은 값), **점유 시 자동 다음 빈 포트 fallback**, 해결된 포트를 `~/.cold-frame/ui.port`에 기록(CLI/MCP deep-link가 stale 안 됨), `--port` override, doctor 보고. DB(SQLite 파일)는 포트 없음 → 충돌 0.

## 4. MCP 위협모델 (H9)
- MCP 도구 인자는 **신뢰경계 밖 입력**(악성 프롬프트가 에이전트로 하여금 메모리 dump/delete 유도 가능).
- 파괴적 도구(delete/purge/import)는 MCP로 **노출 안 함** 또는 confirm/undo 필수(archive-not-delete가 기본 안전망).
- 인자 검증(scope/id 화이트리스트), path 인자 금지, rate/size 한도.

## 5. import sandbox (H7)
- 경로 canonicalize + sandbox(zip-slip 방어), **foreign `.db` ATTACH 금지**(events를 정상 admission+quarantine 경로로 파싱), unsafe deserialization(pickle/`eval`/`yaml.load`) 금지.
- v1: 'unverified source' 경고 + 전량 quarantine(`pending`). v2: Ed25519 서명 검증. (위조된 belief history·purge된 secret 부활 방지.)

## 6. 로깅/관측성(M8) & 공급망(M6)
- 구조적 로깅, **secret/PII 절대 미로깅**(redaction 후만). telemetry는 opt-out·기본 off.
- `[server]`/`[ui]` deps lockfile + SBOM, prebuilt JS 번들 reproducible build + version stamp(동봉 번들 무결성).
