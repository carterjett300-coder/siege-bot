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
HINT_DELAY = 15  # seconds between hints (a bit longer for thumb typing)

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


intents = discord.Intents.default()  # no message-content intent needed anymore
client = discord.Client(intents=intents)
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


@client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {client.user}")


client.run(TOKEN)
