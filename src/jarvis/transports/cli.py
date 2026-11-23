from __future__ import annotations

import asyncio
import json

from ..core.app import App
from ..core.dispatcher import ConfirmRequest

HELP = (
    "Формат: <функция> [JSON-параметры]\n"
    '  пример: fs_mkdir {"path": "проекты/тест"}\n'
    "Команды: tools (список функций), history (последние действия), quit"
)


async def _confirm(req: ConfirmRequest) -> bool:
    answer = await asyncio.to_thread(input, f"🔴 Подтвердить?\n{req.summary()}\n[да/нет] > ")
    return answer.strip().lower() in {"да", "д", "y", "yes"}


async def run_cli(app: App) -> None:
    name = app.settings.assistant_name
    print(f"{name} (CLI). {HELP}")
    while True:
        try:
            line = (await asyncio.to_thread(input, f"{name}> ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line in {"quit", "exit"}:
            break
        if line == "history":
            for r in app.db.recent_actions(15):
                print(f"#{r['id']} {r['ts']} {r['tool']} [{r['status']}] {r['result'] or ''}")
            continue
        if line == "tools":
            line = "tools_help"

        tool_name, _, rest = line.partition(" ")
        try:
            params = json.loads(rest) if rest.strip() else {}
            if not isinstance(params, dict):
                raise ValueError("параметры должны быть JSON-объектом")
        except ValueError as e:
            print(f"❌ Не разобрал параметры: {e}")
            continue

        res = await app.dispatcher.call(tool_name, params, source="cli", user_id="local", confirm=_confirm)
        print(("✅ " if res.ok else "❌ ") + res.message)
