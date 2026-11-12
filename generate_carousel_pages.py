"""
Generate and render Adaptive Carousel Quota Pages for TURZX 3.5" IPS Display.
Supports Dynamic Agent Loading from AgentRegistry, Adaptive Pagination (> 8 agents),
Sub-Quota telemetry (Gemini, Claude, Argon), and real-time reset timers.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

try:
    from turzx.provisioner import AgentProvisioner
    _prov = AgentProvisioner()
except Exception:
    _prov = None

# Canvas dimensions
WIDTH = 320
HEIGHT = 480

# Color Palette inspired by Anthropic Frontend Design & Dark UI Telemetry
PALETTE = {
    "bg_main": (11, 15, 25),          # Deep Charcoal Slate
    "header_bg": (21, 30, 48),        # Navy Slate
    "header_border": (37, 53, 84),
    "card_bg": (19, 27, 46),          # Slate Card
    "card_border": (37, 53, 84),
    "track_bg": (15, 23, 42),         # Bar track
    "track_border": (51, 65, 85),
    "text_primary": (248, 250, 252),  # Ice White
    "text_muted": (148, 163, 184),    # Slate-400
    "text_dim": (100, 116, 139),      # Slate-500
    # Provider Signature Accents
    "gemini": (2, 132, 199),          # Electric Sky Blue
    "gemini_bg": (8, 47, 73),
    "claude": (217, 119, 6),          # Warm Terracotta Amber
    "claude_bg": (69, 26, 3),
    "argon": (124, 58, 237),          # Deep Violet / Iris
    "argon_bg": (46, 16, 101),
    # Status badges
    "healthy": {
        "bar": (5, 150, 105),
        "badge_bg": (6, 78, 59),
        "badge_text": (110, 231, 183),
        "badge_border": (5, 150, 105),
    },
    "warning": {
        "bar": (217, 119, 6),
        "badge_bg": (69, 26, 3),
        "badge_text": (252, 211, 77),
        "badge_border": (217, 119, 6),
    },
    "critical": {
        "bar": (220, 38, 38),
        "badge_bg": (69, 10, 10),
        "badge_text": (252, 165, 165),
        "badge_border": (220, 38, 38),
    },
}


def get_fonts():
    font_path = None
    for cand in [
        "/System/Library/Fonts/SFCompact.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]:
        if os.path.isfile(cand):
            font_path = cand
            break
    if font_path:
        return {
            "title": ImageFont.truetype(font_path, 13),
            "section": ImageFont.truetype(font_path, 12),
            "body": ImageFont.truetype(font_path, 11),
            "caption": ImageFont.truetype(font_path, 10),
            "micro": ImageFont.truetype(font_path, 9),
        }
    f = ImageFont.load_default()
    return {"title": f, "section": f, "body": f, "caption": f, "micro": f}


FONTS = get_fonts()


def get_active_agents(prov: Optional[AgentProvisioner] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Retrieve active AGY and Codex agents dynamically."""
    p = prov or _prov
    if p:
        data = p.get_all_agents()
        return data.get("agy", []), data.get("codex", [])
    # Fallback default static data
    return (
        [
            {"id": "agy1", "name": "AGY1 (Primary)", "usage_pct": 25.5, "status": "healthy", "gemini_tokens_used": 255000, "gemini_tokens_limit": 1000000, "claude_reqs_used": 18, "claude_reqs_limit": 100, "argon_units_used": 4, "argon_units_limit": 25},
            {"id": "agy2", "name": "AGY2 (Research)", "usage_pct": 42.0, "status": "healthy", "gemini_tokens_used": 550000, "gemini_tokens_limit": 1000000, "claude_reqs_used": 35, "claude_reqs_limit": 100, "argon_units_used": 8, "argon_units_limit": 25},
            {"id": "agy3", "name": "AGY3 (Worker)", "usage_pct": 15.0, "status": "healthy", "gemini_tokens_used": 180000, "gemini_tokens_limit": 1000000, "claude_reqs_used": 12, "claude_reqs_limit": 100, "argon_units_used": 5, "argon_units_limit": 25},
            {"id": "agy4", "name": "AGY4 (Reviewer)", "usage_pct": 78.5, "status": "warning", "gemini_tokens_used": 850000, "gemini_tokens_limit": 1000000, "claude_reqs_used": 72, "claude_reqs_limit": 100, "argon_units_used": 40, "argon_units_limit": 50},
            {"id": "agy5", "name": "AGY5 (Standby)", "usage_pct": 10.0, "status": "healthy", "gemini_tokens_used": 120000, "gemini_tokens_limit": 1000000, "claude_reqs_used": 8, "claude_reqs_limit": 100, "argon_units_used": 0, "argon_units_limit": 25},
        ],
        [
            {"id": "codex1", "name": "CODEX1 (Backend Core)", "usage_pct": 88.0, "status": "critical", "claude_reqs_used": 92, "claude_reqs_limit": 100, "argon_units_used": 65, "argon_units_limit": 100, "gemini_tokens_used": 350000, "gemini_tokens_limit": 1000000, "turns": "1,357", "items": "52,027"},
            {"id": "codex2", "name": "CODEX2 (UI / Frontend)", "usage_pct": 54.0, "status": "healthy", "claude_reqs_used": 58, "claude_reqs_limit": 100, "argon_units_used": 40, "argon_units_limit": 100, "gemini_tokens_used": 200000, "gemini_tokens_limit": 1000000, "turns": "925", "items": "29,955"},
            {"id": "codex3", "name": "CODEX3 (Infra / CI)", "usage_pct": 31.0, "status": "healthy", "claude_reqs_used": 35, "claude_reqs_limit": 100, "argon_units_used": 25, "argon_units_limit": 100, "gemini_tokens_used": 100000, "gemini_tokens_limit": 1000000, "turns": "562", "items": "24,499"},
        ],
    )


