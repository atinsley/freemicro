# Using the pad with the Claude desktop app

Claude Code sessions in the desktop app's Code tab have no terminal tab, so
FreeMicro's terminal features cannot reach them. The default keymap therefore
ships a `Claude` profile that applies only while the Claude app is frontmost. Every
other app, including Terminal, keeps the normal bindings, though an Agent Key still
opens its chat from any app.

Status lighting is unaffected, because hooks fire in the desktop app too.

## Key map

"Docs" means the shortcut is listed in the
[desktop documentation](https://code.claude.com/docs/en/desktop.md#keyboard-shortcuts).

| Key | Cap | In Codex | In the Claude app | Basis |
|---|---|---|---|---|
| `AG00`-`AG05` | Agent 1-6 | Select that key's thread | Open that key's chat; an unlit key starts a new chat | Undocumented app link |
| `ACT06` | FAST | Toggle fast mode | Types `/fast`; press CODEX to run | Not documented for the app |
| `ACT07` | APPR | Approve | Approve the newest waiting prompt; hold for "always"; otherwise `Cmd+Enter` | Permission hook; `Cmd+Enter` reported by a user |
| `ACT08` | REJ | Decline | Decline the newest waiting prompt; otherwise Esc, which stops Claude's response | Permission hook |
| `ACT09` | SPLIT | Fork thread | Types `/fork`; press CODEX to run | Confirmed by a user |
| `ACT10` | MIC | Push to talk | Your dictation app's hold shortcut | Bind it in `freemicro config --web` |
| `ACT12` | CODEX | Submit | Return | Standard |
| `ENC_CW` / `ENC_CC` | Dial | Previous / next control | Up / down arrow | Standard |
| `ENC_CLK` | Dial press | Select | Return | Standard |
| Stick | | Plan mode, back, forward, sidebar | Pointer; tap to click | Stick settings are global |

FAST and SPLIT type their command without sending it. A slash command only runs
at the start of a message, so sending automatically into a prompt box holding a
draft would send the draft with the command on the end.

The permission mode, model and effort menus are `Cmd+Shift+M`, `Cmd+Shift+I` and
`Cmd+Shift+E` on the keyboard. The numbers `1`-`9`, or the dial, select in them.

## Opening a chat

An Agent Key opens its chat through `claude://code/continue?session=<id>`, the link
the app's own menus use. It needs the app's `local_...` id, which FreeMicro finds in
the app's `claude-code-sessions` records from the session id that hooks report.

Each key follows one chat: your six most recently active, set by
`"agent_keys": {"group": "chat"}`. A chat waiting on you or in error always gets a
key. A chat keeps its key while it stays in the six, and the next one takes it when
it drops out. Activity means Claude Code doing something, not you looking at a chat.
SDK sessions, such as scripts using the Agent SDK, aren't tracked, because they have
no chat to open.

Neither the link nor those records are documented, so an app update could change
them. If the lookup fails, the key presses `Cmd+Shift+]` for the next chat, while
the Claude app is in front.

## Numbering chats by key

Add `"number_titles": true` to `agent_keys` to put each chat's key number on its
title, as `1 · Code audit`. It uses the `sessionTitle` output of the
`UserPromptSubmit` hook, so it needs no new hook.

- **When it updates:** only when you send that chat a message. A chat that gains or
  loses a key while idle keeps its old title until then, so two chats can briefly
  show the same number. The pad itself is always right.
- **New chats:** the first message gets no number, because the app hasn't made a
  title yet. The number appears on the next message.
- **Your own names:** a name you give a chat is kept, with the number in front.
- **Remote Control chats:** a chat started through Remote Control, such as from a
  phone, gets no number, because the app ignores the rename. Its key still works.
- **Turning it off:** titles keep their numbers until you rename them.

## Answering prompts from the pad

Run `freemicro install` to add the `PermissionRequest` hook. While `freemicro run`
or the daemon is listening, the hook waits for up to five minutes for APPR or REJ.
The prompt stays on screen the whole time, and you can still answer it there.

- **Which prompt:** the newest one waiting, in any chat. A banner says what was
  answered. To tie a key to one repo, add `"project": "~/code/api"` to its binding.
- **Nothing waiting:** APPR presses `Cmd+Enter`, which clicks the first button of
  the app's next-step box, and REJ presses Esc. What `Cmd+Enter` does to a draft in
  the prompt box hasn't been checked. Remove `otherwise` from APPR if it sends one.
- **Hold for "always":** applies the rules Claude Code suggested for that prompt,
  and never a permission mode change. With no rule suggested, it's a single approval.
- **Guards:** a prompt less than half a second old can't be answered, so a press
  already under way can't approve something you haven't read. Further presses are
  ignored for a second after an answer.
- **Failures:** if anything goes wrong the hook makes no decision, and the prompt
  stays with you. Tool input is never written to disk.

## Other keycaps

If you swap caps, these have documented desktop shortcuts: `TERM` (`` Ctrl+` ``),
`DIFF` (`Cmd+Shift+D`), `NEW` (`Cmd+N`) and `PARTY`, which is side chat (`Cmd+;`).

## What it cannot do yet

- Answer a prompt after its five-minute wait. Answer it on screen instead.
- Answer prompts that don't reach the hook, such as a sandboxed command's network
  request.

See the fork's issue tracker for these and the other differences from Codex.
