import asyncio
import difflib
import json
import os
import random
import unicodedata

import discord
from discord import app_commands

TOKEN = os.environ["DISCORD_TOKEN"]  # set in your environment, never hardcode it
SCORES_FILE = "scores.json"
HINT_DELAY = 15  # seconds between hints

# ======================= Guess the operator =======================

# name: (side, speed, country, gadget)
OPERATORS = {
    "Ash": ("Attacker", 3, "Australia", "Breaching Round launcher"),
    "Thermite": ("Attacker", 2, "USA", "Exothermic Charge"),
    "Sledge": ("Attacker", 2, "UK", "Tactical Breaching Hammer"),
    "Lion": ("Attacker", 2, "France", "EE-ONE-D drone"),
    "Zofia": ("Attacker", 2, "Poland", "KS79 Lifeline launcher"),
    "Dokkaebi": ("Attacker", 2, "South Korea", "Logic Bomb"),
    "Hibana": ("Attacker", 3, "Japan", "X-KAIROS pellets"),
    "Jager": ("Defender", 2, "Germany", "Active Defense System"),
    "Bandit": ("Defender", 3, "Germany", "Shock Wire"),
    "Mute": ("Defender", 2, "UK", "Signal Disruptor"),
    "Smoke": ("Defender", 2, "UK", "Remote Gas Grenade"),
    "Valkyrie": ("Defender", 2, "USA", "Black Eye cameras"),
    "Mira": ("Defender", 1, "Spain", "Black Mirror"),
    "Caveira": ("Defender", 3, "Brazil", "Silent Step"),
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def is_match(guess: str, name: str) -> bool:
    # forgiving: tolerates typos and autocorrect (e.g. "Jäger", "dokaebi")
    g, n = norm(guess), norm(name)
    return g == n or difflib.SequenceMatcher(None, g, n).ratio() >= 0.85


def load_scores() -> dict:
    try:
        with open(SCORES_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_scores(scores: dict) -> None:
    with open(SCORES_FILE, "w") as f:
        json.dump(scores, f)


class Game:
    def __init__(self, name: str):
        side, speed, country, gadget = OPERATORS[name]
        self.name = name
        self.hints = [
            f"Side: **{side}**",
            f"Speed: **{speed}**",
            f"Country: **{country}**",
            f"Gadget: **{gadget}**",
        ]
        self.shown = 1
        self.winner = None
        self.solved = asyncio.Event()

    def embed(self, final: bool = False) -> discord.Embed:
        lines = [f"**{i + 1}.** {h}" for i, h in enumerate(self.hints[: self.shown])]
        if final:
            if self.winner:
                lines.append(f"\n✅ {self.winner.mention} got it: **{self.name}**")
            else:
                lines.append(f"\n⏰ Time's up. It was **{self.name}**")
        else:
            lines.append("\nTap **Guess** below 👇")
        color = 0x2ECC71 if self.winner else 0xF5A623
        return discord.Embed(title="🎯 Guess the operator", description="\n".join(lines), color=color)


class GuessModal(discord.ui.Modal, title="Your guess"):
    answer = discord.ui.TextInput(label="Operator name", max_length=20)

    def __init__(self, game: Game):
        super().__init__()
        self.game = game

    async def on_submit(self, interaction: discord.Interaction):
        g = self.game
        if g.solved.is_set():
            await interaction.response.send_message("Too late, already solved!", ephemeral=True)
            return
        if not is_match(self.answer.value, g.name):
            await interaction.response.send_message("❌ Nope, try again.", ephemeral=True)
            return
        g.winner = interaction.user
        points = len(g.hints) - g.shown + 1  # earlier guess = more points
        g.solved.set()
        scores = load_scores()
        uid = str(interaction.user.id)
        scores[uid] = scores.get(uid, 0) + points
        save_scores(scores)
        await interaction.response.send_message(f"✅ {interaction.user.mention} +{points} pts")


class GuessView(discord.ui.View):
    def __init__(self, game: Game):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(label="Guess", style=discord.ButtonStyle.primary, emoji="✍️")
    async def guess_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game.solved.is_set():
            await interaction.response.send_message("Round's over!", ephemeral=True)
            return
        await interaction.response.send_modal(GuessModal(self.game))


# ======================= Bot + commands =======================

class Bot(discord.Client):
    async def setup_hook(self):
        self.add_view(RoleView())  # keeps the role dropdowns alive after restarts


intents = discord.Intents.default()
client = Bot(intents=intents)
tree = app_commands.CommandTree(client)
active_channels = set()


@tree.command(name="guess", description="Start a guess-the-operator round")
async def guess(interaction: discord.Interaction):
    cid = interaction.channel_id
    if cid in active_channels:
        await interaction.response.send_message("A round is already running here.", ephemeral=True)
        return
    active_channels.add(cid)
    try:
        game = Game(random.choice(list(OPERATORS)))
        view = GuessView(game)
        await interaction.response.send_message(embed=game.embed(), view=view)
        msg = await interaction.original_response()
        while not game.solved.is_set():
            try:
                await asyncio.wait_for(game.solved.wait(), HINT_DELAY)
                break
            except asyncio.TimeoutError:
                pass
            if game.shown >= len(game.hints):
                break
            game.shown += 1
            await msg.edit(embed=game.embed())  # one message, edited in place
        for child in view.children:
            child.disabled = True
        await msg.edit(embed=game.embed(final=True), view=view)
    finally:
        active_channels.discard(cid)


@tree.command(name="leaderboard", description="Show the top players")
async def leaderboard(interaction: discord.Interaction):
    scores = load_scores()
    if not scores:
        await interaction.response.send_message("No scores yet. Try `/guess`!")
        return
    top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:10]
    lines = [f"{i + 1}. <@{uid}> {pts}" for i, (uid, pts) in enumerate(top)]
    await interaction.response.send_message("🏆 **Leaderboard**\n" + "\n".join(lines))


# ======================= Roles + layout =======================

# Listed top-to-bottom as they should appear in the role list.
ROLES = [
    ("Attacker Main", 0xE74C3C), ("Defender Main", 0x3498DB),
    ("PC", 0x95A5A6), ("PlayStation", 0x2E86DE), ("Xbox", 0x2ECC71),
    ("Champion", 0xE91E63), ("Diamond", 0x9B59B6), ("Emerald", 0x1ABC9C),
    ("Platinum", 0x5DADE2), ("Gold", 0xF1C40F), ("Silver", 0xC0C0C0),
    ("Bronze", 0xCD7F32), ("Copper", 0xB87333),
]
COLORS = dict(ROLES)

RANKS = ["Copper", "Bronze", "Silver", "Gold", "Platinum", "Emerald", "Diamond", "Champion"]
PLATFORMS = ["PC", "PlayStation", "Xbox"]
SIDES = ["Attacker Main", "Defender Main"]

ROLE_CHANNEL = "🎭│pick-your-roles"

# Top-to-bottom order: (category, [(channel name, kind, read_only)])
LAYOUT = [
    ("📌 INFO", [
        ("👋│welcome", "text", True),
        ("📜│rules", "text", True),
        (ROLE_CHANNEL, "text", True),
        ("📢│announcements", "text", True),
    ]),
    ("💬 COMMUNITY", [
        ("💬│general", "text", False),
        ("🎬│clips", "text", False),
        ("😂│memes", "text", False),
    ]),
    ("🎯 SIEGE", [
        ("🔎│lfg", "text", False),
        ("🧠│strategies", "text", False),
        ("⚔️│scrims", "text", False),
        ("🎮│guess-the-operator", "text", False),
    ]),
    ("🔊 VOICE", [
        ("🔊 Lobby", "voice", False),
        ("🔊 Squad 1", "voice", False),
        ("🔊 Squad 2", "voice", False),
        ("🔊 Squad 3", "voice", False),
    ]),
]


class RoleSelect(discord.ui.Select):
    def __init__(self, custom_id: str, placeholder: str, names: list, single: bool):
        super().__init__(
            custom_id=custom_id,
            placeholder=placeholder,
            min_values=0,  # deselect everything to clear
            max_values=1 if single else len(names),
            options=[discord.SelectOption(label=n) for n in names],
        )
        self.names = names

    async def callback(self, interaction: discord.Interaction):
        guild, member = interaction.guild, interaction.user
        roles = {n: discord.utils.get(guild.roles, name=n) for n in self.names}
        if any(r is None for r in roles.values()):
            await interaction.response.send_message(
                "Some roles are missing. Ask an admin to run /setup again.", ephemeral=True
            )
            return
        chosen = set(self.values)
        to_add = [r for n, r in roles.items() if n in chosen and r not in member.roles]
        to_remove = [r for n, r in roles.items() if n not in chosen and r in member.roles]
        try:
            if to_remove:
                await member.remove_roles(*to_remove, reason="Role picker")
            if to_add:
                await member.add_roles(*to_add, reason="Role picker")
        except discord.Forbidden:
            await interaction.response.send_message(
                "I can't hand out those roles. An admin needs to drag my role above them in Server Settings → Roles.",
                ephemeral=True,
            )
            return
        text = ", ".join(sorted(chosen)) if chosen else "cleared"
        await interaction.response.send_message(f"✅ Updated: **{text}**", ephemeral=True)


class RoleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RoleSelect("roles:rank", "🏅 Pick your rank (one)", RANKS, True))
        self.add_item(RoleSelect("roles:platform", "🎮 Pick your platform(s)", PLATFORMS, False))
        self.add_item(RoleSelect("roles:side", "⚔️ Pick your main side", SIDES, True))


