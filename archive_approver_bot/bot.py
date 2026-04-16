import os
import re
import requests
import discord
from discord.ext import commands

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
FASTAPI_BASE_URL = os.environ.get("FASTAPI_BASE_URL", "http://fastapi:8000")
OPENCLAW_BOT_USER_ID = int(os.environ["OPENCLAW_BOT_USER_ID"])
APPROVER_USER_ID = int(os.environ["APPROVER_USER_ID"])

ARCHIVE_BLOCK_RE = re.compile(
    r"\[ARCHIVE_CANDIDATES\]\s*(.*?)\s*\[/ARCHIVE_CANDIDATES\]",
    re.DOTALL,
)


def archive_threads(thread_ids: list[str]) -> dict:
    r = requests.post(
        f"{FASTAPI_BASE_URL}/gmail/archive_threads",
        json={"thread_ids": thread_ids},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def parse_candidates(text: str) -> list[dict]:
    """
    期待する形式:
    [ARCHIVE_CANDIDATES]
    <thread_id>|<subject>|<from>|<reason>
    <thread_id>|<subject>|<from>|<reason>
    [/ARCHIVE_CANDIDATES]
    """
    m = ARCHIVE_BLOCK_RE.search(text)
    if not m:
        return []

    block = m.group(1).strip()
    candidates = []

    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue

        parts = line.split("|")
        if len(parts) < 4:
            continue

        thread_id, subject, sender, reason = parts[:4]

        candidates.append(
            {
                "thread_id": thread_id.strip(),
                "subject": subject.strip(),
                "from": sender.strip(),
                "reason": reason.strip(),
            }
        )

    return candidates


class ArchiveThreadButton(discord.ui.Button):
    def __init__(self, thread_id: str):
        super().__init__(
            label="Archive",
            style=discord.ButtonStyle.success,
        )
        self.thread_id = thread_id

    async def callback(self, interaction: discord.Interaction):
        view: "ArchiveApprovalView" = self.view  # type: ignore

        if interaction.user.id != view.approver_user_id:
            await interaction.response.send_message(
                "この操作は承認者のみ実行できます。",
                ephemeral=True,
            )
            return

        try:
            result = archive_threads([self.thread_id])
        except Exception as e:
            await interaction.response.send_message(
                f"アーカイブに失敗しました: {e}",
                ephemeral=True,
            )
            return

        if result.get("count_archived", 0) == 1:
            view.status = "ARCHIVED"
        else:
            view.status = "FAILED"

        for item in view.children:
            item.disabled = True

        await interaction.response.edit_message(
            content=view.render_text(),
            view=view,
        )


class SkipThreadButton(discord.ui.Button):
    def __init__(self, thread_id: str):
        super().__init__(
            label="Skip",
            style=discord.ButtonStyle.secondary,
        )
        self.thread_id = thread_id

    async def callback(self, interaction: discord.Interaction):
        view: "ArchiveApprovalView" = self.view  # type: ignore

        if interaction.user.id != view.approver_user_id:
            await interaction.response.send_message(
                "この操作は承認者のみ実行できます。",
                ephemeral=True,
            )
            return

        view.status = "SKIPPED"

        for item in view.children:
            item.disabled = True

        await interaction.response.edit_message(
            content=view.render_text(),
            view=view,
        )


class ArchiveApprovalView(discord.ui.View):
    def __init__(self, approver_user_id: int, candidate: dict):
        super().__init__(timeout=600)
        self.approver_user_id = approver_user_id
        self.candidate = candidate
        self.status = "PENDING"

        self.add_item(ArchiveThreadButton(candidate["thread_id"]))
        self.add_item(SkipThreadButton(candidate["thread_id"]))

    def render_text(self) -> str:
        c = self.candidate
        return (
            f"Status: **[{self.status}]**\n"
            f"Title: **{c['subject']}**\n"
            f"From: {c['from']}\n"
            f"Reason: {c['reason']}\n"
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")


@bot.event
async def on_message(message: discord.Message):
    # OpenClaw bot の投稿だけを見る
    if message.author.id != OPENCLAW_BOT_USER_ID:
        return

    candidates = parse_candidates(message.content)
    if not candidates:
        return

    # 候補1件ごとに本文 + 専用ボタンを別メッセージで出す
    for candidate in candidates[:5]:
        view = ArchiveApprovalView(
            approver_user_id=APPROVER_USER_ID,
            candidate=candidate,
        )

        await message.channel.send(
            content=view.render_text(),
            view=view,
        )

    # OpenClaw の生メッセージは隠す
    try:
        await message.delete()
    except Exception as e:
        print(f"Failed to delete original OpenClaw message: {e}")

    await bot.process_commands(message)


bot.run(DISCORD_TOKEN)