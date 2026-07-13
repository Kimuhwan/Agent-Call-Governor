# Agent Call Governor

**한국어** · [English](README.md)

[![Validate](https://github.com/Kimuhwan/Agent-Call-Governor/actions/workflows/validate.yml/badge.svg)](https://github.com/Kimuhwan/Agent-Call-Governor/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Codex Skill](https://img.shields.io/badge/Codex-Skill-111827)](agent-call-governor/SKILL.md)

품질 제어를 무조건적인 호출 금지로 바꾸지 않으면서 낭비되는 호출을 제거하는 Codex 스킬입니다.

Agent Call Governor는 위임·재시도·멀티 에이전트 실행 전에 품질 보존 호출 정책을 적용합니다. 위험 기반 예산, 중복 fingerprint, 진행 게이트, 정지 조건을 조합해 비용과 지연을 줄이고 작업의 완결성을 지킵니다.

## 왜 필요한가요?

복잡한 모델은 간단한 작업을 지나치게 분해하거나, 새 정보가 없는 호출을 반복하거나, 직접 조회할 일을 다시 에이전트에 위임할 수 있습니다. 이 스킬은 Codex가 다음 순서로 판단하게 합니다.

```mermaid
flowchart LR
    A[현재 컨텍스트] -->|부족함| B[직접 도구]
    B -->|역량 차이가 남음| C[전문 에이전트 1개]
    C -->|독립적인 하위 문제| D[멀티 에이전트]
    A -->|충분함| E[작업 완료]
    B -->|충분함| E
    C -->|충분함| E
```

직접 도구와 에이전트 호출 예산은 별도로 관리합니다. 최신성, 검증, 안전, 비공개 상태 접근, 사용자가 명시한 작업에 필요한 호출은 억제하지 않습니다.

목표는 **최소 호출**이 아니라 **최소 충분 호출**입니다. 비용 제어 때문에 부정확하거나 오래되거나 안전하지 않거나 불완전한 결과가 나올 위험이 커지면 품질 바닥이 우선합니다.

## 설치

### Windows PowerShell

```powershell
git clone https://github.com/Kimuhwan/Agent-Call-Governor.git
powershell -ExecutionPolicy Bypass -File .\Agent-Call-Governor\install.ps1
```

### macOS 또는 Linux

```bash
git clone https://github.com/Kimuhwan/Agent-Call-Governor.git
./Agent-Call-Governor/install.sh
```

설치 후 Codex를 다시 시작하세요. 스킬은 `~/.codex/skills/agent-call-governor`에 설치됩니다.

## 사용

프롬프트에서 명시적으로 호출할 수 있습니다.

```text
$agent-call-governor를 사용해서 이 저장소를 최소한의 충분한 위임으로 조사해줘.
```

Codex가 위임, 재호출, 멀티 에이전트 작업을 계획할 때 암시적으로도 실행될 수 있습니다.

## 판단 방식

호출 전에는 역량 차이, 예상되는 새 정보, 가장 저렴한 충분 경로, 남은 예산, 정지 조건, 정규화된 fingerprint를 확인합니다. 각 결과 이후에는 진행 상태를 분류합니다.

| 결과 | 동작 |
| --- | --- |
| `sufficient` | 호출을 멈추고 작업 완료 |
| `material_progress` | 남은 문제가 명확한 경우에만 계속 |
| `low_progress` | 전략을 바꾼 호출 1회만 허용 |
| `no_progress` | 위임을 멈추고 근거가 있는 최선의 결과 제공 |

전체 정책은 [SKILL.md](agent-call-governor/SKILL.md)에서 확인할 수 있습니다.

## 프로필

| 프로필 | 적합한 작업 | 동작 |
| --- | --- | --- |
| `strict` | 되돌리기 쉽고 위험이 낮으며 지연시간이 중요한 작업 | 작은 예산, low-progress 재시도 없음 |
| `balanced` | 일반적인 제품·엔지니어링 작업 | 기본 검증을 지키면서 낭비 제거 |
| `quality-first` | 영향이 크거나 오류 수정 비용이 높은 작업 | 더 큰 위험 바닥, 전략 변경 재시도 2회 |

고위험 작업에는 설정값이 낮아도 안전한 최소 예산을 자동 적용합니다. 필수 호출은 예산을 넘을 수 있지만, 반복 부작용을 막기 위해 동일 fingerprint는 계속 차단합니다. 자세한 내용은 [프로필 설명](agent-call-governor/references/profiles.md)을 참고하세요.

## 선택적 결정론적 게이트

호출이 많은 워크플로에서는 무의존성 Python 도구로 중복과 예산 초과를 차단할 수 있습니다.

```bash
python agent-call-governor/scripts/governor.py evaluate examples/proposal.json
```

호출 허용 시 종료 코드 `0`, 정책상 거절 시 `2`, 잘못된 입력은 `1`을 반환합니다. 자세한 입력 형식은 [proposal 스키마](agent-call-governor/references/proposal-schema.md)를 참고하세요.

## 로컬 검증

```bash
python -m unittest discover -s tests -v
python evals/run_evals.py
python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py agent-call-governor
```

정책과 실행 도구에는 별도 런타임 의존성이 없습니다. Codex 공식 스킬 검증기에는 PyYAML이 필요합니다.

[평가 도구](evals/README.md)는 `quality_preservation_rate`와 `waste_control_rate`를 함께 보고합니다. 호출을 무작정 줄여 under-call을 만든 결과가 성공으로 보이지 않게 하기 위함입니다. 이 회귀 점수는 규칙의 일관성을 측정하며 실제 서비스 성공률을 주장하는 수치는 아닙니다. [최신 평가 결과](evals/results-2026-07-13.md)도 확인할 수 있습니다.

첫 실제 Codex A/B 테스트에서는 두 조건 모두 4/4 작업을 완료했습니다. `balanced` Governor는 총 호출을 9회에서 3회로 줄였고, 블라인드 심사 품질도 8.63점에서 8.88점으로 높았습니다. 단 한 번의 방향성 테스트이므로 운영 환경의 확정 수치는 아닙니다. 테스트에서 발견한 예산-이력 불일치와 고위험 재시도 문제는 회귀 테스트와 함께 수정했습니다.

## 프로젝트 구조

```text
agent-call-governor/     Codex가 인식하는 스킬
  agents/openai.yaml     Codex UI 메타데이터
  references/            필요할 때만 읽는 제안 스키마
  scripts/governor.py    선택적 결정론적 게이트
examples/                바로 실행 가능한 입력 예제
evals/                   품질 보존·낭비 제어 평가 케이스
tests/                   표준 라이브러리 단위 테스트
```

## 기여

이슈와 Pull Request를 환영합니다. 핵심 스킬은 간결하게 유지하고, 가능한 동작은 결정론적 도구에 구현하며, 정책 변경에는 테스트를 함께 추가해 주세요.

## 라이선스

[MIT](LICENSE)
