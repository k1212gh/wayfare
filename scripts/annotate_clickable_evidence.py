"""메가커피 캡처에 거짓 클릭 불가 영역을 박스+라벨로 표시하고 민감정보를 마스킹.

산출물:
  screenatlas/reports/clickable_evidence/
    01_home_bottom_tabs.png      — 홈 화면, 하단 탭 4개 + 메가오더
    02_home_quickorder.png       — 홈 화면, 퀵오더 진입 카드
    03_payment_options.png       — 결제 화면, 포장방식/결제수단
    04_payment_consent.png       — 결제 화면, 동의 토글
    overview.png                 — 두 화면을 나란히 + 캡션
"""

from __future__ import annotations

import sys
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path("screenatlas/workspace/432b611e/dynamic/raw")
OUT = Path("screenatlas/reports/clickable_evidence")
OUT.mkdir(parents=True, exist_ok=True)

FONT_PATH = "C:/Windows/Fonts/malgun.ttf"  # 맑은 고딕
FONT_BOLD = "C:/Windows/Fonts/malgunbd.ttf"


def load_font(size, bold=False):
    path = FONT_BOLD if bold and Path(FONT_BOLD).exists() else FONT_PATH
    return ImageFont.truetype(path, size)


def parse_bounds(s):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", s or "")
    return tuple(int(g) for g in m.groups()) if m else None


def find_views(xml_path, predicates):
    """predicates: list of (name, fn(attrs, parent_cls) -> bool). 첫 매치만 가져옴."""
    tree = ET.parse(xml_path)
    found = {name: None for name, _ in predicates}

    def walk(elem, parent_cls=""):
        for child in elem:
            attrs = child.attrib
            cls = attrs.get("class", "")
            for name, fn in predicates:
                if found[name] is None and fn(attrs, parent_cls):
                    found[name] = dict(attrs)
            walk(child, cls if child.tag == "node" else parent_cls)

    walk(tree.getroot(), "")
    return found


def draw_box(draw, bounds, color, width=6, label=None, font=None, label_pos="above"):
    x1, y1, x2, y2 = bounds
    draw.rectangle([x1, y1, x2, y2], outline=color, width=width)
    if label and font:
        text_w = font.getbbox(label)[2] - font.getbbox(label)[0]
        text_h = font.getbbox(label)[3] - font.getbbox(label)[1]
        pad = 8
        if label_pos == "above":
            ly = max(0, y1 - text_h - pad * 2)
            lx = x1
        elif label_pos == "below":
            ly = y2 + pad
            lx = x1
        elif label_pos == "right":
            ly = y1
            lx = x2 + pad
        else:
            ly, lx = y1, x1
        draw.rectangle([lx, ly, lx + text_w + pad * 2, ly + text_h + pad * 2], fill=color)
        draw.text((lx + pad, ly + pad - 2), label, fill="white", font=font)


def mask_region(draw, bounds, label="MASKED"):
    """민감 정보 검정 마스킹."""
    x1, y1, x2, y2 = bounds
    draw.rectangle([x1, y1, x2, y2], fill="black")
    font = load_font(18, bold=True)
    text_w = font.getbbox(label)[2]
    text_h = font.getbbox(label)[3]
    cx = x1 + (x2 - x1 - text_w) // 2
    cy = y1 + (y2 - y1 - text_h) // 2
    draw.text((cx, cy), label, fill="white", font=font)


