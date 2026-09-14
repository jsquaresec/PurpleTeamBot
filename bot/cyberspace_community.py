from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

import discord
from discord import app_commands
from PIL import Image, ImageDraw, ImageFilter, ImageFont


WELCOME_CHANNEL = "👋・welcome"
SELF_ROLE_CHANNEL = "🪪・identity-roles"

SELF_ROLES = [
    ("pentester", "⚡", discord.ButtonStyle.danger),
    ("bug-hunter", "🪲", discord.ButtonStyle.danger),
    ("researcher", "🔬", discord.ButtonStyle.primary),
    ("developer", "💻", discord.ButtonStyle.primary),
    ("ctf-player", "🚩", discord.ButtonStyle.success),
    ("mentor", "🤝", discord.ButtonStyle.success),
    ("subscriber", "💗", discord.ButtonStyle.secondary),
]


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int, *, bold: bool = False):
    size = start_size
    while size >= 20:
        font = _font(size, bold=bold)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width:
            return font
        size -= 2
    return _font(20, bold=bold)


def _render_banner(
    avatar_bytes: bytes | None,
    *,
    display_name: str,
    member_count: int,
    joined: bool,
) -> BytesIO:
    width, height = 1100, 360
    image = Image.new("RGB", (width, height), (8, 10, 18))
    draw = ImageDraw.Draw(image)

    # Subtle CyberSpace grid.
    for x in range(0, width, 44):
        draw.line((x, 0, x, height), fill=(20, 24, 42), width=1)
    for y in range(0, height, 44):
        draw.line((0, y, width, y), fill=(20, 24, 42), width=1)

    # Neon network accents.
    purple = (139, 92, 246)
    cyan = (34, 211, 238)
    dim = (81, 52, 144)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=30, outline=purple, width=3)
    draw.line((35, 80, width - 35, 80), fill=dim, width=2)
    draw.line((35, height - 52, width - 35, height - 52), fill=(18, 76, 86), width=2)

    # Terminal decorations.
    draw.ellipse((48, 45, 60, 57), fill=(239, 68, 68))
    draw.ellipse((68, 45, 80, 57), fill=(234, 179, 8))
    draw.ellipse((88, 45, 100, 57), fill=(34, 197, 94))
    draw.text((120, 38), "CYBERSPACE // ENTRY NODE", font=_font(22, bold=True), fill=cyan)

    # Avatar with neon ring.
    avatar_size = 205
    avatar_xy = (58, 105)
    ring_box = (
        avatar_xy[0] - 8,
        avatar_xy[1] - 8,
        avatar_xy[0] + avatar_size + 8,
        avatar_xy[1] + avatar_size + 8,
    )
    draw.ellipse(ring_box, outline=purple, width=7)

    if avatar_bytes:
        try:
            avatar = Image.open(BytesIO(avatar_bytes)).convert("RGB").resize((avatar_size, avatar_size))
            mask = Image.new("L", (avatar_size, avatar_size), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, avatar_size, avatar_size), fill=255)
            image.paste(avatar, avatar_xy, mask)
        except Exception:
            draw.ellipse((avatar_xy[0], avatar_xy[1], avatar_xy[0] + avatar_size, avatar_xy[1] + avatar_size), fill=(30, 35, 52))
    else:
        draw.ellipse((avatar_xy[0], avatar_xy[1], avatar_xy[0] + avatar_size, avatar_xy[1] + avatar_size), fill=(30, 35, 52))

    status = "CONNECTION ESTABLISHED" if joined else "CONNECTION TERMINATED"
    headline = "WELCOME TO CYBERSPACE" if joined else "NODE DISCONNECTED"
    status_colour = cyan if joined else (248, 113, 113)

    text_x = 315
    draw.text((text_x, 112), status, font=_font(23, bold=True), fill=status_colour)
    headline_font = _fit_text(draw, headline, 720, 46, bold=True)
    draw.text((text_x, 150), headline, font=headline_font, fill=(242, 245, 255))

    name_font = _fit_text(draw, display_name, 700, 40, bold=True)
    draw.text((text_x, 213), display_name, font=name_font, fill=purple)

    if joined:
        footer = f"NODE #{member_count:04d}  //  identity synchronized  //  access granted"
    else:
        footer = f"{member_count:04d} active nodes remain  //  session closed"
    draw.text((text_x, 274), footer, font=_font(21), fill=(160, 170, 194))

    # Soft glow pass on a copy of the accents.
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=30, outline=(*purple, 120), width=9)
    glow = glow.filter(ImageFilter.GaussianBlur(10))
    image = Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


async def _find_text_channel(guild: discord.Guild, name: str) -> discord.TextChannel | None:
    try:
        channels = await guild.fetch_channels()
    except discord.HTTPException:
        return None
    for channel in channels:
        if isinstance(channel, discord.TextChannel) and channel.name == name:
            return channel
    return None


