# Bluesky guard — installation, step by step

**Advanced.** Admin rights are required.
Plan for one and a half to two hours the first time; splitting it into two sessions is fine (there is a safe pause point after chapter 3).

The core idea in one sentence:

> **The protection does not rest on Claude's good intentions, but on Windows file permissions.**

What Claude does NOT know and does not want to know: the guard account's password, the admin account's password, the Bluesky app password, and the bot token.
You type these in by hand — and, per chapter 6, **not** under the Windows user that runs Claude.

## Placeholders

| Placeholder | Meaning |
|---|---|
| `<windows-user>` | the Windows user that runs Claude (the UNTRUSTED side) |
| `<guard-user>` | the guard's own Windows user (the TRUSTED side) |
| `<admin-user>` | a separate, enabled administrator account (variant B) |
| `{GUARD_ROOT}` | the guard's folder tree, e.g. `C:\BskyGuard` |
| `{SOURCE}` | where the guard's code lives in the repository clone: `{REPO}\bluesky\or\` |
| `{REPO}`, `{HOME}` | the repository clone, and `<windows-user>`'s profile folder (as in INSTALL.md) |
| `{PYTHON}` | a system-wide Python that `<windows-user>` cannot write to (`python.exe` path) |
| `{PYTHON_DIR}` | the folder of that Python |
| `<COMPUTERNAME>` | the machine name (`$env:COMPUTERNAME`) |
| `<task-name>` | the scheduled task's name |

The guard's root folder defaults to `C:\BskyGuard` in the code; another root can be given with `python or.py --gyoker <path>` (then use that path everywhere below, and set the same path in the environment variable `BSKY_GUARD_ROOT` for the reader toolkit's waker, `bluesky/inbox_ebreszto.py`, which has the same default). The owner's Telegram ids are the `OWNER_USER_ID` / `OWNER_CHAT_ID` fields of `or_bot.env`.

## Which guide, which script

**This file is the CANONICAL guard guide.** Follow it; where another document differs, this one wins.

- `bluesky/or/INSTALL.md` is the longer original, written with fixed names. Keep it for reference only (it has more background per step).
- Its names map to this guide's placeholders as follows:

| Original guide (fixed name) | This guide |
|---|---|
| `C:\BskyGuard` | `{GUARD_ROOT}` |
| `bsky-guard` | `<guard-user>` |
| `agent-user` | `<windows-user>` |
| `admin` | `<admin-user>` |

- `bluesky/or/telepit_1_resz.ps1` (read `bluesky/or/TELEPIT_1_RESZ_OLVASSEL.md` first) performs chapters 2–5 of the ORIGINAL guide, following variant A (chapter 1 skipped). The chapter numbers are the same here, so it does this guide's chapters 2–5, but with the original's fixed names: the code uses `C:\BskyGuard` and `bsky-guard`, and takes `<windows-user>` from the `BSKY_AGENT_USER` environment variable, which must be set in the ADMIN window before it runs. Use the script only if you chose `{GUARD_ROOT}` = `C:\BskyGuard` and `<guard-user>` = `bsky-guard`, and variant A; otherwise do chapters 2–5 by hand from this guide. The path in its read-me (`...\bluesky\or\telepit_1_resz.ps1`) means `{SOURCE}telepit_1_resz.ps1`. It writes no secrets and creates no scheduled task: chapters 6–8 stay manual either way.

## What will happen, and why — in ten lines

1. You create a separate Windows user (`<guard-user>`); the program that posts to Bluesky runs **as that user**, not as you.
2. Separate, because the account that runs Claude can be deceived: anyone writing to it online may try to talk it into something.
3. Between the two accounts there is a locked folder tree (`{GUARD_ROOT}`), and Windows itself enforces who may access what.
4. The secrets (bot token, Bluesky app password) go into a folder that `<windows-user>` **cannot even read**.
5. Claude can only drop a *proposal* into a shared folder. It cannot write or post anywhere else.
6. The guard takes over the proposal, checks it, and re-checks **live** on Bluesky whose conversation the reply would land in.
7. Then it sends a preview to **your phone** via a new Telegram bot: exactly the text that would go out, and to whom.
8. Nothing goes out until you reply to that message with: `IGEN` ("yes").
9. One approval = one post. The guard sends the **stored** text and never re-reads the proposal, because that could have been swapped in the meantime.
10. If anything is uncertain (network, permissions, a suspicious character), the guard **does not post**, and says why.

## Roles

