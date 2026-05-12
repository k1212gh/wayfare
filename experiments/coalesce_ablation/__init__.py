"""Coalesce policy ablation experiments.

각 매칭 정책 (L1 단독, L2 단독, L3 단독, L1+L2, L1+L2+L3) 의 효과를
정량적으로 비교하기 위한 실험 묶음.

사용법:
  python -m experiments.coalesce_ablation.run_replay <tour_id> [tour_id ...]
  python -m experiments.coalesce_ablation.threshold_scan <tour_id>
  python -m experiments.coalesce_ablation.synthetic
  python -m experiments.coalesce_ablation.ground_truth
"""
