import os
import json
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, button, Modal, TextInput, Button as UIButton, UserSelect
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

# ——— Localization ———————————————————————————————————————
LANG = os.getenv("BOT_LANG", "en")
lang_path = os.path.join(os.path.dirname(__file__), "lang", f"{LANG}.json")
with open(lang_path, encoding="utf-8") as f:
    _T = json.load(f)
def t(key: str, **kwargs) -> str:
    return _T.get(key, key).format(**kwargs)


# ——— Constants & Paths —————————————————————————————————————
# Channels written as default are for my channel, if you want just change it here or change it via commands
CREATE_VC_CHANNEL_ID = 1386893005578834020  # fallback trigger channel
VC_CATEGORY_ID       = 1386453793012453417  # fallback category
DEFAULT_TIMEOUT      = 5   # minutes before auto-delete
BASE_DIR             = os.path.dirname(__file__)
TEMPLATES_FILE       = "templates.json"
TEMPLATES_PATH       = os.path.join(BASE_DIR, TEMPLATES_FILE)


# ——— Per-Guild Configuration ——————————————————————————————————
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
else:
    config = {"guilds": {}}

def save_config():
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def _add_permission(guild_id: str, list_name: str, user_id: int, duration_s: int | None):
    """Add an entry to 'allowed' or 'banned' for a guild, with optional expiry in seconds."""
    cfg = config["guilds"].setdefault(guild_id, {})
    perms = cfg.setdefault("permissions", {"allowed": [], "banned": []})
    entry = {"type": "user", "id": user_id, "expires": None}
    if duration_s:
        entry["expires"] = int((datetime.utcnow() + timedelta(seconds=duration_s)).timestamp())
    perms.setdefault(list_name, []).append(entry)
    save_config()


# ——— Per-User Templates & Active VCs —————————————————————————————
templates: dict[int, dict] = {}
private_vcs: dict[int, dict] = {}

