"""평가 지표 — canonical 카운트, precision/recall (ground truth 있을 때),
 ablation 비교 표.

ground truth 가 없는 시뮬레이션 모드에선 canonical 수 + 정책 간 일치율로
간접 평가. ground truth 가 있는 모드에선 정확한 precision/recall.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PolicyResult:
    """한 정책의 한 잡 (또는 데이터셋) 에 대한 결과."""
    policy_name: str
    n_screens: int = 0
    n_canonicals: int = 0
    n_pairs_evaluated: int = 0
    n_pairs_matched: int = 0
    true_positive: int = 0
    false_positive: int = 0
    true_negative: int = 0
    false_negative: int = 0
    match_log: list[tuple[int, int, bool]] = field(default_factory=list)
        # (screen_idx_a, screen_idx_b, matched_or_not)

    @property
    def precision(self) -> float:
        denom = self.true_positive + self.false_positive
        return self.true_positive / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positive + self.false_negative
        return self.true_positive / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def summary_row(self) -> dict:
        return {
            "policy": self.policy_name,
            "n_screens": self.n_screens,
            "n_canonicals": self.n_canonicals,
            "n_pairs_evaluated": self.n_pairs_evaluated,
            "n_pairs_matched": self.n_pairs_matched,
            "TP": self.true_positive,
            "FP": self.false_positive,
            "TN": self.true_negative,
            "FN": self.false_negative,
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "f1": round(self.f1, 3),
        }


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    if not u:
        return 0.0
    return len(a & b) / len(u)


def format_table(rows: list[dict], cols: list[str]) -> str:
    """간단한 ASCII 표 생성. CLI 출력용."""
    if not rows:
        return "(no rows)"
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    header = " | ".join(c.ljust(widths[c]) for c in cols)
    sep = "-+-".join("-" * widths[c] for c in cols)
    lines = [header, sep]
    for r in rows:
        lines.append(" | ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))
    return "\n".join(lines)