def draw_header_and_tabs(draw, title: str, active_page: int, total_pages: int = 3):
    """Draw distinctive header with pagination dots/tabs."""
    draw.rectangle([(0, 0), (WIDTH, 34)], fill=PALETTE["header_bg"])
    draw.line([(0, 34), (WIDTH, 34)], fill=PALETTE["header_border"], width=1)

    # Title
    draw.text((12, 10), title, fill=PALETTE["text_primary"], font=FONTS["title"])

    # Carousel Tabs / Dots
    tab_x = WIDTH - 20 - (total_pages * 14)
    tab_y = 17
    for i in range(total_pages):
        dot_x = tab_x + (i * 12)
        if i == active_page:
            draw.ellipse([(dot_x - 4, tab_y - 4), (dot_x + 4, tab_y + 4)], fill=(56, 189, 248), outline=PALETTE["text_primary"])
        else:
            draw.ellipse([(dot_x - 3, tab_y - 3), (dot_x + 3, tab_y + 3)], fill=PALETTE["track_bg"], outline=PALETTE["text_dim"])

    page_lbl = f"{active_page+1}/{total_pages}"
    draw.text((WIDTH - 12, 11), page_lbl, fill=PALETTE["text_muted"], font=FONTS["caption"], anchor="ra")


def draw_footer(draw, text: str = "HERMES SCHEDULER: 10S CADENCE | AUTO-RESET: 00:00 UTC"):
    draw.rectangle([(0, HEIGHT - 22), (WIDTH, HEIGHT)], fill=PALETTE["header_bg"])
    draw.line([(0, HEIGHT - 22), (WIDTH, HEIGHT - 22)], fill=PALETTE["header_border"], width=1)
    draw.text((WIDTH // 2, HEIGHT - 11), text, fill=PALETTE["text_dim"], font=FONTS["caption"], anchor="mm")


def get_reset_timer_str(prov: Optional[AgentProvisioner] = None) -> str:
    p = prov or _prov
    if p:
        info = p.get_reset_schedule_info()
        return info.get("time_remaining", "05h 20m")
    return "05h 20m"


# =========================================================================
# Page 0: Cluster Overview (renders all agents if <= 8, or cluster-specific if > 8)
# =========================================================================
def render_page_overview(
    active_page: int = 0,
    total_pages: int = 3,
    cluster: Optional[str] = None,
    prov: Optional[AgentProvisioner] = None,
) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), color=PALETTE["bg_main"])
    draw = ImageDraw.Draw(img)

    agy_list, codex_list = get_active_agents(prov=prov)

    if cluster == "agy" or (total_pages > 3 and active_page == 0 and cluster != "codex"):
        title = "HERMES: AGY CLUSTER"
        agents_to_show = [(a["id"].upper(), a.get("usage_pct", 0.0), a.get("status", "healthy"), "AGY") for a in agy_list]
        footer_sub = "AGY OVERVIEW"
    elif cluster == "codex" or (total_pages > 3 and active_page == 1):
        title = "HERMES: CODEX CLUSTER"
        agents_to_show = [(c["id"].upper(), c.get("usage_pct", 0.0), c.get("status", "healthy"), "CODEX") for c in codex_list]
        footer_sub = "CODEX OVERVIEW"
    else:
        title = "HERMES AGENT CLUSTERS"
        combined = []
        for a in agy_list:
            combined.append((a["id"].upper(), a.get("usage_pct", 0.0), a.get("status", "healthy"), "AGY"))
        for c in codex_list:
            combined.append((c["id"].upper(), c.get("usage_pct", 0.0), c.get("status", "healthy"), "CODEX"))
        agents_to_show = combined
        footer_sub = "OVERVIEW"

    draw_header_and_tabs(draw, title, active_page=active_page, total_pages=total_pages)

    # Render up to 8 agents on this overview screen
    display_agents = agents_to_show[:8]
    y = 40
    card_h = 48
    gap = 4

    for name, pct, status, cluster_type in display_agents:
        st = PALETTE.get(status, PALETTE["healthy"])
        draw.rounded_rectangle([(8, y), (WIDTH - 8, y + card_h)], radius=4, fill=PALETTE["card_bg"], outline=PALETTE["card_border"], width=1)

        # Name
        draw.text((16, y + 6), name, fill=PALETTE["text_primary"], font=FONTS["section"])

        # Tag
        tag_color = PALETTE["gemini"] if cluster_type == "AGY" else PALETTE["claude"]
        draw.text((105, y + 7), f"[{cluster_type}]", fill=tag_color, font=FONTS["micro"])

        # Badge
        badge_text = f"{pct:.1f}%"
        draw.rounded_rectangle([(WIDTH - 66, y + 5), (WIDTH - 16, y + 20)], radius=3, fill=st["badge_bg"], outline=st["badge_border"], width=1)
        draw.text((WIDTH - 41, y + 12), badge_text, fill=st["badge_text"], font=FONTS["caption"], anchor="mm")

        # Bar Track
        bar_x = 16
        bar_y = y + 26
        bar_w = WIDTH - 32
        bar_h = 14
        draw.rectangle([(bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h)], fill=PALETTE["track_bg"], outline=PALETTE["track_border"])

        fill_w = int(bar_w * (pct / 100.0))
        if fill_w > 0:
            draw.rectangle([(bar_x + 1, bar_y + 1), (bar_x + fill_w, bar_y + bar_h - 1)], fill=st["bar"])

        y += card_h + gap

    reset_str = get_reset_timer_str(prov=prov)
    draw_footer(draw, f"PAGE {active_page+1}/{total_pages}: {footer_sub} | RESET IN: {reset_str}")
    return img