def save_templates():
    data = {
        str(owner): tpl for owner, tpl in templates.items()
    }
    with open(TEMPLATES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

async def load_templates():
    if not os.path.exists(TEMPLATES_PATH):
        return
    with open(TEMPLATES_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    for owner_str, tpl in raw.items():
        try:
            owner = int(owner_str)
        except ValueError:
            continue
        templates[owner] = tpl

def update_template_from_channel(owner_id: int, channel: discord.VoiceChannel, deputies: list[int]):
    invited = []
    kicked = []
    for target, perm in channel.overwrites.items():
        if isinstance(target, discord.Member):
            if perm.connect is True:
                invited.append(target.id)
            if perm.connect is False:
                kicked.append(target.id)
    default_overwrite = channel.overwrites.get(channel.guild.default_role)
    visible = True
    locked = False
    if default_overwrite:
        if default_overwrite.view_channel is not None:
            visible = default_overwrite.view_channel
        if default_overwrite.connect is not None:
            locked = not default_overwrite.connect
    templates[owner_id] = {
        "name":       channel.name,
        "user_limit": channel.user_limit,
        "invited":    invited,
        "kicked":     kicked,
        "visible":    visible,
        "locked":     locked,
        "deputies":   deputies,
    }
    save_templates()

def get_user_template(owner_id: int) -> dict | None:
    return templates.get(owner_id)

def get_user_vc(owner_id: int):
    return next((info for info in private_vcs.values() if info["owner"] == owner_id), None)


# ——— Bot Initialization ——————————————————————————————————————
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

@bot.event
async def on_ready():
    await tree.sync()
    await bot.wait_until_ready()
    await load_templates()
    print(f"✅ Bot {bot.user} ready! Loaded {len(templates)} templates.")


# ——— Modals for Rename & Limit ——————————————————————————————————

class RenameModal(Modal):
    def __init__(self, channel: discord.VoiceChannel, owner_id: int):
        super().__init__(title=t("modal_rename_title"))
        self.channel = channel
        self.owner_id = owner_id
        self.input = TextInput(label=t("modal_rename_label"), max_length=100)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        new_name = self.input.value
        await self.channel.edit(name=new_name)
        tpl = private_vcs[self.channel.id]
        update_template_from_channel(self.owner_id, self.channel, tpl["deputies"])
        await interaction.response.send_message(
            t("modal_rename_success", name=new_name), ephemeral=True
        )

class LimitModal(Modal):
    def __init__(self, channel: discord.VoiceChannel, owner_id: int):
        super().__init__(title=t("modal_limit_title"))
        self.channel = channel
        self.owner_id = owner_id
        self.input = TextInput(label=t("modal_limit_label"), max_length=2)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = int(self.input.value)
            if not 0 <= n <= 99:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(t("modal_limit_error"), ephemeral=True)
        await self.channel.edit(user_limit=n)
        tpl = private_vcs[self.channel.id]
        update_template_from_channel(self.owner_id, self.channel, tpl["deputies"])
        await interaction.response.send_message(t("modal_limit_success", limit=n), ephemeral=True)


# ——— User Select Components ——————————————————————————————————

class InviteUserSelect(UserSelect):
    def __init__(self, channel: discord.VoiceChannel, owner_id: int):
        super().__init__(placeholder=t("select_invite_placeholder"))
        self.channel = channel
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        member = self.values[0]
        perms = self.channel.overwrites
        perms[member] = discord.PermissionOverwrite(view_channel=True, connect=True)
        await self.channel.edit(overwrites=perms)
        tpl = private_vcs[self.channel.id]
        update_template_from_channel(self.owner_id, self.channel, tpl["deputies"])
        await interaction.response.send_message(
            t("modal_invite_success", user=member.mention), ephemeral=True
        )
        self.view.stop()

class KickUserSelect(UserSelect):
    def __init__(self, channel: discord.VoiceChannel, owner_id: int):
        super().__init__(placeholder=t("select_kick_placeholder"))
        self.channel = channel
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        member = self.values[0]
        perms = self.channel.overwrites
        perms[member] = discord.PermissionOverwrite(connect=False)
        await self.channel.edit(overwrites=perms)
        tpl = private_vcs[self.channel.id]
        update_template_from_channel(self.owner_id, self.channel, tpl["deputies"])
        await interaction.response.send_message(
            t("modal_kick_success", user=member.mention), ephemeral=True
        )
        self.view.stop()

class AssignUserSelect(UserSelect):
    def __init__(self, channel: discord.VoiceChannel, owner_id: int):
        super().__init__(placeholder=t("select_assign_placeholder"))
        self.channel = channel
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        member = self.values[0]
        tpl = private_vcs[self.channel.id]
        deps = tpl.setdefault("deputies", [])
        if member.id in deps:
            return await interaction.response.send_message(t("modal_assign_error"), ephemeral=True)
        deps.append(member.id)
        perms = self.channel.overwrites
        perms[member] = discord.PermissionOverwrite(
            view_channel=True, connect=True, manage_channels=True
        )
        await self.channel.edit(overwrites=perms)
        update_template_from_channel(self.owner_id, self.channel, deps)
        await interaction.response.send_message(
            f"✅ {member.mention} {t('button_assign').lower()}!", ephemeral=True
        )
        self.view.stop()

class RemoveUserSelect(UserSelect):
    def __init__(self, channel: discord.VoiceChannel, owner_id: int):
        super().__init__(placeholder=t("select_unassign_placeholder"))
        self.channel = channel
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        member = self.values[0]
        tpl = private_vcs[self.channel.id]
        deps = tpl.get("deputies", [])
        if member.id not in deps:
            return await interaction.response.send_message(t("modal_unassign_error"), ephemeral=True)
        deps.remove(member.id)
        perms = self.channel.overwrites
        perms.pop(member, None)
        await self.channel.edit(overwrites=perms)
        update_template_from_channel(self.owner_id, self.channel, deps)
        await interaction.response.send_message(
            f"✅ {member.mention} {t('button_unassign').lower()}!", ephemeral=True
        )
        self.view.stop()


# ——— Selection Views ——————————————————————————————————————

class InviteSelectView(View):
    def __init__(self, channel, owner_id):
        super().__init__(timeout=60)
        self.add_item(InviteUserSelect(channel, owner_id))

class KickSelectView(View):
    def __init__(self, channel, owner_id):
        super().__init__(timeout=60)
        self.add_item(KickUserSelect(channel, owner_id))

class AssignSelectView(View):
    def __init__(self, channel, owner_id):
        super().__init__(timeout=60)
        self.add_item(AssignUserSelect(channel, owner_id))

class RemoveSelectView(View):
    def __init__(self, channel, owner_id):
        super().__init__(timeout=60)
        self.add_item(RemoveUserSelect(channel, owner_id))


# ——— Management Buttons View ——————————————————————————————————

class ChannelManagementView(View):
    def __init__(self, channel: discord.VoiceChannel, owner_id: int):
        super().__init__(timeout=None)
        self.channel = channel
        self.owner_id = owner_id

    def owner_check(self, interaction):
        if interaction.user.id != self.owner_id:
            asyncio.create_task(interaction.response.send_message(
                t("error_not_owner"), ephemeral=True
            ))
            return False
        return True

    @button(label=t("button_rename"),    style=discord.ButtonStyle.primary,   custom_id="rename_btn")
    async def rename_btn(self, interaction, button: UIButton):
        if not self.owner_check(interaction): return
        await interaction.response.send_modal(RenameModal(self.channel, self.owner_id))

    @button(label=t("button_limit"),     style=discord.ButtonStyle.secondary, custom_id="limit_btn")
    async def limit_btn(self, interaction, button: UIButton):
        if not self.owner_check(interaction): return
        await interaction.response.send_modal(LimitModal(self.channel, self.owner_id))

    @button(label=t("button_invite"),    style=discord.ButtonStyle.success,   custom_id="invite_btn")
    async def invite_btn(self, interaction, button: UIButton):
        if not self.owner_check(interaction): return
        await interaction.response.send_message(
            t("button_invite"), view=InviteSelectView(self.channel, self.owner_id), ephemeral=True
        )

    @button(label=t("button_kick"),      style=discord.ButtonStyle.danger,    custom_id="kick_btn")
    async def kick_btn(self, interaction, button: UIButton):
        if not self.owner_check(interaction): return
        await interaction.response.send_message(
            t("button_kick"), view=KickSelectView(self.channel, self.owner_id), ephemeral=True
        )

    @button(label=t("button_visible"),   style=discord.ButtonStyle.success,   custom_id="visible_btn")
    async def visible_btn(self, interaction, button: UIButton):
        if not self.owner_check(interaction): return
        perms = self.channel.overwrites
        perms[self.channel.guild.default_role] = discord.PermissionOverwrite(view_channel=True)
        await self.channel.edit(overwrites=perms)
        tpl = private_vcs[self.channel.id]
        update_template_from_channel(self.owner_id, self.channel, tpl["deputies"])
        await interaction.response.send_message(t("button_visible"), ephemeral=True)

    @button(label=t("button_invisible"), style=discord.ButtonStyle.secondary, custom_id="invisible_btn")
    async def invisible_btn(self, interaction, button: UIButton):
        if not self.owner_check(interaction): return
        perms = self.channel.overwrites
        perms[self.channel.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        await self.channel.edit(overwrites=perms)
        tpl = private_vcs[self.channel.id]
        update_template_from_channel(self.owner_id, self.channel, tpl["deputies"])
        await interaction.response.send_message(t("button_invisible"), ephemeral=True)

    @button(label=t("button_lock"),      style=discord.ButtonStyle.danger,    custom_id="lock_btn")
    async def lock_btn(self, interaction, button: UIButton):
        if not self.owner_check(interaction): return
        perms = self.channel.overwrites
        perms[self.channel.guild.default_role] = discord.PermissionOverwrite(connect=False)
        await self.channel.edit(overwrites=perms)
        tpl = private_vcs[self.channel.id]
        update_template_from_channel(self.owner_id, self.channel, tpl["deputies"])
        await interaction.response.send_message(t("button_lock"), ephemeral=True)

    @button(label=t("button_unlock"),    style=discord.ButtonStyle.success,   custom_id="unlock_btn")
    async def unlock_btn(self, interaction, button: UIButton):
        if not self.owner_check(interaction): return
        perms = self.channel.overwrites
        perms[self.channel.guild.default_role] = discord.PermissionOverwrite(connect=True)
        await self.channel.edit(overwrites=perms)
        tpl = private_vcs[self.channel.id]
        update_template_from_channel(self.owner_id, self.channel, tpl["deputies"])
        await interaction.response.send_message(t("button_unlock"), ephemeral=True)

    @button(label=t("button_assign"),    style=discord.ButtonStyle.primary,   custom_id="assign_btn")
    async def assign_btn(self, interaction, button: UIButton):
        if not self.owner_check(interaction): return
        await interaction.response.send_message(
            t("button_assign"), view=AssignSelectView(self.channel, self.owner_id), ephemeral=True
        )

    @button(label=t("button_unassign"),  style=discord.ButtonStyle.danger,    custom_id="unassign_btn")
    async def unassign_btn(self, interaction, button: UIButton):
        if not self.owner_check(interaction): return
        await interaction.response.send_message(
            t("button_unassign"), view=RemoveSelectView(self.channel, self.owner_id), ephemeral=True
        )

    @button(label=t("button_delete"),    style=discord.ButtonStyle.danger,    custom_id="delete_btn")
    async def delete_btn(self, interaction, button: UIButton):
        if not self.owner_check(interaction): return
        await self.channel.delete()
        data = private_vcs.pop(self.channel.id, {})
        if thread := data.get("thread"):
            await thread.delete()
        await interaction.response.send_message(t("button_delete"), ephemeral=True)


# ——— Voice State Update: Config + Private VC Logic —————————————————————

@bot.event
async def on_voice_state_update(member: discord.Member,
                                before: discord.VoiceState,
                                after: discord.VoiceState):
    # 0) Clean up stale record if channel was deleted out-of-band
    existing = get_user_vc(member.id)
    if existing:
        chan_id = existing["channel"].id
        if member.guild.get_channel(chan_id) is None:
            private_vcs.pop(chan_id, None)
            existing = None

    # 1) Config-based permission cleanup & fetch trigger/category
    gid = str(member.guild.id)
    cfg = config["guilds"].get(gid, {})
    perms = cfg.setdefault("permissions", {"allowed": [], "banned": []})
    now_ts = int(datetime.utcnow().timestamp())
    for list_name in ("allowed", "banned"):
        perms[list_name] = [
            e for e in perms.get(list_name, [])
            if e["expires"] is None or e["expires"] > now_ts
        ]
    save_config()

    trigger_id = cfg.get("trigger_channel_id", CREATE_VC_CHANNEL_ID)
    default_cat = cfg.get("default_category_id", VC_CATEGORY_ID)
    create_cat  = cfg.get("create_category_id", default_cat)

    # 2) Handle join trigger -> move to existing or create new VC
    if after.channel and after.channel.id == trigger_id:
        # Permission check: banned
        if any(e["type"] == "user" and e["id"] == member.id for e in perms["banned"]):
            try:
                await member.send(t("error_banned"))
            except:
                pass
            return
        # Permission check: allowed-list if non-empty
        if perms["allowed"]:
            ok = any(e["type"] == "user" and e["id"] == member.id for e in perms["allowed"])
            if not ok and not member.guild_permissions.administrator:
                try:
                    await member.send(t("error_no_permission"))
                except:
                    pass
                return

        # If user already has a VC, just move them
        if existing:
            return await member.move_to(existing["channel"])

        # Otherwise, create new VC
        guild    = member.guild
        category = guild.get_channel(create_cat)
        tpl      = get_user_template(member.id) or {}

        name        = tpl.get("name", f"{member.display_name}'s VC")
        user_limit  = tpl.get("user_limit", 5)
        default_view    = tpl.get("visible", True)
        default_connect = not tpl.get("locked", False)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=default_view,
                connect=default_connect
            )
        }
        for uid in tpl.get("invited", []):
            m = guild.get_member(uid)
            if m:
                overwrites[m] = discord.PermissionOverwrite(view_channel=True, connect=True)
        for uid in tpl.get("kicked", []):
            m = guild.get_member(uid)
            if m:
                overwrites[m] = discord.PermissionOverwrite(connect=False)
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True, connect=True, manage_channels=True
        )
        for uid in tpl.get("deputies", []):
            m = guild.get_member(uid)
            if m:
                overwrites[m] = discord.PermissionOverwrite(
                    view_channel=True, connect=True, manage_channels=True
                )

        vc = await guild.create_voice_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            user_limit=user_limit
        )
        await asyncio.sleep(1)
        await member.move_to(vc)

        # Send management embed & view
        commands_list = "\n".join(
            f"• /{cmd} — {t('cmd_' + cmd + '_desc')}"
            for cmd in ["limit","rename","invite","kick","visible","invisible","lock","unlock","assign","unassign","delete"]
        )
        embed = discord.Embed(
            title=t("embed_title"),
            description=t("embed_desc", owner=member.mention, commands=commands_list),
            color=discord.Color.blurple()
        )
        try:
            msg = await vc.send(embed=embed, view=ChannelManagementView(vc, member.id))
            thread = await msg.create_thread(name=f"{member.display_name}-management", auto_archive_duration=60)
            await thread.send(f"{member.mention}, manage your channel here 👇")
        except Exception:
            thread = None

        private_vcs[vc.id] = {
            "owner":    member.id,
            "channel":  vc,
            "thread":   thread,
            "timeout":  DEFAULT_TIMEOUT,
            "deputies": tpl.get("deputies", [])
        }
        return

    # 3) Auto-delete empty private VC after timeout
    if before.channel and before.channel.id in private_vcs:
        data = private_vcs[before.channel.id]
        if not before.channel.members:
            await asyncio.sleep(data["timeout"] * 60)
            if not data["channel"].members:
                await data["channel"].delete()
                if data["thread"]:
                    await data["thread"].delete()
                private_vcs.pop(before.channel.id, None)