async def post_role_menu(channel: discord.TextChannel):
    embed = discord.Embed(
        title="🎭 Pick your roles",
        description=(
            "Use the dropdowns below. Tap one, choose, and you're set.\n\n"
            "🏅 **Rank**: pick **one**. Choosing a new one replaces the old.\n"
            "🎮 **Platform**: pick all that apply.\n"
            "⚔️ **Main side**: pick **one**.\n\n"
            "To remove a role, open the dropdown and untick it."
        ),
        color=0xF5A623,
    )
    await channel.send(embed=embed, view=RoleView())


async def ensure_roles(guild: discord.Guild) -> int:
    made = 0
    have = {r.name for r in guild.roles}
    # New roles land at the bottom, so create bottom-first to get the listed order.
    for name, color in reversed(ROLES):
        if name not in have:
            await guild.create_role(name=name, colour=discord.Colour(color))
            made += 1
    return made


RULES_CHANNEL = "📜│rules"


def build_rules_embed(guild: discord.Guild) -> discord.Embed:
    roles_ch = discord.utils.get(guild.text_channels, name=ROLE_CHANNEL)
    pick = roles_ch.mention if roles_ch else "the pick-your-roles channel"
    e = discord.Embed(
        title="🛡️ OPERATION: RULES",
        description="Read the briefing before you drop in.",
        color=0xC0392B,
    )
    e.add_field(
        name="🤝 CONDUCT",
        value=(
            "**`01`** **Respect the squad.** No harassment, hate speech, or slurs.\n"
            "**`02`** **Keep it cool.** No flaming teammates or toxic spam.\n"
            "**`03`** **No cheating.** No hacks, boosting, or account selling."
        ),
        inline=False,
    )
    e.add_field(
        name="📍 CHANNELS",
        value=(
            "**`04`** **Stay on target.** Clips in clips, teams in lfg, memes in memes.\n"
            "**`05`** **No spam or ads.** No invite links or self-promo without a mod's OK.\n"
            "**`06`** **Keep it clean.** No NSFW, gore, or anything illegal."
        ),
        inline=False,
    )
    e.add_field(
        name="🔒 SAFETY",
        value=(
            "**`07`** **Protect your intel.** No sharing personal info. No doxxing, ever.\n"
            "**`08`** **Listen to command.** Mods have the final say. Disagree? Message a mod privately."
        ),
        inline=False,
    )
    e.add_field(
        name="✅ READY TO DEPLOY?",
        value=f"Head to {pick} and grab your **rank**, **platform**, and **main side**.",
        inline=False,
    )
    e.set_footer(text="Break the rules and you may get a warning, mute, or ban.")
    return e


