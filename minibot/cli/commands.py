"""CLI commands (minimal)."""

import asyncio
import sys
from pathlib import Path

# Windows 終端機同步設定 UTF-8，避免 emoji 編碼錯誤
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import typer
from rich.console import Console
from rich.markdown import Markdown
from prompt_toolkit import PromptSession

from minibot import __version__
from minibot.bus.queue import MessageBus
from minibot.config.loader import load_config, save_config, get_config_path
from minibot.i18n import init as i18n_init, t
from minibot.providers.litellm_provider import LiteLLMProvider
from minibot.session.manager import SessionManager
from minibot.utils.helpers import ensure_dir, get_data_path

app = typer.Typer(help="minibot - Personal AI Assistant (powered by mini_bot)")
console = Console()

config = load_config()
i18n_init(config_locale=config.locale)


@app.command()
def onboard():
    """初始化 minibot 設定與 workspace。"""
    config_path = get_config_path()
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            '{\n  "providers": {\n    "minimax": {\n      "apiKey": "",\n      "apiBase": "https://api.minimax.io/v1"\n    }\n  }\n}'
        )
        console.print(t("cli.onboard.config_created", path=config_path))
    else:
        console.print(t("cli.onboard.config_exists", path=config_path))

    ws = Path("~/.minibot/workspace").expanduser()
    ensure_dir(ws)
    ensure_dir(ws / "memory")
    agents_file = ws / "AGENTS.md"
    if not agents_file.exists():
        agents_file.write_text("# Agent Instructions\n\nYou are a helpful AI assistant.\n")
    console.print(t("cli.onboard.workspace_ready", path=ws))
    console.print(t("cli.onboard.edit_config"))


def _make_provider(config):
    """根據 config 建立 LLM Provider。優先順序：minimax > openrouter > anthropic > openai > deepseek > gemini。"""
    model = config.agents.defaults.model

    # 依序檢查哪個 provider 有 API key
    providers_map = {
        "minimax": ("MINIMAX_API_KEY", "minimax", "https://api.minimax.io/v1"),
        "openrouter": ("OPENROUTER_API_KEY", "openrouter", None),
        "anthropic": ("ANTHROPIC_API_KEY", "", None),
        "openai": ("OPENAI_API_KEY", "", None),
        "deepseek": ("DEEPSEEK_API_KEY", "deepseek", None),
        "gemini": ("GEMINI_API_KEY", "gemini", None),
    }

    for name, (env_key, prefix, default_api_base) in providers_map.items():
        prov_cfg = getattr(config.providers, name)
        if prov_cfg.api_key:
            api_base = prov_cfg.api_base or default_api_base
            return LiteLLMProvider(
                api_key=prov_cfg.api_key,
                model=model,
                api_base=api_base,
                env_key=env_key,
                litellm_prefix=prefix,
            )

    console.print(f"[red]{t('cli.error.no_api_key')}[/red]")
    raise typer.Exit(1)


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Single message mode"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown"),
):
    """與 mini_bot Agent 互動聊天。"""
    config = load_config()
    provider = _make_provider(config)
    workspace = Path(config.agents.defaults.workspace).expanduser()
    ensure_dir(workspace)

    bus = MessageBus()
    from minibot.agent.loop import AgentLoop
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        memory_window=config.agents.defaults.memory_window,
    )

    def _print_response(text: str):
        if markdown:
            console.print(Markdown(text))
        else:
            console.print(text)

    # 單次訊息模式
    if message:
        result = asyncio.run(agent_loop.process_direct(message))
        _print_response(result)
        return

    # 互動模式
    console.print(t("cli.agent.welcome", version=__version__))
    console.print(f"{t('cli.agent.exit_hint')}\n")

    session = PromptSession()

    async def run_interactive():
        while True:
            try:
                user_input = await session.prompt_async("You> ")
            except (EOFError, KeyboardInterrupt):
                break

            if user_input.strip().lower() in ("exit", "quit", "/exit", ":q"):
                break
            if not user_input.strip():
                continue

            console.print(f"[dim]{t('cli.agent.thinking')}[/dim]")
            result = await agent_loop.process_direct(user_input)
            console.print()
            _print_response(result)
            console.print()

    asyncio.run(run_interactive())
    console.print(f"\n[dim]{t('cli.agent.goodbye')}[/dim]")