# —————————————————— ADMIN CONFIG & PERMISSION COMMANDS ——————————————————

@tree.command(name="vcconfig_trigger_set", description=t("cmd_vcconfig_trigger_set"))
@app_commands.checks.has_permissions(administrator=True)
async def vcconfig_trigger_set(interaction: discord.Interaction, channel: discord.VoiceChannel):
    gid = str(interaction.guild.id)
    cfg = config["guilds"].setdefault(gid, {})
    cfg["trigger_channel_id"] = channel.id
    cfg.setdefault("default_category_id", channel.category_id)
    save_config()
    await interaction.response.send_message(
        t("vcconfig_trigger_set_success", channel=channel.name), ephemeral=True
    )

@tree.command(name="vcconfig_default_cat", description=t("cmd_vcconfig_default_cat"))
@app_commands.checks.has_permissions(administrator=True)
async def vcconfig_default_cat(interaction: discord.Interaction, category: discord.CategoryChannel):
    gid = str(interaction.guild.id)
    cfg = config["guilds"].setdefault(gid, {})
    cfg["default_category_id"] = category.id
    save_config()
    await interaction.response.send_message(
        t("vcconfig_default_cat_success", category=category.name), ephemeral=True
    )

@tree.command(name="vcconfig_create_cat", description=t("cmd_vcconfig_create_cat"))
@app_commands.checks.has_permissions(administrator=True)
async def vcconfig_create_cat(interaction: discord.Interaction, category: discord.CategoryChannel):
    gid = str(interaction.guild.id)
    cfg = config["guilds"].setdefault(gid, {})
    cfg["create_category_id"] = category.id
    save_config()
    await interaction.response.send_message(
        t("vcconfig_create_cat_success", category=category.name), ephemeral=True
    )

