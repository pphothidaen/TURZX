"""
Generate and render Adaptive Carousel Quota Pages for TURZX 3.5" IPS Display.
Supports Dynamic Agent Loading from AgentRegistry, Adaptive Pagination (> 8 agents),
Sub-Quota telemetry (Gemini, Claude, Argon for AGY; Primary, Secondary, Reasoning for Codex),
and real-time reset timers.
"""

from __future__ import annotations

import os
import threading
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

# Deep Navy / Dark Blue Gradient Theme colors to mask IPS glow and reduce eye strain
GRADIENT_START = (5, 11, 20)    # #050B14 (Deep Navy Top)
GRADIENT_END = (10, 17, 40)     # #0A1128 (Dark Blue Bottom)
GRADIENT_SLATE = (11, 19, 41)   # #0B1329 (Slate Variant)

# Pre-rendered background template cache in memory to eliminate per-frame CPU overhead
_GRADIENT_TEMPLATE_CACHE: Dict[Tuple[int, int, Tuple[int, int, int], Tuple[int, int, int]], Image.Image] = {}
_GRADIENT_CACHE_LOCK = threading.Lock()


def get_gradient_background(
    width: int = WIDTH,
    height: int = HEIGHT,
    c_start: Tuple[int, int, int] = GRADIENT_START,
    c_end: Tuple[int, int, int] = GRADIENT_END,
) -> Image.Image:
    """Retrieve pre-rendered background template from memory cache.

    Generates a vertical linear gradient from #050B14 to #0A1128 to mask IPS glow,
    reduce eye strain, and eliminate per-frame gradient calculation overhead.
    """
    key = (width, height, c_start, c_end)
    with _GRADIENT_CACHE_LOCK:
        if key not in _GRADIENT_TEMPLATE_CACHE:
            base = Image.new("RGB", (width, height))
            pixels = []
            for y in range(height):
                ratio = y / max(1, height - 1)
                r = int(c_start[0] + (c_end[0] - c_start[0]) * ratio)
                g = int(c_start[1] + (c_end[1] - c_start[1]) * ratio)
                b = int(c_start[2] + (c_end[2] - c_start[2]) * ratio)
                pixels.extend([(r, g, b)] * width)
            base.putdata(pixels)
            _GRADIENT_TEMPLATE_CACHE[key] = base
        return _GRADIENT_TEMPLATE_CACHE[key].copy()


# Color Palette inspired by Anthropic Frontend Design & Dark UI Telemetry
PALETTE = {
    "bg_main": (5, 11, 20),           # Deep Navy (#050B14)
    "bg_gradient_start": GRADIENT_START,
    "bg_gradient_end": GRADIENT_END,
    "bg_gradient_slate": GRADIENT_SLATE,
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
    "codex_primary": (16, 185, 129),   # OpenAI Emerald Mint
    "codex_secondary": (56, 189, 248), # Electric Sky Blue
    "codex_reasoning": (139, 92, 246), # Indigo Violet (OpenAI Reasoning Effort)
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
            "nano": ImageFont.truetype(font_path, 8),
        }
    f = ImageFont.load_default()
    return {"title": f, "section": f, "body": f, "caption": f, "micro": f, "nano": f}


FONTS = get_fonts()


def truncate_to_width(draw: ImageDraw.ImageDraw, text: str, font: Any, max_width: int) -> str:
    """Safely truncate text with ellipsis if it exceeds max_width in pixels."""
    if not text or max_width <= 0:
        return ""
    bbox = draw.textbbox((0, 0), text, font=font)
    if bbox[2] - bbox[0] <= max_width:
        return text

    for i in range(len(text) - 1, 0, -1):
        candidate = text[:i] + "..."
        c_bbox = draw.textbbox((0, 0), candidate, font=font)
        if c_bbox[2] - c_bbox[0] <= max_width:
            return candidate
    return "..."


