# Using the pad with the Claude desktop app

Claude Code sessions in the desktop app's Code tab have no terminal tab, so
FreeMicro's terminal features cannot reach them: an Agent Key cannot jump to one
and APPR cannot answer its permission prompt. The default keymap therefore ships
a `Claude` profile that applies only while the Claude app is frontmost. Every
other app, including Terminal, keeps the normal bindings.

Status lighting is unaffected, because hooks fire in the desktop app too.

## Key map

"Docs" means the shortcut is listed in the
[desktop documentation](https://code.claude.com/docs/en/desktop.md#keyboard-shortcuts).

| Key | Cap | In Codex | In the Claude app | Basis |
|---|---|---|---|---|
| `AG00` | Agent 1 | Select thread 1 | Previous chat (`Cmd+Shift+[`) | Docs |
| `AG01` | Agent 2 | Select thread 2 | Next chat (`Cmd+Shift+]`) | Docs |
| `AG02` | Agent 3 | Select thread 3 | New chat (`Cmd+N`) | Docs |
| `AG03` | Agent 4 | Select thread 4 | Permission mode menu (`Cmd+Shift+M`) | Docs |
| `AG04` | Agent 5 | Select thread 5 | Model menu (`Cmd+Shift+I`) | Docs |
| `AG05` | Agent 6 | Select thread 6 | Effort menu (`Cmd+Shift+E`) | Docs |
| `ACT06` | FAST | Toggle fast mode | Types `/fast`; press CODEX to run | Not documented for the app |
| `ACT07` | APPR | Approve | Nothing | No documented shortcut |
| `ACT08` | REJ | Decline | Esc, which stops Claude's response | Docs for stopping; declining is not documented |
| `ACT09` | SPLIT | Fork thread | Types `/branch`; press CODEX to run | Not documented for the app |
| `ACT10` | MIC | Push to talk | Your dictation app's hold shortcut | Bind it in `freemicro config --web` |
| `ACT12` | CODEX | Submit | Return | Standard |
| `ENC_CW` / `ENC_CC` | Dial | Previous / next control | Up / down arrow | Standard |
| `ENC_CLK` | Dial press | Select | Return | Standard |
| Stick | | Plan mode, back, forward, sidebar | Pointer; tap to click | Stick settings are global |

FAST and SPLIT type their command without sending it. A slash command only runs
at the start of a message, so sending automatically into a prompt box holding a
draft would send the draft with the command on the end.

The numbers `1`-`9` select an item in an open menu, so `AG03`-`AG05` followed by
a number or the dial and dial press gives you mode, model and effort.

## Other keycaps

If you swap caps, these have documented desktop shortcuts: `TERM` (`` Ctrl+` ``),
`DIFF` (`Cmd+Shift+D`), `NEW` (`Cmd+N`) and `PARTY`, which is side chat (`Cmd+;`).

## What it cannot do yet

- Jump straight to a particular chat. The app documents next and previous only,
  so an Agent Key's light can show one project while the key moves through chats.
- Approve or decline a permission prompt from the pad.

See the fork's issue tracker for these and the other differences from Codex.