async def _send_member_banner(member: discord.Member, *, joined: bool) -> None:
    channel = await _find_text_channel(member.guild, WELCOME_CHANNEL)
    if channel is None:
        return

    avatar_bytes: bytes | None = None
    try:
        avatar_bytes = await member.display_avatar.with_size(256).read()
    except discord.HTTPException:
        pass

    count = member.guild.member_count or 0
    if not joined and count > 0:
        # Discord's cached count can briefly still include the leaving member.
        count = max(0, count - 1)

    banner = await asyncio.to_thread(
        _render_banner,
        avatar_bytes,
        display_name=member.display_name,
        member_count=count,
        joined=joined,
    )
    filename = "cyberspace-welcome.png" if joined else "cyberspace-departure.png"

    if joined:
        content = f"**{member.mention} connected to CyberSpace.**  Read `📜・protocols`, then choose your identity roles."
    else:
        content = f"**{member.display_name} disconnected from CyberSpace.**"

    try:
        await channel.send(content=content, file=discord.File(banner, filename=filename))
    except discord.HTTPException:
        pass


class SelfRoleButton(discord.ui.Button):
    def __init__(self, role_name: str, emoji: str, style: discord.ButtonStyle, row: int):
        label = role_name.replace("-", " ").title()
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=f"cyberspace:selfrole:{role_name}",
            row=row,
        )
        self.role_name = role_name

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This button only works inside CyberSpace.", ephemeral=True)
            return

        try:
            roles = await interaction.guild.fetch_roles()
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"Could not load roles: {exc}", ephemeral=True)
            return

        role = next((item for item in roles if item.name == self.role_name), None)
        if role is None:
            await interaction.response.send_message(
                f"The `{self.role_name}` role is not currently available.", ephemeral=True
            )
            return

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None:
            try:
                member = await interaction.guild.fetch_member(interaction.user.id)
            except discord.HTTPException:
                await interaction.response.send_message("Could not resolve your server membership.", ephemeral=True)
                return

        # Fetch the member so the role list is authoritative even with a small cache.
        try:
            member = await interaction.guild.fetch_member(member.id)
        except discord.HTTPException:
            pass

        has_role = any(item.id == role.id for item in member.roles)
        try:
            if has_role:
                await member.remove_roles(role, reason="CyberSpace self-role button")
                action = "removed"
            else:
                await member.add_roles(role, reason="CyberSpace self-role button")
                action = "added"
        except discord.Forbidden:
            await interaction.response.send_message(
                "Demon Scope cannot manage that role. Move its bot role above the self-role hierarchy.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"Role update failed: {exc}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"{self.emoji} `{self.role_name}` **{action}**.", ephemeral=True
        )


class SelfRoleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for index, (role_name, emoji, style) in enumerate(SELF_ROLES):
            self.add_item(SelfRoleButton(role_name, emoji, style, 0 if index < 5 else 1))


def _role_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="CYBERSPACE // IDENTITY MATRIX",
        description=(
            "Select the disciplines that represent you across the network.\n\n"
            "Buttons are **toggles** — press once to connect a role and press again to disconnect it."
        ),
        colour=discord.Colour.from_rgb(139, 92, 246),
    )
    embed.add_field(
        name="Available identities",
        value=(
            "⚡ **Pentester**  • offensive security\n"
            "🪲 **Bug Hunter**  • vulnerability research\n"
            "🔬 **Researcher**  • security research\n"
            "💻 **Developer**  • code & engineering\n"
            "🚩 **CTF Player**  • challenges & competitions\n"
            "🤝 **Mentor**  • community guidance\n"
            "💗 **Subscriber**  • J2 supporter"
        ),
        inline=False,
    )
    embed.set_footer(text="CyberSpace Identity Service • persistent self-role controls")
    return embed


def install_cyberspace_community(bot: discord.Client) -> None:
    bot.add_view(SelfRoleView())

    async def on_member_join(member: discord.Member) -> None:
        await _send_member_banner(member, joined=True)

    async def on_member_remove(member: discord.Member) -> None:
        await _send_member_banner(member, joined=False)

    bot.add_listener(on_member_join, "on_member_join")
    bot.add_listener(on_member_remove, "on_member_remove")


def register_cyberspace_community_commands(bot: discord.Client) -> None:
    group = app_commands.Group(
        name="cyberspace",
        description="CyberSpace community controls",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @group.command(name="roles-panel", description="Post the persistent CyberSpace self-role panel")
    @app_commands.guild_only()
    async def roles_panel(interaction: discord.Interaction):
        if interaction.guild is None:
            return
        channel = await _find_text_channel(interaction.guild, SELF_ROLE_CHANNEL)
        if channel is None:
            await interaction.response.send_message(
                f"Could not find `{SELF_ROLE_CHANNEL}`.", ephemeral=True
            )
            return
        try:
            await channel.send(embed=_role_panel_embed(), view=SelfRoleView())
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"Could not post the role panel: {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Self-role panel posted in {channel.mention}.", ephemeral=True
        )

    bot.tree.add_command(group)
