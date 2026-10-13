# Installation — step by step

This guide assumes you know **nothing** about Windows permissions.
Every command can be copied as is. At the end of every step you will find
**what you see if it worked**, and **what to do if it did not**.

**What Claude does NOT know and does not want to know:** the password of
`bsky-guard`, the password of the administrator account, the Bluesky app
password and the bot token. You type these in yourself, by hand — and, as
Chapter 6 explains, **not under the `agent-user` account**.

## WHAT WILL HAPPEN AND WHY — in ten lines

1. We create a separate Windows user (working name: `bsky-guard`), and the
   program that posts to Bluesky will run **under that user**, not under yours.
2. It is separate because your account that runs Claude (`agent-user`) can be
   deceived: anyone who writes to it from the internet may try to talk it into
   something.
   (Note: `agent-user` is a placeholder for the Windows user that runs the agent.
   Set it machine-wide so the guard sees it too, e.g. in an administrator shell:
   `setx /M BSKY_AGENT_USER "<agent windows user>"`. Without it the guard's
   permission check fails closed and it refuses to start.)
3. Between the two accounts there will be a locked folder tree (`C:\BskyGuard`),
   and Windows itself enforces who can access what — not good intentions.
4. The secrets (bot token, Bluesky password) go into a folder that your
   everyday account **cannot even read**.
5. Claude can only drop a *proposal* into a shared folder. It cannot write
   anything else, and it cannot post.
6. The program picks up the proposal, inspects it, and checks **live** on
   Bluesky whose conversation the reply would land in.
7. Then it sends a preview to **your phone** through a new Telegram bot:
   exactly the text that would go out, and to whom.
8. Nothing goes out until you reply to that message with: `IGEN` (yes).
9. One approval = one post. The program sends the **stored** text; it does not
   re-read the proposal — that could have been swapped in the meantime.
10. If anything is uncertain (network, permissions, a suspicious character),
    the program **does not post**, and tells you why.

## Before you start

- Plan for **one and a half to two hours** if you are doing this for the
  first time. You do not have to do it in one go: **splitting it into two
  sessions is fine.**
  - **First session (about 45–60 minutes): Chapters 0–3.** At the end you have
    both accounts and the complete folder tree with the correct permissions.
  - **⏸ YOU CAN SAFELY STOP HERE.** At the end of Chapter 3 nothing is
    running, nothing goes out, and nothing breaks if it stays like this for
    days. The machine works exactly as before.
  - **Second session (about 45–60 minutes): Chapters 4–9.** This is Python,
    the code, the secrets, the test run and the scheduled task.
- Why not 30–40 minutes? Because it includes two full account switches (the
  profile creation at the first sign-in alone takes several minutes), plus
  creating the Telegram bot and the Bluesky app password on your phone.
- You will need two kinds of PowerShell window:
  - **ADMINISTRATOR**: Start menu → type `powershell` → right-click
    *Windows PowerShell* → **Run as administrator**. Its title bar will say
    *Administrator*.
  - **NORMAL**: Start menu → `powershell` → Enter. (This is your `agent-user`
    account with everyday rights — we use it for checking.)
- Paste the commands **one at a time**, and after each one look at what it
  printed.
- Where it says **IN THE ADMINISTRATOR ACCOUNT'S OWN SESSION**, "Run as
  administrator" is not enough: you really have to sign in as that account.
  I explain why at the start of Chapter 6. There are **two** such chapters:
  Chapter 6 (secrets) and Chapter 8 (the scheduled task's password).
- **After option B), "Run as administrator" is no longer an elevation but a
  sign-in to a DIFFERENT account.** This matters because in that
  administrator window you then see the *other* user's desktop and profile
  folder. That is why this guide never uses the `$env:USERPROFILE` form:
  everywhere there is a **full, spelled-out path**, so that it points to the
  same file from both windows.

## Order of the chapters

**Go in order, from 0 to 11; at the end of Chapter 3 there is a pause point (⏸).**

That is all. The execution order is the same as the numbering — no going
back, no exceptions. (An earlier version asked for a different order than the
one the chapters appeared in; that was exactly where a beginner goes wrong, so
we renumbered.)

---

# Chapter 0 — DECIDE FIRST: does `agent-user` stay an administrator?

This decision has to be made **before installation**, because the order
matters, and because the two options protect different things. I will not
sugar-coat it: one is more convenient, the other is more secure, and there is
no option that is both.

## What is at stake

