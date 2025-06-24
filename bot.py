import os
import json
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, button, Modal, TextInput, Button as UIButton, UserSelect
from dotenv import load_dotenv

load_dotenv()

# ——— Локализация ———————————————————————————————————————
LANG = os.getenv("BOT_LANG", "en")
lang_path = os.path.join(os.path.dirname(__file__), "lang", f"{LANG}.json")
with open(lang_path, encoding="utf-8") as f:
    _T = json.load(f)
def t(key: str, **kwargs) -> str:
    return _T.get(key, key).format(**kwargs)

# ——— Константы ————————————————————————————————————————
CREATE_VC_CHANNEL_ID = 1386893005578834020
VC_CATEGORY_ID       = 1386453793012453417
DEFAULT_TIMEOUT      = 5   # минут
TEMPLATES_FILE       = "templates.json"

# Путь к файлу шаблонов рядом со скриптом
BASE_DIR       = os.path.dirname(__file__)
TEMPLATES_PATH = os.path.join(BASE_DIR, TEMPLATES_FILE)

# ——— Инициализация бота —————————————————————————————————————
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Хранилище: шаблоны настроек по владельцам
# templates: { owner_id: {
#     name: str,
#     user_limit: int,
#     invited: list[int],
#     kicked: list[int],
#     visible: bool,
#     locked: bool,
#     deputies: list[int]
# } }
templates: dict[int, dict] = {}

# Активные приватные каналы: не сохраняются на диск
# private_vcs: { vc_id: {
#     owner: int,
#     channel: VoiceChannel,
#     thread: Thread|None,
#     timeout: int,
#     deputies: list[int]
# } }
private_vcs: dict[int, dict] = {}