# =========================================================================
# Page 1: AGY Cluster Deep-Dive
# =========================================================================
def render_page_agy(
    active_page: int = 1,
    total_pages: int = 3,
    prov: Optional[AgentProvisioner] = None,
) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), color=PALETTE["bg_main"])
    draw = ImageDraw.Draw(img)
    draw_header_and_tabs(draw, "AGY CLUSTER: SUB-QUOTAS", active_page=active_page, total_pages=total_pages)

    agy_list, _ = get_active_agents(prov=prov)
    agents_to_display = agy_list[:6]
    num_agents = len(agents_to_display)
    if num_agents <= 5:
        y = 38
        card_h = 78
        gap = 4
    else:
        y = 36
        card_h = 66
        gap = 3

    for agent in agents_to_display:
        st = PALETTE.get(agent.get("status", "healthy"), PALETTE["healthy"])
        draw.rounded_rectangle([(8, y), (WIDTH - 8, y + card_h)], radius=4, fill=PALETTE["card_bg"], outline=PALETTE["card_border"], width=1)

        # Header line: ID + Overall Badge
        agent_id = agent["id"].upper()
        draw.text((16, y + 4), agent_id, fill=PALETTE["text_primary"], font=FONTS["section"])
        badge_text = f"{agent.get('usage_pct', 0.0):.1f}%"
        draw.rounded_rectangle([(WIDTH - 66, y + 3), (WIDTH - 16, y + 17)], radius=3, fill=st["badge_bg"], outline=st["badge_border"])
        draw.text((WIDTH - 41, y + 10), badge_text, fill=st["badge_text"], font=FONTS["caption"], anchor="mm")

        # Sub-quotas: Gemini, Claude, Argon
        # 1. Gemini
        g_used = agent.get("gemini_tokens_used", 0)
        g_limit = agent.get("gemini_tokens_limit", 1000000)
        g_pct = (g_used / g_limit * 100.0) if g_limit > 0 else 0
        g_y = y + (20 if num_agents > 5 else 23)
        draw.text((16, g_y), "GEMINI", fill=PALETTE["gemini"], font=FONTS["micro"])
        draw.rectangle([(62, g_y + 2), (140, g_y + 9)], fill=PALETTE["track_bg"], outline=PALETTE["track_border"])
        gw = int(76 * (min(g_pct, 100.0) / 100.0))
        if gw > 0:
            draw.rectangle([(63, g_y + 3), (63 + gw, g_y + 8)], fill=PALETTE["gemini"])
        draw.text((146, g_y), f"{g_pct:.0f}%", fill=PALETTE["text_muted"], font=FONTS["micro"])

        # 2. Claude
        c_used = agent.get("claude_reqs_used", 0)
        c_limit = agent.get("claude_reqs_limit", 100)
        c_pct = (c_used / c_limit * 100.0) if c_limit > 0 else 0
        draw.text((178, g_y), "CLAUDE", fill=PALETTE["claude"], font=FONTS["micro"])
        draw.rectangle([(226, g_y + 2), (290, g_y + 9)], fill=PALETTE["track_bg"], outline=PALETTE["track_border"])
        cw = int(62 * (min(c_pct, 100.0) / 100.0))
        if cw > 0:
            draw.rectangle([(227, g_y + 3), (227 + cw, g_y + 8)], fill=PALETTE["claude"])
        draw.text((294, g_y), f"{c_pct:.0f}%", fill=PALETTE["text_muted"], font=FONTS["micro"])

        # 3. Argon
        a_used = agent.get("argon_units_used", 0)
        a_limit = agent.get("argon_units_limit", 25)
        a_pct = (a_used / a_limit * 100.0) if a_limit > 0 else 0
        a_y = y + (34 if num_agents > 5 else 40)
        draw.text((16, a_y), "ARGON", fill=PALETTE["argon"], font=FONTS["micro"])
        draw.rectangle([(62, a_y + 2), (140, a_y + 9)], fill=PALETTE["track_bg"], outline=PALETTE["track_border"])
        aw = int(76 * (min(a_pct, 100.0) / 100.0))
        if aw > 0:
            draw.rectangle([(63, a_y + 3), (63 + aw, a_y + 8)], fill=PALETTE["argon"])
        draw.text((146, a_y), f"{a_pct:.0f}%", fill=PALETTE["text_muted"], font=FONTS["micro"])

        draw.text((178, a_y), "RESET: 00:00 UTC", fill=PALETTE["text_dim"], font=FONTS["micro"])

        foot_y = y + (49 if num_agents > 5 else 60)
        draw.line([(16, foot_y), (WIDTH - 16, foot_y)], fill=(30, 41, 59), width=1)
        draw.text((16, foot_y + 3), f"Preset: {agent.get('preset', 'full_stack')} | Mode: Daily Cap", fill=PALETTE["text_dim"], font=FONTS["micro"])

        y += card_h + gap

    reset_str = get_reset_timer_str(prov=prov)
    draw_footer(draw, f"PAGE {active_page+1}/{total_pages}: AGY DETAIL | NEXT: CODEX | IN: {reset_str}")
    return img