The **Administrators** group has access to the `titok\` (secrets) and
`allapot\` (state) folders — otherwise the folders would be unmanageable (you
could not look into them, back them up or repair them). The `agent-user`
account is **currently a member** of this group. That means a deceived agent
(me) running under `agent-user` can reach the secrets and rewrite the state
with a single elevation.

## Option A) — `agent-user` stays an administrator

**What the system protects this way:**

- everything that can be reached **without elevation**. This is the everyday
  case: a deceived agent simply starts out and hits a wall at the `titok\`
  folder;
- the anchor of the approval in everyday use: the preview's `message_id` and
  the stored canonical record are under `allapot\`, which `agent-user` cannot
  write;
- the bot token and the app password, against being leaked by a simple file
  read.

**What it does NOT protect — this must be said clearly:**

- **a single UAC approval bypasses the whole thing.** If an elevated process
  runs under the `agent-user` account, it can read the `titok\` folder and
  write the `allapot\` folder. In that case the protection essentially rests on
  good intentions;
- the same applies to every program that is already running in an elevated
  window.

**What you should do in any case with this option** (this is your decision, I
will not change it):

1. Set UAC to **"Always notify"**. That way every elevation opens a window and
   cannot slip through unnoticed.
   Start menu → `UAC` → *Change User Account Control settings* → drag the
   slider to the **top** position → OK.
2. Do Chapter 6 of the installation (entering the secrets) in the administrator
   account's own session anyway — that step belongs there in option A) too.

If you choose this: **skip to Chapter 2**, and leave out Chapter 1. The guard
will start, but it will warn you on every round — in the
`kozos\be\allapot.json` file and on Telegram as well. The warning is not an
error, it is a reminder.

## Option B) — a separate administrator account, `agent-user` is removed

This is what makes the full model work. The order, however, is **critical**:
the machine must never be left without a working administrator.

**What changes in everyday life — honestly:**

- every elevation (UAC) will from now on be a **password prompt** for the
  administrator account, not a simple "Yes" click. This can happen several
  times a day;
- installing programs, uninstalling programs, driver updates, system settings,
  firewall rules, network settings: they all ask for the password;
- **I, running under the `agent-user` account, lose the ability to do
  administrator tasks.** Specifically: installing a system-wide MCP server,
  installing a system-wide Python package, starting or restarting a service,
  `icacls` changes on protected paths, writing under `C:\Program Files`,
  scheduled tasks in another user's name. For these I will ask you from now on.

**What does NOT break:** all work inside your own profile, `pip install
--user`, scheduled tasks running in your own name, controlling the robot, the
Telegram and calendar tools, document creation, desktop icons, starting
`ngrok` from a desktop icon. Most of our daily work stays.

**The way back** is a single command (see step 1/d) — but honestly: that is
only true **as long as you know the administrator account's password**. The
way-back command has to be run as an administrator, and if `agent-user` is no
longer an administrator, you have to be able to sign in to the `admin`
account. That is why I ask you in step 1/b to write the password down **on
paper** as well.

If you choose this: continue with **Chapter 1**, and only then come to Chapter 2.

---

# Chapter 1 — ONLY for option B): the administrator account

> If you chose option A), **skip** this chapter.

## 1/a — The current state of the machine

In an **ADMINISTRATOR** window:

```powershell
Get-LocalUser | Where-Object { $_.Name -in @("admin","Rendszergazda","agent-user") } | Format-Table Name,Enabled
Get-LocalGroupMember -SID "S-1-5-32-544" | Select-Object Name
```

(`Rendszergazda` is the name of the built-in Administrator account on Hungarian
Windows; on English Windows it is `Administrator` — adjust the name in the
filter accordingly.)

**What you will probably see:** the `admin` and the built-in Administrator
(`Rendszergazda`) accounts show `Enabled : False` — that is, they are
**disabled**. All three names are in the Administrators group, but that does
not mean you can sign in to the disabled accounts.

> **This is why you must not remove `agent-user` first.** If you remove it
> while the other accounts are disabled, **the machine is left without a
> usable administrator.**

## 1/b — Enabling the `admin` account and setting its password

```powershell
Enable-LocalUser -Name "admin"
Set-LocalUser -Name "admin" -Password (Read-Host "Make up a STRONG password for the admin account" -AsSecureString)
Set-LocalUser -Name "admin" -PasswordNeverExpires $true
```

> **YOU make up the password, and YOU write it down** (on paper or in a
> password manager). **Do not tell it** to Claude. From now on you will need
> this password every day.

> **`Set-LocalUser` does NOT ask for confirmation**: if you mistype the
> password, the typo is silently set. That is not a problem, because step 1/c
> will catch exactly that — and **you can safely repeat the 1/b command at any
> time**, since `agent-user` is still an administrator at this point. If 1/c
> fails, simply set it again.

**If it worked:**

```powershell
Get-LocalUser admin | Format-List Name,Enabled,PasswordExpires
```

Expected output: `Enabled : True`.

## 1/c — TRY IT: can you really sign in?

**This step cannot be skipped.** Until you have actually signed in to the
`admin` account, you do not know whether it works.

1. **Win + L** (or Start menu → your picture → *Sign out*).
2. In the bottom-left corner of the sign-in screen choose the **admin** account.
3. Type the password you have just set.
4. Wait for the first sign-in to finish (this can take a few minutes, because
   Windows is creating the profile now).
5. When the desktop appears: **it worked.** Sign out from here, and go back to
   the `agent-user` account.

> **The PIN trap — read this.** After the first sign-in Windows will offer to
> set up a **PIN** for this account. A PIN is convenient for signing in, **but
> the UAC window asks for the PASSWORD, not the PIN.** If you sign in with the
> PIN and forget the password in the meantime, after option B) the machine
> becomes unmanageable. So: you may set up the PIN, **but the password is what
> really counts** — that is what you must know, and that is what you should
> write down.

**If it did NOT work** (wrong password, "Your account has been disabled", the
account does not even appear in the list): **do not go on.** Repeat step 1/b.
The `agent-user` account stays an administrator, and the system works
according to option A).

## 1/d — Removing `agent-user` from the Administrators

**Only if step 1/c WORKED.**

```powershell
Remove-LocalGroupMember -SID "S-1-5-32-544" -Member "agent-user"
```

**If it worked:**

```powershell
Get-LocalGroupMember -SID "S-1-5-32-544" | Select-Object Name
```

`agent-user` is **no longer** in the output. From now on the UAC window will
**ask for the password** of the `admin` account — this is normal, and this is
the point.

> **WARNING: this change does not take effect immediately.** The "pass card"
> of your current sign-in (in Windows terms: the access token) was created *at
> the moment you signed in*, and **it contains the Administrators group until
> you sign out**. So for a while the UAC window will still let you through with
> a simple "Yes", as if nothing had changed. This is **not an error**, and it
> does not mean the command failed — the output of `Get-LocalGroupMember` is
> what counts.
>
> **What to do: sign out and back in** (Start → your picture → *Sign out*)
> before you go on. That way the check in Chapter 7 gives a true result.

**FINAL CHECK — do not skip this.** Make sure the administrator account is
still enabled AND still a member:

```powershell
Get-LocalUser admin | Format-List Name,Enabled
Get-LocalGroupMember -SID "S-1-5-32-544" | Select-Object Name
```

What you should see: `Enabled : True`, and `admin` in the group list.

> **Never disable and never delete this account.** It is your only working
> administrator, and the way back also goes through it.

**THE WAY BACK.** If at any time you decide this is too inconvenient, a single
command, run while signed in to the administrator account, restores it:

```powershell
Add-LocalGroupMember -SID "S-1-5-32-544" -Member "agent-user"
```

Then sign out and back in to the `agent-user` account, and everything is as it
was. No data loss, nothing needs to be reinstalled.

---

# Chapter 2 — The guard's own Windows user

In an **ADMINISTRATOR** window:

```powershell
New-LocalUser -Name "bsky-guard" -FullName "BskyGuard service" -Description "The Bluesky guard runs under this account" -Password (Read-Host "Make up a STRONG password for the bsky-guard account" -AsSecureString)
```

Type the password (it will not be visible while you type), Enter.

> **YOU make up the password, and YOU write it down.** **Do not tell it** to
> Claude. You will need it once more at the end of the installation.

Then:

```powershell
Set-LocalUser -Name "bsky-guard" -PasswordNeverExpires $true
Add-LocalGroupMember -SID "S-1-5-32-545" -Member "bsky-guard"
```

**If it worked, you see this:** `New-LocalUser` prints a small table about the
newly created account (`Name`, `Enabled`, `Description`) — **that is a good
sign**. `Set-LocalUser` and `Add-LocalGroupMember`, however, are silent; they
really print nothing. The check below prints the account in any case:

```powershell
Get-LocalUser bsky-guard | Format-List Name,Enabled,PasswordExpires
```

Expected output: `Name : bsky-guard`, `Enabled : True`, `PasswordExpires :` (empty).

Also check that it is **not** an administrator:

```powershell
Get-LocalGroupMember -SID "S-1-5-32-544" | Select-Object Name
```

The `bsky-guard` account **must not** appear in the list.

**If it did not work:**
- *"The password does not meet the password policy requirements"* → choose a
  longer password (at least 12 characters, lower- and upper-case letters, a
  digit).
- *"The account already exists"* → then you have already created it; go on.

---

# Chapter 3 — Folders and permissions

In an **ADMINISTRATOR** window, can be pasted in one go:

```powershell
New-Item -ItemType Directory -Force -Path "C:\BskyGuard","C:\BskyGuard\titok","C:\BskyGuard\program","C:\BskyGuard\allapot","C:\BskyGuard\allapot\varo","C:\BskyGuard\allapot\feldolgozott","C:\BskyGuard\allapot\zart","C:\BskyGuard\kozos","C:\BskyGuard\kozos\be","C:\BskyGuard\kozos\ki" | Out-Null
```

(Folder names: `titok` = secrets, `program` = the guard's code, `allapot` =
state, `varo` = pending, `feldolgozott` = processed, `zart` = sealed, `kozos` =
shared, `be` = in, `ki` = out. The names are used by the code — keep them.)

Now the permissions. `/inheritance:r` means: "forget what you inherited from
the parent folder"; and `/grant:r` means: "exactly these rights, nothing
else". `*S-1-5-18` is the system, `*S-1-5-32-544` is the Administrators group —
given by number because their names differ on localized (e.g. Hungarian)
Windows.

```powershell
icacls "C:\BskyGuard" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "bsky-guard:(OI)(CI)(RX)" "agent-user:(OI)(CI)(RX)"
```

```powershell
icacls "C:\BskyGuard\titok" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "bsky-guard:(OI)(CI)(RX)"
```

```powershell
icacls "C:\BskyGuard\program" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "bsky-guard:(OI)(CI)(RX)" "agent-user:(OI)(CI)(RX)"
```

```powershell
icacls "C:\BskyGuard\allapot" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "bsky-guard:(OI)(CI)(M)" "agent-user:(OI)(CI)(RX)"
```

**Now come three folders in which the NAME of `agent-user` DOES NOT EVEN
APPEAR.** Everything derived from the content of the dropped drafts goes here:
the not-yet-processed draft (`varo`), the already closed draft
(`feldolgozott`) and the guard's internal fingerprint key (`zart`). The draft
is written by `agent-user`, so in principle it already knows it — but if a
dropped file turned out not to be what it seems, its content still cannot be
read back from here. **This is the third, independent layer of protection**,
and the guard checks at startup that it is in place.

```powershell
icacls "C:\BskyGuard\allapot\varo" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "bsky-guard:(OI)(CI)(M)"
```

```powershell
icacls "C:\BskyGuard\allapot\feldolgozott" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "bsky-guard:(OI)(CI)(M)"
```

```powershell
icacls "C:\BskyGuard\allapot\zart" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "bsky-guard:(OI)(CI)(M)"
```

```powershell
icacls "C:\BskyGuard\kozos" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "bsky-guard:(OI)(CI)(M)" "agent-user:(OI)(CI)(RX)"
```

```powershell
icacls "C:\BskyGuard\kozos\be" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "bsky-guard:(OI)(CI)(M)" "agent-user:(OI)(CI)(RX)"
```

```powershell
icacls "C:\BskyGuard\kozos\ki" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "bsky-guard:(OI)(CI)(M)" "agent-user:(OI)(CI)(M)"
```

**And immediately after it, this line — do not skip it:**

```powershell
icacls "C:\BskyGuard\kozos\ki" /deny "agent-user:(DE)"
```

> **Why it is needed.** `agent-user` gets Modify rights on the `ki` folder
> because that is where I put the drafts — that is its job. Modify, however,
> also includes deleting **the folder itself**. Without this line a deceived
> agent could simply **empty and delete** the `ki` folder, and from then on
> the guard would no longer start, failing with *"missing folder: ki"*. That is
> a silent denial of service that only an administrator can repair.
>
> The `/deny "agent-user:(DE)"` line has **no `(OI)(CI)` flags**, so it only
> applies to the folder itself: `agent-user` can still create, write and
> delete the files inside it. The guard **checks** at startup that this deny
> entry is present.

**If `agent-user` is not accepted as a name** (*"No mapping between account
names and security IDs was done"*): this can happen if the local account is
linked to a Microsoft account. In that case write the machine name too instead
of the bare `agent-user`: `PC\agent-user`. This applies to all ten
`icacls /grant:r` commands and to the `/deny` line as well — **if name
resolution fails, the WHOLE command fails, and the folder may be left without
permissions.** You can look up the machine name with:
`$env:COMPUTERNAME`.

## Setting the OWNER — do not skip this

The access list is not everything: the **owner** of a folder can give itself
access back even if it is not on the list. That is why the guard also checks
at startup that the owner is the Administrators group. Let us set it:

```powershell
foreach ($ut in @("C:\BskyGuard","C:\BskyGuard\titok","C:\BskyGuard\program","C:\BskyGuard\allapot","C:\BskyGuard\allapot\varo","C:\BskyGuard\allapot\feldolgozott","C:\BskyGuard\allapot\zart","C:\BskyGuard\kozos","C:\BskyGuard\kozos\be","C:\BskyGuard\kozos\ki")) { icacls $ut /setowner "*S-1-5-32-544" }
```

Check — **for all ten folders**, with the same list as above:

```powershell
foreach ($ut in @("C:\BskyGuard","C:\BskyGuard\titok","C:\BskyGuard\program","C:\BskyGuard\allapot","C:\BskyGuard\allapot\varo","C:\BskyGuard\allapot\feldolgozott","C:\BskyGuard\allapot\zart","C:\BskyGuard\kozos","C:\BskyGuard\kozos\be","C:\BskyGuard\kozos\ki")) { "{0} -> {1}" -f $ut, (Get-Acl -LiteralPath $ut).Owner }
```

**What you should see:** `BUILTIN\Administrators` in **all ten** lines (on
Hungarian Windows `BUILTIN\Rendszergazdák`).

> **Look at the `kozos\ki` folder especially closely.** This is the line most
> easily skimmed over, yet it is the most important one here. The
> `/deny "agent-user:(DE)"` entry that protects the `ki` folder **rests
> entirely on `agent-user` NOT being the owner of the folder.** The owner can
> rewrite the permissions even if it is not on the list — so with a single
> command it could remove the deny entry from itself and delete the folder. If
> you see `PC\agent-user` in this line, the protection does not protect. (The
> guard catches this on its own at startup and does not start — but only at
> the NEXT start, so we check it here as well.)

**If `PC\agent-user` appears for any of them:** run the `/setowner` command
again for that folder. Without this the guard does not start — and that is
intentional.

**If the `/setowner "*S-1-5-32-544"` form gives an error** (*"Invalid
parameter"*, *"failed to set the owner"*): the `*SID` form definitely works
with the `/grant` switch, but for `/setowner` the documentation mentions a
name. In that case give the name — on Hungarian Windows:

```powershell
foreach ($ut in @("C:\BskyGuard","C:\BskyGuard\titok","C:\BskyGuard\program","C:\BskyGuard\allapot","C:\BskyGuard\allapot\varo","C:\BskyGuard\allapot\feldolgozott","C:\BskyGuard\allapot\zart","C:\BskyGuard\kozos","C:\BskyGuard\kozos\be","C:\BskyGuard\kozos\ki")) { icacls $ut /setowner "Rendszergazdák" }
```

On English Windows use `"Administrators"`. **Do not run the same failing
command again and again** — if the `*SID` form failed, try the name.

## Check 1 — what does icacls say?

```powershell
icacls "C:\BskyGuard\titok"
```

**What you should see:** three lines — `NT AUTHORITY\SYSTEM`,
`BUILTIN\Administrators`, `PC\bsky-guard`. **`agent-user` MUST NOT appear.**

On the drop folder, however, it must appear, and in two ways:

```powershell
icacls "C:\BskyGuard\kozos\ki"
```

**What you should see:** a `PC\agent-user:(OI)(CI)(M)` line **and** a
`PC\agent-user:(DENY)(DE)` line. If the `(DENY)(DE)` line is missing, run the
`/deny` command again — the guard does not start without it.

**And its owner must be `BUILTIN\Administrators` here too** — see the box
above. The two together protect: the deny entry prevents deletion, and the
owner setting prevents the deny entry from being removed.

## Check 2 — the most important one: are you really locked out?

Open a **NORMAL** (non-administrator) PowerShell window and type:

```powershell
Get-ChildItem "C:\BskyGuard\titok"
```

**If it worked, you see this:** a red error message containing
*"Access to the path … is denied"*.

> **This red error is the GOOD result.** It means that the user running
> Claude cannot reach the secrets.

In the same **NORMAL** window, check the three sealed folders as well:

```powershell
foreach ($ut in @("C:\BskyGuard\allapot\varo","C:\BskyGuard\allapot\feldolgozott","C:\BskyGuard\allapot\zart")) { Get-ChildItem $ut }
```

**What you should see:** a red *"Access … is denied"* for **all three**.
If any of them can be listed, run the `icacls` command again for that folder —
the guard does not start without it.

And check that you **can still see** the `allapot` folder (this is
intentional: that is where you find out what the guard is doing):

```powershell
Get-ChildItem "C:\BskyGuard\allapot"
```

**If instead it lists the `titok` folder:** something went wrong. Run the
`icacls` command for `titok` again, and check that you really are in an
**administrator** window. If `agent-user` is still an administrator (option
A)), a window **started as administrator** will be able to see in anyway —
that is why the check must be done in a NORMAL window.

---

## ⏸ YOU CAN SAFELY STOP HERE

So far you have created two accounts and built the locked folder tree.
**Nothing is running, nothing goes out, and the machine works exactly as
before** — it can safely stay like this for days. When you continue, simply
start with **Chapter 4**, and from there go on in order.

If you chose option B) and have not signed out since step 1/d, **do it now** —
from then on the permission picture is accurate.

---

# Chapter 4 — Python for the other user

**Start here after the pause.** Steps 5/a, 5/c and 6/e already run Python,
and 6/e even uses the `C:\Python314\python.exe` path explicitly — so Python
has to be verified first.

The guard runs under another account, so it needs a Python that **every
user** can reach and that **`agent-user` cannot modify**.

Your machine already has one: **`C:\Python314\python.exe` (Python 3.14)**. We
use that. Check in an **ADMINISTRATOR** window:

```powershell
icacls "C:\Python314"
```

**What you should see:** `BUILTIN\Users:(OI)(CI)(RX)` (on Hungarian Windows
`BUILTIN\Felhasználók`) — that is, everyone can run it, but nobody can write
to it.

> **IMPORTANT — do NOT use the `C:\Python312` folder.** The
> `Authenticated Users` group has **Modify** rights on it, so `agent-user`
> could write into it, and thereby smuggle code into the guard. If for some
> reason you still want to use it, fix it first:
> `icacls "C:\Python312" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "*S-1-5-32-545:(OI)(CI)(RX)"`

Check that the `bsky-guard` account can really run it:

```powershell
runas /user:bsky-guard "cmd /k C:\Python314\python.exe -V"
```

**If it worked:** the window that opens shows a line starting with
`Python 3.14`. The third number (the patch version) may differ — that does not
matter, only `3.14` counts.

**If it did not work** (*"not found"*): install a system-wide Python from
python.org, **tick the "Install for all users"** option in the installer,
and then change the path in the commands below.

---

# Chapter 5 — Copying the code, WITH VERIFICATION

## Why simply copying is not enough

The source of the code is the `{HOME}\Desktop\Claude\bluesky\or\` folder —
which is exactly the place that, according to the threat model, the attacker
can write freely. If you copy without checking, a deceived agent could smuggle
its own code in during an update. From then on **its code runs** under the
`bsky-guard` account, with access to the `titok\` folder — and the startup
permission check is worthless too, because that is also its code.

So the procedure is: **fingerprint → you look at it → only then do you copy.**

## 5/a — Creating the fingerprint

In a **NORMAL** window (no administrator needed for this):

```powershell
cd "{HOME}\Desktop\Claude\bluesky\or"
python ujjlenyomat.py --csak-telepitendo --kiir "{HOME}\Desktop\bskyguard_fingerprint.txt"
```

> **Two things in this command are intentional.**
>
> The **`--csak-telepitendo`** (installable files only) switch looks at exactly
> those files that actually get copied into the `program\` folder: the `.py`
> files and the two rule files. Without it the list would also contain the
> documentation and the sample files, the 5/c check would report four
> "MISSING" files, and **it could never say that EVERYTHING MATCHES.** That
> would teach you to treat the protection's alarms as errors — and then you
> would treat the real one that way too.
>
> The **full, spelled-out path** (not `$env:USERPROFILE`) is needed because
> step 5/a runs in a NORMAL window, while the 5/c check runs in an
> administrator one. After option B) that administrator window is **another
> user's window**, where `$env:USERPROFILE` would point to that other user's
> profile — and this file is not there.

On the screen, next to every file there will be a long, sixty-four-digit
number (its SHA-256 hash), and at the end of the list two lines:

```
OSSZESITETT  <sixty-four-digit number>
FAJLOK       <file count>
```

(`OSSZESITETT` = combined fingerprint, `FAJLOK` = number of files.)

## 5/b — THE ONE STEP THAT MAKES THIS MEANINGFUL

On its own this list **protects you from nothing**. Whoever can rewrite the
code can also rewrite the list, and the script that computes the fingerprint.

**It is worth something if and only if you record the fingerprint on a channel
that the `agent-user` side CANNOT rewrite afterwards.** The simplest such
channel:

> **At the VERY FIRST installation, take a photo of the `OSSZESITETT` line and
> the `FAJLOK` count with your phone.** The computer has no access to your
> phone's gallery.

When updating, do the same: a new photo, and compare the two. If the combined
fingerprint has changed, that alone is not a problem — Claude does improve the
code. The question is whether it changed **what we talked about**. That is
what the `--osszevet` (compare) switch is for: it names the changed files.

If this is too much for you, say honestly to yourself that you are skipping
this step, and then the authenticity of the code depends on good intentions.
That is a choice too — just let us not pretend it is protection.

## 5/c — Copying

In an **ADMINISTRATOR** window. **Note: we do NOT copy the `.json` files.**

```powershell
Copy-Item "{HOME}\Desktop\Claude\bluesky\or\*.py" -Destination "C:\BskyGuard\program\" -Force
```

The rule files are copied as a **separate, deliberate step** — only now, at
the first installation:

```powershell
Copy-Item "{HOME}\Desktop\Claude\bluesky\or\tilto_mintak.json" -Destination "C:\BskyGuard\program\" -Force
Copy-Item "{HOME}\Desktop\Claude\bluesky\or\szivesen_latnak.json" -Destination "C:\BskyGuard\program\" -Force
```

> `szivesen_latnak.json` is the **allowlist**: the accounts to which the guard
> may also send an unsolicited reply. `tilto_mintak.json` is the block filter.
> You edit these, and **an update never overwrites them** — see Chapter 10.

**If it worked:**

```powershell
Get-ChildItem "C:\BskyGuard\program" | Select-Object Name
```

The list contains `or.py`, `motor.py`, `szures.py`, `bsky_api.py`,
`ujjlenyomat.py`, `tilto_mintak.json`, `szivesen_latnak.json` and the rest.

Check that the copy is the same as what you looked at:

```powershell
python "C:\BskyGuard\program\ujjlenyomat.py" "C:\BskyGuard\program" --csak-telepitendo --osszevet "{HOME}\Desktop\bskyguard_fingerprint.txt"
```

**If it worked:** `EVERYTHING MATCHES. Not a single file has changed.`

**If it says it cannot read the earlier list:** then you did not use the full,
spelled-out path in one of the steps. The file is on the `agent-user`
account's desktop, named `{HOME}\Desktop\bskyguard_fingerprint.txt` — type
that into both commands.

**If it reports "MISSING" or "NEW" files:** check that the
`--csak-telepitendo` switch is present in BOTH commands. The two must use the
same filter.

**Chapter 6 comes next** (the secrets).

---

# Chapter 6 — THE SECRETS: the bot token and the Bluesky app password

Step 6/e already needs the code in the `program\` folder and the working,
machine-wide Python — you did both in Chapters 4 and 5.

## Why we do not do this on the `agent-user` desktop

If Notepad opens on the `agent-user` user's desktop — even if you started it
as administrator — the window **is on the screen** of which a program running
under `agent-user` **can take a screenshot**. Windows protects an elevated
window against keyboard eavesdropping, but it **does not prevent** screenshots.
I, Claude, have a screenshot tool. A deceived instance could read the token and
the app password this way.

With the app password an attacker **does not need the guard at all**: they can
post directly in the account's name, and the whole approval chain can be
bypassed.

That is why this chapter happens in **another session**.

## 6/a — Switching to the administrator account

1. **Win + L** (or Start menu → your picture → *Sign out*).
2. On the sign-in screen choose the **admin** account (option B)), and sign in.
   - **If you chose option A)** (`agent-user` stayed an administrator) and you
     have no separate, enabled administrator account: then at least **close the
     programs that run Claude** (Claude Code windows, scheduled Claude tasks,
     the Telegram watcher), and only then continue. This is weaker protection,
     but better than nothing.
3. Pick up your phone — the token and the app password come **from there**.

## 6/b — The NEW Telegram bot (ON YOUR PHONE)

This is **not** the existing bot. A new one is needed because the existing
bot's database can be written by the `agent-user` account, so it cannot be the
anchor of the approval.

1. On **your phone**: Telegram → find the bot called **@BotFather**.
2. Send it: `/newbot`
3. Give it a name (anything, e.g. *Bluesky guard*), then a username (it must
   end in `bot`).
4. BotFather replies with a **token** of the form `<digits>:<letters>…`.
5. Tap the link BotFather sends, and press **Start** in the bot.

> **You read the token off your phone and type it in from there.** Do not copy
> it into the `agent-user` account's browser, do not e-mail it to yourself, and
> do not show it to Claude either. If it leaks by accident, you can invalidate
> it at BotFather with the `/revoke` command.

## 6/c — Entering the token (IN THE ADMINISTRATOR ACCOUNT'S OWN SESSION)

Signed in to the administrator account, in an **ADMINISTRATOR** PowerShell
window:

```powershell
notepad "C:\BskyGuard\titok\or_bot.env"
```

Notepad asks whether to create it — **Yes**. Type this into it, reading from
your phone:

```
OR_BOT_TOKEN=type-the-BotFather-token-here
OWNER_USER_ID=0
OWNER_CHAT_ID=0
```

Save it (Ctrl+S), close it.

> **About the encoding.** For a file that does not exist, Notepad first asks
> whether to create it — answer **Yes**. The encoding can then be set in the
> **bottom-right corner** of the save dialog. **UTF-8** is the right choice
> there. If you accidentally chose the "UTF-8 with BOM" variant, that is fine
> too: the guard has handled it correctly since the 2026-09-20 fix. Before
> that, it gave a *"Missing field"* error for a perfectly correct file.

## 6/d — The Bluesky app password (created ON YOUR PHONE)

1. On **your phone**, in the Bluesky app: **Settings → Privacy and security →
   App passwords → Add App Password**.
2. Give it a name (e.g. *bsky-guard*), and read off the password you get.
   It has the form `xxxx-xxxx-xxxx-xxxx`, and **you only see it once**.

> **This is NOT your main password.** An app password cannot delete the
> account and cannot change the main password.
> **Revoking is done in the same place:** *App passwords → Revoke*. That stops
> posting in an instant — this is the emergency brake.
>
> **Do not create it from the `agent-user` account's browser.** It would
> appear on that screen.

In the same administrator session, in an **ADMINISTRATOR** window:

```powershell
notepad "C:\BskyGuard\titok\bsky_kulcs.env"
```

Its content (with your own handle, and the app password typed in from your
phone):

```
BSKY_HANDLE=your-handle-here
BSKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