def get_active_agents(prov: Optional[AgentProvisioner] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Retrieve active AGY and Codex agents dynamically."""
    p = prov or _prov
    if p:
        data = p.get_all_agents()
        return data.get("agy", []), data.get("codex", [])
    # Fallback default static data
    return (
        [
            {"id": "agy1", "name": "AGY1 (Primary)", "email": "pansakorn@gmail.com", "usage_pct": 25.5, "status": "healthy", "gemini_tokens_used": 255000, "gemini_tokens_limit": 1000000, "claude_reqs_used": 18, "claude_reqs_limit": 100, "argon_units_used": 4, "argon_units_limit": 25},
            {"id": "agy2", "name": "AGY2 (Research)", "email": "pansakorn.pho@gmail.com", "usage_pct": 42.0, "status": "healthy", "gemini_tokens_used": 550000, "gemini_tokens_limit": 1000000, "claude_reqs_used": 35, "claude_reqs_limit": 100, "argon_units_used": 8, "argon_units_limit": 25},
            {"id": "agy3", "name": "AGY3 (Worker)", "email": "kimlenglim.work@gmail.com", "usage_pct": 15.0, "status": "healthy", "gemini_tokens_used": 180000, "gemini_tokens_limit": 1000000, "claude_reqs_used": 12, "claude_reqs_limit": 100, "argon_units_used": 5, "argon_units_limit": 25},
            {"id": "agy4", "name": "AGY4 (Reviewer)", "email": "kimlenglimp11@gmail.com", "usage_pct": 78.5, "status": "warning", "gemini_tokens_used": 850000, "gemini_tokens_limit": 1000000, "claude_reqs_used": 72, "claude_reqs_limit": 100, "argon_units_used": 40, "argon_units_limit": 50},
            {"id": "agy5", "name": "AGY5 (Standby)", "email": "emkimlenglim@gmail.com", "usage_pct": 10.0, "status": "healthy", "gemini_tokens_used": 120000, "gemini_tokens_limit": 1000000, "claude_reqs_used": 8, "claude_reqs_limit": 100, "argon_units_used": 0, "argon_units_limit": 25},
        ],
        [
            {"id": "codex1", "name": "CODEX1 (Backend Core)", "email": "kimlenglimp11@gmail.com", "usage_pct": 23.0, "status": "healthy", "primary_used_pct": 23.0, "primary_reset": "2026-11-17 14:53", "secondary_used_pct": 5.0, "secondary_reset": "05h 20m", "turns": "1,357", "items": "52,027", "model": "gpt-5.6-terra", "reasoning_effort": "medium"},
            {"id": "codex2", "name": "CODEX2 (UI / Frontend)", "email": "pansakorn@gmail.com", "usage_pct": 45.0, "status": "healthy", "primary_used_pct": 45.0, "primary_reset": "2026-11-18 10:30", "secondary_used_pct": 12.0, "secondary_reset": "05h 20m", "turns": "925", "items": "29,955", "model": "gpt-5.6-terra", "reasoning_effort": "high"},
            {"id": "codex3", "name": "CODEX3 (Infra / CI)", "email": "taijustarrett417@gmail.com", "usage_pct": 12.0, "status": "healthy", "primary_used_pct": 12.0, "primary_reset": "2026-11-19 08:15", "secondary_used_pct": 0.0, "secondary_reset": "05h 20m", "turns": "562", "items": "24,499", "model": "gpt-5.6-terra", "reasoning_effort": "medium"},
        ],
    )


def draw_header_and_tabs(
    draw, 
    title: str, 
    active_page: int, 
    total_pages: int = 3, 
    timestamp: Optional[str] = None
):
    """Draw distinctive header with pagination dots/tabs and current time at top-right."""
    draw.rectangle([(0, 0), (WIDTH, 34)], fill=PALETTE["header_bg"])
    draw.line([(0, 34), (WIDTH, 34)], fill=PALETTE["header_border"], width=1)

    # Title
    draw.text((12, 10), title, fill=PALETTE["text_primary"], font=FONTS["title"])

    # Current time at top-right header (e.g. 11:51:15)
    ts = timestamp or datetime.now().strftime("%H:%M:%S")
    draw.text((WIDTH - 12, 11), ts, fill=PALETTE["text_muted"], font=FONTS["body"], anchor="ra")

    # Time bounding box width to position pagination dots/tabs dynamically
    time_w = 46
    try:
        bbox = draw.textbbox((WIDTH - 12, 11), ts, font=FONTS["body"], anchor="ra")
        time_w = bbox[2] - bbox[0]
    except Exception:
        pass

    # Carousel Tabs / Dots positioned to the left of the timestamp
    dots_end_x = WIDTH - 12 - time_w - 8
    dot_spacing = 10
    total_dots_width = (total_pages - 1) * dot_spacing
    dot_start_x = dots_end_x - total_dots_width
    tab_y = 17
    for i in range(total_pages):
        dot_x = dot_start_x + (i * dot_spacing)
        if i == active_page:
            draw.ellipse([(dot_x - 3.5, tab_y - 3.5), (dot_x + 3.5, tab_y + 3.5)], fill=(56, 189, 248), outline=PALETTE["text_primary"])
        else:
            draw.ellipse([(dot_x - 2.5, tab_y - 2.5), (dot_x + 2.5, tab_y + 2.5)], fill=PALETTE["track_bg"], outline=PALETTE["text_dim"])


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


def format_reset_timestamp(raw: Any) -> str:
    """Format raw reset timestamp into clean YYYY-MM-DD HH:MM format."""
    if not raw:
        return "2026-11-17 14:53"
    s = str(raw).strip()
    for pfx in ("(Resets: ", "Resets: ", "RESET: ", "("):
        if s.startswith(pfx):
            s = s[len(pfx):]
    s = s.rstrip(")")
    s = s.replace("T", " ")
    if len(s) >= 16 and (s[4] == "-" or s[4] == "/"):
        s = s[:16]
    return s


# =========================================================================
# Page 0: Cluster Overview (renders all agents if <= 8, or cluster-specific if > 8)
# =========================================================================
def render_page_overview(
    active_page: int = 0,
    total_pages: int = 3,
    cluster: Optional[str] = None,
    prov: Optional[AgentProvisioner] = None,
    timestamp: Optional[str] = None,
) -> Image.Image:
    img = get_gradient_background(WIDTH, HEIGHT)
    draw = ImageDraw.Draw(img)

    agy_list, codex_list = get_active_agents(prov=prov)

    if cluster == "agy" or (total_pages > 3 and active_page == 0 and cluster != "codex"):
        title = "HERMES: AGY CLUSTER"
        agents_to_show = [(a["id"].upper(), a.get("usage_pct", 0.0), a.get("status", "healthy"), "AGY", a.get("email", "")) for a in agy_list]
        footer_sub = "AGY OVERVIEW"
    elif cluster == "codex" or (total_pages > 3 and active_page == 1):
        title = "HERMES: CODEX CLUSTER"
        agents_to_show = [(c["id"].upper(), c.get("usage_pct", 0.0), c.get("status", "healthy"), "CODEX", c.get("email", "")) for c in codex_list]
        footer_sub = "CODEX OVERVIEW"
    else:
        title = "HERMES AGENT CLUSTERS"
        combined = []
        for a in agy_list:
            combined.append((a["id"].upper(), a.get("usage_pct", 0.0), a.get("status", "healthy"), "AGY", a.get("email", "")))
        for c in codex_list:
            combined.append((c["id"].upper(), c.get("usage_pct", 0.0), c.get("status", "healthy"), "CODEX", c.get("email", "")))
        agents_to_show = combined
        footer_sub = "OVERVIEW"

    draw_header_and_tabs(draw, title, active_page=active_page, total_pages=total_pages, timestamp=timestamp)

    # Render up to 8 agents on this overview screen
    display_agents = agents_to_show[:8]
    y = 40
    card_h = 48
    gap = 4

    for agent_data in display_agents:
        if len(agent_data) == 5:
            name, pct, status, cluster_type, email = agent_data
        else:
            name, pct, status, cluster_type = agent_data[:4]
            email = ""
        st = PALETTE.get(status, PALETTE["healthy"])
        draw.rounded_rectangle([(8, y), (WIDTH - 8, y + card_h)], radius=4, fill=PALETTE["card_bg"], outline=PALETTE["card_border"], width=1)

        # Name
        draw.text((16, y + 6), name, fill=PALETTE["text_primary"], font=FONTS["section"])
        name_bbox = draw.textbbox((16, y + 6), name, font=FONTS["section"])
        name_end = name_bbox[2]

        # Tag
        tag_color = PALETTE["gemini"] if cluster_type == "AGY" else PALETTE["codex_primary"]
        tag_text = f"[{cluster_type}]"
        draw.text((name_end + 4, y + 7), tag_text, fill=tag_color, font=FONTS["nano"])
        tag_bbox = draw.textbbox((name_end + 4, y + 7), tag_text, font=FONTS["nano"])
        tag_end = tag_bbox[2]

        # Email
        if email:
            email_x = tag_end + 5
            max_email_w = (WIDTH - 70) - email_x
            if max_email_w > 20:
                clean_email = truncate_to_width(draw, email, FONTS["nano"], max_email_w)
                draw.text((email_x, y + 7), clean_email, fill=PALETTE["text_muted"], font=FONTS["nano"])

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
    timestamp: Optional[str] = None,
) -> Image.Image:
    img = get_gradient_background(WIDTH, HEIGHT)
    draw = ImageDraw.Draw(img)
    draw_header_and_tabs(draw, "AGY CLUSTER: SUB-QUOTAS", active_page=active_page, total_pages=total_pages, timestamp=timestamp)

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

        # Header line: ID + Email + Overall Badge
        agent_id = agent["id"].upper()
        draw.text((16, y + 4), agent_id, fill=PALETTE["text_primary"], font=FONTS["section"])
        id_bbox = draw.textbbox((16, y + 4), agent_id, font=FONTS["section"])
        id_end = id_bbox[2]

        email = agent.get("email", "")
        if email:
            avail_w = (WIDTH - 70) - (id_end + 6)
            if avail_w > 20:
                email_disp = truncate_to_width(draw, f"· {email}", FONTS["micro"], avail_w)
                draw.text((id_end + 6, y + 5), email_disp, fill=PALETTE["text_muted"], font=FONTS["micro"])

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
    timestamp: Optional[str] = None,
) -> Image.Image:
    img = get_gradient_background(WIDTH, HEIGHT)
    draw = ImageDraw.Draw(img)
    draw_header_and_tabs(draw, "CODEX CLUSTER: SUB-QUOTAS", active_page=active_page, total_pages=total_pages, timestamp=timestamp)

    _, codex_list = get_active_agents(prov=prov)
    agents_to_display = codex_list[:6]
    num_cdx = len(agents_to_display)
    if num_cdx <= 4:
        y = 38
        card_h = 80
        gap = 5
    elif num_cdx <= 5:
        y = 38
        card_h = 78
        gap = 4
    else:
        y = 36
        card_h = 66
        gap = 3

    for cdx in agents_to_display:
        st = PALETTE.get(cdx.get("status", "healthy"), PALETTE["healthy"])
        draw.rounded_rectangle([(8, y), (WIDTH - 8, y + card_h)], radius=4, fill=PALETTE["card_bg"], outline=PALETTE["card_border"], width=1)

        # Header line: ID + Email / Model + Overall Badge
        cdx_id = cdx.get("id", "CODEX").upper()
        draw.text((16, y + 4), cdx_id, fill=PALETTE["text_primary"], font=FONTS["section"])
        id_bbox = draw.textbbox((16, y + 4), cdx_id, font=FONTS["section"])
        id_end = id_bbox[2]

        model_name = cdx.get("model", "gpt-5.6-terra")
        if not model_name or any(bad in str(model_name).lower() for bad in ("claude", "gemini", "argon")):
            model_name = "gpt-5.6-terra"
        clean_model = model_name.split("@")[0].split(":")[0][:16]

        email = cdx.get("email", "")
        if email:
            avail_w = (WIDTH - 70) - (id_end + 6)
            if avail_w > 20:
                email_disp = truncate_to_width(draw, f"· {email}", FONTS["micro"], avail_w)
                draw.text((id_end + 6, y + 5), email_disp, fill=PALETTE["text_muted"], font=FONTS["micro"])
        else:
            model_x = id_end + 6
            tag_text = f"[{clean_model}]"
            tag_bbox = draw.textbbox((model_x, y + 6), tag_text, font=FONTS["micro"])
            if tag_bbox[2] < WIDTH - 68:
                draw.text((model_x, y + 6), tag_text, fill=PALETTE["text_dim"], font=FONTS["micro"])

        # Top-right Badge
        p_pct = float(cdx.get("primary_used_pct", cdx.get("usage_pct", 23.0)))
        badge_text = f"{p_pct:.1f}%"
        draw.rounded_rectangle([(WIDTH - 66, y + 3), (WIDTH - 16, y + 17)], radius=3, fill=st["badge_bg"], outline=st["badge_border"])
        draw.text((WIDTH - 41, y + 10), badge_text, fill=st["badge_text"], font=FONTS["caption"], anchor="mm")

        # Sub-quotas: Row 1 - Primary & Secondary (AGY Template Layout)
        p_y = y + (20 if num_cdx > 5 else 23)
        p_color = PALETTE.get("codex_primary", (16, 185, 129))
        draw.text((16, p_y), "PRIMARY", fill=p_color, font=FONTS["micro"])
        draw.rectangle([(62, p_y + 2), (122, p_y + 9)], fill=PALETTE["track_bg"], outline=PALETTE["track_border"])
        pw = int(58 * (min(p_pct, 100.0) / 100.0))
        if pw > 0:
            draw.rectangle([(63, p_y + 3), (63 + pw, p_y + 8)], fill=p_color)
        draw.text((126, p_y), f"{p_pct:.0f}%", fill=PALETTE["text_muted"], font=FONTS["micro"])

        s_pct = float(cdx.get("secondary_used_pct", 0.0))
        s_color = PALETTE.get("codex_secondary", (56, 189, 248))
        draw.text((160, p_y), "SECONDARY", fill=s_color, font=FONTS["micro"])
        draw.rectangle([(216, p_y + 2), (270, p_y + 9)], fill=PALETTE["track_bg"], outline=PALETTE["track_border"])
        sw = int(52 * (min(s_pct, 100.0) / 100.0))
        if sw > 0:
            draw.rectangle([(217, p_y + 3), (217 + sw, p_y + 8)], fill=s_color)
        draw.text((274, p_y), f"{s_pct:.0f}%", fill=PALETTE["text_muted"], font=FONTS["micro"])

        # Sub-quotas: Row 2 - Reasoning Effort & Exact Reset Time (Resets: 2026-11-17 14:53)
        r_y = y + (34 if num_cdx > 5 else 40)
        r_color = PALETTE.get("codex_reasoning", (139, 92, 246))
        draw.text((16, r_y), "REASONING", fill=r_color, font=FONTS["micro"])
        draw.rectangle([(72, r_y + 2), (122, r_y + 9)], fill=PALETTE["track_bg"], outline=PALETTE["track_border"])
        effort = str(cdx.get("reasoning_effort", "medium")).lower()
        r_pct = 75.0 if "high" in effort else (100.0 if "max" in effort else (50.0 if "med" in effort else 25.0))
        rw = int(48 * (r_pct / 100.0))
        if rw > 0:
            draw.rectangle([(73, r_y + 3), (73 + rw, r_y + 8)], fill=r_color)
        draw.text((126, r_y), effort[:3].upper(), fill=PALETTE["text_muted"], font=FONTS["micro"])

        clean_reset = format_reset_timestamp(cdx.get("primary_reset", "2026-11-17 14:53"))
        draw.text((WIDTH - 16, r_y), f"(Resets: {clean_reset})", fill=PALETTE["text_dim"], font=FONTS["micro"], anchor="ra")

        # Card Footer: Separator line + Model, Turns, Items, Mode
        foot_y = y + (50 if num_cdx > 5 else 61)
        draw.line([(16, foot_y), (WIDTH - 16, foot_y)], fill=(30, 41, 59), width=1)
        turns = cdx.get("turns", "0")
        items = cdx.get("items", "0")
        draw.text((16, foot_y + 3), f"{clean_model} | Turns: {turns} | Items: {items}", fill=PALETTE["text_dim"], font=FONTS["micro"])

        y += card_h + gap

    reset_str = get_reset_timer_str(prov=prov)
    draw_footer(draw, f"PAGE {active_page+1}/{total_pages}: CODEX DETAIL | IN: {reset_str}")
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