def save_templates():
    """Сохраняет текущее состояние `templates` в JSON."""
    data = {}
    for owner_id, tpl in templates.items():
        data[str(owner_id)] = {
            "name":       tpl.get("name", ""),
            "user_limit": tpl.get("user_limit", 5),
            "invited":    tpl.get("invited", []),
            "kicked":     tpl.get("kicked", []),
            "visible":    tpl.get("visible", True),
            "locked":     tpl.get("locked", False),
            "deputies":   tpl.get("deputies", []),
        }
    with open(TEMPLATES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

async def load_templates():
    """Загружает шаблоны из JSON (если файл есть)."""
    if not os.path.exists(TEMPLATES_PATH):
        return
    with open(TEMPLATES_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    for owner_str, entry in raw.items():
        try:
            owner = int(owner_str)
        except ValueError:
            continue
        templates[owner] = {
            "name":       entry.get("name", ""),
            "user_limit": entry.get("user_limit", 5),
            "invited":    entry.get("invited", []),
            "kicked":     entry.get("kicked", []),
            "visible":    entry.get("visible", True),
            "locked":     entry.get("locked", False),
            "deputies":   entry.get("deputies", []),
        }

def update_template_from_channel(owner_id: int, channel: discord.VoiceChannel, deputies: list[int]):
    """Обновляет шаблон для владельца, исходя из текущих настроек канала."""
    # Собираем списки приглашённых и кикнутых
    invited = []
    kicked  = []
    for target, perm in channel.overwrites.items():
        if isinstance(target, discord.Member):
            if perm.connect is True:
                invited.append(target.id)
            if perm.connect is False:
                kicked.append(target.id)
    # Проверяем @everyone
    default_overwrite = channel.overwrites.get(channel.guild.default_role)
    visible = True
    locked  = False
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

@bot.event
async def on_ready():
    await tree.sync()
    await bot.wait_until_ready()
    await load_templates()
    print(f"✅ Bot {bot.user} ready! Loaded {len(templates)} templates.")

# ——— Модалки —————————————————————————————————————————————

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
        # обновляем шаблон
        tpl = private_vcs[self.channel.id]
        update_template_from_channel(self.owner_id, self.channel, tpl["deputies"])
        await interaction.response.send_message(t("modal_rename_success", name=new_name), ephemeral=True)

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

# ——— Селекты —————————————————————————————————————————————

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
        # добавляем в invited, убираем из kicked
        deputies = tpl["deputies"]
        update_template_from_channel(self.owner_id, self.channel, deputies)
        await interaction.response.send_message(t("modal_invite_success", user=member.mention), ephemeral=True)
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
        deputies = tpl["deputies"]
        update_template_from_channel(self.owner_id, self.channel, deputies)
        await interaction.response.send_message(t("modal_kick_success", user=member.mention), ephemeral=True)
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
            return await interaction.response.send_message(t("modal_invite_error"), ephemeral=True)
        deps.append(member.id)
        perms = self.channel.overwrites
        perms[member] = discord.PermissionOverwrite(
            view_channel=True, connect=True, manage_channels=True
        )
        await self.channel.edit(overwrites=perms)
        update_template_from_channel(self.owner_id, self.channel, deps)
        await interaction.response.send_message(f"✅ {member.mention} {t('button_assign').lower()}!", ephemeral=True)
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
            return await interaction.response.send_message(t("modal_kick_error"), ephemeral=True)
        deps.remove(member.id)
        perms = self.channel.overwrites
        perms.pop(member, None)
        await self.channel.edit(overwrites=perms)
        update_template_from_channel(self.owner_id, self.channel, deps)
        await interaction.response.send_message(f"✅ {member.mention} {t('button_unassign').lower()}!", ephemeral=True)
        self.view.stop()

# ——— Вью для селектов ——————————————————————————————————

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

# ——— View управления каналом —————————————————————————————————

class ChannelManagementView(View):
    def __init__(self, channel: discord.VoiceChannel, owner_id: int):
        super().__init__(timeout=None)
        self.channel = channel
        self.owner_id = owner_id

    def owner_check(self, interaction):
        if interaction.user.id != self.owner_id:
            asyncio.create_task(interaction.response.send_message(t("error_not_owner"), ephemeral=True))
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
        # удаляем только активный канал, шаблон остаётся
        await self.channel.delete()
        data = private_vcs.pop(self.channel.id, {})
        if thread := data.get("thread"):
            await thread.delete()
        await interaction.response.send_message(t("button_delete"), ephemeral=True)

# ——— Создание и авто-удаление приватных VC —————————————————————

@bot.event
async def on_voice_state_update(member, before, after):
    # 0) Попытаемся «очистить» stale-запись, если канал уже удалён
    existing = get_user_vc(member.id)
    if existing:
        chan_id = existing["channel"].id
        # если в гильдии нет канала с таким ID — удаляем запись
        if member.guild.get_channel(chan_id) is None:
            private_vcs.pop(chan_id, None)
            existing = None

    # 1) Если вошли в триггер и уже есть живой канал — просто перекинуть
    if after.channel and after.channel.id == CREATE_VC_CHANNEL_ID:
        if existing:
            return await member.move_to(existing["channel"])

        # 2) Иначе — создаём новый
        guild    = member.guild
        category = guild.get_channel(VC_CATEGORY_ID)
        tpl      = get_user_template(member.id) or {}

        name        = tpl.get("name", f"{member.display_name}'s VC")
        user_limit  = tpl.get("user_limit", 5)
        default_view    = tpl.get("visible", True)
        default_connect = not tpl.get("locked", False)

        base_overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=default_view,
                connect=default_connect
            )
        }
        # приглашённые
        for uid in tpl.get("invited", []):
            m = guild.get_member(uid)
            if m:
                base_overwrites[m] = discord.PermissionOverwrite(view_channel=True, connect=True)
        # кикнутые
        for uid in tpl.get("kicked", []):
            m = guild.get_member(uid)
            if m:
                base_overwrites[m] = discord.PermissionOverwrite(connect=False)
        # владелец и помощники
        base_overwrites[member] = discord.PermissionOverwrite(
            view_channel=True, connect=True, manage_channels=True
        )
        for uid in tpl.get("deputies", []):
            m = guild.get_member(uid)
            if m:
                base_overwrites[m] = discord.PermissionOverwrite(
                    view_channel=True, connect=True, manage_channels=True
                )

        vc = await guild.create_voice_channel(
            name=name,
            category=category,
            overwrites=base_overwrites,
            user_limit=user_limit
        )
        await asyncio.sleep(1)
        await member.move_to(vc)

        # отправляем Embed + вью управления...
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
            thread = await msg.create_thread(name=f"{member.display_name}-управление", auto_archive_duration=60)
            await thread.send(f"{member.mention}, управляй каналом здесь 👇")
        except Exception:
            thread = None

        private_vcs[vc.id] = {
            "owner":    member.id,
            "channel":  vc,
            "thread":   thread,
            "timeout":  DEFAULT_TIMEOUT,
            "deputies": tpl.get("deputies", [])
        }

    # 3) Авто-удаление пустого канала (как было)
    if before.channel and before.channel.id in private_vcs:
        data = private_vcs[before.channel.id]
        if not before.channel.members:
            await asyncio.sleep(data["timeout"] * 60)
            if not data["channel"].members:
                await data["channel"].delete()
                if data["thread"]:
                    await data["thread"].delete()
                private_vcs.pop(before.channel.id, None)