Save as UTF-8, close.

**If it worked:** `Get-ChildItem "C:\BskyGuard\titok"` shows two files:
`bsky_kulcs.env` and `or_bot.env`.

## 6/e — Your own Telegram ID (in the SAME session)

This step belongs here too: `--ki-vagyok` (who am I) prints your IDs to the
console, and that could be photographed just the same.

It needs the code in the `program\` folder — if you went in order, you copied
it in Chapter 5. If not, **jump back to Chapter 5 now**, and come back here
afterwards. This step uses the `C:\Python314\python.exe` path — Chapter 4
verifies that, so have that behind you as well.

In an **ADMINISTRATOR** window:

```powershell
runas /user:bsky-guard "C:\Python314\python.exe C:\BskyGuard\program\or.py --ki-vagyok --gyoker C:\BskyGuard"
```

Type the `bsky-guard` password (not visible). A window opens that says:
*"Now send a message (anything) to the NEW bot from your phone."*

Send the bot anything (e.g. `hi`). The window prints:

```
OWNER_USER_ID=<your numeric user id>
OWNER_CHAT_ID=<your numeric chat id>
(chat type: private — it must be "private")
```

**Write down these two numbers**, then open the file again and type them in
place of the `0`s:

```powershell
notepad "C:\BskyGuard\titok\or_bot.env"
```

**If it did not work:**
- *"No message arrived"* → did you press Start in the bot? Did you write to the
  right bot?
- *"Missing field …: OR_BOT_TOKEN"* → you mistyped the line in the file, or a
  space got around the `=`.
- The `runas` window closes immediately → run it like this instead, so that
  you can see the error:
  `runas /user:bsky-guard "cmd /k C:\Python314\python.exe C:\BskyGuard\program\or.py --ki-vagyok --gyoker C:\BskyGuard"`

## 6/f — Back to the `agent-user` account

Close the Notepad and PowerShell windows, sign out of the administrator
account, and go back to the `agent-user` account. From now on the secrets are
in the `titok\` folder, which `agent-user` cannot see into.

**Chapter 7 comes next** (dry-run test).

---

# Chapter 7 — Dry-run test

By now the code and both secret files are in place.

In an **ADMINISTRATOR** window:

```powershell
runas /user:bsky-guard "cmd /k C:\Python314\python.exe C:\BskyGuard\program\or.py --ellenoriz --gyoker C:\BskyGuard"
```

**If it worked, you see this:** `Permissions are OK. The guard can start.`

(If you chose option A), it prints a WARNING line about the Administrators
group before this. That is not an obstacle — it is a reminder, and from now on
you will also see it in the `kozos\be\allapot.json` file and on Telegram.)

**If you see this:** `THE GUARD WILL NOT START — permission error:` — below it
is the folder with the problem, and what is wrong with it. The possible lines:

| What it may say | What it means | What to do |
| --- | --- | --- |
| `…has been given unauthorized write access: …` | someone can write the protected folder | run that folder's `icacls` command from Chapter 3 again |
| `…OWNER is not an allowed identity: …` | the owner could give itself the rights back | run the `/setowner` command from Chapter 3 again |
| `…owner cannot be determined…` | the query did not run | try again; if it persists, show it to Claude |
| `The owner cannot be queried (…)` **or** `The owner query returned an error/empty response…` | **this is a PROGRAM BUG, not a configuration error** — the guard could not run the owner-query command | **re-running the `icacls` commands will NOT help.** Show this line to Claude |
| `…folder is missing the entry that denies DELETION…` | the `/deny` line was left out on the `kozos\ki` folder | run: `icacls "C:\BskyGuard\kozos\ki" /deny "agent-user:(DE)"` |
| `…found an unknown permission flag (…)…` | an unusual, but perhaps harmless entry | **show this line to Claude** — I cannot know in advance what it is |
| `missing folder: …` | one of the folders was not created | run the `New-Item` command from Chapter 3 again |

Now the full run, but **in a way that nothing goes out**:

```powershell
runas /user:bsky-guard "cmd /k C:\Python314\python.exe C:\BskyGuard\program\or.py --szaraz --egyszer --gyoker C:\BskyGuard"
```

**If it worked:** it prints how many incoming items it found, and if there is
a draft in the `C:\BskyGuard\kozos\ki\` folder, it prints the **full preview**
it would send on Telegram — but **it sends nothing and does not post**.

**If you see this:** `OPERATIONAL OBSTACLE: network error at the login step …` —
the Bluesky login failed. Look at the `bsky_kulcs.env` file: is the handle
right? Is the app password right? (If the error message says `HTTP 401`, the
password is wrong.)

**If you see this:** `OPERATIONAL OBSTACLE: paging through our own feed did not
reach the end of the 24-hour window` — the guard could not look through the
past day's posts, so out of caution it does not post. This usually goes away
by itself in the next round.

---

# Chapter 8 — The scheduled task

## Why this also happens in the administrator account's own session

In this chapter you have to type the password of the `bsky-guard` account. The
same applies to it as to the secrets in Chapter 6: **with the password an
attacker does not need the guard** — with the `runas` command they can run
anything under the guard's account, reach the `titok\` folder, and the whole
approval chain becomes meaningless.

So:

1. **Switch to the administrator account** (the same way as in step 6/a).
   With option A), at least close the programs that run Claude.
2. Run the commands below **there**, in an ADMINISTRATOR window.
3. At the end, sign back in to the `agent-user` account.

## 8/a — Creating the task

**IN THE ADMINISTRATOR ACCOUNT'S OWN SESSION**, in an ADMINISTRATOR window.
**Do NOT paste it in one go!** The `Read-Host` line would swallow the NEXT
pasted line as the password (you would get a "The user name or password is
incorrect" error). In two parts: first the first five lines (up to and
including the `Read-Host` line), type the password, Enter — and only AFTER
that the remaining five lines.

```powershell
$akcio = New-ScheduledTaskAction -Execute "C:\Python314\python.exe" -Argument '"C:\BskyGuard\program\or.py" --gyoker "C:\BskyGuard"' -WorkingDirectory "C:\BskyGuard\program"
$inditas = New-ScheduledTaskTrigger -AtStartup
$ismetles = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$beallitas = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
$titkos = Read-Host "Password of the bsky-guard account" -AsSecureString
$mutato = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($titkos)
$nyilt = [Runtime.InteropServices.Marshal]::PtrToStringAuto($mutato)
Register-ScheduledTask -TaskName "BskyGuard" -Action $akcio -Trigger $inditas,$ismetles -Settings $beallitas -User "bsky-guard" -Password $nyilt -RunLevel Limited
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($mutato)
Remove-Variable nyilt, titkos, mutato
```

> **The `-AsSecureString` switch cannot be left out.** Without it the typed
> password **appears as readable text in the console window** — and Windows
> does not protect against screenshots. With it, only asterisks appear where
> you type.
> The `ZeroFreeBSTR` line **zeroes and frees** the memory area that
> `SecureStringToBSTR` allocated — without it the password would have stayed
> in the process memory. And `Remove-Variable` deletes the variables.
> (Even this is not perfect: the .NET string in `$nyilt` is immutable, and only
> the garbage collector cleans it up in its own time. In the administrator
> account's own, short-lived session this is an acceptable residual risk — but
> **close** the window after the command.)
>
> **What you should never use instead:** `schtasks /create … /RP <password>`.
> With that, the password ends up on the **command line**, and from there in
> the PowerShell history file
> (`…\PowerShell\PSReadLine\ConsoleHost_history.txt`), and, if command-line
> logging is enabled, in event log 4688 as well. Input to `Read-Host`, by
> contrast, **does not go into the history file**, because that only saves
> submitted command lines.
>
> **And do not copy the password to the clipboard.** A program running under
> the `agent-user` account can read the clipboard contents too; and Windows
> clipboard history may even sync it to the cloud. Type it in.

Giving `-Password` means that **it runs even when the user is not signed in**;
and `MultipleInstances IgnoreNew` means that **no second instance starts** if
one is already running.

**If it worked, you see this:** a table with `TaskName: BskyGuard`,
`State: Ready`.

Start it now:

```powershell
Start-ScheduledTask -TaskName "BskyGuard"
Start-Sleep -Seconds 20
Get-ScheduledTask -TaskName "BskyGuard" | Get-ScheduledTaskInfo | Format-List TaskName,LastRunTime,LastTaskResult,NumberOfMissedRuns
```

**If it worked:** `LastTaskResult : 267009` (this means: *it is running right
now*).
And the best proof — in a NORMAL window:

```powershell
Get-Content "C:\BskyGuard\kozos\be\allapot.json"
```

You see a JSON with an `"or_allapot": "fut"` line (`fut` = running) and a fresh
timestamp.

If everything is fine, **sign back in to the `agent-user` account** — Chapter 8
is done.

**If it did not work:**

- `LastTaskResult` = 0x8007052E (PowerShell prints it in decimal) → wrong
  password. Run the `Register-ScheduledTask` block again (starting from the
  `Read-Host` line).
- `LastTaskResult : 3` → the guard stopped with a permission error; look at the
  `C:\BskyGuard\kozos\be\allapot.json` file, the reason is in it.
- **"Logon failure: the user has not been granted the requested logon type"**
  (the account has no right to log on as a batch job) → see below.

## If the "Log on as a batch job" right is missing

`Register-ScheduledTask` usually grants this right by itself. If it does not,
it has to be done by hand. **`secpol.msc` (Local Security Policy) does NOT
EXIST on Windows 11 Home**, so the usual recipe cannot be used on this
machine. The `secedit` command works instead.

In an **ADMINISTRATOR** window, step by step:

```powershell
$mappa = "C:\BskyGuard_hazirend"
New-Item -ItemType Directory -Force -Path $mappa | Out-Null
secedit /export /cfg "$mappa\hazirend.inf" /areas USER_RIGHTS
Copy-Item "$mappa\hazirend.inf" "$mappa\hazirend_eredeti.inf"
(Get-LocalUser bsky-guard).SID.Value
```

> **The `Copy-Item` line is the way back.** `secedit /configure … USER_RIGHTS`
> **replaces** the membership of **every** right listed in the `.inf` file with
> the file's content. If you accidentally delete or mistype a line, that right
> is silently lost or widened, and you get no feedback about it. With the
> untouched copy (`hazirend_eredeti.inf` = original policy) you can restore it
> with one command — see below.

The last command prints a long identifier starting with `S-1-5-21-`. **Copy
the WHOLE line** — in the examples below it goes in place of
`<YOUR-SID-HERE>`, literally, as you see it on the screen.

```powershell
notepad "$mappa\hazirend.inf"
```

> **DO NOT TOUCH THE ENCODING.** `secedit` created this file in **UTF-16
> (Unicode)** encoding. Notepad recognizes it by itself and saves it that way
> too — in the save dialog **do not switch it to UTF-8**. (The UTF-8 rule from
> Chapter 6 applies to the `.env` files, **not** to this file.) If you save it
> as UTF-8, `secedit /configure` cannot parse it.

Find the line starting with `SeBatchLogonRight`. If it is there, at the end of
the line, separated by a comma, add the `*` sign and the identifier. The line
then looks like this (the beginning of the example may differ on your machine):

```
SeBatchLogonRight = *S-1-5-32-544,*S-1-5-32-551,*<YOUR-SID-HERE>
```

If **there is no such line**, add a new one in the `[Privilege Rights]`
section:

```
SeBatchLogonRight = *<YOUR-SID-HERE>
```

> In place of `<YOUR-SID-HERE>` goes the full identifier starting with
> `S-1-5-21-`, **without the angle brackets**, directly after the `*` sign.

Save it (Ctrl+S), close it. Then:

```powershell
secedit /configure /db "$mappa\secedit.sdb" /cfg "$mappa\hazirend.inf" /areas USER_RIGHTS
```

**If it worked:** `The task has completed successfully` (localized on
non-English Windows).

**FINAL CHECK — is it really there:**

```powershell
secedit /export /cfg "$mappa\hazirend_ellenorzes.inf" /areas USER_RIGHTS
Select-String -Path "$mappa\hazirend_ellenorzes.inf" -Pattern "SeBatchLogonRight"
```

Your identifier must be in the resulting line. If it is not, the edit was not
saved, or the encoding got corrupted — start again from the
`hazirend_eredeti.inf` copy.

**IF SOMETHING WENT WRONG — the way back:**

```powershell
secedit /configure /db "$mappa\secedit_vissza.sdb" /cfg "$mappa\hazirend_eredeti.inf" /areas USER_RIGHTS
```

**If `secedit` gives an error**, the informative place is:
`C:\Windows\security\logs\scesrv.log` — its last few lines tell you which
line is the problem.

Then run the `Register-ScheduledTask` block again.

> **Only delete** the contents of `$mappa` once the scheduled task is already
> working — until then `hazirend_eredeti.inf` is your safety net.

---

# Chapter 9 — The first real test

1. Claude writes a draft into the `C:\BskyGuard\kozos\ki\` folder.
2. Within a few minutes at most, **a Telegram message arrives from the new
   bot**: the full outgoing text in a frame, and the very last line:
   `Jóváhagyás: válaszolj ERRE az üzenetre: IGEN · Elvetés: NEM`
   (the approval line stays in Hungarian on purpose — it reads "Approval: reply
   to THIS message: IGEN (yes) · Rejection: NEM (no)"; the guard's filters match
   exactly this wording)
3. **Reply to that message** (long-press it → *Reply*), and type:
   `IGEN`
4. If everything is fine, it writes back: `BskyGuard: SENT.` and the link to
   the post.

> **Only an exact `IGEN` text sent as a reply counts.** "igen!", "IGEN 123", a
> forwarded message and an edited message do **not**. This is intentional.
> If you do not write it as a reply, nothing happens — just try again.
> (`IGEN` = yes, `NEM` = no; these are the literal approval words the guard
> expects.)
>
> A preview is valid for **48 hours**; after that it expires.

## What to check on the preview

- **The text in the frame is exactly what goes out.** No rewriting, no
  shortening. If the guard would have to change something for display, it
  rejects the draft instead — so what you see is what the world gets.
- **For a reply, you see the target post in a separate block**: whom we are
  replying to.
- **And in a separate section also WHICH THREAD the post lands in** — this is
  under the `THE POST GOES INTO THIS THREAD:` heading. This matters because on
  Bluesky the participants of the thread also get notified about our reply.
  Starting from the target post, the guard **walks up the entire
  conversation**, fetching every step live from Bluesky, and it can print
  three kinds of things:
  - *"I checked the whole ancestor chain: there is NO foreign third party in
    it."* — this is the calm case: only we and the person who addressed us are
    in the conversation;
  - **`WARNING: A FOREIGN THIRD PARTY ALSO TAKES PART IN THIS THREAD.`** —
    in that case their names and identifiers are below it. **This is not an
    error**; on Bluesky it is common for someone to address us inside someone
    else's conversation. But our reply lands in their conversation, and they
    may get a notification about it too. **Read who they are before you write
    IGEN.**
  - **`WARNING: THE ACTUAL TOP OF THE THREAD IS NOT THE SAME AS WHAT THE TARGET
    POST MARKS AS ITS ROOT.`** — this is already suspicious: the post of the
    person addressing us claims something about itself that is not true. In
    that case the guard shows both. **Be very strict here.**
- **If you see in capitals: `UNSOLICITED REPLY`**, then either we were not
  addressed (we go there based on the allowlist), or an unrelated third party
  also takes part in the thread. Be stricter in that case. At most **two
  unsolicited replies a day** can go out.
- **If the person who addressed us changes their handle after your approval**,
  the guard does NOT send the post: you get a message that it stopped. The
  anchor is the DID, but you recognize whom we are replying to by the handle —
  so this is correct.

---

# Chapter 10 — Updating (when Claude improves the code)

**Only an administrator** can write into the `program\` folder — this is
intentional.

## 10/a — First look at WHAT changed

In a **NORMAL** window:

```powershell
cd "{HOME}\Desktop\Claude\bluesky\or"
python ujjlenyomat.py --csak-telepitendo --osszevet "{HOME}\Desktop\bskyguard_fingerprint.txt"
```

This tells you **which file changed, which is new, and which disappeared**.
The `--csak-telepitendo` switch and the full path are needed for the same
reason as in step 5/a — and must **always be used the same way**, otherwise
the comparison gives a false alarm.

Compare it with what we talked about. If a file also changed that was not
discussed — **do not copy it**, ask about it. And compare the `OSSZESITETT`
line with the photo you took at the very first installation: that tells you
whether the *earlier* list itself is authentic.

> A reminder, honestly: if someone can rewrite both the code and the list,
> this comparison proves nothing. The only thing the computer cannot reach is
> the photo on your phone.

## 10/b — Copying

In an **ADMINISTRATOR** window:

```powershell
Disable-ScheduledTask -TaskName "BskyGuard"
Stop-ScheduledTask -TaskName "BskyGuard"
Copy-Item "{HOME}\Desktop\Claude\bluesky\or\*.py" -Destination "C:\BskyGuard\program\" -Force
runas /user:bsky-guard "cmd /k C:\Python314\python.exe C:\BskyGuard\program\or.py --szaraz --egyszer --gyoker C:\BskyGuard"
Enable-ScheduledTask -TaskName "BskyGuard"
Start-ScheduledTask -TaskName "BskyGuard"
```

> **`Disable-ScheduledTask` cannot be left out, and it must come BEFORE
> `Stop`.** `Stop` only stops the instance that is **running right now**; the
> five-minute repeating trigger stays in effect. So during `Copy-Item` a new
> instance could start — **with half a code base**. `Disable` turns off the
> trigger too, and `Enable` turns it back on once the copying is done.

**We only copy the `.py` files.** `szivesen_latnak.json` (allowlist) and
`tilto_mintak.json` (block filter) are **intentionally left out**: you edited
them, and we do not want an update to silently overwrite them. If the
allowlist needs to be extended, that should be a **separate, explicit
decision** — step 10/c.

**If it worked:** the dry run completes without errors, and among its first
lines there is: `DRY MODE: the state is in the sandbox (C:\BskyGuard\allapot_szaraz)…`.

> **Since 2026-09-22 the dry run is SAFE.** Previously it used the same
> folders as the live guard: if a draft was waiting in the drop folder, the
> test TOOK it (removed it from the drop folder, and wrote a preview with a
> made-up Telegram ID into the live state) — after that, the live guard waited
> two days for the approval of a preview that was never sent. Now the dry run,
> AFTER the permission check, switches to the `C:\BskyGuard\allapot_szaraz\`
> folder: it only takes a COPY from the drop folder, and the live `allapot\`,
> `kozos\be\allapot.json` and the mailbox remain untouched. ⚠️ The guard CANNOT
> create the `allapot_szaraz` folder itself (bsky-guard only has read access on
> the root; this turned up live on the morning of 2026-10-02:
> `PermissionError … allapot_szaraz`). ONCE, in an administrator PowerShell:
>     New-Item -ItemType Directory -Force "C:\BskyGuard\allapot_szaraz"
>     icacls "C:\BskyGuard\allapot_szaraz" /grant "PC\bsky-guard:(OI)(CI)M"
> After that the folder stays; if you delete it, the same two lines are needed
> again.
> It follows that the dry run does NOT update the timestamp in
> `kozos\be\allapot.json` — the first round of the restarted live guard will do
> that (within 5 minutes).

Update the fingerprint list too (and take a new photo of it):

```powershell
python "{HOME}\Desktop\Claude\bluesky\or\ujjlenyomat.py" "{HOME}\Desktop\Claude\bluesky\or" --csak-telepitendo --kiir "{HOME}\Desktop\bskyguard_fingerprint.txt"
```

> **The list is made from the SOURCE FOLDER, not from the `program\` folder.**
> That way the next 10/a comparison — which also looks at the source folder —
> works correctly. You can check separately after the update that the
> `program\` folder matches, with the same list:
>
> ```powershell
> python "C:\BskyGuard\program\ujjlenyomat.py" "C:\BskyGuard\program" --csak-telepitendo --osszevet "{HOME}\Desktop\bskyguard_fingerprint.txt"
> ```
>
> This must say `EVERYTHING MATCHES`.

## 10/c — Changing the allowlist or the block filter (a SEPARATE step)

Only do this if you **deliberately** want to change to whom the guard may send
a reply without being addressed, or what the filter blocks.

In an **ADMINISTRATOR** window, one at a time, and in both cases **look at the
content before you save**:

```powershell
notepad "C:\BskyGuard\program\szivesen_latnak.json"
```

```powershell
notepad "C:\BskyGuard\program\tilto_mintak.json"
```

> The allowlist matches **only on DID** (`did:plc:…`), never on handle — a
> handle can be changed, a DID cannot. Once a DID is on the list, the guard may
> send a reply to that account even if it did not address us (at most two a
> day, and you have to approve every single one separately).

If you break the JSON, the guard **does not stop**: it carries on with an
empty allowlist (that is, no unsolicited reply goes out), and writes this into
the `kozos\be\allapot.json` file.

## 10/d — The new actions: FOLLOW and LIKE (2026-10-02)

At your request the guard can do two new kinds of things: **follow** a profile
and **like** a post. (Bluesky has no emoji reactions, only likes.)
These go through **exactly the same gate** as posts: Claude drops a draft, the
guard checks the target on the network, checks the cap, and **sends you a
preview** — nothing happens until you reply to it with `IGEN`. `NEM` rejects
it the same way.

**What you see on your phone** — for a follow:

```
FOLLOW: @someone.bsky.social
No outgoing text: the account only starts following this profile — …
THE PROFILE (fetched live from Bluesky):
  handle, did, name, followers / posts, and the profile description in a frame
