<!--
Doc-ID: DOC-DESIGN-086
Version: 1.0.0
Status: approved
Owner: strategy
Created: 2026-03-05
-->

# Peak Probability Model — 설계 문서

작성일: 2026-03-05
기반 문서: [docs/ouroboros/source/ouroboros_plan_v2.txt](../ouroboros/source/ouroboros_plan_v2.txt)
관련 계획: [85_loss_recovery_action_plan.md](../ouroboros/85_loss_recovery_action_plan.md)

---

## 1. 목적

`exit_rules.py`의 `pred_down_prob` 입력값을 생성하는 실제 ML 모델을 구현한다.
현재 `backtest_pipeline.py`의 `_m1_pred`는 `train_labels[-1]`을 반환하는 플레이스홀더이며,
이를 Walk-forward 검증을 통과한 `HistGradientBoostingClassifier` 기반 모델로 교체한다.

성공 기준:
- M1(trailing + 모델)이 B0(고정 손절/익절), B1(trailing only) 대비 OOS fold 분포 기준으로 일관된 우위
- PR-AUC, Brier Score, Calibration으로 예측 성능 별도 평가

---

## 2. 아키텍처 및 컴포넌트

```
src/analysis/
  peak_probability_model.py    ← 신규: 피처 엔지니어링 + 모델 인터페이스 + HGB 구현
  peak_model_metrics.py        ← 신규: PR-AUC, Brier, Calibration 평가
  backtest_pipeline.py         ← 수정: _m1_pred 플레이스홀더 → 실제 모델 연동
                                        BacktestBar에 volume 필드 추가 (선택, 하위호환)
                                        BacktestFoldResult에 m1_pr_auc, m1_brier 추가
tests/
  test_peak_probability_model.py  ← 신규
  test_peak_model_metrics.py      ← 신규
```

### 레이어 구성

```
FeatureBuilder
  └─ build(bars, entry_index) → np.ndarray
       └─ 슬라이싱: bars[:entry_index+1] 만 사용 (미래 접근 봉인)
       └─ rolling z-score 정규화 (entry_index 이전 구간만)

PeakProbabilityModel (Protocol/ABC)
  ├─ fit(X: ndarray, y: ndarray) → None
  └─ predict_proba(X: ndarray) → ndarray  # shape (n, 2), col[1] = 하락확률

HistGBPeakModel (기본 구현)
  └─ HistGradientBoostingClassifier
       max_depth=4, min_samples_leaf=20, class_weight balanced
```

---

## 3. 피처 목록

모든 피처는 `entry_index` 이전 데이터(`bars[:entry_index+1]`)만 사용한다.

| 피처 | 설명 | 계산 구간 |
|------|------|-----------|
| `return_1b` | 1봉 수익률 | `close[i] / close[i-1] - 1` |
| `return_3b` | 3봉 수익률 | `close[i] / close[i-3] - 1` |
| `return_5b` | 5봉 수익률 | `close[i] / close[i-5] - 1` |
| `atr_14` | ATR(14) | `bars[i-13:i+1]` |
| `hl_spread` | 고저 스프레드 | `(high - low) / close` |
| `rsi_14` | RSI(14) | `bars[i-13:i+1]` close 기준 |
| `volume_ratio` | 볼륨 비율 | `volume[i] / mean(volume[i-n:i])` — `BacktestBar.volume` 추가 시 활성화 |

---

## 4. Leakage 방지 규칙

### 4.1 Feature Window 봉인

```
entry_index
     │
     ▼
[... bar[i-n] ... bar[i]] │ [bar[i+1] ... bar[i+k]]
◄──── Feature Window ────►│◄──── Label Window (Triple Barrier) ────►
```

- `FeatureBuilder.build(bars, entry_index)` 내부에서 `bars[:entry_index+1]`로 슬라이싱 후 전달
- 구현자가 실수로 미래 데이터를 참조해도 인덱스 초과로 차단됨

### 4.2 Rolling Scaling (Global Scaling 금지)

| 금지 | 허용 |
|------|------|
| `StandardScaler.fit(전체 X)` | rolling window Z-score (entry_index 이전) |
| `MinMaxScaler` with future data | fold의 train set 내에서만 fit, test에 transform |

Rolling Z-score 공식:
```python
z = (value - mean(window)) / (std(window) + ε)
# window = bars[max(0, entry_index - W + 1) : entry_index + 1]
```

### 4.3 Walk-forward Fold 경계 규칙

- 스케일러는 **train fold에서만 fit**, test fold에 transform (fold 경계 누수 차단)
- Random split 금지 — 시계열 순서 엄수

---

## 5. 검증 메트릭

`peak_model_metrics.py`에 구현:

| 메트릭 | 목적 |
|--------|------|
| PR-AUC | 불균형 레이블에서 예측 품질 |
| Brier Score | 확률 캘리브레이션 오차 |
| Calibration curve | 예측 확률 신뢰성 시각화 |

`BacktestFoldResult`에 `m1_pr_auc: float`, `m1_brier: float` 필드 추가.

---

## 6. 채택 기준

- M1이 B0/B1 대비 OOS fold 분포 기준으로 일관된 우위 (단일 fold 성과 기준 금지)
- Brier Score < 0.25 (캘리브레이션 품질)
- PR-AUC > B1 baseline on majority of folds

---

## 7. 파라미터 초기값

`ouroboros_plan_v2.txt` 섹션 8 기준:

| 시장 | `p_thresh` | `atr_multiplier_k` | `arm_pct` | `be_arm_pct` |
|------|-----------|-------------------|-----------|-------------|
| KR   | 0.62      | 2.2               | 2.8       | 1.2         |
| US   | 0.60      | 2.0               | 2.4       | 1.0         |

---

## 8. 구현 제외 범위 (이번 PR)

- LightGBM 교체 (인터페이스로 추후 가능)
- 실시간 모델 서빙 / 모델 파일 저장
- 하이퍼파라미터 자동 튜닝

---

## 9. 관련 파일 변경 요약

| 파일 | 변경 유형 | 내용 |
|------|-----------|------|
| `src/analysis/peak_probability_model.py` | 신규 | FeatureBuilder, PeakProbabilityModel, HistGBPeakModel |
| `src/analysis/peak_model_metrics.py` | 신규 | PR-AUC, Brier, Calibration |
| `src/analysis/backtest_pipeline.py` | 수정 | _m1_pred 교체, BacktestBar.volume, BacktestFoldResult 메트릭 |
| `tests/test_peak_probability_model.py` | 신규 | 피처 누수 방지 포함 유닛 테스트 |
| `tests/test_peak_model_metrics.py` | 신규 | 메트릭 유닛 테스트 |
