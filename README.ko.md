# Agent Call Governor

**한국어** · [English](README.md)

[![Validate](https://github.com/Kimuhwan/Agent-Call-Governor/actions/workflows/validate.yml/badge.svg)](https://github.com/Kimuhwan/Agent-Call-Governor/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Codex Plugin](https://img.shields.io/badge/Codex-Plugin-111827)](.codex-plugin/plugin.json)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB)](https://github.com/Kimuhwan/Agent-Call-Governor/blob/v0.3.0/pyproject.toml)

Codex 호출을 위한 로컬 우선, 품질 보존형 거버넌스 및 관측 도구입니다.

## 무엇인가요?

Agent Call Governor v0.3.0은 여섯 가지 수명 주기 이벤트를 로컬 SQLite 원장에 기록하고 결정론적 호출 정책을 적용하는 루트 Codex 플러그인입니다. 필수 작업, 검증, 위험에 맞는 재시도를 보존하면서 완전히 같은 중복 호출이나 소진된 호출을 줄입니다. 번들 Codex 훅은 **observe/warn-only**이며, 실제 강제 차단은 애플리케이션이 소유한 래퍼의 역할입니다.

## 3줄 요약

1. 원본 훅 페이로드를 저장하지 않고 Codex 도구와 서브에이전트 활동을 로컬에서 관찰합니다.
2. 명시적인 CLI 명령으로 세션 기록을 조회, 보고, 내보내기, 보존 또는 삭제합니다.
3. 호출 감소보다 부족 호출 방지를 먼저 측정하면서 최소 충분 호출을 지향합니다.

플러그인이 처리하는 호스트 이벤트는 정확히 `SessionStart`, `PreToolUse`, `PostToolUse`, `SubagentStart`, `SubagentStop`, `Stop` 여섯 가지입니다.

## Codex 플러그인 설치

지원되는 대화형 경로는 Codex Desktop의 Plugins 디렉터리 또는 **Settings -> Plugins**입니다. 저장소 루트 `Kimuhwan/Agent-Call-Governor`의 `main` ref를 추가해 설치하세요. 플러그인 매니페스트, 스킬, 디스패처, 훅이 함께 설치됩니다.

Codex 마켓플레이스 셸 명령을 사용할 수 있다면 다음은 별도의 선택적 설치 방법입니다.

```console
codex plugin marketplace add Kimuhwan/Agent-Call-Governor --ref main
```

플러그인을 설치하거나 활성화해도 훅이 자동으로 신뢰되지는 않습니다. `/hooks`를 열어 훅 내용과 **exact hash**를 검토한 뒤 해당 버전을 명시적으로 신뢰하세요. Codex는 새 훅이나 변경된 훅을 새 exact hash로 다시 검토하고 신뢰할 때까지 건너뜁니다. 이 경계를 우회하지 마세요. 현재 계약 참고 자료는 [Codex 훅](https://learn.chatgpt.com/docs/hooks.md)과 [플러그인 만들기](https://learn.chatgpt.com/docs/build-plugins.md)이며, 호환성 확인일은 **2026-07-15**입니다.

호스트는 지원되는 `PreToolUse` 호출에 `hookSpecificOutput.permissionDecision: "deny"`를 반환해 거부할 수 있습니다. Agent Call Governor v0.3은 이 응답을 의도적으로 내보내지 않습니다. 설치된 훅은 observe/warn-only이며 방화벽이 아닙니다.

### 보조 CLI 휠 설치

플러그인은 자체 번들 소스에서 실행됩니다. 아래의 전역 `agent-call-governor-runtime` 명령이 필요할 때만 보조 휠을 설치하세요.

```console
python -m pip install https://github.com/Kimuhwan/Agent-Call-Governor/releases/download/v0.3.0/agent_call_governor_runtime-0.3.0-py3-none-any.whl
```

### 호환성 전용 스킬 설치기

다음 스크립트는 스킬만 `CODEX_HOME/skills/agent-call-governor`에 복사합니다. 루트 플러그인 설치를 대신하는 방법이 아니라 이전 버전 또는 스킬 전용 Codex 환경을 위한 호환성 기능입니다.

```console
git clone https://github.com/Kimuhwan/Agent-Call-Governor.git
cd Agent-Call-Governor
```

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

```sh
./install.sh
```

## 첫 로컬 세션

플러그인을 설치하고 exact hash 신뢰까지 마친 뒤 Codex를 다시 시작해 일반적인 도구 또는 서브에이전트 작업을 수행하세요. 훅은 기본적으로 호스트가 제공하는 `PLUGIN_DATA` 디렉터리 아래 `events.sqlite3`에 기록합니다. 플러그인 훅이 이벤트를 받은 뒤에만 데이터베이스가 생기며, 호환성 스킬만 설치해도 이벤트 데이터가 만들어지지는 않습니다.

다른 데이터베이스를 사용하려면 `AGENT_CALL_GOVERNOR_DB`를 **Codex 실행 전에** 설정하고, 모든 CLI 명령의 `--db`에도 같은 경로를 전달하세요.

```powershell
$env:AGENT_CALL_GOVERNOR_DB = "PATH_TO_EVENTS"
$env:AGENT_CALL_GOVERNOR_RETENTION_DAYS = "7"
```

```sh
export AGENT_CALL_GOVERNOR_DB="PATH_TO_EVENTS"
export AGENT_CALL_GOVERNOR_RETENTION_DAYS="7"
```

데이터베이스 경로를 확인한 뒤 일곱 가지 검사를 수행하는 doctor를 실행하세요. Windows의 권한 결과는 소유자 전용 ACL을 증명하는 것이 아니라 최선형 경고일 수 있습니다.

## 조회, 내보내기, 보존, 삭제

플러그인과 같은 데이터베이스 경로를 사용하세요.

```console
agent-call-governor-runtime doctor --plugin-root PATH_TO_CHECKOUT --db PATH_TO_EVENTS
agent-call-governor-runtime sessions --db PATH_TO_EVENTS
agent-call-governor-runtime inspect SESSION_ID --db PATH_TO_EVENTS
agent-call-governor-runtime report --db PATH_TO_EVENTS
agent-call-governor-runtime export --format jsonl --output PATH_TO_EXPORT --db PATH_TO_EVENTS
agent-call-governor-runtime delete-session SESSION_ID --yes --db PATH_TO_EVENTS
```

`sessions`는 개인정보에 안전한 추적 참조를 반환하며, 그중 하나를 `SESSION_ID`로 사용합니다. `inspect`와 `report`는 권위 저장소인 SQLite 원장을 읽습니다. `export`는 특정 시점의 정제된 JSONL 파일을 만듭니다. JSONL은 내보내기와 이전 버전 호환용일 뿐이며 플러그인은 실시간 미러를 유지하지 않습니다.

기본 보존 기간은 7일(`AGENT_CALL_GOVERNOR_RETENTION_DAYS=7`)입니다. `SessionStart`와 `Stop` 시 플러그인은 마지막 이벤트가 기준일보다 오래된 닫힌 추적만 안전하게 정리합니다. `session.stopped` 없이 중단된 세션은 활성 상태로 취급되어 자동 보존 정리에서 제외되므로 `delete-session`으로 직접 삭제해야 합니다. 삭제는 SQLite 보안 삭제와 저장 공간 정리를 사용하지만 파일시스템, SSD, 백업, 스냅샷 동작 때문에 과거의 모든 바이트가 복구 불가능하다고 보장할 수는 없습니다.

## 모드, 프로필, 위험도

루트 플러그인은 `observe`와 `warn`만 지원합니다. `observe`는 사용자 경고 없이 결정을 기록하고, `warn`은 `systemMessage`를 반환할 수 있지만 호출은 계속 진행됩니다. `enforce`는 지원되는 애플리케이션 소유 래퍼에서만 사용합니다.

기본 프로필은 `balanced`이고, 되돌리기 쉬운 저위험 작업에는 `strict`, 불확실하거나 오류 수정 비용이 큰 작업에는 `quality-first`를 선택합니다. 이들은 모드가 아니라 프로필입니다. 위험도는 `low`, `medium`, `high`로 분류하며 플러그인 기본값은 `medium`입니다. 위험 하한은 지나치게 작은 설정 예산을 높일 수 있고, 고위험 작업에는 전략을 실질적으로 바꾼 재시도를 최소 한 번 보존합니다. 예산은 일반 작업의 상한이지 목표나 일괄 금지가 아닙니다.

## 지원하는 통합

| 표면 | 목적 | 동작 |
| --- | --- | --- |
| 루트 Codex 플러그인 | 여섯 이벤트 수명 주기 수집과 정책 경고 | observe/warn-only, 프로세스 수준 fail-open |
| Codex 스킬 | 최소 충분 에이전트, 도구, 모델 호출 계획 | 프롬프트 수준 거버넌스 |
| 보조 런타임 | 애플리케이션 소유 동기/비동기 호출 래핑 | `observe`, `warn`, `enforce` |
| OpenAI Agents SDK 어댑터 | 실행 및 지원되는 함수 도구 래핑 | 애플리케이션 소유 래퍼/가드레일 동작 |
| 정책 CLI | JSON 제안 평가 | 결정론적 허용/거부 결과 |

`strict`, `balanced`, `quality-first`는 정책 **프로필**입니다. 훅/런타임 모드는 `observe`, `warn`, 그리고 지원되는 애플리케이션 소유 래퍼에서만 쓰는 `enforce`입니다.

플러그인 데이터 경로는 다음과 같습니다.

`Codex event -> dispatcher -> normalizer -> policy -> SQLite -> CLI/export`

컴포넌트와 신뢰 경계는 [아키텍처](docs/architecture.md)를 참고하세요.

## 보안 및 개인정보

- SQLite가 권위 저장소이며 로컬에 남습니다. 플러그인은 원격 텔레메트리를 구성하지 않습니다.
- 번들 훅은 원본 목표, 프롬프트, 도구 인자, 도구 결과, 대화 기록, 예외 메시지를 저장하지 않습니다. 호스트 식별자와 관련 입력은 SHA-256 참조로 바뀝니다.
- 해시 참조는 식별자이지 암호화가 아닙니다. 엔트로피가 낮은 값은 추측할 수 있으며 정제된 내보내기 파일도 접근 제어가 필요합니다.
- 훅 오류는 호스트 페이로드를 노출하거나 Codex를 중단하지 않도록 예외 유형만 남기고 fail-open 처리합니다.
- exact hash 신뢰 검토는 훅 실행을 보호하고 파일시스템 권한은 저장 데이터를 보호합니다. Windows의 소유자 전용 ACL 적용은 최선형입니다.

운영 환경에서 쓰기 전에 [보안 아키텍처](docs/security.md)와 [취약점 보고 정책](SECURITY.md)을 읽어주세요.

## 평가 근거와 한계

평가 우선순위는 **작업 성공**, **부족 호출률**, **오차단률**, 그리고 **호출 효율** 순입니다. 호출 수 감소는 품질 하한을 지킨 뒤에만 의미가 있습니다.

체크인된 결정론적 정책 모음, 런타임 재생, 소규모 매칭 A/B 연구는 방향성 근거입니다. v0.3의 10개 사례 계측 파일럿은 모델 품질 벤치마크가 아니며, 실행기와 날짜가 있는 결과가 체크인된 뒤에만 근거로 사용해야 합니다. 어느 결과도 운영 환경의 작업 성공률, 절감률, 지연 시간 또는 일반화 성능을 입증하지 않습니다. [벤치마크 방법론](docs/benchmark-methodology.md)과 [평가 설명](https://github.com/Kimuhwan/Agent-Call-Governor/blob/v0.3.0/evals/README.md)을 참고하세요.

주요 한계는 다음과 같습니다.

- Fingerprint v2는 정확히 같은 중복만 집행하며 의미상 중복이나 유사 중복은 처리하지 않습니다.
- 스키마 v2 기록은 조회할 수 있지만 정책 재생은 이후 버전으로 미뤄져 있습니다.
- 호스트 이벤트가 신뢰할 만한 값을 주지 않으면 사용량과 비용은 null일 수 있습니다.
- 마이그레이션한 legacy-v1 행은 예산과 진행 상황 계산에는 계속 포함되지만 fingerprint-v2의 정확 중복 후보는 아닙니다. 따라서 v0.2에서 업그레이드하면 새 정확 중복 epoch가 시작됩니다.
- `session.stopped`를 받지 못한 열린 세션이나 비정상 종료 세션은 직접 삭제해야 합니다.
- Codex 훅 텔레메트리는 observe/warn-only입니다. 호출을 중단해야 한다면 지원되는 애플리케이션 소유 런타임 래퍼를 사용하세요.

전체 목록은 [한계](docs/limitations.md)에 있습니다.

## 업데이트, 비활성화, 제거

보조 휠은 독립적으로 업데이트하거나 제거합니다.

```console
python -m pip install --upgrade https://github.com/Kimuhwan/Agent-Call-Governor/releases/download/v0.3.0/agent_call_governor_runtime-0.3.0-py3-none-any.whl
python -m pip uninstall agent-call-governor-runtime
```

루트 플러그인은 Codex Desktop **Settings -> Plugins**에서 비활성화하거나 제거하세요. 다시 활성화할 때는 현재 훅 해시를 검토하고 신뢰해야 합니다. 호환성 전용 스킬을 설치했다면 별도로 제거합니다.

```powershell
Remove-Item -LiteralPath "$HOME\.codex\skills\agent-call-governor" -Recurse -Force
```

```sh
rm -rf -- "$HOME/.codex/skills/agent-call-governor"
```

플러그인 제거, 휠 제거, 호환성 스킬 제거, SQLite 데이터 삭제는 서로 독립된 네 가지 작업입니다. 어느 작업도 나머지를 자동으로 수행하지 않습니다. `events.sqlite3`의 보존 또는 삭제를 직접 결정하세요.

## 로컬 검증

아래 명령은 소스 체크아웃에서 실행하세요. 평가 및 패키징 소스는 설치용 플러그인 압축 파일에 의도적으로 포함되지 않습니다.

```console
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python evals/run_evals.py
python evals/run_runtime_evals.py
python evals/run_instrumentation_evals.py
python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/agent-call-governor
python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
python -m build
```

## 기여 및 로드맵

기여를 환영합니다. [CONTRIBUTING.md](CONTRIBUTING.md)에서 시작하고 과도 호출과 부족 호출 양쪽에 대한 테스트를 추가하며 공개 주장을 재현 가능한 근거에 연결해 주세요. 로드맵은 충분한 스키마 v2 근거를 확보한 뒤 정책 재생을 추가하고, 그다음 신중하게 측정하는 강제 집행 실험을 진행하는 순서입니다.

## 라이선스

[MIT](LICENSE)
