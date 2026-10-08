# client/secrets (not in git)

Create these files by hand; everything else in this folder is ignored by git.

| File | Contents | Used by |
|---|---|---|
| `service_token.txt` | Bearer token of the GPU service; the same value as `service_token.txt` next to `server/service.py` on the GPU box. Any random string, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"` | `watcher.py` (job API) |
| `bitrix_webhook.txt` | Bitrix24 incoming webhook URL with `calendar` and `user` permissions (a trailing example method like `/profile.json` is fine) | `bitrix.py`, `reminders.py` |
| `telegram_token.txt` | Bot token from @BotFather; then send any message to the bot and run `python notify.py setup` | `notify.py` |