@app.command()
def status():
    """顯示 minibot 目前狀態。"""
    config = load_config()
    console.print(t("cli.status.title", version=__version__))
    console.print(t("cli.status.config", path=get_config_path()))
    console.print(t("cli.status.model", model=config.agents.defaults.model))

    for name in ("minimax", "openrouter", "anthropic", "openai", "deepseek", "gemini"):
        prov = getattr(config.providers, name)
        if prov.api_key:
            masked = prov.api_key[:8] + "..." + prov.api_key[-4:]
            console.print(t("cli.status.provider_enabled", name=name, masked=masked))

    tg_token = config.channels.telegram.bot_token
    if tg_token:
        masked = tg_token[:8] + "..." + tg_token[-4:]
        console.print(t("cli.status.telegram_enabled", masked=masked))
    else:
        console.print(t("cli.status.telegram_disabled"))


@app.command()
def config_show():
    """顯示目前所有設定值（除錯用）。"""
    from minibot.i18n import get_locale
    import json

    config = load_config()
    config_path = get_config_path()

    console.print(f"\n[bold]🔧 Config File: {config_path}[/bold]")
    console.print(f"[dim]Locale: {get_locale()}[/dim]\n")

    data = config.model_dump(by_alias=True)

    console.print("[bold]Agents Defaults:[/bold]")
    console.print(f"  model: {data.get('agents', {}).get('defaults', {}).get('model')}")
    console.print(f"  workspace: {data.get('agents', {}).get('defaults', {}).get('workspace')}")
    console.print(f"  max_tool_iterations: {data.get('agents', {}).get('defaults', {}).get('max_tool_iterations')}")
    console.print(f"  temperature: {data.get('agents', {}).get('defaults', {}).get('temperature')}")
    console.print(f"  max_tokens: {data.get('agents', {}).get('defaults', {}).get('max_tokens')}")
    console.print(f"  memory_window: {data.get('agents', {}).get('defaults', {}).get('memory_window')}")

    console.print("\n[bold]Providers:[/bold]")
    for name in ("minimax", "openrouter", "anthropic", "openai", "deepseek", "gemini"):
        prov = data.get("providers", {}).get(name, {})
        api_key = prov.get("apiKey", "")
        api_base = prov.get("apiBase", "")
        if api_key:
            console.print(f"  {name}:")
            console.print(f"    apiKey: {api_key[:8]}...{api_key[-4:]}")
            console.print(f"    apiBase: {api_base or '(default)'}")

    console.print("\n[bold]Channels:[/bold]")
    tg = data.get("channels", {}).get("telegram", {})
    bot_token = tg.get("botToken", "")
    if bot_token:
        console.print(f"  telegram:")
        console.print(f"    botToken: {bot_token[:8]}...{bot_token[-4:]}")
        console.print(f"    botUsername: {tg.get('botUsername', '(not set)')}")


@app.command()
def telegram():
    """啟動 Telegram Bot（Polling 模式）。"""
    config = load_config()
    bot_token = config.channels.telegram.bot_token

    if not bot_token:
        console.print(f"[red]{t('cli.telegram.error_no_token')}[/red]")
        console.print(t("cli.telegram.error_token_hint"))
        raise typer.Exit(1)

    provider = _make_provider(config)
    workspace = Path(config.agents.defaults.workspace).expanduser()
    ensure_dir(workspace)

    console.print(t("cli.telegram.welcome", version=__version__))
    console.print(f"{t('cli.telegram.starting')}\n")

    from minibot.channels.telegram import TelegramChannel
    channel = TelegramChannel(
        bot_token=bot_token,
        provider=provider,
        workspace=workspace,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        memory_window=config.agents.defaults.memory_window,
    )
    channel.run()