# ── 1. 홈 화면 — 하단 탭 ─────────────────────────
def render_home_bottom_tabs():
    img = Image.open(ROOT / "capture_0000.png").convert("RGB")
    draw = ImageDraw.Draw(img)
    xml = ROOT / "capture_0000.xml"

    targets = [
        ("홈",      lambda a, p: a.get("text") == "홈" and parse_bounds(a.get("bounds"))[1] > 2200),
        ("이벤트",  lambda a, p: a.get("text") == "이벤트" and parse_bounds(a.get("bounds"))[1] > 2200),
        ("선물하기",lambda a, p: a.get("text") == "선물하기" and parse_bounds(a.get("bounds"))[1] > 2200),
        ("전체메뉴",lambda a, p: a.get("text") == "전체메뉴" and parse_bounds(a.get("bounds"))[1] > 2200),
        ("점포",    lambda a, p: a.get("text") == "역삼대로점"),
    ]
    found = find_views(xml, targets)

    # 점포 마스킹 — 하단 탭 박스 그리기 전에
    if found["점포"]:
        b = parse_bounds(found["점포"]["bounds"])
        if b:
            mask_region(draw, b, "점포명")

    font = load_font(28, bold=True)
    color = (220, 38, 38)  # red

    # 하단 탭 4개 — 텍스트가 작은 자리니까 부모 영역으로 확장
    for name in ["홈", "이벤트", "선물하기", "전체메뉴"]:
        v = found[name]
        if v:
            b = parse_bounds(v["bounds"])
            # 탭 1개의 시각 영역은 텍스트보다 크다 — 위로 확장 (아이콘 포함)
            x1, y1, x2, y2 = b
            tab_h = 180  # 아이콘 + 라벨
            x1 -= 30
            x2 += 30
            y1 = y2 - tab_h
            draw_box(draw, (x1, y1, x2, y2), color, width=5)

    # 캡션 박스 (상단)
    caption_h = 80
    caption_bg = Image.new("RGB", (img.width, caption_h), (250, 240, 230))
    new_img = Image.new("RGB", (img.width, img.height + caption_h), "white")
    new_img.paste(caption_bg, (0, 0))
    new_img.paste(img, (0, caption_h))
    cd = ImageDraw.Draw(new_img)
    f = load_font(26, bold=True)
    cd.text((20, 22), "거짓 클릭 불가 — 하단 4개 탭 (홈 · 이벤트 · 선물하기 · 전체메뉴)", fill=(80, 30, 0), font=f)

    out = OUT / "01_home_bottom_tabs.png"
    new_img.save(out)
    return out


# ── 2. 홈 화면 — 퀵오더 진입 카드 ─────────────────
def render_home_quickorder():
    img = Image.open(ROOT / "capture_0000.png").convert("RGB")
    draw = ImageDraw.Draw(img)
    xml = ROOT / "capture_0000.xml"

    targets = [
        ("최근주문", lambda a, p: a.get("text") == "최근주문"),
        ("MY퀵오더", lambda a, p: a.get("text") == "MY퀵오더"),
        ("바로 주문", lambda a, p: a.get("text") == "바로 주문"),
        ("점포",    lambda a, p: a.get("text") == "역삼대로점"),
    ]
    found = find_views(xml, targets)

    if found["점포"]:
        b = parse_bounds(found["점포"]["bounds"])
        if b: mask_region(draw, b, "점포명")

    color = (220, 38, 38)
    font = load_font(24, bold=True)

    for name in ["최근주문", "MY퀵오더", "바로 주문"]:
        v = found[name]
        if v:
            b = parse_bounds(v["bounds"])
            x1, y1, x2, y2 = b
            x1 -= 20; x2 += 20; y1 -= 14; y2 += 14
            draw_box(draw, (x1, y1, x2, y2), color, width=5,
                     label=name + " (clickable=false)", font=font, label_pos="above")

    caption_h = 80
    new_img = Image.new("RGB", (img.width, img.height + caption_h), (250, 240, 230))
    new_img.paste(img, (0, caption_h))
    cd = ImageDraw.Draw(new_img)
    f = load_font(26, bold=True)
    cd.text((20, 22), "거짓 클릭 불가 — 홈 화면 핵심 진입 텍스트", fill=(80, 30, 0), font=f)

    out = OUT / "02_home_quickorder.png"
    new_img.save(out)
    return out


# ── 3. 결제 화면 — 포장 / 결제수단 ─────────────────
def render_payment_options():
    img = Image.open(ROOT / "capture_0003.png").convert("RGB")
    draw = ImageDraw.Draw(img)
    xml = ROOT / "capture_0003.xml"

    targets = [
        ("포장주문", lambda a, p: a.get("text") == "포장주문" and 1300 < parse_bounds(a.get("bounds"))[1] < 1600),
        ("매장이용", lambda a, p: a.get("text") == "매장이용"),
        ("결제수단", lambda a, p: a.get("text") == "결제수단"),
        ("변경하기", lambda a, p: a.get("text") == "변경하기"),
        ("점포",    lambda a, p: a.get("text") == "역삼대로점"),
        ("카드",    lambda a, p: a.get("text") and "신용카드" in a.get("text")),
    ]
    found = find_views(xml, targets)

    # 마스킹: 점포명, 카드 정보
    for k in ["점포", "카드"]:
        if found[k]:
            b = parse_bounds(found[k]["bounds"])
            label = "점포명" if k == "점포" else "카드정보"
            if b: mask_region(draw, b, label)

    color = (220, 38, 38)
    font = load_font(22, bold=True)
    for name in ["포장주문", "매장이용", "결제수단", "변경하기"]:
        v = found[name]
        if v:
            b = parse_bounds(v["bounds"])
            x1, y1, x2, y2 = b
            x1 -= 14; x2 += 14; y1 -= 10; y2 += 10
            draw_box(draw, (x1, y1, x2, y2), color, width=4,
                     label=name + " (clickable=false)", font=font, label_pos="right")

    caption_h = 80
    new_img = Image.new("RGB", (img.width, img.height + caption_h), (250, 240, 230))
    new_img.paste(img, (0, caption_h))
    cd = ImageDraw.Draw(new_img)
    f = load_font(26, bold=True)
    cd.text((20, 22), "거짓 클릭 불가 — 결제 화면 옵션/결제수단", fill=(80, 30, 0), font=f)

    out = OUT / "03_payment_options.png"
    new_img.save(out)
    return out


