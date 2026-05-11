"""화면 중복 탐지 노션 페이지용 다이어그램 2장 생성.

Diagram 1 — 사건 흐름: 같은 PNG 5개가 어떻게 5개 노드로 굳어지는지
Diagram 2 — 보정 로직: 캡처 → 노이즈 제거 → 4지문 → 매칭 → 식별자
"""
from __future__ import annotations
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.font_manager as fm
import platform

# 한글 폰트
if platform.system() == "Windows":
    plt.rcParams["font.family"] = "Malgun Gothic"
else:
    plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["axes.unicode_minus"] = False

OUT = Path(__file__).resolve().parent.parent / "reports"
OUT.mkdir(exist_ok=True)


def _box(ax, x, y, w, h, text, *, fc="#E8F0FE", ec="#3367D6", lw=1.2, fontsize=10, fontweight="normal"):
    rect = patches.FancyBboxPatch(
        (x - w / 2, y - h / 2), w, h,
        boxstyle="round,pad=0.02",
        linewidth=lw, edgecolor=ec, facecolor=fc,
    )
    ax.add_patch(rect)
    ax.text(x, y, text, ha="center", va="center", fontsize=fontsize, fontweight=fontweight, wrap=True)


def _arrow(ax, x1, y1, x2, y2, text="", color="#666"):
    ax.annotate(
        "", xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(arrowstyle="->", color=color, lw=1.4),
    )
    if text:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.15, text,
                ha="center", va="bottom", fontsize=8, color="#444",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.9))


# ─── Diagram 1: 사건 흐름 (수정 전) ─────────────────────────────
def render_incident_flow():
    fig, ax = plt.subplots(figsize=(14, 9))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 11)
    ax.axis("off")

    ax.text(7, 10.4, "스탬프 유의사항 5중복 분기 — 사건 흐름",
            ha="center", fontsize=15, fontweight="bold")
    ax.text(7, 9.95, "(같은 픽셀의 화면이 어떻게 5개의 별개 노드로 굳어졌나)",
            ha="center", fontsize=10, color="#666")

    # 1. 디스크의 같은 PNG
    _box(ax, 2, 8.6, 3.2, 0.9, "디스크에 같은 PNG 5장\n(바이트 단위 동일)",
         fc="#FCE8E6", ec="#D33", fontweight="bold", fontsize=10)

    # 2. 5번 캡처 분기 — 작은 변동 5가지
    _box(ax, 7, 8.6, 4.2, 0.9, "캡처마다 끼어든 잡음\n(① 익명 뷰 1~4개 차이  ② 시계 분 변화)",
         fc="#FFF4E5", ec="#E8800B", fontsize=9)

    _arrow(ax, 3.6, 8.6, 4.9, 8.6, "캡처")

    # 3. 구조 해시 5개 분기
    hash_y = 7.0
    hash_xs = [1.6, 4.0, 6.4, 8.8, 11.2]
    hash_labels = [
        "구조 해시 ①\n(뷰 109 / 9시22분)",
        "구조 해시 ②\n(뷰 109 / 9시21분)",
        "구조 해시 ③\n(뷰 107 / 9시19분)",
        "구조 해시 ④\n(뷰 110 / 9시24분)",
        "구조 해시 ⑤\n(뷰 111 / 9시20분)",
    ]
    for x, lbl in zip(hash_xs, hash_labels):
        _box(ax, x, hash_y, 1.95, 0.95, lbl, fc="#FFEAEA", ec="#D33", fontsize=8)
        _arrow(ax, 7, 8.1, x, 7.5, "")

    # 분기 caption
    ax.text(13.0, hash_y, "→ 5개 다른\n식별자",
            ha="left", va="center", fontsize=9, color="#D33", fontweight="bold")

    # 4. 후속 머지 게이트들 (줄줄이 막힘)
    gate_y = 5.2
    gates = [
        ("\"구조 해시 결정권\" 정책",     "다르면 시각 유사도가\n뒤집을 수 없음", "#FFEAEA"),
        ("페이지 클러스터링",            "탐지 식별자를\n묶음 키로 사용",       "#FFEAEA"),
        ("LLM 라벨링",                   "5개 노드에 \"뒤로가기·\n독립·상세·이벤트\" 차별 라벨", "#FFEAEA"),
        ("픽셀 머지 게이트",             "라벨 다름 →\n머지 거부",            "#FFEAEA"),
        ("라벨 유사도 게이트",           "유사도 0.84 < 임계값 0.85\n→ 미달",   "#FFEAEA"),
    ]
    for i, (g, why, fc) in enumerate(gates):
        x = 1.6 + i * 2.4
        _box(ax, x, gate_y, 2.1, 1.2,
             f"{g}\n\n{why}", fc=fc, ec="#D33", fontsize=8)
        # 위에서 내려오는 차단 마크
        ax.text(x + 0.95, gate_y + 0.7, "X", fontsize=18, ha="center",
                color="#D33", fontweight="bold")

    ax.text(7, 4.2, "↓  모든 게이트가 차례로 막힘",
            ha="center", fontsize=10, color="#D33", fontweight="bold")

    # 5. 결과: 5개 노드로 굳어짐
    _box(ax, 7, 3.0, 8.0, 1.1,
         "최종 그래프에 \"스탬프 유의사항\" 화면이 5개의 별개 노드로 등록\n"
         "탐색 봇은 매번 새 화면이라고 인식 → 같은 화면을 5번 누르며 예산 낭비",
         fc="#D33", ec="#A00", fontsize=10, fontweight="bold")
    # 흰 글자
    ax.texts[-1].set_color("white")

    # 6. 교훈 문구
    ax.text(7, 1.2,
            "교훈 — 탐지의 false split 은 후속의 좋은 가드들도 무너뜨린다.\n"
            "라벨 가드는 false merge 를 막기 위한 안전장치인데, 탐지가 잘못 분리하면\n"
            "LLM 이 분리된 노드들에 차별 라벨을 만들어 그 가드를 거꾸로 활성화시킨다.",
            ha="center", fontsize=10, color="#222",
            bbox=dict(boxstyle="round,pad=0.5", fc="#FFF8E1", ec="#E8800B", lw=1.2))

    fig.tight_layout()
    out = OUT / "coalesce_incident_flow.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  saved: {out}")
    return out


