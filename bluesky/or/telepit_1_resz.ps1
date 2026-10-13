#requires -Version 5.1
<#
    telepit_1_resz.ps1 - BskyGuard installation, PART ONE

    What it covers: chapters 2, 3, 4 and 5 of INSTALL.md,
    following VARIANT A) (the `agent-user` account STAYS an administrator,
    chapter 1 is skipped).

    What it does NOT do: it does not touch the secrets (chapter 6), does not create
    a scheduled task (chapter 8), does not post, does not make network calls.
    It writes NOT A SINGLE file into the C:\BskyGuard\titok folder.

    How to run: in an ADMINISTRATOR PowerShell window:
      powershell -NoProfile -ExecutionPolicy Bypass -File "<path to this folder>\telepit_1_resz.ps1"

    Before running, set the Windows user that runs the agent (the untrusted side):
      $env:BSKY_AGENT_USER = "<agent windows user>"

    Also runs under Windows PowerShell 5.1 (it contains no && , ?: , ??).
#>

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Helper functions (output, running icacls). Not part of any chapter.
# ---------------------------------------------------------------------------

function Cim([string]$szoveg) {
    Write-Host ""
    Write-Host ("=== " + $szoveg + " ") -ForegroundColor Cyan
}

function Ok([string]$szoveg) {
    Write-Host ("  OK    " + $szoveg) -ForegroundColor Green
}

function Info([string]$szoveg) {
    Write-Host ("        " + $szoveg) -ForegroundColor Gray
}

function Megall([string]$szoveg) {
    Write-Host ("  ERROR " + $szoveg) -ForegroundColor Red
    throw $szoveg
}

# icacls is always called this way: we capture the output and check the exit code.
function Icacls-Futtat {
    param([string[]]$Argumentumok, [string]$Mit)
    # stderr is INTENTIONALLY not redirected (2>&1): Windows PowerShell 5.1
    # would turn it into a "NativeCommandError" exception. The exit code is what counts.
    $kimenet = & icacls.exe @Argumentumok
    if ($LASTEXITCODE -ne 0) {
        Write-Host ($kimenet | Out-String) -ForegroundColor Red
        Megall ($Mit + " - icacls returned an error (code: " + $LASTEXITCODE + ").")
    }
    return ($kimenet | Out-String)
}

# Parsing the output of `icacls <folder>` - with the same logic as the
# `bejegyzesek_ertelmezese` function in jogok.py. Returns: identity, rights, deny.
function Acl-Sorok {
    param([string]$Utvonal, [string]$Kimenet)
    $eredmeny = @()
    foreach ($nyers in ($Kimenet -split "`r?`n")) {
        $sor = $nyers.Trim()
        if ($sor -eq "") { continue }
        if ($sor -like "Successfully*") { continue }
        if ($sor -like "Sikeresen*") { continue }   # Hungarian-language data: the icacls summary line on a Hungarian Windows; add your Windows language's equivalent the same way
        if ($sor.ToLower().StartsWith($Utvonal.ToLower())) {
            $sor = $sor.Substring($Utvonal.Length).Trim()
            if ($sor -eq "") { continue }
        }
        $egyezes = [regex]::Match($sor, '^(?<id>.+?):(?<r>(\([^()]*\))+)\s*$')
        if (-not $egyezes.Success) { continue }
        $jogok = @()
        $tiltas = $false
        foreach ($m in [regex]::Matches($egyezes.Groups['r'].Value, '\(([^()]*)\)')) {
            foreach ($token in ($m.Groups[1].Value -split ',')) {
                $t = $token.Trim().ToUpper()
                if ($t -eq "") { continue }
                if ($t -eq "DENY") { $tiltas = $true; continue }
                if (@("I","OI","CI","IO","NP") -contains $t) { continue }
                $jogok += $t
            }
        }
        $eredmeny += [PSCustomObject]@{
            Azonosito = $egyezes.Groups['id'].Value.Trim()
            Jogok     = $jogok
            Tiltas    = $tiltas
        }
    }
    return $eredmeny
}

