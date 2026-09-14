from __future__ import annotations

from typing import Any

import discord

import bot.server_template_v2 as template_v2
from bot.server_template import _load_template, _overwrite_from_spec


def _everyone_role(guild: discord.Guild, roles: list[discord.Role]) -> discord.Role:
    role = next((item for item in roles if item.id == guild.id), None)
    if role is None:
        raise RuntimeError("build: Discord did not return @everyone.")
    return role


def _build_overwrites(
    guild: discord.Guild,
    roles: list[discord.Role],
    role_map: dict[str, discord.Role],
    spec: dict[str, Any] | None,
    *,
    base: dict[discord.abc.Snowflake, discord.PermissionOverwrite] | None = None,
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    result = dict(base or {})
    everyone = _everyone_role(guild, roles)

    for target, overwrite_spec in (spec or {}).items():
        if target == "@everyone":
            result[everyone] = _overwrite_from_spec(overwrite_spec)
            continue
        role = role_map.get(target)
        if role is None:
            raise RuntimeError(f"build: template references missing role {target!r}.")
        result[role] = _overwrite_from_spec(overwrite_spec)

    return result


async def install_private_template_fast(
    guild: discord.Guild,
    *,
    bot_user_id: int,
    bot_display_name: str,
    effective_permissions: discord.Permissions,
) -> dict[str, int]:
    """Full J2 rebuild with minimal Discord API chatter.

    The server is wiped first, so the creation phase never performs per-channel
    discovery or position edits. Categories and channels are created sequentially
    in the authoritative template order.
    """
    data = _load_template()

    bot_role = await template_v2._preflight_replace(
        guild,
        bot_user_id=bot_user_id,
        bot_display_name=bot_display_name,
        effective_permissions=effective_permissions,
    )

    wipe_stats = await template_v2._wipe_existing_layout(guild, bot_role)

    bot_role = await template_v2._resolve_bot_top_role(
        guild,
        bot_user_id,
        bot_display_name,
    )

    # main.py replaces template_v2._ensure_roles with the hardened role-order
    # implementation, so call the module attribute at runtime rather than
    # importing a stale function reference here.
    role_map = await template_v2._ensure_roles(guild, data["roles"], bot_role)

    roles = await guild.fetch_roles()
    role_map = {role.name: role for role in roles}

    created_categories = 0
    created_channels = 0

    for category_spec in data["categories"]:
        category_name = category_spec["name"]
        category_overwrites = _build_overwrites(
            guild,
            roles,
            role_map,
            category_spec.get("overwrites"),
        )

        try:
            category = await guild.create_category(
                name=category_name,
                overwrites=category_overwrites,
                reason="Purple Team private J2 template install",
            )
            created_categories += 1
        except Exception as exc:
            raise RuntimeError(
                f"create-categories: failed creating {category_name!r}: {exc}"
            ) from exc

        for channel_spec in category_spec.get("channels", []):
            channel_name = channel_spec["name"]
            channel_type = channel_spec.get("type", "text")
            topic = channel_spec.get("topic")
            channel_overwrites = _build_overwrites(
                guild,
                roles,
                role_map,
                channel_spec.get("overwrites"),
                base=category_overwrites,
            )

            common = {
                "name": channel_name,
                "category": category,
                "overwrites": channel_overwrites,
                "reason": "Purple Team private J2 template install",
            }

            try:
                if channel_type == "voice":
                    await guild.create_voice_channel(**common)
                elif channel_type == "forum":
                    await guild.create_forum(topic=topic, **common)
                else:
                    await guild.create_text_channel(topic=topic, **common)
                created_channels += 1
            except Exception as exc:
                raise RuntimeError(
                    f"create-channels: failed creating {channel_name!r} in {category_name!r}: {exc}"
                ) from exc

    root_role = role_map.get(data.get("root_role", "root"))
    if root_role is not None:
        try:
            owner_member = await guild.fetch_member(guild.owner_id)
            await owner_member.add_roles(root_role, reason="Purple Team J2 owner/root mapping")
        except discord.HTTPException:
            pass

    return {
        "roles": len(data["roles"]),
        "categories_created": created_categories,
        "channels_created": created_channels,
        **wipe_stats,
    }