in the last 24 hours: 1 follow (cap: 5)
```

for a like:

```
LIKE: post by @author: "the first 80 characters of the post…"
THE POST (fetched live from Bluesky):
  author, did, uri, and the text of the post in a frame
in the last 24 hours: 0 likes (cap: 10)
```

Below it, if there is one, Claude's note in the usual frame, and at the very
bottom the familiar approval line. After successful execution the guard writes
back: `BskyGuard: FOLLOWED.` or `BskyGuard: LIKED.`, with a reference to the
created record.

**The caps** (in `program\beallitasok.py`, rolling 24 hours, counted from
Bluesky's own records):

| Key | Value | Meaning |
|---|---|---|
| `KOVETES_NAPI_MAX` | 5 | at most 5 new follows / 24 hours |
| `KEDVELES_NAPI_MAX` | 10 | at most 10 likes / 24 hours |
| `FUGGO_MAX` | 3 | at most 3 previews awaiting approval at the same time — **shared** with posts |

If the cap is full, the draft **waits**, and later goes ahead by itself.

**What bounces back by itself** (without a preview, with the reason in
`kozos\be\allapot.json`): a non-existent profile or post; someone we already
follow, something we already liked; our own account / post; a block; and if a
preview is already waiting for the same target. If something changes between
your approval and the execution (we are already following in the meantime, the
content of the post changed, or the target **renamed itself**), the guard
**does not execute it**; in case of a rename it also tells you on the bot.

**The draft templates for Claude** (into the `kozos\ki\` folder):

```json
{"verzio": 1, "tipus": "kovetes", "cel_handle": "someone.bsky.social", "megjegyzes_tulajnak": "short reason"}
```

```json
{"verzio": 1, "tipus": "kedveles", "cel_uri": "at://did:plc:…/app.bsky.feed.post/…", "megjegyzes_tulajnak": "short reason"}
```

(`tipus`: `kovetes` = follow, `kedveles` = like; `cel_handle` / `cel_did` /
`cel_uri` = the target; `megjegyzes_tulajnak` = note for the owner. The keys and
type values are read by the code — keep them as they are.)

For a follow, `cel_did` can be given instead of `cel_handle` — exactly one of
the two. The details: `README_or.md`, "Follow and like".

**Installation**: following the 10/b recipe (the follow/like module ships with the
package; there is no separate file list).

---

# Chapter 11 — Stopping, revoking, uninstalling

**Quick stop** (ADMINISTRATOR):

```powershell
Stop-ScheduledTask -TaskName "BskyGuard"
Disable-ScheduledTask -TaskName "BskyGuard"
```

**Emergency brake — stopping posting immediately** (no computer needed):
on your phone, Bluesky → *Settings → Privacy and security → App passwords →
Revoke*.

**Full uninstall** (ADMINISTRATOR):

```powershell
Unregister-ScheduledTask -TaskName "BskyGuard" -Confirm:$false
Remove-Item -Recurse -Force "C:\BskyGuard"
Remove-LocalUser -Name "bsky-guard"
```

Do not forget to revoke the app password on Bluesky, and at BotFather the
bot's token (`/revoke`), and to delete the bot (`/deletebot`).

## The command-line switches — all six

| switch | what it does |
| --- | --- |
| `--gyoker <path>` | which folder tree to work with (always `C:\BskyGuard`) |
| `--ellenoriz` | only checks the permissions, then exits — does not post |
| `--ki-vagyok` | prints the ID of the sender of the first incoming message to the CONSOLE (step 6/e) |
| `--szaraz` | full run, but the Telegram message goes to the console, and NO post goes out either; the state is in a separate sandbox (`allapot_szaraz\`), and only a copy is taken from the drop folder |
| `--egyszer` | runs a single round, then exits (instead of running continuously) |
| `--idovonal N` | in the incoming run, also reads the last N posts of followed accounts into `inbox.jsonl`. Default: 0, that is, notifications only. Has no effect on outgoing posts. |

If you chose option B) and want to restore the machine:

```powershell
Add-LocalGroupMember -SID "S-1-5-32-544" -Member "agent-user"
```

---

# If you get stuck

The two most informative places:

```powershell
Get-Content "C:\BskyGuard\kozos\be\allapot.json"          # what the guard is doing now
Get-Content "C:\BskyGuard\allapot\naplo.jsonl" -Tail 20   # what happened
```

You can read both **from a NORMAL window** too: the `agent-user` account was
given read access to the `allapot\` folder (as per Chapter 3), and this folder
contains no secrets.

There is **no secret of any kind** in them — you can safely show them to
Claude. No token, no password, no Telegram IDs, and no text of outgoing posts
(only their hashes) goes into them.

What you should **never show** or copy out: the content of the two files in
the `C:\BskyGuard\titok\` folder.