@tree.command(
    name="vcperm_grant",
    description=t("cmd_vcperm_grant_desc")
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    user="Whos getting permission?",
    years="Years",
    months="Months",
    days="Days",
    hours="Hours",
    minutes="Minutes",
    seconds="Seconds"
)
async def vcperm_grant(
    interaction: discord.Interaction,
    user: discord.Member,
    years: int = 0,
    months: int = 0,
    days: int = 0,
    hours: int = 0,
    minutes: int = 0,
    seconds: int = 0
):
    total_secs = (
        years   * 365 * 24 * 3600 +
        months  * 30  * 24 * 3600 +
        days    * 24  * 3600 +
        hours   * 3600 +
        minutes * 60 +
        seconds
    )
    duration = total_secs if total_secs > 0 else None

    _add_permission(str(interaction.guild.id), "allowed", user.id, duration)

    if duration:
        expires_at = (datetime.utcnow() + timedelta(seconds=duration)) \
                     .strftime("%Y-%m-%d %H:%M UTC")
        await interaction.response.send_message(
            t("vcperm_grant_success", user=user.mention)
            + f" (до {expires_at})",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            t("vcperm_grant_success", user=user.mention),
            ephemeral=True
        )

@tree.command(
    name="vcperm_revoke",
    description=t("cmd_vcperm_revoke_desc")
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    user="Whos getting banned?",
    years="Years",
    months="Months",
    days="Days",
    hours="Hours",
    minutes="Minutes",
    seconds="Seconds"
)
async def vcperm_revoke(
    interaction: discord.Interaction,
    user: discord.Member,
    years: int = 0,
    months: int = 0,
    days: int = 0,
    hours: int = 0,
    minutes: int = 0,
    seconds: int = 0
):
    gid   = str(interaction.guild.id)
    perms = config["guilds"].setdefault(gid, {}).setdefault("permissions", {"allowed": [], "banned": []})
    perms["allowed"] = [
        e for e in perms.get("allowed", [])
        if not (e["type"] == "user" and e["id"] == user.id)
    ]

    total_secs = (
        years   * 365*24*3600 +
        months  * 30*24*3600 +
        days    * 24*3600 +
        hours   * 3600 +
        minutes * 60 +
        seconds
    )
    duration = total_secs if total_secs > 0 else None

    _add_permission(gid, "banned", user.id, duration)

    if duration:
        expires_at = (datetime.utcnow() + timedelta(seconds=duration)) \
                     .strftime("%Y-%m-%d %H:%M UTC")
        await interaction.response.send_message(
            t("vcperm_revoke_success", user=user.mention)
            + f" (до {expires_at})",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            t("vcperm_revoke_success", user=user.mention),
            ephemeral=True
        )

