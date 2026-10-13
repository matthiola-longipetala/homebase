# Read me — the first part of the installation in one script

This script performs **chapters 2, 3, 4 and 5** of `INSTALL.md`
for you, following **variant A)** (the `agent-user` account stays an administrator,
chapter 1 is skipped). About **10 minutes**. Read chapter 0 of the guide first.

## 1. Open an ADMINISTRATOR PowerShell

Start menu → type: `powershell` → **right-click** *Windows PowerShell* →
**Run as administrator** → *Yes*. The title bar will say: *Administrator*.

## 2. Paste this single line and press Enter

```
powershell -NoProfile -ExecutionPolicy Bypass -File "{HOME}\Desktop\Claude\bluesky\or\telepit_1_resz.ps1"
```

## 3. What you will see, what it asks

1. It prints what it is going to do, and **waits for ENTER**.
2. **It asks for the password of the `bsky-guard` account, twice.** Nothing is
   shown while you type — this is normal. **You make up** the password (at least 12 characters,
   lower- and upper-case letters, a digit), **write it down on paper** (you will need it again in chapter 8), and
   **do not tell it to Claude**. Do not copy it to the clipboard: type it in.
   *(If the account already exists, it does not overwrite it — it asks whether to continue.)*
3. It builds the folder tree and sets the permissions. After each step a
   green **OK** line appears.
4. **It prints the code fingerprint, and stops.** At this point **photograph with your
   phone** the `OSSZESITETT` line and the `FAJLOK` count — this is the
   one thing the machine has no access to. Then ENTER.
5. It copies the code over and verifies it: `EVERYTHING MATCHES`.
6. At the end it runs the self-check: `Permissions are OK.`

The script does **not** ask for the Bluesky and Telegram secrets, does **not** write into the `titok`
folder, and does **not** create a scheduled task. You can run it several times:
whatever is already done, it does not break.

## 4. If it prints something RED

Next to the red **ERROR** line it says what the problem is and where the fix is in the guide.
The script **stops** in that case — it does not continue blindly, nothing gets broken.
Read the line, and if it is not clear, **show it to Claude**.
(Exception: at the very end a red *"Access is denied"* is the **good** result —
the script tells you separately what to paste into a **normal** PowerShell window.)

## 5. Afterwards

Continue with **chapter 6** of `INSTALL.md` (the secrets). That
cannot be put into a script: you have to type in the token and the app password from your
phone. In variant A) there is no separate administrator account, so beforehand
**tell Claude to close its log, then close the Claude windows**
(step 6/a describes this). The same applies to chapter 8 (scheduled task).
