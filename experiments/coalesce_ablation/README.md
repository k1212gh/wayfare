# Coalesce 정책 Ablation 실험

멘토 피드백 항목 1 — "L1+L2+L3 가 단일 신호보다 나은가, 어느 수준으로 coalesce 잘 하는가" 에 데이터로 답하기 위한 실험 묶음.

## 디렉토리

```
experiments/coalesce_ablation/
├── policies.py          # 6가지 매칭 정책 정의 (L1 only / L2 only / L3 only / L1+L2 / Union / L1 authoritative)
├── metrics.py           # precision / recall / canonical count 지표
├── replay.py            # 기존 잡 데이터로 정책 비교 (CLI)
├── synthetic.py         # 합성 케이스 단위 검증 (ground truth 명확)
├── threshold_scan.py   # L2 거리 / L3 cosine 임계값 sensitivity
├── ground_truth.py      # 사람 라벨링 기반 precision/recall
├── data/
│   └── ground_truth_pairs.jsonl   # 사람 라벨링 (시드)
└── reports/             # 실험 출력
```

## 6가지 정책

| 이름 | 동작 |
|---|---|
| `L1_only` | 구조 해시 단독 |
| `L2_only` | pHash 거리 단독 |
| `L3_only` | 임베딩 cosine 단독 |
| `L1+L2` | L1 OR L2 union |
| `L1+L2+L3` | 세 신호 모두 union |
| `L1_authoritative` | 현재 production 정책. L1 양쪽 있으면 L1 만, 한쪽 없으면 L2/L3 폴백 |

## 사용 흐름

### 1. 합성 케이스 — ground truth 명확, 즉시 비교

```
cd wayfare
python -m experiments.coalesce_ablation.synthetic
```

5가지 합성 케이스에 각 정책 적용 후 precision/recall 표 출력:
1. 시계 ticking 같은 화면 (모든 정책 hit 해야)
2. webview 익명 view jitter 같은 화면
3. 같은 layout 다른 메뉴 카테고리 (분리되어야)
4. 시계 앱 알람 탭 vs 타이머 탭 (분리되어야 — L3 사고 케이스)
5. 양쪽 view 비어있음 (매칭 안 하는 게 보수적)

### 2. 잡 데이터 replay — production 데이터로 정책 비교

```
python -m experiments.coalesce_ablation.replay e88edb42 a27f2c3a
# 또는 모든 잡
python -m experiments.coalesce_ablation.replay --all
```

각 정책이 만드는 canonical 수와 정책 간 일치율 (jaccard) 출력. ground truth 없어서 정확도 측정 X, 정책 간 결정 차이만 비교.

### 3. 임계값 sensitivity — 매직 넘버 검증

```
python -m experiments.coalesce_ablation.threshold_scan e88edb42
```

pHash 거리 2~20 / cosine 0.70~0.98 scan 후 canonical 수 변화. 임계값 변경 시 canonical 수가 monotonic 으로 변하면 임계값이 의미 있는 것. 평평하면 임계값 선택이 무의미.

### 4. Ground truth 평가 — precision/recall

```
# 1단계: 라벨링 시작
python -m experiments.coalesce_ablation.ground_truth --label

# 2단계: data/ground_truth_pairs.jsonl 에 손으로 라벨 추가
# (워크스페이스의 두 캡처를 봐서 같은 화면인지 사람이 판단)

# 3단계: 평가
python -m experiments.coalesce_ablation.ground_truth
```

각 정책의 precision / recall / f1 출력. **이 실험이 멘토 질문의 진짜 답**.

## 권장 실험 순서

1. **synthetic** 으로 알고리즘 자체 sanity check — production 데이터 없이도 즉시 실행
2. **replay** 로 production 잡들의 canonical 수 비교 — 정책별 차이가 있는지 우선 확인
3. **threshold_scan** 으로 임계값 매직 넘버의 정당성 검증
4. **ground_truth** 로 최소 50쌍 라벨링 후 정확도 측정

처음 3개는 ground truth 없이 가능 — production 데이터만으로 정책 간 상대 비교까지 도달. 4번이 절대 정확도 측정.

## 출력 형식

- `reports/replay_summary.json` — 정책별 canonical 수 + jaccard
- `reports/threshold_scan_<tour>.json` — scan 결과
- `reports/ground_truth_eval.json` — precision/recall

CLI 도 ASCII 표로 stdout 에 같이 출력.

## 한계

- replay 와 scan 은 ground truth 없이 정책 간 상대 비교만. 절대 정확도 X.
- ground truth 는 사람 라벨링 비용 발생. 최소 30-50쌍 권장.
- synthetic 은 5케이스만. 더 다양한 케이스 추가하면 알고리즘 검증 강화 가능.