@tree.command(
    name="vcban_add",
    description=t("cmd_vcban_add_desc")
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    user="Whos getting banned?",
    years="Years",
    days="Days",
    hours="Hours",
    minutes="Minutes",
    seconds="Seconds"
)
async def vcban_add(
    interaction: discord.Interaction,
    user: discord.Member,
    years: int = 0,
    days: int = 0,
    hours: int = 0,
    minutes: int = 0,
    seconds: int = 0
):
    total_secs = (
        years  * 365 * 24 * 3600 +
        days   * 24  * 3600 +
        hours  * 3600 +
        minutes * 60 +
        seconds
    )
    duration = total_secs if total_secs > 0 else None

    _add_permission(str(interaction.guild.id), "banned", user.id, duration)

    if duration:
        expires_at = (datetime.utcnow() + timedelta(seconds=duration)) \
                     .strftime("%Y-%m-%d %H:%M UTC")
        await interaction.response.send_message(
            t("vcban_add_success", user=user.mention) +
            f" (до {expires_at})",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            t("vcban_add_success", user=user.mention),
            ephemeral=True
        )

@tree.command(name="vcban_remove", description=t("cmd_vcban_remove_desc"))
@app_commands.checks.has_permissions(administrator=True)
async def vcban_remove(interaction: discord.Interaction, user: discord.Member):
    gid = str(interaction.guild.id)
    perms = config["guilds"].get(gid, {}).get("permissions", {})
    perms["banned"] = [
        e for e in perms.get("banned", [])
        if not (e["type"] == "user" and e["id"] == user.id)
    ]
    save_config()
    await interaction.response.send_message(
        t("vcban_remove_success", user=user.mention), ephemeral=True
    )