# ─── Diagram 2: 보정 로직 (수정 후) ─────────────────────────────
def render_remediation_flow():
    fig, ax = plt.subplots(figsize=(14, 9.5))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 11)
    ax.axis("off")

    ax.text(7, 10.5, "탐지의 다층 가드 — 어떻게 풀었는가",
            ha="center", fontsize=15, fontweight="bold")
    ax.text(7, 10.05, "(노이즈 제거 → 4가지 지문 → 매칭 우선순위 → 픽셀 동일성 안전장치)",
            ha="center", fontsize=10, color="#666")

    # 입력
    _box(ax, 7, 9.2, 3.2, 0.7, "새 화면 캡처", fc="#E8F0FE", ec="#3367D6", fontweight="bold")

    # 1차 방어선: 노이즈 제거
    _box(ax, 7, 8.05, 11.5, 1.1,
         "1차 방어선 — 캡처 시점 노이즈 제거\n"
         "시계·타이머 위젯 제외  ·  리스트 항목 일련번호 마스킹  ·  시각 텍스트(\"3시 45분\") 마스킹  ·  "
         "스크롤 자식 항목 제외  ·  시스템 상태바 제외",
         fc="#E6F4EA", ec="#137333", fontsize=9)
    _arrow(ax, 7, 8.85, 7, 8.6, "")

    # 4가지 지문
    _arrow(ax, 7, 7.5, 7, 7.0, "")
    fp_y = 6.3
    fps = [
        ("픽셀 동일성",     "이미지 바이트\n해시",          "#FCE8E6", "#D33", "최상위 안전장치"),
        ("구조 기반 해시",  "뷰 트리 + 식별자\n+ 텍스트 설명", "#E8F0FE", "#3367D6", "주력 — 결정권"),
        ("시각 유사도",     "스크린샷 시각\n특징 압축",      "#FFF4E5", "#E8800B", "보조"),
        ("그래프 임베딩",   "뷰 트리 그래프\n→ 신경망 벡터", "#F3E8FD", "#9334E6", "마지막 보조선"),
    ]
    fp_xs = [1.8, 5.4, 9.0, 12.6]
    for x, (title, body, fc, ec, role) in zip(fp_xs, fps):
        _box(ax, x, fp_y, 2.6, 1.4,
             f"{title}\n\n{body}", fc=fc, ec=ec, fontsize=9, fontweight="bold")
        ax.text(x, fp_y - 1.05, role, ha="center", fontsize=8, color=ec, fontweight="bold")

    # 매칭 우선순위
    rule_y = 4.0
    _box(ax, 7, rule_y, 12.0, 0.9,
         "매칭 우선순위 — 픽셀 동일 = 무조건 같은 화면 ▷ 구조 해시 양쪽 존재 = 결정권 "
         "▷ 구조 해시 빈약 시에만 시각·그래프 fallback",
         fc="#FFF8E1", ec="#E8800B", fontsize=10, fontweight="bold")
    _arrow(ax, 7, 5.4, 7, 4.5, "")

    # 웹뷰 보정 (별도 박스)
    _box(ax, 3.5, 2.7, 6.5, 0.95,
         "웹뷰·컴포즈 보정 ①\n클릭 요소들의 세로 위치를 50픽셀 단위로 양자화해 해시에 포함",
         fc="#E8F0FE", ec="#3367D6", fontsize=9)
    _box(ax, 10.5, 2.7, 6.5, 0.95,
         "웹뷰·컴포즈 보정 ②\n익명 뷰 개수를 로그 구간(1, 2, 3-4, 5-9, 10-19...)으로 묶어 흔들림 흡수",
         fc="#E8F0FE", ec="#3367D6", fontsize=9)
    _arrow(ax, 7, 3.55, 3.5, 3.2, "")
    _arrow(ax, 7, 3.55, 10.5, 3.2, "")

    # 결과
    _box(ax, 7, 1.2, 11.5, 1.0,
         "→ 화면 식별자 부여  (메가커피 잡 기준 188개 → 29개로 6.5배 압축)\n"
         "→ 같은 식별자 안에서는 방문 횟수·시도 액션이 누적되어 같은 행동 반복 안 함",
         fc="#137333", ec="#0C5926", fontsize=10, fontweight="bold")
    ax.texts[-1].set_color("white")
    _arrow(ax, 7, 2.1, 7, 1.7, "")

    fig.tight_layout()
    out = OUT / "coalesce_remediation_flow.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  saved: {out}")
    return out


if __name__ == "__main__":
    print("[render]")
    render_incident_flow()
    render_remediation_flow()
    print("[done]")
