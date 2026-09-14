from __future__ import annotations

import asyncio
from typing import Any

import discord

import bot.server_template_v2 as template_v2
from bot.server_template import _load_template, _overwrite_from_spec


_INSTALL_LOCKS: dict[int, asyncio.Lock] = {}


def _install_lock(guild_id: int) -> asyncio.Lock:
    lock = _INSTALL_LOCKS.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        _INSTALL_LOCKS[guild_id] = lock
    return lock


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


async def _create_category(
    guild: discord.Guild,
    *,
    name: str,
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
) -> discord.CategoryChannel:
    try:
        return await guild.create_category(
            name=name,
            overwrites=overwrites,
            reason="Purple Team private J2 template install",
        )
    except Exception as exc:
        raise RuntimeError(
            f"create-categories: failed creating {name!r}: {exc}"
        ) from exc


async def _category_still_exists(
    guild: discord.Guild,
    category_id: int,
) -> bool:
    channels = await guild.fetch_channels()
    return any(
        isinstance(channel, discord.CategoryChannel) and channel.id == category_id
        for channel in channels
    )


async def _create_channel_once(
    guild: discord.Guild,
    *,
    category: discord.CategoryChannel,
    channel_spec: dict[str, Any],
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
) -> None:
    channel_name = channel_spec["name"]
    channel_type = channel_spec.get("type", "text")
    topic = channel_spec.get("topic")

    common = {
        "name": channel_name,
        "category": category,
        "overwrites": overwrites,
        "reason": "Purple Team private J2 template install",
    }

    if channel_type == "voice":
        await guild.create_voice_channel(**common)
    elif channel_type == "forum":
        await guild.create_forum(topic=topic, **common)
    else:
        await guild.create_text_channel(topic=topic, **common)


async def _install_private_template_fast_locked(
    guild: discord.Guild,
    *,
    bot_user_id: int,
    bot_display_name: str,
    effective_permissions: discord.Permissions,
) -> dict[str, int]:
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

        category = await _create_category(
            guild,
            name=category_name,
            overwrites=category_overwrites,
        )
        created_categories += 1

        for channel_spec in category_spec.get("channels", []):
            channel_name = channel_spec["name"]
            channel_overwrites = _build_overwrites(
                guild,
                roles,
                role_map,
                channel_spec.get("overwrites"),
                base=category_overwrites,
            )

            try:
                await _create_channel_once(
                    guild,
                    category=category,
                    channel_spec=channel_spec,
                    overwrites=channel_overwrites,
                )
                created_channels += 1
                continue
            except discord.HTTPException as exc:
                # Error 50035 with parent_id means Discord no longer knows the
                # category ID. This can happen if an older overlapping installer
                # invocation deletes the newly created category. Re-check once,
                # recreate the category if needed, and retry the channel once.
                text = str(exc)
                vanished_parent = exc.code == 50035 and "parent_id" in text
                if not vanished_parent:
                    raise RuntimeError(
                        f"create-channels: failed creating {channel_name!r} in {category_name!r}: {exc}"
                    ) from exc

                try:
                    exists = await _category_still_exists(guild, category.id)
                except Exception:
                    exists = False

                if not exists:
                    category = await _create_category(
                        guild,
                        name=category_name,
                        overwrites=category_overwrites,
                    )
                    created_categories += 1

                try:
                    await _create_channel_once(
                        guild,
                        category=category,
                        channel_spec=channel_spec,
                        overwrites=channel_overwrites,
                    )
                    created_channels += 1
                except Exception as retry_exc:
                    raise RuntimeError(
                        f"create-channels: retry failed creating {channel_name!r} in {category_name!r}: {retry_exc}"
                    ) from retry_exc
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


async def install_private_template_fast(
    guild: discord.Guild,
    *,
    bot_user_id: int,
    bot_display_name: str,
    effective_permissions: discord.Permissions,
) -> dict[str, int]:
    """Run exactly one destructive J2 replacement per guild at a time."""
    lock = _install_lock(guild.id)

    if lock.locked():
        raise RuntimeError(
            "replacement-lock: another CyberSpace replacement is already running for this server. "
            "Wait for its completion DM before starting another one."
        )

    async with lock:
        return await _install_private_template_fast_locked(
            guild,
            bot_user_id=bot_user_id,
            bot_display_name=bot_display_name,
            effective_permissions=effective_permissions,
        )