@tree.command(
    name="vcban_list",
    description=t("cmd_vcban_list_desc")
)
@app_commands.checks.has_permissions(administrator=True)
async def vcban_list(interaction: discord.Interaction):
    gid    = str(interaction.guild.id)
    banned = config["guilds"].get(gid, {}) \
                     .get("permissions", {}) \
                     .get("banned", [])
    if not banned:
        return await interaction.response.send_message(
            t("vcban_list_empty"), ephemeral=True
        )

    lines = []
    for e in banned:
        if e["type"] == "user":
            m = interaction.guild.get_member(e["id"])
            name = m.mention if m else f"`User ID {e['id']}`"
        else:
            r = interaction.guild.get_role(e["id"])
            name = r.mention if r else f"`Role ID {e['id']}`"

        if e["expires"]:
            exp = datetime.utcfromtimestamp(e["expires"]).strftime("%Y-%m-%d %H:%M UTC")
        else:
            exp = t("never")
        lines.append(t("vcban_list_entry", entity=name, expires=exp))

    header = t("vcban_list_header", count=len(lines))
    await interaction.response.send_message(
        header + "\n" + "\n".join(lines),
        ephemeral=True
    )


@tree.command(
    name="vcperm_list",
    description=t("cmd_vcperm_list_desc")
)
@app_commands.checks.has_permissions(administrator=True)
async def vcperm_list(interaction: discord.Interaction):
    gid     = str(interaction.guild.id)
    allowed = config["guilds"].get(gid, {}) \
                       .get("permissions", {}) \
                       .get("allowed", [])

    if not allowed:
        total = interaction.guild.member_count
        return await interaction.response.send_message(
            t("vcperm_list_all", count=total),
            ephemeral=True
        )

    lines = []
    for e in allowed:
        if e["type"] == "user":
            m    = interaction.guild.get_member(e["id"])
            name = m.mention if m else f"`User ID {e['id']}`"
        else:
            r    = interaction.guild.get_role(e["id"])
            name = r.mention if r else f"`Role ID {e['id']}`"

        exp = datetime.utcfromtimestamp(e["expires"]).strftime("%Y-%m-%d %H:%M UTC") \
              if e["expires"] else t("never")
        lines.append(t("vcperm_list_entry", entity=name, expires=exp))

    header = t("vcperm_list_header", count=len(lines))
    await interaction.response.send_message(
        header + "\n" + "\n".join(lines),
        ephemeral=True
    )


