from typing import Any

import discord

from bot.server_template import _colour, _permissions


async def ensure_roles_top_down(
    guild: discord.Guild,
    definitions: list[dict[str, Any]],
    bot_role: discord.Role,
) -> dict[str, discord.Role]:
    """Create/sync template roles and bulk-order them beneath Purple Team.

    The template definition order is authoritative: definitions[0] is the
    highest J2 role. Role positioning is done with one bulk Discord operation
    so intermediate reindexing cannot invert the hierarchy.
    """
    try:
        fresh_roles = await guild.fetch_roles()
    except Exception as exc:
        raise RuntimeError(f"create-roles: could not fetch roles: {exc}") from exc

    role_map = {role.name: role for role in fresh_roles}

    # Create bottom-to-top so the initial stack is close to the desired order.
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

    # Re-fetch after creation so every role object and position is current.
    fresh_roles = await guild.fetch_roles()
    current_bot_role = next((role for role in fresh_roles if role.id == bot_role.id), None)
    if current_bot_role is None:
        raise RuntimeError("create-roles: Purple Team control role disappeared while ordering roles.")

    role_map = {role.name: role for role in fresh_roles}
    positions: dict[discord.Role, int] = {}

    # Discord role position 0 is @everyone and larger numbers are higher.
    # Put root immediately beneath Demon Scope, then descend in definition order.
    for index, item in enumerate(definitions):
        role = role_map.get(item["name"])
        if role is None:
            raise RuntimeError(f"create-roles: role {item['name']!r} could not be re-fetched.")
        target_position = current_bot_role.position - 1 - index
        if target_position < 1:
            raise RuntimeError(
                "create-roles: Purple Team's control role is not high enough to fit the full J2 hierarchy beneath it."
            )
        positions[role] = target_position

    try:
        await guild.edit_role_positions(
            positions=positions,
            reason="Purple Team J2 role hierarchy",
        )
    except Exception as exc:
        raise RuntimeError(f"create-roles: bulk role positioning failed: {exc}") from exc

    # Verify the final hierarchy from fresh Discord data rather than trusting cache.
    final_roles = await guild.fetch_roles()
    final_map = {role.name: role for role in final_roles}
    final_bot_role = next((role for role in final_roles if role.id == bot_role.id), None)
    if final_bot_role is None:
        raise RuntimeError("create-roles: Purple Team control role disappeared after positioning roles.")

    previous_position = final_bot_role.position
    for item in definitions:
        role = final_map.get(item["name"])
        if role is None:
            raise RuntimeError(f"create-roles: role {item['name']!r} disappeared after positioning.")
        if role.position >= previous_position:
            raise RuntimeError(
                f"create-roles: hierarchy verification failed at {item['name']!r}; Discord did not keep the requested order."
            )
        previous_position = role.position

    return final_map