# (the same normalization as in jogok.py `_normalizal_identitas`)
function Normalizal([string]$nev) {
    $n = ("" + $nev).Trim().Trim('"')
    if ($n.Contains("\")) { $n = $n.Substring($n.LastIndexOf("\") + 1) }
    $n = $n.ToLowerInvariant()
    return ([regex]::Replace($n, '[^a-z0-9_-]', ''))
}

# ---------------------------------------------------------------------------
# 0. PREREQUISITES - are we administrator, is PowerShell OK, what we will do
# (the guide's "Before you start" section + 0/A: variant A)
# ---------------------------------------------------------------------------

Cim "0. Prerequisites"

$azonossag = [Security.Principal.WindowsIdentity]::GetCurrent()
$szemely   = New-Object Security.Principal.WindowsPrincipal($azonossag)
if (-not $szemely.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "  ERROR This window is NOT an administrator window." -ForegroundColor Red
    Write-Host "        Start menu -> type: powershell -> RIGHT-CLICK Windows PowerShell" -ForegroundColor Red
    Write-Host "        -> Run as administrator. The title bar will say: Administrator." -ForegroundColor Red
    exit 1
}
Ok "Running in an administrator window."

if ($PSVersionTable.PSVersion.Major -lt 5) {
    Megall "PowerShell is too old. At least 5.1 is required."
}
Ok ("PowerShell version: " + $PSVersionTable.PSVersion.ToString())

$Gyoker   = "C:\BskyGuard"
$Forras   = $PSScriptRoot   # the folder this script lives in (source of the guard code)
$Lista    = Join-Path ([Environment]::GetFolderPath('Desktop')) "bskyguard_fingerprint.txt"
$PythonUt = "C:\Python314\python.exe"
$Gep      = $env:COMPUTERNAME

Write-Host ""
Write-Host "WHAT THIS SCRIPT WILL DO (chapters 2-5 of the guide, variant A):" -ForegroundColor Yellow
Write-Host "  Chapter 2 - creates the `"bsky-guard`" Windows user (NOT an administrator),"
Write-Host "              YOU type its password, twice, and it is not shown."
Write-Host "  Chapter 3 - creates the C:\BskyGuard folder tree and sets the permissions"
Write-Host "              and the owner (this is the point: you will not see the titok folder)."
Write-Host "  Chapter 4 - checks that C:\Python314 is OK."
Write-Host "  Chapter 5 - prints the code fingerprint (YOU MUST PHOTOGRAPH THIS),"
Write-Host "              then copies the code and checks that it stayed the same."
Write-Host "  At the end it runs the permission self-check."
Write-Host ""
Write-Host "WHAT IT DOES NOT DO: it does not write into the titok folder, does not create a scheduled task," -ForegroundColor Yellow
Write-Host "  does not ask for the Bluesky or Telegram secrets. Those are chapters 6-8, done by YOU." -ForegroundColor Yellow
Write-Host ""
Write-Host "If something goes wrong, the script STOPS with a red ERROR line. It does not continue blindly." -ForegroundColor Yellow
Write-Host "The script can be run several times: whatever is already done, it does not break." -ForegroundColor Yellow
Write-Host ""
Read-Host "Press ENTER to continue (or Ctrl+C to quit)" | Out-Null

# ---------------------------------------------------------------------------
# not blindly: we check that it exists, and print what we will work with.
# ---------------------------------------------------------------------------

Cim "0/B. Identifying the untrusted account"

# The untrusted Windows user (the one that runs the agent) comes from the environment;
# it must match beallitasok.NEM_MEGBIZHATO_FELHASZNALO (BSKY_AGENT_USER) in the guard's environment.
$NemMegbizhato = $env:BSKY_AGENT_USER
if (-not $NemMegbizhato) {
    Megall "BSKY_AGENT_USER is not set. Set it to the Windows user that runs the agent, then rerun."
}
try {
    $nmFiok = Get-LocalUser -Name $NemMegbizhato
} catch {
    Megall ("There is no local user named `"" + $NemMegbizhato + "`" on this machine. Check the BSKY_AGENT_USER value (it feeds beallitasok.NEM_MEGBIZHATO_FELHASZNALO too).")
}
Ok ("The untrusted account (the one running Claude): " + $nmFiok.Name)

$OrFelhasznalo = "bsky-guard"
$IdentNem = "{0}\{1}" -f $Gep, $NemMegbizhato
$IdentOr  = "{0}\{1}" -f $Gep, $OrFelhasznalo
Info ("Machine name: " + $Gep + " - in the icacls commands I refer to them like this: " + $IdentNem)

# A trial run of name resolution (according to the guide, this is where an
# icacls line can fail silently, and the folder would be left without permissions).
try {
    $proba = New-Object Security.Principal.NTAccount($Gep, $NemMegbizhato)
    $proba.Translate([Security.Principal.SecurityIdentifier]) | Out-Null
    Ok "The account name can be resolved (icacls will accept it)."
} catch {
    Megall ("Windows cannot resolve the name `"" + $IdentNem + "`". See INSTALL.md, chapter 3.")
}

# ---------------------------------------------------------------------------
# CHAPTER 2 - The guard's own Windows user
# ---------------------------------------------------------------------------

Cim "Chapter 2 - the bsky-guard user"

$vanMar = $null
try { $vanMar = Get-LocalUser -Name $OrFelhasznalo } catch { $vanMar = $null }

if ($vanMar -ne $null) {
    Write-Host ""
    Write-Host ("The `"" + $OrFelhasznalo + "`" account ALREADY EXISTS.") -ForegroundColor Yellow
    Write-Host "I will NOT overwrite its password (if you have set it once, that one is right)." -ForegroundColor Yellow
    $valasz = Read-Host "Continue with the existing account? (type y = yes, anything else = stop)"
    if ($valasz.Trim().ToLower() -ne "y") {
        Megall "Stopped at your request. The account was left untouched."
    }
    Ok "Continuing with the existing account - its password is unchanged."
} else {
    Write-Host ""
    Write-Host "NOW I NEED THE PASSWORD. Read these four lines:" -ForegroundColor Yellow
    Write-Host "  * YOU make up the password. At least 12 characters, lower- and upper-case letters, a digit." -ForegroundColor Yellow
    Write-Host "  * WRITE IT DOWN ON PAPER. You will need it once more in chapter 8 of the installation." -ForegroundColor Yellow
    Write-Host "  * Do NOT tell it to Claude, and do not copy it to the clipboard - type it in." -ForegroundColor Yellow
    Write-Host "  * NOTHING will be shown while you type. This is normal. I ask for it twice." -ForegroundColor Yellow
    Write-Host ""

    $jelszo1 = $null
    $jelszo2 = $null
    $bstr1 = [IntPtr]::Zero
    $bstr2 = [IntPtr]::Zero
    try {
        $jelszo1 = Read-Host "Password for the bsky-guard account" -AsSecureString
        $jelszo2 = Read-Host "The same once more (confirmation)" -AsSecureString

        if ($jelszo1.Length -eq 0) { Megall "An empty password is not accepted. Restart the script." }

        # Compare the two SecureStrings OUTSIDE managed memory: read them as BSTR
        # character by character, so that no plain-text string is made from them.
        $bstr1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($jelszo1)
        $bstr2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($jelszo2)
        $azonos = $true
        $i = 0
        while ($true) {
            $k1 = [Runtime.InteropServices.Marshal]::ReadInt16($bstr1, $i)
            $k2 = [Runtime.InteropServices.Marshal]::ReadInt16($bstr2, $i)
            if ($k1 -ne $k2) { $azonos = $false; break }
            if ($k1 -eq 0) { break }
            $i = $i + 2
        }
    } finally {
        if ($bstr1 -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr1) }
        if ($bstr2 -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr2) }
        if ($jelszo2 -ne $null) { $jelszo2.Dispose() }
    }

    if (-not $azonos) {
        if ($jelszo1 -ne $null) { $jelszo1.Dispose() }
        Megall "The two passwords do NOT match. Nothing was created - restart the script."
    }
    Ok "The two passwords match."

    try {
        New-LocalUser -Name $OrFelhasznalo `
                      -FullName "BskyGuard service" `
                      -Description "The Bluesky guard runs under this account" `
                      -Password $jelszo1 | Out-Null
    } finally {
        $jelszo1.Dispose()
    }
    Ok "The bsky-guard account was created."
}

# These lines are idempotent: they can safely run on an existing account too.
Set-LocalUser -Name $OrFelhasznalo -PasswordNeverExpires $true
Ok "Its password does not expire (PasswordNeverExpires)."

# The group query fails on some machines (because of unknown SIDs); that is why
# there is a try/catch, and why an "already a member" error is accepted as success.
$tagjaMar = $false
try {
    foreach ($tag in (Get-LocalGroupMember -SID "S-1-5-32-545")) {
        if ((Normalizal $tag.Name) -eq (Normalizal $OrFelhasznalo)) { $tagjaMar = $true }
    }
} catch {
    Info "Membership of the Users group cannot be queried - trying to add it."
}
if (-not $tagjaMar) {
    try {
        Add-LocalGroupMember -SID "S-1-5-32-545" -Member $OrFelhasznalo
    } catch {
        if ($_.Exception.GetType().Name -ne "MemberExistsException") { throw }
    }
}
Ok "Member of the Users group (S-1-5-32-545)."

# Check: it must NOT be an administrator.
$adminTag = $false
$adminKerdezheto = $true
try {
    foreach ($tag in (Get-LocalGroupMember -SID "S-1-5-32-544")) {
        if ((Normalizal $tag.Name) -eq (Normalizal $OrFelhasznalo)) { $adminTag = $true }
    }
} catch {
    $adminKerdezheto = $false
}
if ($adminTag) {
    Megall "The bsky-guard account is a member of the Administrators group - that is not right. Remove it: Remove-LocalGroupMember -SID `"S-1-5-32-544`" -Member `"bsky-guard`""
}
if ($adminKerdezheto) {
    Ok "NOT a member of the Administrators group (S-1-5-32-544) - this is correct."
} else {
    Info "Membership of the Administrators group cannot be queried - check it by hand: Get-LocalGroupMember -SID `"S-1-5-32-544`""
}

$fiok = Get-LocalUser -Name $OrFelhasznalo
Info ("Name: " + $fiok.Name + " | Enabled: " + $fiok.Enabled + " | Password expires: " + $fiok.PasswordExpires)

# ---------------------------------------------------------------------------
# CHAPTER 3 - Folders and permissions
# ---------------------------------------------------------------------------

Cim "Chapter 3 - folder tree"

$Mappak = @(
    "C:\BskyGuard",
    "C:\BskyGuard\titok",
    "C:\BskyGuard\program",
    "C:\BskyGuard\allapot",
    "C:\BskyGuard\allapot\varo",
    "C:\BskyGuard\allapot\feldolgozott",
    "C:\BskyGuard\allapot\zart",
    "C:\BskyGuard\kozos",
    "C:\BskyGuard\kozos\be",
    "C:\BskyGuard\kozos\ki"
)

New-Item -ItemType Directory -Force -Path $Mappak | Out-Null
Ok ("All " + $Mappak.Count + " folders are in place.")

# --- 3/b. The permissions (icacls /inheritance:r /grant:r) ------------------
# Inheritance turned off + EXACTLY these rights. Groups are given by SID
# (*S-1-5-18 = SYSTEM, *S-1-5-32-544 = Administrators), because their names
# differ on a localized Windows. In the varo / feldolgozott / zart folders the
# untrusted user's name DOES NOT APPEAR AT ALL (jogok.py: the "semmi" (none) rule).

Cim "Chapter 3 - permissions"

$JogRend = @(
    @{ Ut = "C:\BskyGuard";                      Or = "RX"; Nem = "RX" },
    @{ Ut = "C:\BskyGuard\titok";                Or = "RX"; Nem = $null },
    @{ Ut = "C:\BskyGuard\program";              Or = "RX"; Nem = "RX" },
    @{ Ut = "C:\BskyGuard\allapot";              Or = "M";  Nem = "RX" },
    @{ Ut = "C:\BskyGuard\allapot\varo";         Or = "M";  Nem = $null },
    @{ Ut = "C:\BskyGuard\allapot\feldolgozott"; Or = "M";  Nem = $null },
    @{ Ut = "C:\BskyGuard\allapot\zart";         Or = "M";  Nem = $null },
    @{ Ut = "C:\BskyGuard\kozos";                Or = "M";  Nem = "RX" },
    @{ Ut = "C:\BskyGuard\kozos\be";             Or = "M";  Nem = "RX" },
    @{ Ut = "C:\BskyGuard\kozos\ki";             Or = "M";  Nem = "M"  }
)

foreach ($elem in $JogRend) {
    $argok = @($elem.Ut, "/inheritance:r", "/grant:r",
               "*S-1-5-18:(OI)(CI)(F)",
               "*S-1-5-32-544:(OI)(CI)(F)",
               ("{0}:(OI)(CI)({1})" -f $IdentOr, $elem.Or))
    if ($elem.Nem -ne $null) {
        $argok += ("{0}:(OI)(CI)({1})" -f $IdentNem, $elem.Nem)
    }
    Icacls-Futtat -Argumentumok $argok -Mit ("permissions: " + $elem.Ut) | Out-Null
    Ok ($elem.Ut)
}

# --- 3/c. DELETE deny on `kozos\ki` ------------------------------------------
# /grant:r overwrites the whole list, so /deny ALWAYS comes after it.
# It has no (OI)(CI): it applies only to the folder itself; the untrusted user
# can still create and delete the files inside it.
# jogok.py `_torles_tiltva_e` REQUIRES this line.

Icacls-Futtat -Argumentumok @("C:\BskyGuard\kozos\ki", "/deny", ("{0}:(DE)" -f $IdentNem)) `
              -Mit "delete deny on the kozos\ki folder" | Out-Null
Ok "C:\BskyGuard\kozos\ki - the delete-denying (DENY)(DE) line has been added."

# --- 3/d. The OWNER ---------------------------------------------------------
# The owner has an implicit right to give itself access back, so
# jogok.py checks for every folder that the owner is an allowed identity.

Cim "Chapter 3 - owner"

$AdminSid  = New-Object Security.Principal.SecurityIdentifier("S-1-5-32-544")
$AdminNev  = $AdminSid.Translate([Security.Principal.NTAccount]).Value
Info ("The name of the Administrators group on this machine: " + $AdminNev)

foreach ($ut in $Mappak) {
    $kimenet = & icacls.exe $ut "/setowner" "*S-1-5-32-544"
    if ($LASTEXITCODE -ne 0) {
        # The guide mentions it: /setowner does not accept the *SID form everywhere.
        # In that case we try BY NAME (the name was translated from the SID, so
        # it is right on any Windows language).
        Info ("The *SID form did not work on the folder " + $ut + " - trying by name.")
        $kimenet = & icacls.exe $ut "/setowner" $AdminNev
        if ($LASTEXITCODE -ne 0) {
            Write-Host ($kimenet | Out-String) -ForegroundColor Red
            Megall ("Could not set the owner: " + $ut)
        }
    }
}

$tulajHiba = $false
foreach ($ut in $Mappak) {
    $tulajSid = (Get-Acl -LiteralPath $ut).GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($tulajSid -ne "S-1-5-32-544") {
        Write-Host ("  ERROR " + $ut + " owner: " +
                    (Get-Acl -LiteralPath $ut).Owner) -ForegroundColor Red
        $tulajHiba = $true
    }
}
if ($tulajHiba) {
    Megall "Some folder is not owned by the Administrators group. The guard will not start without this - see INSTALL.md, chapter 3 (`"Setting the OWNER`")."
}
Ok ("Owner of all " + $Mappak.Count + " folders: " + $AdminNev)

# --- 3/e. Check 1 - what does icacls say? -----------------------------------
# We look at the same thing as jogok.py: the untrusted user must NOT appear on
# the titok folder, and the DENY line must be present on the ki folder.

Cim "Chapter 3 - Check 1"

$titokKimenet = Icacls-Futtat -Argumentumok @("C:\BskyGuard\titok") -Mit "querying the titok folder"
Write-Host ($titokKimenet.TrimEnd())
$titokSorok = Acl-Sorok -Utvonal "C:\BskyGuard\titok" -Kimenet $titokKimenet
foreach ($s in $titokSorok) {
    if ((Normalizal $s.Azonosito) -eq (Normalizal $NemMegbizhato)) {
        Megall "The untrusted user appears on the titok folder - this must not happen."
    }
}
Ok "The untrusted user does NOT appear on the titok folder."

foreach ($zart in @("C:\BskyGuard\allapot\varo", "C:\BskyGuard\allapot\feldolgozott", "C:\BskyGuard\allapot\zart")) {
    $k = Icacls-Futtat -Argumentumok @($zart) -Mit ("query: " + $zart)
    foreach ($s in (Acl-Sorok -Utvonal $zart -Kimenet $k)) {
        if ((Normalizal $s.Azonosito) -eq (Normalizal $NemMegbizhato)) {
            Megall ("The untrusted user appears on the folder " + $zart + " - it must have NO rights at all on these folders.")
        }
    }
    Ok ($zart + " - closed, as it should be.")
}

$kiKimenet = Icacls-Futtat -Argumentumok @("C:\BskyGuard\kozos\ki") -Mit "querying the kozos\ki folder"
Write-Host ($kiKimenet.TrimEnd())
$vanDeny  = $false
$vanGrant = $false
foreach ($s in (Acl-Sorok -Utvonal "C:\BskyGuard\kozos\ki" -Kimenet $kiKimenet)) {
    if ((Normalizal $s.Azonosito) -ne (Normalizal $NemMegbizhato)) { continue }
    if ($s.Tiltas) {
        foreach ($j in $s.Jogok) {
            if (@("DE","D","F","M","GA") -contains $j) { $vanDeny = $true }
        }
    } else {
        $vanGrant = $true
    }
}
if (-not $vanGrant) { Megall "The untrusted user's Modify right is missing on the kozos\ki folder - that is where it puts the drafts." }
if (-not $vanDeny)  { Megall "The (DENY)(DE) line is missing on the kozos\ki folder - the guard will not start without it." }
Ok "The kozos\ki folder has the Modify right AND the delete-denying line."

# ---------------------------------------------------------------------------
# CHAPTER 4 - Python for the other user
# Non-administrators must not be able to write the C:\Python314 folder - otherwise
# the untrusted side could smuggle code into the guard.
# ---------------------------------------------------------------------------

Cim "Chapter 4 - Python"

if (-not (Test-Path -LiteralPath $PythonUt)) {
    Megall ("Not found: " + $PythonUt + ". See INSTALL.md, chapter 4: install a machine-wide Python from python.org, with the `"Install for all users`" option.")
}
Ok ("Found: " + $PythonUt)

$pyMappa = Split-Path -Parent $PythonUt
$pyKimenet = Icacls-Futtat -Argumentumok @($pyMappa) -Mit "querying the Python folder"
Write-Host ($pyKimenet.TrimEnd())

# The same allowlist as ENGEDETT_IDENTITASOK in beallitasok.py (includes Hungarian-language data: the localized SYSTEM/Administrators names on a Hungarian Windows; add your language's names the same way).
$EngedettNorm = @()
foreach ($n in @("SYSTEM","RENDSZER","Administrators","Rendszergazdák","Rendszergazdak","TrustedInstaller")) {
    $EngedettNorm += (Normalizal $n)
}
$IroJogok = @("F","M","W","WD","AD","WEA","WA","WDAC","WO","D","DE","DC","GW","GA")

$irokat = @()
foreach ($s in (Acl-Sorok -Utvonal $pyMappa -Kimenet $pyKimenet)) {
    if ($s.Tiltas) { continue }
    if ($EngedettNorm -contains (Normalizal $s.Azonosito)) { continue }
    foreach ($j in $s.Jogok) {
        if ($IroJogok -contains $j) { $irokat += $s.Azonosito; break }
    }
}
if ($irokat.Count -gt 0) {
    Write-Host ("  ERROR These can write the folder " + $pyMappa + ": " +
                (($irokat | Sort-Object -Unique) -join ", ")) -ForegroundColor Red
    Megall ("A non-administrator can also write the Python folder - so code could be smuggled into the guard. See INSTALL.md, chapter 4 (the IMPORTANT box about C:\Python312: it contains the fixing icacls command).")
}
Ok ("Only SYSTEM and Administrators can write the folder " + $pyMappa + ".")
Info "(This script skips the `"runas /user:bsky-guard ... python -V`" test from chapter 4 of the guide: it would need you to type the password. You can run it afterwards if you like.)"

# ---------------------------------------------------------------------------
# CHAPTER 5 - Copying the code, WITH VERIFICATION
# ---------------------------------------------------------------------------

Cim "Chapter 5 - the code fingerprint"

if (-not (Test-Path -LiteralPath $Forras)) {
    Megall ("Source folder not found: " + $Forras)
}
Write-Host ("SOURCE of the code (full path, printed): " + $Forras) -ForegroundColor Yellow
Write-Host ("The fingerprint list goes here:          " + $Lista) -ForegroundColor Yellow
Write-Host ""

& $PythonUt (Join-Path $Forras "ujjlenyomat.py") $Forras "--csak-telepitendo" "--kiir" $Lista
if ($LASTEXITCODE -ne 0) {
    Megall "Creating the fingerprint failed."
}
Ok "The fingerprint is ready."

Write-Host ""
Write-Host "STOP FOR A MINUTE - THIS IS THE ONE STEP THAT MAKES ALL OF THIS MEANINGFUL." -ForegroundColor Yellow
Write-Host "PHOTOGRAPH WITH YOUR PHONE the `"OSSZESITETT`" line above and the `"FAJLOK`" count." -ForegroundColor Yellow
Write-Host "This machine has no access to your phone's gallery - that is why it is worth something." -ForegroundColor Yellow
Write-Host "If you need to scroll up on the screen, do it now." -ForegroundColor Yellow
Write-Host ""
Read-Host "When you have the photo, press ENTER" | Out-Null

Cim "Chapter 5 - copying"

# Only the files TO BE INSTALLED: the top-level .py files (not the tesztek\ and
# __pycache__ folders), plus the two rule files.
Copy-Item (Join-Path $Forras "*.py") -Destination "C:\BskyGuard\program\" -Force
Ok "The .py files have been copied."

foreach ($szabaly in @("tilto_mintak.json", "szivesen_latnak.json")) {
    $cel = Join-Path "C:\BskyGuard\program" $szabaly
    if (Test-Path -LiteralPath $cel) {
        Info ($szabaly + " - ALREADY EXISTS, not overwriting it (you write into this one).")
    } else {
        Copy-Item (Join-Path $Forras $szabaly) -Destination $cel
        Ok ($szabaly + " - copied (first installation).")
    }
}

Write-Host ""
Get-ChildItem "C:\BskyGuard\program" | Select-Object Name | Format-Table -AutoSize | Out-String | Write-Host

Cim "Chapter 5 - verifying the copy"

& $PythonUt "C:\BskyGuard\program\ujjlenyomat.py" "C:\BskyGuard\program" "--csak-telepitendo" "--osszevet" $Lista
$ossz = $LASTEXITCODE
if ($ossz -eq 0) {
    Ok "EVERYTHING MATCHES - the copied code is exactly the code whose fingerprint you photographed."
} elseif ($ossz -eq 1) {
    Write-Host "  WARNING  There is a difference between the source and the copy." -ForegroundColor Yellow
    Write-Host "           If ONLY tilto_mintak.json or szivesen_latnak.json appears on the" -ForegroundColor Yellow
    Write-Host "           CHANGED list, that is EXPECTED: I intentionally did not overwrite them," -ForegroundColor Yellow
    Write-Host "           because you wrote into them. For ANY OTHER difference, STOP and tell Claude." -ForegroundColor Yellow
} else {
    Megall "The comparison did not run (it could not read the earlier list). Look at the lines above."
}

# ---------------------------------------------------------------------------
# FINAL CHECK - the guard's own permission self-check (the first command of
# chapter 7 of the guide). Here we run it as administrator: jogok.py only ACLs
# ---------------------------------------------------------------------------

Cim "Final check - permission self-check"

Push-Location "C:\BskyGuard\program"
try {
    & $PythonUt "C:\BskyGuard\program\or.py" "--ellenoriz" "--gyoker" $Gyoker
    $ellKod = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($ellKod -eq 0) {
    Ok "Permissions are OK. The guard can start."
    Info "(In variant A) a WARNING line about the Administrators group is printed before this. That is not an error, just a reminder.)"
} else {
    Write-Host "  ERROR The self-check reported an error (code: $ellKod)." -ForegroundColor Red
    Write-Host "        The lines above say which folder is the problem. The fix is in the" -ForegroundColor Red
    Write-Host "        table in chapter 7 of INSTALL.md." -ForegroundColor Red
    Write-Host "        If you do not understand it, show that line to Claude." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# "Check 2" - this script CANNOT perform it, because as administrator it
# would see into the titok folder. So it must be checked from a NORMAL window.
# (guide chapter 3, "Check 2 - the most important one")
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "=======================================================================" -ForegroundColor Cyan
Write-Host " DONE - but there is ONE more thing that only YOU can check." -ForegroundColor Cyan
Write-Host "=======================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Open a NORMAL (NOT administrator) PowerShell window:"
Write-Host "  Start menu -> type: powershell -> Enter"
Write-Host ""
Write-Host "And paste this ONE line:"
Write-Host ""
Write-Host '  Get-ChildItem "C:\BskyGuard\titok"' -ForegroundColor White
Write-Host ""
Write-Host "WHAT YOU SHOULD SEE: a red error message containing `"Access is denied`" (in your Windows language)." -ForegroundColor Yellow
Write-Host "THIS RED ERROR IS THE GOOD RESULT. It means that the user running Claude" -ForegroundColor Yellow
Write-Host "cannot reach the secret." -ForegroundColor Yellow
Write-Host ""
Write-Host "If instead it lists the folder (even empty, without an error message):"
Write-Host "  - check that you really are in a NORMAL window (the title bar does NOT say Administrator),"
Write-Host "  - if you are, something went wrong: tell Claude."
Write-Host ""
Write-Host "In the same NORMAL window, the three closed folders must also give `"access denied`":"
Write-Host ""
Write-Host '  foreach ($ut in @("C:\BskyGuard\allapot\varo","C:\BskyGuard\allapot\feldolgozott","C:\BskyGuard\allapot\zart")) { Get-ChildItem $ut }' -ForegroundColor White
Write-Host ""
Write-Host "The C:\BskyGuard\allapot folder, however, you SHOULD see - that is intentional."
Write-Host ""
Write-Host "Then continue with CHAPTER 6 of INSTALL.md (the secrets)." -ForegroundColor Green
Write-Host "That cannot be put into a script: you have to type the token and the app password from your phone," -ForegroundColor Green
Write-Host "in the administrator account's own session." -ForegroundColor Green
Write-Host ""