@tree.command(
    name="vcrevoke_list",
    description=t("cmd_vcrevoke_list_desc")
)
@app_commands.checks.has_permissions(administrator=True)
async def vcrevoke_list(interaction: discord.Interaction):
    gid    = str(interaction.guild.id)
    perms  = config["guilds"].get(gid, {}).get("permissions", {})
    allowed = perms.get("allowed", [])
    banned  = perms.get("banned", [])

    lines = []

    if allowed:
        allowed_ids = {e["id"] for e in allowed if e["type"] == "user"}
        for m in interaction.guild.members:
            if m.guild_permissions.administrator:
                continue
            if m.id not in allowed_ids:
                lines.append(m.mention)
        header = t("vcrevoke_list_header", count=len(lines))
    else:
        if not banned:
            return await interaction.response.send_message(
                t("vcrevoke_list_empty"), ephemeral=True
            )
        for e in banned:
            if e["type"] == "user":
                m    = interaction.guild.get_member(e["id"])
                name = m.mention if m else f"`User ID {e['id']}`"
            else:
                r    = interaction.guild.get_role(e["id"])
                name = r.mention if r else f"`Role ID {e['id']}`"
            lines.append(name)
        header = t("vcrevoke_list_banned_header", count=len(lines))

    await interaction.response.send_message(
        header + "\n" + "\n".join(lines),
        ephemeral=True
    )

@tree.command(
    name="vcperm_grant_all",
    description=t("cmd_vcperm_grant_all_desc")
)
@app_commands.checks.has_permissions(administrator=True)
async def vcperm_grant_all(interaction: discord.Interaction):
    gid = str(interaction.guild.id)
    cfg = config["guilds"].setdefault(gid, {})
    cfg["permissions"] = {"allowed": [], "banned": []}
    save_config()
    await interaction.response.send_message(
        t("vcperm_grant_all_success"), ephemeral=True
    )

@tree.command(
    name="vcperm_revoke_all",
    description=t("cmd_vcperm_revoke_all_desc")
)
@app_commands.checks.has_permissions(administrator=True)
async def vcperm_revoke_all(interaction: discord.Interaction):
    gid = str(interaction.guild.id)
    cfg = config["guilds"].setdefault(gid, {})
    cfg["permissions"] = {
        "allowed": [],
        "banned": [{"type":"role", "id": interaction.guild.default_role.id, "expires": None}]
    }
    save_config()
    await interaction.response.send_message(
        t("vcperm_revoke_all_success"), ephemeral=True
    )
    
# —————————————————— SLASH-COMMANDS FOR PRIVATE VC MANAGEMENT ——————————————————

@tree.command(name="limit", description=t("cmd_limit_desc"))
async def limit_cmd(interaction: discord.Interaction, number: int):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(
            t("error_not_owner"), ephemeral=True
        )
    n = max(0, min(99, number))
    await data["channel"].edit(user_limit=n)
    update_template_from_channel(data["owner"], data["channel"], data["deputies"])
    await interaction.response.send_message(
        t("modal_limit_success", limit=n), ephemeral=True
    )

@tree.command(name="rename", description=t("cmd_rename_desc"))
async def rename_cmd(interaction: discord.Interaction, name: str):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(
            t("error_not_owner"), ephemeral=True
        )
    await data["channel"].edit(name=name)
    update_template_from_channel(data["owner"], data["channel"], data["deputies"])
    await interaction.response.send_message(
        t("modal_rename_success", name=name), ephemeral=True
    )

@tree.command(name="invite", description=t("cmd_invite_desc"))
async def invite_cmd(interaction: discord.Interaction, user: discord.Member):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(
            t("error_not_owner"), ephemeral=True
        )
    perms = data["channel"].overwrites
    perms[user] = discord.PermissionOverwrite(view_channel=True, connect=True)
    await data["channel"].edit(overwrites=perms)
    update_template_from_channel(data["owner"], data["channel"], data["deputies"])
    await interaction.response.send_message(
        t("modal_invite_success", user=user.mention), ephemeral=True
    )

@tree.command(name="kick", description=t("cmd_kick_desc"))
async def kick_cmd(interaction: discord.Interaction, user: discord.Member):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(
            t("error_not_owner"), ephemeral=True
        )
    perms = data["channel"].overwrites
    perms[user] = discord.PermissionOverwrite(connect=False)
    await data["channel"].edit(overwrites=perms)
    update_template_from_channel(data["owner"], data["channel"], data["deputies"])
    await interaction.response.send_message(
        t("modal_kick_success", user=user.mention), ephemeral=True
    )