# ── 4. 결제 화면 — 동의 토글 ───────────────────────
def render_payment_consent():
    img = Image.open(ROOT / "capture_0003.png").convert("RGB")
    draw = ImageDraw.Draw(img)
    xml = ROOT / "capture_0003.xml"

    targets = [
        ("동의문",   lambda a, p: a.get("text") and a.get("text").startswith("상기 주문")),
        ("약관링크", lambda a, p: a.get("text") and "결제대행" in a.get("text") and "이용약관" in a.get("text")),
        ("점포",     lambda a, p: a.get("text") == "역삼대로점"),
        ("카드",     lambda a, p: a.get("text") and "신용카드" in a.get("text")),
    ]
    found = find_views(xml, targets)

    for k in ["점포", "카드"]:
        if found[k]:
            b = parse_bounds(found[k]["bounds"])
            label = "점포명" if k == "점포" else "카드정보"
            if b: mask_region(draw, b, label)

    color = (220, 38, 38)
    font = load_font(22, bold=True)
    for name, label in [("동의문", "동의 본문"), ("약관링크", "약관 링크")]:
        v = found[name]
        if v:
            b = parse_bounds(v["bounds"])
            x1, y1, x2, y2 = b
            x1 -= 10; x2 += 10; y1 -= 8; y2 += 8
            draw_box(draw, (x1, y1, x2, y2), color, width=4,
                     label=label + " (clickable=false)", font=font, label_pos="above")

    caption_h = 80
    new_img = Image.new("RGB", (img.width, img.height + caption_h), (250, 240, 230))
    new_img.paste(img, (0, caption_h))
    cd = ImageDraw.Draw(new_img)
    f = load_font(26, bold=True)
    cd.text((20, 22), "거짓 클릭 불가 — 결제 화면 동의 토글/약관", fill=(80, 30, 0), font=f)

    out = OUT / "04_payment_consent.png"
    new_img.save(out)
    return out


# ── 5. 두 화면을 나란히 합친 overview ─────────────
def render_overview(paths):
    images = [Image.open(p) for p in paths]
    # 4장을 2x2로 — 각 너비를 맞춤
    target_w = 540  # 작게
    resized = []
    for im in images:
        ratio = target_w / im.width
        h = int(im.height * ratio)
        resized.append(im.resize((target_w, h), Image.LANCZOS))
    max_h = max(im.height for im in resized)
    margin = 20
    title_h = 80
    total_w = target_w * 2 + margin * 3
    total_h = max_h * 2 + margin * 3 + title_h
    canvas = Image.new("RGB", (total_w, total_h), "white")
    cd = ImageDraw.Draw(canvas)
    f = load_font(30, bold=True)
    cd.text((margin, margin), "메가커피 — 거짓 클릭 불가 사례 4장",
            fill=(40, 40, 40), font=f)
    positions = [
        (margin, title_h + margin),
        (margin * 2 + target_w, title_h + margin),
        (margin, title_h + margin * 2 + max_h),
        (margin * 2 + target_w, title_h + margin * 2 + max_h),
    ]
    for im, pos in zip(resized, positions):
        canvas.paste(im, pos)
    out = OUT / "overview.png"
    canvas.save(out)
    return out


if __name__ == "__main__":
    p1 = render_home_bottom_tabs()
    p2 = render_home_quickorder()
    p3 = render_payment_options()
    p4 = render_payment_consent()
    overview = render_overview([p1, p2, p3, p4])
    print("생성 완료:")
    for p in [p1, p2, p3, p4, overview]:
        print(f"  {p}  ({p.stat().st_size // 1024} KB)")