async def post_rules(channel: discord.TextChannel):
    await channel.send(embed=build_rules_embed(channel.guild))


@tree.command(name="rules", description="Post the rules embed (admin only)")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def rules(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    channel = discord.utils.get(interaction.guild.text_channels, name=RULES_CHANNEL) or interaction.channel
    try:
        await post_rules(channel)
    except discord.Forbidden:
        await interaction.response.send_message("I can't send messages in that channel.", ephemeral=True)
        return
    await interaction.response.send_message(f"✅ Rules posted in {channel.mention}", ephemeral=True)


@tree.command(name="setup", description="Build the full server layout in order (admin only)")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)
    roles_made = channels_made = 0
    new_rules = None
    try:
        roles_made = await ensure_roles(guild)
        for i, (cat_name, channels) in enumerate(LAYOUT):
            category = discord.utils.get(guild.categories, name=cat_name)
            if category is None:
                category = await guild.create_category(cat_name, position=i)
            for ch_name, kind, read_only in channels:
                if discord.utils.get(category.channels, name=ch_name):
                    continue  # never touch what already exists
                if kind == "voice":
                    await guild.create_voice_channel(ch_name, category=category)
                else:
                    ow = {guild.default_role: discord.PermissionOverwrite(send_messages=False)} if read_only else {}
                    ch = await guild.create_text_channel(ch_name, category=category, overwrites=ow)
                    if ch_name == ROLE_CHANNEL:
                        await post_role_menu(ch)
                    if ch_name == RULES_CHANNEL:
                        new_rules = ch
                channels_made += 1
        if new_rules:
            await post_rules(new_rules)  # after the roles channel exists, so it can be linked
    except discord.Forbidden:
        await interaction.followup.send(
            f"I'm missing permissions (made {channels_made} channels, {roles_made} roles so far). "
            "Give my role **Manage Channels** and **Manage Roles**, then run /setup again. "
            "It skips whatever already exists.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(
        f"✅ Done! Created {channels_made} channels and {roles_made} roles, in order. "
        "Existing stuff was left alone.",
        ephemeral=True,
    )


@tree.command(name="rolemenu", description="Re-post the role picker (admin only)")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def rolemenu(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)
    try:
        await ensure_roles(guild)
        channel = discord.utils.get(guild.text_channels, name=ROLE_CHANNEL)
        if channel is None:
            category = discord.utils.get(guild.categories, name="📌 INFO")
            ow = {guild.default_role: discord.PermissionOverwrite(send_messages=False)}
            channel = await guild.create_text_channel(ROLE_CHANNEL, category=category, overwrites=ow)
        await post_role_menu(channel)
    except discord.Forbidden:
        await interaction.followup.send(
            "I'm missing permissions. Give my role **Manage Channels** and **Manage Roles**, then try again.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(f"✅ Role picker posted in {channel.mention}", ephemeral=True)


@client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {client.user}")


client.run(TOKEN)