# ============== SLASH-КОМАНДЫ ——————————————————————————————————

@tree.command(name="limit", description=t("cmd_limit_desc"))
async def limit_cmd(interaction: discord.Interaction, number: int):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(t("error_not_owner"), ephemeral=True)
    n = max(0, min(99, number))
    await data["channel"].edit(user_limit=n)
    update_template_from_channel(data["owner"], data["channel"], data["deputies"])
    await interaction.response.send_message(t("modal_limit_success", limit=n), ephemeral=True)

@tree.command(name="rename", description=t("cmd_rename_desc"))
async def rename_cmd(interaction: discord.Interaction, name: str):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(t("error_not_owner"), ephemeral=True)
    await data["channel"].edit(name=name)
    update_template_from_channel(data["owner"], data["channel"], data["deputies"])
    await interaction.response.send_message(t("modal_rename_success", name=name), ephemeral=True)

@tree.command(name="invite", description=t("cmd_invite_desc"))
async def invite_cmd(interaction: discord.Interaction, user: discord.Member):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(t("error_not_owner"), ephemeral=True)
    perms = data["channel"].overwrites
    perms[user] = discord.PermissionOverwrite(view_channel=True, connect=True)
    await data["channel"].edit(overwrites=perms)
    update_template_from_channel(data["owner"], data["channel"], data["deputies"])
    await interaction.response.send_message(t("modal_invite_success", user=user.mention), ephemeral=True)

@tree.command(name="kick", description=t("cmd_kick_desc"))
async def kick_cmd(interaction: discord.Interaction, user: discord.Member):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(t("error_not_owner"), ephemeral=True)
    perms = data["channel"].overwrites
    perms[user] = discord.PermissionOverwrite(connect=False)
    await data["channel"].edit(overwrites=perms)
    update_template_from_channel(data["owner"], data["channel"], data["deputies"])
    await interaction.response.send_message(t("modal_kick_success", user=user.mention), ephemeral=True)

@tree.command(name="assign", description=t("cmd_assign_desc"))
async def assign_cmd(interaction: discord.Interaction, user: discord.Member):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(t("error_not_owner"), ephemeral=True)
    deps = data.setdefault("deputies", [])
    if user.id in deps:
        return await interaction.response.send_message(f"❌ {user.mention} {t('button_assign').lower()}.", ephemeral=True)
    deps.append(user.id)
    perms = data["channel"].overwrites
    perms[user] = discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
    await data["channel"].edit(overwrites=perms)
    update_template_from_channel(data["owner"], data["channel"], deps)
    await interaction.response.send_message(f"✅ {user.mention} {t('button_assign').lower()}!", ephemeral=True)

@tree.command(name="unassign", description=t("cmd_unassign_desc"))
async def unassign_cmd(interaction: discord.Interaction, user: discord.Member):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(t("error_not_owner"), ephemeral=True)
    deps = data.get("deputies", [])
    if user.id not in deps:
        return await interaction.response.send_message(f"❌ {user.mention} {t('button_unassign').lower()}.", ephemeral=True)
    deps.remove(user.id)
    perms = data["channel"].overwrites
    perms.pop(user, None)
    await data["channel"].edit(overwrites=perms)
    update_template_from_channel(data["owner"], data["channel"], deps)
    await interaction.response.send_message(f"✅ {user.mention} {t('button_unassign').lower()}!", ephemeral=True)

@tree.command(name="delete", description=t("cmd_delete_desc"))
async def delete_cmd(interaction: discord.Interaction):
    data = get_user_vc(interaction.user.id)
    if not data:
        return await interaction.response.send_message(t("error_not_owner"), ephemeral=True)
    await data["channel"].delete()
    if data["thread"]:
        await data["thread"].delete()
    private_vcs.pop(data["channel"].id, None)
    await interaction.response.send_message(t("button_delete"), ephemeral=True)

if __name__ == "__main__":
    bot.run(os.getenv("TOKEN"))