# =========================================================================
# Page 2: Codex Cluster Deep-Dive
# =========================================================================
def render_page_codex(
    active_page: int = 2,
    total_pages: int = 3,
    prov: Optional[AgentProvisioner] = None,
) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), color=PALETTE["bg_main"])
    draw = ImageDraw.Draw(img)
    draw_header_and_tabs(draw, "CODEX CLUSTER: TELEMETRY", active_page=active_page, total_pages=total_pages)

    _, codex_list = get_active_agents(prov=prov)
    agents_to_display = codex_list[:4]
    num_cdx = len(agents_to_display)
    if num_cdx <= 3:
        y = 40
        card_h = 130
        gap = 8
    else:
        y = 36
        card_h = 98
        gap = 5

    for cdx in agents_to_display:
        st = PALETTE.get(cdx.get("status", "healthy"), PALETTE["healthy"])
        draw.rounded_rectangle([(8, y), (WIDTH - 8, y + card_h)], radius=5, fill=PALETTE["card_bg"], outline=PALETTE["card_border"], width=1)

        # Row 1: Header
        cdx_id = cdx["id"].upper()
        draw.text((16, y + 6), cdx_id, fill=PALETTE["text_primary"], font=FONTS["section"])
        turns = cdx.get("turns", "0")
        items = cdx.get("items", "0")
        draw.text((100, y + 7), f"Turns: {turns} | Items: {items}", fill=PALETTE["text_muted"], font=FONTS["caption"])

        badge_text = f"{cdx.get('usage_pct', 0.0):.1f}%"
        draw.rounded_rectangle([(WIDTH - 66, y + 5), (WIDTH - 16, y + 19)], radius=3, fill=st["badge_bg"], outline=st["badge_border"])
        draw.text((WIDTH - 41, y + 12), badge_text, fill=st["badge_text"], font=FONTS["caption"], anchor="mm")

        # Bar 1: Claude Sonnet
        c_used = cdx.get("claude_reqs_used", 0)
        c_limit = cdx.get("claude_reqs_limit", 100)
        c_pct = (c_used / c_limit * 100.0) if c_limit > 0 else 0
        b1_y = y + (22 if num_cdx > 3 else 28)
        draw.text((16, b1_y), "CLAUDE 3.5 SONNET", fill=PALETTE["claude"], font=FONTS["micro"])
        draw.text((WIDTH - 50, b1_y), f"{c_pct:.0f}%", fill=PALETTE["claude"], font=FONTS["micro"], anchor="ra")
        bar1_rect_y = b1_y + (10 if num_cdx > 3 else 12)
        draw.rectangle([(16, bar1_rect_y), (WIDTH - 16, bar1_rect_y + 7)], fill=PALETTE["track_bg"], outline=PALETTE["track_border"])
        cw = int((WIDTH - 34) * (min(c_pct, 100.0) / 100.0))
        if cw > 0:
            draw.rectangle([(17, bar1_rect_y + 1), (17 + cw, bar1_rect_y + 6)], fill=PALETTE["claude"])

        # Bar 2: Argon Reasoning
        a_used = cdx.get("argon_units_used", 0)
        a_limit = cdx.get("argon_units_limit", 100)
        a_pct = (a_used / a_limit * 100.0) if a_limit > 0 else 0
        b2_y = y + (42 if num_cdx > 3 else 54)
        draw.text((16, b2_y), "ARGON REASONING CORE", fill=PALETTE["argon"], font=FONTS["micro"])
        draw.text((WIDTH - 50, b2_y), f"{a_pct:.0f}%", fill=PALETTE["argon"], font=FONTS["micro"], anchor="ra")
        bar2_rect_y = b2_y + (10 if num_cdx > 3 else 12)
        draw.rectangle([(16, bar2_rect_y), (WIDTH - 16, bar2_rect_y + 7)], fill=PALETTE["track_bg"], outline=PALETTE["track_border"])
        aw = int((WIDTH - 34) * (min(a_pct, 100.0) / 100.0))
        if aw > 0:
            draw.rectangle([(17, bar2_rect_y + 1), (17 + aw, bar2_rect_y + 6)], fill=PALETTE["argon"])

        # Bar 3: Gemini Auxiliary
        g_used = cdx.get("gemini_tokens_used", 0)
        g_limit = cdx.get("gemini_tokens_limit", 1000000)
        g_pct = (g_used / g_limit * 100.0) if g_limit > 0 else 0
        b3_y = y + (62 if num_cdx > 3 else 80)
        draw.text((16, b3_y), "GEMINI 1.5 FLASH (AUX)", fill=PALETTE["gemini"], font=FONTS["micro"])
        draw.text((WIDTH - 50, b3_y), f"{g_pct:.0f}%", fill=PALETTE["gemini"], font=FONTS["micro"], anchor="ra")
        bar3_rect_y = b3_y + (10 if num_cdx > 3 else 12)
        draw.rectangle([(16, bar3_rect_y), (WIDTH - 16, bar3_rect_y + 7)], fill=PALETTE["track_bg"], outline=PALETTE["track_border"])
        gw = int((WIDTH - 34) * (min(g_pct, 100.0) / 100.0))
        if gw > 0:
            draw.rectangle([(17, bar3_rect_y + 1), (17 + gw, bar3_rect_y + 6)], fill=PALETTE["gemini"])

        # Card Footer: Reset countdown
        reset_str = get_reset_timer_str(prov=prov)
        foot_y = y + (82 if num_cdx > 3 else 108)
        draw.line([(16, foot_y), (WIDTH - 16, foot_y)], fill=(30, 41, 59), width=1)
        draw.text((16, foot_y + 4), f"Daily Cap Reset in: {reset_str} (00:00 UTC)", fill=PALETTE["text_dim"], font=FONTS["micro"])

        y += card_h + gap

    reset_str = get_reset_timer_str(prov=prov)
    draw_footer(draw, f"PAGE {active_page+1}/{total_pages}: CODEX TELEMETRY | IN: {reset_str}")
    return img