| Who | Runs where | Can do |
|---|---|---|
| Claude | under `<windows-user>` | **UNTRUSTED** — may be deceived. Writes to `kozos\ki\` (outbox), reads `kozos\be\` (inbox). Nothing else. |
| The guard | under `<guard-user>`, started by Task Scheduler | **TRUSTED.** Holds the Bluesky app password and the bot token. |
| The owner | phone, a NEW (second) Telegram bot | Says `IGEN` (yes) or `NEM` (no). |

## Folders and the trust in them

The folder names are defined by the code (Hungarian); the meaning is given here.

```
{GUARD_ROOT}\
  titok\        secrets: bsky_kulcs.env, or_bot.env      <windows-user>: NO access
  program\      the code + blocklist + allowlist         <windows-user>: read only
  allapot\      state: previews, log, offsets            <windows-user>: read only
    varo\         canonical form of a submitted draft    <windows-user>: NO access
    feldolgozott\ drafts already closed                  <windows-user>: NO access
    zart\         the guard's fingerprint key            <windows-user>: NO access
  kozos\
    be\         inbox.jsonl, allapot.json  (guard writes, Claude reads)
    ki\         drafts (*.json)            (Claude writes, guard reads)
```

At startup the guard checks all of this with `icacls`, and if anything is off, **it does not start**; it writes the error into `kozos\be\allapot.json`.
The check is an allowlist: anyone outside SYSTEM / Administrators / `<guard-user>` holding a right stops startup.
It also checks every folder's **owner**, because an owner can always grant itself access back.

## Two kinds of PowerShell window

- **ADMIN**: Start → type `powershell` → right-click *Windows PowerShell* → **Run as administrator**. The title bar says *Administrator*.
- **NORMAL**: Start → `powershell` → Enter. This is `<windows-user>` with everyday rights; checks are done here.

Where a step says **IN THE ADMIN ACCOUNT'S OWN SESSION**, "Run as administrator" is not enough: you really have to sign in to the other account. There are two such chapters: 6 (secrets) and 8 (the scheduled task's password).

Paste commands **one at a time**, and look at the output after each.

---

## 0. Decide first: does `<windows-user>` stay an administrator?

Decide this **before** installing; the two variants protect different things.

**What's at stake.** The Administrators group has access to `titok\` and `allapot\` (otherwise the folders would be unmanageable).
If `<windows-user>` is in that group, a deceived agent running under it can reach the secrets with a single elevation.

**Variant A — `<windows-user>` stays an administrator.**

- Protects everything reachable **without elevation**; that is the everyday case.
- Does **not** protect against a single UAC approval.
- Do this anyway: set UAC to **"Always notify"** (Start → `UAC` → move the slider to the top → OK).
- Skip chapter 1, go to chapter 2. The guard will start, but warn every round (in `kozos\be\allapot.json` and on Telegram). The warning is a reminder, not an error.

**Variant B — a separate admin account, and `<windows-user>` leaves the Administrators group.**

- This makes the whole model work.
- Every elevation becomes a **password prompt** for `<admin-user>`.
- Claude, running under `<windows-user>`, loses the ability to do admin tasks; it will ask you instead.
- The order is **critical**: the machine must never be left without a working administrator.
- Continue with chapter 1.

---

## 1. Variant B only: the admin account

### 1/a — Current state

ADMIN window:
```powershell
Get-LocalUser | Format-Table Name,Enabled
Get-LocalGroupMember -SID "S-1-5-32-544" | Select-Object Name
```
Built-in admin accounts are often **disabled**. Do not remove `<windows-user>` first: next to disabled accounts, that would leave the machine without a usable administrator.

### 1/b — Enable `<admin-user>` and set its password

```powershell
Enable-LocalUser -Name "<admin-user>"
Set-LocalUser -Name "<admin-user>" -Password (Read-Host "Choose a STRONG password for the admin account" -AsSecureString)
Set-LocalUser -Name "<admin-user>" -PasswordNeverExpires $true
```
You choose the password, and you write it down (paper or password manager). Don't tell Claude.
`Set-LocalUser` does not ask for confirmation; 1/c will catch a typo, and 1/b can be repeated.

**Verify:** `Get-LocalUser <admin-user> | Format-List Name,Enabled,PasswordExpires` → `Enabled : True`.

### 1/c — TEST: can you actually sign in?

This step cannot be skipped.
Win + L → choose `<admin-user>` → sign in → wait for the first-login profile setup → sign out → back to `<windows-user>`.

> **PIN trap.** Windows will offer a PIN. The UAC prompt asks for the **password**, not the PIN. If you forget the password, after variant B the machine becomes unmanageable.

**If it fails:** don't go on. Repeat 1/b. `<windows-user>` remains admin, and the system works as variant A.

### 1/d — Remove `<windows-user>` from Administrators

**Only if 1/c SUCCEEDED.**
```powershell
Remove-LocalGroupMember -SID "S-1-5-32-544" -Member "<windows-user>"
```
**Verify:** `Get-LocalGroupMember -SID "S-1-5-32-544" | Select-Object Name` no longer lists `<windows-user>`.

> **This does not take effect immediately.** Your current sign-in token still contains the Administrators group until you sign out. **Sign out and back in** before moving on.

**Final check:** `<admin-user>` is still `Enabled : True` and still a member. Never disable or delete this account.

**The way back** (from the admin account, one command), then sign out and in:
```powershell
Add-LocalGroupMember -SID "S-1-5-32-544" -Member "<windows-user>"
```

---

## 2. The guard's own Windows user

ADMIN window:
```powershell
New-LocalUser -Name "<guard-user>" -Description "Bluesky guard service account" -Password (Read-Host "Choose a STRONG password for the guard account" -AsSecureString)
Set-LocalUser -Name "<guard-user>" -PasswordNeverExpires $true
Add-LocalGroupMember -SID "S-1-5-32-545" -Member "<guard-user>"
```
You choose the password and write it down; Claude doesn't get it. You'll need it again in chapter 8.

**Verify:**
```powershell
Get-LocalUser <guard-user> | Format-List Name,Enabled,PasswordExpires
Get-LocalGroupMember -SID "S-1-5-32-544" | Select-Object Name
```
`Enabled : True`, and `<guard-user>` is **not** in the Administrators list.

**If it fails:** "password does not meet policy" → longer password (12+ characters, upper and lower case, digit). "Account already exists" → move on.

---

## 3. Folders and permissions

ADMIN window:
```powershell
New-Item -ItemType Directory -Force -Path "{GUARD_ROOT}","{GUARD_ROOT}\titok","{GUARD_ROOT}\program","{GUARD_ROOT}\allapot","{GUARD_ROOT}\allapot\varo","{GUARD_ROOT}\allapot\feldolgozott","{GUARD_ROOT}\allapot\zart","{GUARD_ROOT}\kozos","{GUARD_ROOT}\kozos\be","{GUARD_ROOT}\kozos\ki" | Out-Null
```

`/inheritance:r` = forget what was inherited from the parent; `/grant:r` = exactly these rights.
`*S-1-5-18` is SYSTEM, `*S-1-5-32-544` is Administrators — given by SID because the names differ by Windows language.

```powershell
icacls "{GUARD_ROOT}" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "<guard-user>:(OI)(CI)(RX)" "<windows-user>:(OI)(CI)(RX)"
icacls "{GUARD_ROOT}\titok" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "<guard-user>:(OI)(CI)(RX)"
icacls "{GUARD_ROOT}\program" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "<guard-user>:(OI)(CI)(RX)" "<windows-user>:(OI)(CI)(RX)"
icacls "{GUARD_ROOT}\allapot" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "<guard-user>:(OI)(CI)(M)" "<windows-user>:(OI)(CI)(RX)"
```

**Three folders in which `<windows-user>` does not appear at all.** Everything derived from submitted drafts goes here. This is the third, independent layer of protection, and the guard checks it at startup.
```powershell
icacls "{GUARD_ROOT}\allapot\varo" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "<guard-user>:(OI)(CI)(M)"
icacls "{GUARD_ROOT}\allapot\feldolgozott" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "<guard-user>:(OI)(CI)(M)"
icacls "{GUARD_ROOT}\allapot\zart" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "<guard-user>:(OI)(CI)(M)"
```

The shared folders:
```powershell
icacls "{GUARD_ROOT}\kozos" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "<guard-user>:(OI)(CI)(M)" "<windows-user>:(OI)(CI)(RX)"
icacls "{GUARD_ROOT}\kozos\be" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "<guard-user>:(OI)(CI)(M)" "<windows-user>:(OI)(CI)(RX)"
icacls "{GUARD_ROOT}\kozos\ki" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "<guard-user>:(OI)(CI)(M)" "<windows-user>:(OI)(CI)(M)"
```

**And right after it — don't skip this:**
```powershell
icacls "{GUARD_ROOT}\kozos\ki" /deny "<windows-user>:(DE)"
```
Modify includes deleting the folder itself. Without this line, a deceived agent could empty and delete the outbox, and the guard would never start again (silent denial of service).
The deny has no `(OI)(CI)`, so it applies only to the folder: files inside can still be created, written and deleted. The guard checks that this line is present.

**If the user name is not accepted** ("No mapping between account names and security IDs"): the local account may be linked to a Microsoft account. Use `<COMPUTERNAME>\<windows-user>` in every `icacls` line. If name resolution fails, the WHOLE command fails, and the folder may be left without permissions.

### Set the OWNER — don't skip this

An owner can give itself access back even if it isn't on the list. The guard checks that every folder's owner is Administrators.
```powershell
foreach ($p in "{GUARD_ROOT}","{GUARD_ROOT}\titok","{GUARD_ROOT}\program","{GUARD_ROOT}\allapot","{GUARD_ROOT}\allapot\varo","{GUARD_ROOT}\allapot\feldolgozott","{GUARD_ROOT}\allapot\zart","{GUARD_ROOT}\kozos","{GUARD_ROOT}\kozos\be","{GUARD_ROOT}\kozos\ki") { icacls $p /setowner "*S-1-5-32-544" }
foreach ($p in "{GUARD_ROOT}","{GUARD_ROOT}\titok","{GUARD_ROOT}\program","{GUARD_ROOT}\allapot","{GUARD_ROOT}\allapot\varo","{GUARD_ROOT}\allapot\feldolgozott","{GUARD_ROOT}\allapot\zart","{GUARD_ROOT}\kozos","{GUARD_ROOT}\kozos\be","{GUARD_ROOT}\kozos\ki") { "{0} -> {1}" -f $p, (Get-Acl -LiteralPath $p).Owner }
```
All ten lines must show `BUILTIN\Administrators` (or the localized name).
**Windows display language.** The guard's permission check compares group NAMES, and the code knows only the English and the Hungarian names (`SYSTEM` / `RENDSZER`, `Administrators` / `Rendszergazdák`). On a Windows in any other language, add that language's names of SYSTEM and Administrators to the `ENGEDETT_IDENTITASOK` list in `{SOURCE}\beallitasok.py` BEFORE making the fingerprint in chapter 5 (otherwise the guard will refuse to start, naming the "outsider" group). Extending this list is safe; never remove from it.
Look at `kozos\ki` especially: the deny line protects only as long as `<windows-user>` is NOT the owner.
If the `*SID` form fails with `/setowner`, use the group name instead (`"Administrators"`, or its localized name). Don't repeat a failing command.

### Check 1 — what does icacls say?

```powershell
icacls "{GUARD_ROOT}\titok"
icacls "{GUARD_ROOT}\kozos\ki"
```
`titok`: three entries — SYSTEM, Administrators, `<guard-user>`. `<windows-user>` must NOT appear.
`kozos\ki`: one `<windows-user>:(OI)(CI)(M)` entry **and** one `<windows-user>:(DENY)(DE)` entry.

### Check 2 — the important one: are you really locked out?

NORMAL window:
```powershell
Get-ChildItem "{GUARD_ROOT}\titok"
foreach ($p in "{GUARD_ROOT}\allapot\varo","{GUARD_ROOT}\allapot\feldolgozott","{GUARD_ROOT}\allapot\zart") { Get-ChildItem $p }
Get-ChildItem "{GUARD_ROOT}\allapot"
```
The first four must give a red **"Access is denied"** — that red error is the GOOD result.
The last one must list the folder (you follow the guard's activity from there).
In variant A, an elevated window would still see inside; that's why this check runs in a NORMAL window.

---

## ⏸ SAFE PAUSE POINT

Two accounts exist and the locked tree is in place.
Nothing runs, nothing goes out, and the machine works as before; it can stay like this for days.
Continue with chapter 4. In variant B, if you haven't signed out since 1/d, do it now.

---

## 4. Python for the other user

The guard runs under another account, so it needs a Python that **every user** can run and that `<windows-user>` **cannot modify**.

ADMIN window:
```powershell
icacls "{PYTHON_DIR}"
```
You should see `BUILTIN\Users:(OI)(CI)(RX)`: everyone can run it, nobody can write.
If "Authenticated Users" has **Modify** on a Python folder, do NOT use it: `<windows-user>` could smuggle code into the guard.

Check that `<guard-user>` can run it:
```powershell
runas /user:<guard-user> "cmd /k {PYTHON} -V"
```
**If not found:** install a system-wide Python from python.org with **"Install for all users"** ticked, and use that path below.

---

## 5. Copying the code, WITH VERIFICATION

### Why plain copying isn't enough

The code's source is in `<windows-user>`'s reach — exactly what the threat model says an attacker can write.
Without verification, a deceived agent could slip its own code in during an update, and that code would run as `<guard-user>` with access to `titok\`.
So: **fingerprint → you look at it → only then copy.**

### 5/a — Make the fingerprint

NORMAL window:
```powershell
cd "{SOURCE}"
python ujjlenyomat.py --csak-telepitendo --kiir "{HOME}\guard_fingerprint.txt"
```
`--csak-telepitendo` limits the list to the files that actually get installed; without it the comparison could never say "all match", and you'd learn to treat alarms as noise.
Use the **full, written-out path**: in variant B, the admin window belongs to another user, where `$env:USERPROFILE` points elsewhere.
At the end of the list: an `OSSZESITETT` (total) line with a 64-digit hash, and a `FAJLOK` (files) count.

### 5/b — THE ONE STEP THAT MAKES THIS MEANINGFUL

The list protects nothing by itself: whoever can rewrite the code can rewrite the list too.
It only counts if you record the fingerprint on a channel the `<windows-user>` side cannot alter afterwards.

> **On the first install, photograph the total line and the file count with your phone.** The computer can't reach your phone's gallery.

On updates: new photo, compare. A changed hash is not bad in itself; the question is whether what changed is what you agreed on. `--osszevet` (compare) names the changed files.
If you skip this, say so honestly to yourself: then code integrity rests on good intentions.

### 5/c — Copy

ADMIN window. **The `.json` rule files are copied separately, on purpose, only now:**
```powershell
Copy-Item "{SOURCE}\*.py" -Destination "{GUARD_ROOT}\program\" -Force
Copy-Item "{SOURCE}\tilto_mintak.json" -Destination "{GUARD_ROOT}\program\" -Force
```
The second line (`tilto_mintak.json` with `-Force`) is for the **FIRST install only**. Never repeat it later: it would overwrite your own blocklist. Updates (chapter 10) copy only the `.py` files.
`tilto_mintak.json` is the blocklist (it ships with empty lists; the base patterns are built into the code). `szivesen_latnak.json` is the allowlist (accounts the guard may reply to unprompted); it is not shipped — a missing file means an EMPTY allowlist, and when you want one, create `{GUARD_ROOT}\program\szivesen_latnak.json` with the content `{"fiokok": []}` and fill the list. You edit these; **updates never overwrite them**.

**Verify:**
```powershell
python "{GUARD_ROOT}\program\ujjlenyomat.py" "{GUARD_ROOT}\program" --csak-telepitendo --osszevet "{HOME}\guard_fingerprint.txt"
```
Expected: `MINDEN EGYEZIK` ("everything matches").
If it reports missing or new files: check that `--csak-telepitendo` is in BOTH commands.

### 5/d — Tell the guard who is who (before it first starts)

The guard needs the two Windows user names to check the permissions: `BSKY_AGENT_USER` (the untrusted side, `<windows-user>`) and `BSKY_GUARD_USER` (`<guard-user>`). Without them it uses placeholder defaults (`agent-user`, `bsky-guard`) that match no real account, so the permission check fails closed and **the guard refuses to start**.
The guard runs under another user, so set them system-wide. ADMIN window:
```powershell
setx /M BSKY_AGENT_USER "<windows-user>"
setx /M BSKY_GUARD_USER "<guard-user>"
```
`setx` affects only processes started afterwards: open a NEW window for the `runas` steps of chapters 6 and 7. A scheduled task (chapter 8) picks them up at its next start.

**Verify** (in a new window): `[Environment]::GetEnvironmentVariable("BSKY_AGENT_USER","Machine")` and the same for `BSKY_GUARD_USER` print the two names.

---

## 6. THE SECRETS: bot token and Bluesky app password

### Why not on `<windows-user>`'s desktop

A Notepad window on that desktop — even an elevated one — can be **screenshotted** by a program running as `<windows-user>`. Claude has screenshot tools.
With the app password, an attacker doesn't need the guard at all.
So this chapter happens **in another session**.

### 6/a — Switch to the admin account

Win + L → sign in as `<admin-user>`.
In variant A without a separate admin account: at least **close everything that runs Claude** (Claude Code windows, scheduled Claude tasks, the Telegram watcher). Weaker, but better than nothing.
Take your phone: the token and the app password come **from there**.

### 6/b — A NEW Telegram bot (on your PHONE)

Not the existing bot: `<windows-user>` can write the existing bot's database, so it can't anchor approvals.
In Telegram, open BotFather → `/newbot` → name and username (must end in `bot`) → you get a `<bot-token>` → open the bot and press **Start**.
Read the token from your phone and type it; don't copy it into `<windows-user>`'s browser, don't email it, don't show it to Claude. If it leaks: `/revoke` at BotFather.

### 6/c — Enter the token (IN THE ADMIN ACCOUNT'S OWN SESSION)

ADMIN window:
```powershell
notepad "{GUARD_ROOT}\titok\or_bot.env"
```
Yes to creating it. Contents:
```
OR_BOT_TOKEN=<bot-token>
OWNER_USER_ID=0
OWNER_CHAT_ID=0
```
Save as UTF-8 (UTF-8 with BOM is also handled).

### 6/d — Bluesky app password (created on your PHONE)

Bluesky app → **Settings → Privacy and security → App passwords → Add App Password**. Name it; it's shown **only once**.
It is not your main password: it cannot delete the account or change the main password. **Revoke** in the same place is the emergency brake.
```powershell
notepad "{GUARD_ROOT}\titok\bsky_kulcs.env"
```
```
BSKY_HANDLE=<your-handle>
BSKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```
**Verify:** `Get-ChildItem "{GUARD_ROOT}\titok"` shows two files.

### 6/e — Your Telegram ID (SAME session)

```powershell
runas /user:<guard-user> "{PYTHON} {GUARD_ROOT}\program\or.py --ki-vagyok --gyoker {GUARD_ROOT}"
```
Type `<guard-user>`'s password. A window asks you to message the new bot; send anything.
It prints your `<user_id>` and chat ID (chat type must be `private`). Write them into `or_bot.env` in place of the zeros.

**If it fails:** "no message arrived" → did you press Start, right bot? "Missing field" → typo or spaces around `=`. Window closes at once → run with `cmd /k` in front to see the error.

### 6/f — Back to `<windows-user>`

Close Notepad and PowerShell, sign out of the admin account, sign back in. The secrets are now where `<windows-user>` can't see them.

---

## 7. Dry run

ADMIN window:
```powershell
runas /user:<guard-user> "cmd /k {PYTHON} {GUARD_ROOT}\program\or.py --ellenoriz --gyoker {GUARD_ROOT}"
```
Expected: the permissions-OK message ("the guard may start"). In variant A, a WARNING line about Administrators comes first; that's a reminder.

If it says the guard won't start because of a permission error, the line below names the folder and the problem:

| Message (in substance) | Meaning | What to do |
|---|---|---|
| unauthorized write right | someone can write a protected folder | rerun that folder's `icacls` from chapter 3 |
| OWNER is not an allowed identity | the owner could grant itself access | rerun `/setowner` from chapter 3 |
| owner cannot be determined / owner query returned error or empty | **program bug, not a setup error** | rerunning `icacls` won't help; show the line to Claude |
| delete-deny entry missing | the `/deny` line on `kozos\ki` is missing | rerun the `/deny` command |
| unknown permission token | unusual, maybe harmless | show the line to Claude |
| missing folder | a folder wasn't created | rerun the `New-Item` from chapter 3 |

Then the full run **without sending anything**:
```powershell
runas /user:<guard-user> "cmd /k {PYTHON} {GUARD_ROOT}\program\or.py --szaraz --egyszer --gyoker {GUARD_ROOT}"
```
It prints how many inbound items it found and, if a draft is waiting, the full preview — but sends and posts nothing. The dry run works in a sandbox state folder; it only copies drafts.
The sandbox folder must be created once by an admin, because the guard can't create it at the root:
```powershell
New-Item -ItemType Directory -Force "{GUARD_ROOT}\allapot_szaraz"
icacls "{GUARD_ROOT}\allapot_szaraz" /grant "<COMPUTERNAME>\<guard-user>:(OI)(CI)M"
```
Login network error with `HTTP 401` → wrong app password. "Own feed paging did not reach the end of the 24-hour window" → the guard can't verify its limits, so it stays silent; usually clears next round.

---

## 8. The scheduled task

Here you type `<guard-user>`'s password. With that password an attacker could run anything as the guard (`runas`), so: **switch to the admin account's own session** (as in 6/a), run this there, then switch back.

ADMIN window. **Don't paste it all at once**: `Read-Host` would swallow the next pasted line as the password. First the five lines up to and including `Read-Host`, type the password, Enter; then the rest.
```powershell
$action = New-ScheduledTaskAction -Execute "{PYTHON}" -Argument '"{GUARD_ROOT}\program\or.py" --gyoker "{GUARD_ROOT}"' -WorkingDirectory "{GUARD_ROOT}\program"
$atStart = New-ScheduledTaskTrigger -AtStartup
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
$secret = Read-Host "Password of the guard account" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($ptr)
Register-ScheduledTask -TaskName "<task-name>" -Action $action -Trigger $atStart,$repeat -Settings $settings -User "<guard-user>" -Password $plain -RunLevel Limited
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
Remove-Variable plain, secret, ptr
```
- `-AsSecureString` is not optional: without it the password appears as plain text on screen.
- `ZeroFreeBSTR` wipes the memory the password occupied; close the window afterwards.
- **Never** use `schtasks /create … /RP <password>`: the password would land in the PowerShell history file and possibly the event log.
- Don't copy the password to the clipboard: programs under `<windows-user>` can read it, and clipboard history may sync to the cloud.

`-Password` means it runs even when nobody is signed in; `IgnoreNew` means no second instance.

Start it:
```powershell
Start-ScheduledTask -TaskName "<task-name>"
Start-Sleep -Seconds 20
Get-ScheduledTask -TaskName "<task-name>" | Get-ScheduledTaskInfo | Format-List TaskName,LastRunTime,LastTaskResult,NumberOfMissedRuns
```
`LastTaskResult : 267009` means *running*. Best proof, in a NORMAL window: `Get-Content "{GUARD_ROOT}\kozos\be\allapot.json"` shows a running state with a fresh timestamp.

**If not:**
- result code 0x8007052E (shown in decimal) → wrong password; rerun the block from `Read-Host`.
- `LastTaskResult : 3` → the guard stopped on a permission error; the reason is in `allapot.json`.
- "the account lacks the right to log on as a batch job" → see below.

### If "Log on as a batch job" is missing

`Register-ScheduledTask` usually grants it. If not: `secpol.msc` **does not exist on Windows 11 Home**; use `secedit` instead.
ADMIN window:
```powershell
$dir = "C:\GuardPolicy"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
secedit /export /cfg "$dir\policy.inf" /areas USER_RIGHTS
Copy-Item "$dir\policy.inf" "$dir\policy_original.inf"
(Get-LocalUser <guard-user>).SID.Value
```
The copy is your way back: `secedit /configure` **replaces** every right in the file with the file's contents.
Edit with `notepad "$dir\policy.inf"`. **Don't touch the encoding** (it is UTF-16; saving as UTF-8 breaks it).
Find the `SeBatchLogonRight` line and append `,*<SID>` (the `S-1-5-21-…` value printed above, without angle brackets). If the line doesn't exist, add `SeBatchLogonRight = *<SID>` under `[Privilege Rights]`.
```powershell
secedit /configure /db "$dir\secedit.sdb" /cfg "$dir\policy.inf" /areas USER_RIGHTS
secedit /export /cfg "$dir\policy_check.inf" /areas USER_RIGHTS
Select-String -Path "$dir\policy_check.inf" -Pattern "SeBatchLogonRight"
```
**Way back if something broke:**
```powershell
secedit /configure /db "$dir\secedit_back.sdb" /cfg "$dir\policy_original.inf" /areas USER_RIGHTS
```
Errors are explained at the end of `C:\Windows\security\logs\scesrv.log`. Delete `$dir` only once the task works.

---

## 9. The first real run

1. Claude writes a draft into `{GUARD_ROOT}\kozos\ki\`.
2. Within minutes, **the new bot sends a Telegram message**: the full outgoing text in a frame, and at the bottom the approval line.
3. **Reply to that message** (long-press → Reply) with: `IGEN`.
4. The guard answers: sent, with the post's link.

Only an exact `IGEN` sent **as a reply** counts. "igen!", "IGEN 123", forwarded or edited messages do not. A preview expires after **48 hours**.

**What to check on the preview.**
- The framed text is exactly what goes out: no rewriting, no shortening.
- For replies: the target post, and **which thread** the post lands in. The guard walks the whole ancestor chain live from Bluesky.
- **"FOREIGN THIRD PARTY IN THIS THREAD"** is not an error, but they may get notified: read who they are before saying yes.
- **"THE THREAD'S ACTUAL TOP DIFFERS FROM WHAT THE TARGET CLAIMS"** is suspicious: be very strict.
- **INITIATED REPLY** means we weren't addressed (allowlist) or a third party is in the thread: be stricter.
- If the addressee changes their handle after approval, the guard does NOT send, and tells you.

### Limits

Counted over a rolling 24 hours, from Bluesky's own record (`program\beallitasok.py`):

| Limit | Value |
|---|---|
| posts | 5 / 24 h |
| initiated replies | 2 / 24 h |
| gap between two posts | 10 minutes |
| new follows | 5 / 24 h |
| likes | 10 / 24 h |
| previews awaiting approval at once | 3 (shared by posts, follows, likes) |

When a limit is reached, the draft **waits** and continues later by itself. If paging the own feed can't verify the limit, the guard **does not post** (fail-closed).

Follows and likes go through **the same gate** as posts: draft → live check → preview → `IGEN`.

---

## 10. Updating (when Claude changes the code)

Only an admin can write `program\`; that's intentional.

### 10/a — First see WHAT changed

NORMAL window:
```powershell
cd "{SOURCE}"
python ujjlenyomat.py --csak-telepitendo --osszevet "{HOME}\guard_fingerprint.txt"
```
Compare with what you agreed on. If a file changed that nobody mentioned: **don't copy**, ask. Compare the total line with your phone photo.

### 10/b — Copy

ADMIN window:
```powershell
Disable-ScheduledTask -TaskName "<task-name>"
Stop-ScheduledTask -TaskName "<task-name>"
Copy-Item "{SOURCE}\*.py" -Destination "{GUARD_ROOT}\program\" -Force
runas /user:<guard-user> "cmd /k {PYTHON} {GUARD_ROOT}\program\or.py --szaraz --egyszer --gyoker {GUARD_ROOT}"
Enable-ScheduledTask -TaskName "<task-name>"
Start-ScheduledTask -TaskName "<task-name>"
```
`Disable` must come **before** `Stop`: `Stop` only ends the running instance; the 5-minute trigger stays active and could start a new one mid-copy, with half a codebase.
Only `.py` files are copied; the allowlist and blocklist are never overwritten.

Refresh the fingerprint list (from the SOURCE folder) and take a new photo:
```powershell
python "{SOURCE}\ujjlenyomat.py" "{SOURCE}" --csak-telepitendo --kiir "{HOME}\guard_fingerprint.txt"
```

### 10/c — Changing the allowlist or blocklist (SEPARATE step)

Only on purpose. ADMIN window, look at the content before saving:
```powershell
notepad "{GUARD_ROOT}\program\szivesen_latnak.json"
notepad "{GUARD_ROOT}\program\tilto_mintak.json"
```
The allowlist matches **only DIDs** (`did:plc:…`), never handles: a handle can change, a DID can't.
If the JSON is broken, the guard does not stop: it continues with an empty allowlist (no initiated replies) and records this in `allapot.json`.

---

## 11. Stopping, revoking, removing

**Quick stop** (ADMIN):
```powershell
Stop-ScheduledTask -TaskName "<task-name>"
Disable-ScheduledTask -TaskName "<task-name>"
```
**Emergency brake** (no computer needed): Bluesky → Settings → Privacy and security → App passwords → **Revoke**.

**Full removal** (ADMIN):
```powershell
Unregister-ScheduledTask -TaskName "<task-name>" -Confirm:$false
Remove-Item -Recurse -Force "{GUARD_ROOT}"
Remove-LocalUser -Name "<guard-user>"
```
Also revoke the app password on Bluesky, and the bot token at BotFather (`/revoke`, or `/deletebot`).
In variant B, the way back is `Add-LocalGroupMember -SID "S-1-5-32-544" -Member "<windows-user>"`.

### Command-line switches

| Switch | What it does |
|---|---|
| `--gyoker <path>` | which folder tree to use (`{GUARD_ROOT}`) |
| `--ellenoriz` | checks permissions only, then exits; never posts |
| `--ki-vagyok` | prints the first incoming sender's ID to the CONSOLE (step 6/e) |
| `--szaraz` | full run, but the Telegram message goes to the console and nothing is posted; state in a sandbox, drafts only copied |
| `--egyszer` | one round, then exit |
| `--idovonal N` | also reads the last N posts of followed accounts into `inbox.jsonl`; default 0 |

---

## What happens on errors

The guard is built to **stop** rather than guess.

- Permission problem at startup → it does not start; the reason is in `kozos\be\allapot.json`.
- Network uncertainty, limit not verifiable → it does not post.
- Something changed between approval and sending (target, thread, handle) → it does not send, and the approval expires.
- A send that got stuck midway → it does **not** resend; it tells you.

## If you get stuck

The two most telling places, readable from a NORMAL window:
```powershell
Get-Content "{GUARD_ROOT}\kozos\be\allapot.json"          # what the guard is doing now
Get-Content "{GUARD_ROOT}\allapot\naplo.jsonl" -Tail 20   # what happened
```
Neither contains secrets — no token, no password, no Telegram IDs, no outgoing post text (only its hash). You can show them to Claude.
**Never** show or copy the contents of the two files in `{GUARD_ROOT}\titok\`.