@tree.command(name="assign", description=t("cmd_assign_desc"))
async def assign_cmd(interaction: discord.Interaction, user: discord.Member):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(
            t("error_not_owner"), ephemeral=True
        )
    deps = data.setdefault("deputies", [])
    if user.id in deps:
        return await interaction.response.send_message(
            f"❌ {user.mention} {t('button_assign').lower()}.", ephemeral=True
        )
    deps.append(user.id)
    perms = data["channel"].overwrites
    perms[user] = discord.PermissionOverwrite(
        view_channel=True, connect=True, manage_channels=True
    )
    await data["channel"].edit(overwrites=perms)
    update_template_from_channel(data["owner"], data["channel"], deps)
    await interaction.response.send_message(
        f"✅ {user.mention} {t('button_assign').lower()}!", ephemeral=True
    )

@tree.command(name="unassign", description=t("cmd_unassign_desc"))
async def unassign_cmd(interaction: discord.Interaction, user: discord.Member):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(
            t("error_not_owner"), ephemeral=True
        )
    deps = data.get("deputies", [])
    if user.id not in deps:
        return await interaction.response.send_message(
            f"❌ {user.mention} {t('button_unassign').lower()}.", ephemeral=True
        )
    deps.remove(user.id)
    perms = data["channel"].overwrites
    perms.pop(user, None)
    await data["channel"].edit(overwrites=perms)
    update_template_from_channel(data["owner"], data["channel"], deps)
    await interaction.response.send_message(
        f"✅ {user.mention} {t('button_unassign').lower()}!", ephemeral=True
    )

@tree.command(name="delete", description=t("cmd_delete_desc"))
async def delete_cmd(interaction: discord.Interaction):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(
            t("error_not_owner"), ephemeral=True
        )
    await data["channel"].delete()
    if data["thread"]:
        await data["thread"].delete()
    private_vcs.pop(data["channel"].id, None)
    await interaction.response.send_message(
        t("button_delete"), ephemeral=True
    )

@tree.command(name="lock", description=t("cmd_lock_desc"))
async def lock_cmd(interaction: discord.Interaction):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(
            t("error_not_owner"), ephemeral=True
        )
    perms = data["channel"].overwrites
    default = perms.get(data["channel"].guild.default_role, discord.PermissionOverwrite())
    perms[data["channel"].guild.default_role] = discord.PermissionOverwrite(
        connect=False, view_channel=default.view_channel
    )
    await data["channel"].edit(overwrites=perms)
    update_template_from_channel(data["owner"], data["channel"], data["deputies"])
    await interaction.response.send_message(
        t("button_lock"), ephemeral=True
    )

@tree.command(name="unlock", description=t("cmd_unlock_desc"))
async def unlock_cmd(interaction: discord.Interaction):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(
            t("error_not_owner"), ephemeral=True
        )
    perms = data["channel"].overwrites
    default = perms.get(data["channel"].guild.default_role, discord.PermissionOverwrite())
    perms[data["channel"].guild.default_role] = discord.PermissionOverwrite(
        connect=True, view_channel=default.view_channel
    )
    await data["channel"].edit(overwrites=perms)
    update_template_from_channel(data["owner"], data["channel"], data["deputies"])
    await interaction.response.send_message(
        t("button_unlock"), ephemeral=True
    )

@tree.command(name="visible", description=t("cmd_visible_desc"))
async def visible_cmd(interaction: discord.Interaction):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(
            t("error_not_owner"), ephemeral=True
        )
    perms = data["channel"].overwrites
    default = perms.get(data["channel"].guild.default_role, discord.PermissionOverwrite())
    perms[data["channel"].guild.default_role] = discord.PermissionOverwrite(
        view_channel=True, connect=default.connect
    )
    await data["channel"].edit(overwrites=perms)
    update_template_from_channel(data["owner"], data["channel"], data["deputies"])
    await interaction.response.send_message(
        t("button_visible"), ephemeral=True
    )

@tree.command(name="invisible", description=t("cmd_invisible_desc"))
async def invisible_cmd(interaction: discord.Interaction):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(
            t("error_not_owner"), ephemeral=True
        )
    perms = data["channel"].overwrites
    default = perms.get(data["channel"].guild.default_role, discord.PermissionOverwrite())
    perms[data["channel"].guild.default_role] = discord.PermissionOverwrite(
        view_channel=False, connect=default.connect
    )
    await data["channel"].edit(overwrites=perms)
    update_template_from_channel(data["owner"], data["channel"], data["deputies"])
    await interaction.response.send_message(
        t("button_invisible"), ephemeral=True
    )


# ─── Run Bot ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(os.getenv("TOKEN"))
