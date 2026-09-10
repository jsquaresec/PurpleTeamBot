from __future__ import annotations

from datetime import datetime, timezone

import discord

PURPLE = 0x7C3AED
GREEN = 0x22C55E
RED = 0xEF4444
AMBER = 0xF59E0B
BLUE = 0x3B82F6
MUTED = 0x64748B

BRAND_NAME = "Purple Team"
BRAND_FOOTER = "J2SEC / JSquareSec • Systems • Security • Software"


def make_embed(
    title: str,
    description: str = "",
    *,
    kind: str = "default",
    footer: str | None = None,
) -> discord.Embed:
    """Create a consistent branded embed for PurpleTeamBot."""
    palette = {
        "default": PURPLE,
        "success": GREEN,
        "error": RED,
        "warning": AMBER,
        "info": BLUE,
        "muted": MUTED,
    }
    icons = {
        "default": "🟣",
        "success": "✅",
        "error": "⛔",
        "warning": "⚠️",
        "info": "🔎",
        "muted": "◼️",
    }

    embed = discord.Embed(
        title=f"{icons.get(kind, '🟣')} {BRAND_NAME}  •  {title}",
        description=description[:4000] if description else None,
        color=palette.get(kind, PURPLE),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=footer or BRAND_FOOTER)
    return embed


def status_dot(enabled: bool, *, label: str | None = None) -> str:
    state = "🟢 Online" if enabled else "⚫ Not configured"
    return f"{label}: {state}" if label else state


def local_status(label: str) -> str:
    return f"{label}: 🟣 Local"


def always_status(label: str) -> str:
    return f"{label}: 🟢 Available"
