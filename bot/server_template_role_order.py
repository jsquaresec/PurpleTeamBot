from typing import Any

import discord

from bot.server_template import _colour, _permissions


async def ensure_roles_top_down(
    guild: discord.Guild,
    definitions: list[dict[str, Any]],
    bot_role: discord.Role,
) -> dict[str, discord.Role]:
    """Create/sync template roles and then order them top-down beneath the bot role.

    The template definition order is authoritative: definitions[0] is highest.
    Fresh role data is fetched before every move because Discord adjusts role
    positions after each edit.
    """
    try:
        fresh_roles = await guild.fetch_roles()
    except Exception as exc:
        raise RuntimeError(f"create-roles: could not fetch roles: {exc}") from exc

    role_map = {role.name: role for role in fresh_roles}

    # Create from bottom to top so the initial stack is already close to the
    # desired hierarchy before the explicit positioning pass.
    for item in reversed(definitions):
        name = item["name"]
        role = role_map.get(name)
        perms = _permissions(item.get("permissions", []))
        colour = _colour(item.get("colour"))
        try:
            if role is None:
                role = await guild.create_role(
                    name=name,
                    permissions=perms,
                    colour=colour,
                    hoist=bool(item.get("hoist", False)),
                    mentionable=bool(item.get("mentionable", False)),
                    reason="Purple Team private J2 template install",
                )
                role_map[name] = role
            elif not role.managed:
                await role.edit(
                    permissions=perms,
                    colour=colour,
                    hoist=bool(item.get("hoist", False)),
                    mentionable=bool(item.get("mentionable", False)),
                    reason="Purple Team private J2 template sync",
                )
        except Exception as exc:
            raise RuntimeError(f"create-roles: failed on role {name!r}: {exc}") from exc

    # Position from highest to lowest. Re-fetch on every iteration so each move
    # uses Discord's current positions rather than stale pre-move values.
    for index, item in enumerate(definitions):
        fresh_roles = await guild.fetch_roles()
        current_bot_role = next((role for role in fresh_roles if role.id == bot_role.id), None)
        if current_bot_role is None:
            raise RuntimeError("create-roles: Purple Team control role disappeared while ordering roles.")

        current_role = next((role for role in fresh_roles if role.name == item["name"]), None)
        if current_role is None:
            raise RuntimeError(f"create-roles: role {item['name']!r} could not be re-fetched.")

        target_position = max(1, current_bot_role.position - 1 - index)
        if current_role.position != target_position:
            try:
                await current_role.edit(
                    position=target_position,
                    reason="Purple Team J2 role hierarchy",
                )
            except Exception as exc:
                raise RuntimeError(
                    f"create-roles: failed positioning {item['name']!r}: {exc}"
                ) from exc

    return {role.name: role for role in await guild.fetch_roles()}