def get_carousel_pages(prov: Optional[AgentProvisioner] = None) -> List[Tuple[str, Any]]:
    """Return active list of pages for carousel."""
    agy_list, codex_list = get_active_agents(prov=prov)
    total_agents = len(agy_list) + len(codex_list)

    if total_agents <= 8:
        return [
            ("Page 0: Cluster Overview", lambda: render_page_overview(0, 3, prov=prov)),
            ("Page 1: AGY Cluster Deep-Dive", lambda: render_page_agy(1, 3, prov=prov)),
            ("Page 2: Codex Cluster Deep-Dive", lambda: render_page_codex(2, 3, prov=prov)),
        ]
    else:
        # Adaptive 4 pages
        return [
            ("Page 0a: AGY Cluster Overview", lambda: render_page_overview(0, 4, cluster="agy", prov=prov)),
            ("Page 0b: Codex Cluster Overview", lambda: render_page_overview(1, 4, cluster="codex", prov=prov)),
            ("Page 1: AGY Cluster Deep-Dive", lambda: render_page_agy(2, 4, prov=prov)),
            ("Page 2: Codex Cluster Deep-Dive", lambda: render_page_codex(3, 4, prov=prov)),
        ]


if __name__ == "__main__":
    p0 = render_page_overview(0, 3)
    p0.save("carousel_page_0_overview.png")
    print("Saved: carousel_page_0_overview.png")

    p1 = render_page_agy(1, 3)
    p1.save("carousel_page_1_agy.png")
    print("Saved: carousel_page_1_agy.png")

    p2 = render_page_codex(2, 3)
    p2.save("carousel_page_2_codex.png")
    print("Saved: carousel_page_2_codex.png")
