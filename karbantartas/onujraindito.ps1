# onujraindito.ps1 ("onujraindito" = self-restarter) - lets Claude restart the Claude desktop app, or the machine, by itself.
# Usage (from the Claude session, launched DETACHED, so the death of Claude's own process doesn't cut it off):
#   Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File "{REPO}\karbantartas\onujraindito.ps1" -Mod app'
#   Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File "{REPO}\karbantartas\onujraindito.ps1" -Mod gep -Masodperc 90'
# -Mod app : a full restart of the Claude app only. Every 'claude*' process is stopped (closing the window with X only closes
#            the window; the background processes stay, and that is often what a restart is meant to fix), then the app is
#            started again from its Startup shortcut. The session continues after the app comes back.
# -Mod gep : ("gep" = machine; "machine" works too) a scheduled MACHINE restart (shutdown /r); the app and the daemons come
#            back from the Startup folder. Cancel with: shutdown /a
# WARNING - BLAST RADIUS of -Mod app: it stops EVERY process named 'claude*' on this machine (the desktop app with ALL its sessions,
#    a second Claude included, and any Claude Code CLI running in a terminal) and, unless -KeepWatchers is given, every
#    python watcher of this package (kontextus_mero, monitor_wake_v3_db, posta_figyelo, csoport_tars_figyelo, egyszer.py),
#    the other Claude's too. With two Claudes: tell the other one first (mailbox), and let only one of you run it.
#    Don't run it on a machine where someone else's Claude sessions run.
# BEFORE: close the journal and write out memory in the session (the hook chain does NOT replace that).
# AFTER: the SessionStart hook reminds the returning instance to start its watchers again.
# Log: <memory folder>\naplo\onujraindito.log (CLAUDE_MEMORY_DIR, else MEMORIA_ROOT, else ..\memoria next to this folder).
param(
  [ValidateSet("app","gep","machine")] [string]$Mod = "app",
  [int]$Masodperc = 60,    # ("seconds") delay of the machine restart
  [int]$Varakozas = 8,     # ("wait") seconds to wait so the calling tool call can return before anything is stopped
  [switch]$Ebreszto,       # ("wake-up") ONLY with the owner's explicit, recorded consent: types a wake-up line into the returned window
  [int]$EbresztoVar = 45,  # seconds to wait AFTER the 'Claude' window appears, before typing (20 s proved too tight)
  [string]$SessionCim = "",  # ("session title") YOUR session's title in the sidebar: the script presses that button before typing
  [string]$Uzenet = "",    # ("message") optional reason appended to the wake-up line - plain ASCII, no brackets (SendKeys eats them)
  [switch]$KeepWatchers,   # leave the package's python watchers running (they are stopped by default as orphans)
  [string]$AppId = "Claude_pzs8sxrjxfjjc!Claude"   # MSIX app id, used only when there is no Startup shortcut
)
if ($Mod -eq "machine") { $Mod = "gep" }

# --- Selecting the session in the sidebar: the app opens a NEW session at start, so without this the wake-up line would land
#     in the wrong place. UI Automation: the Chromium accessibility tree fills up on the 2nd query; the sidebar item is a button
#     named "Running <title>" / "Idle <title>" -> InvokePattern. Only inside the Claude window.
function Select-Session($proc, $cim) {
  Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes
  $root = [System.Windows.Automation.AutomationElement]::FromHandle($proc.MainWindowHandle)
  $cond = [System.Windows.Automation.Condition]::TrueCondition
  $db = 0
  for ($k = 0; $k -lt 8; $k++) {
    $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond); $db = $all.Count
    foreach ($e in $all) {
      $n = $e.Current.Name
      if ($n -and ($n -like ("* " + $cim)) -and -not $n.StartsWith('More options') -and $e.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button) {
        $e.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
        return ("PRESSED: {0} (attempts: {1}, elements: {2})" -f $n, ($k + 1), $db)
      }
    }
    Start-Sleep -Seconds 3
  }
  return ("NOT FOUND: {0} (elements: {1})" -f $cim, $db)
}

$memoria = $env:CLAUDE_MEMORY_DIR
if (-not $memoria) { $memoria = $env:MEMORIA_ROOT }
if (-not $memoria) { $memoria = Join-Path (Split-Path -Parent $PSScriptRoot) "memoria" }
$naplo = Join-Path $memoria "naplo\onujraindito.log"
try { New-Item -ItemType Directory -Force (Split-Path $naplo) | Out-Null } catch { }
function Log($s) { try { [IO.File]::AppendAllText($naplo, ("{0} | {1} | {2}`r`n" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Mod, $s), [Text.Encoding]::UTF8) } catch { } }
Log "start (waiting ${Varakozas}s)"
Start-Sleep -Seconds $Varakozas
if ($Mod -eq "gep") {
  Log "shutdown /r /t $Masodperc"
  & shutdown.exe /r /t $Masodperc /c "Claude self-restart ($Masodperc s). Cancel: shutdown /a"
  exit 0
}
# --- app mode ---
$lnk = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Claude Desktop.lnk"
$procs = Get-Process | Where-Object { $_.ProcessName -like 'claude*' }
Log ("stopping: {0} process(es)" -f $procs.Count)
$procs | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 4
$marad = Get-Process | Where-Object { $_.ProcessName -like 'claude*' }
if ($marad) { Log ("STILL RUNNING: {0} - trying again" -f $marad.Count); $marad | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 3 }
# The watchers' python processes keep running ORPHANED after the claude processes stop (a context meter once survived a
# restart this way) -> stop them here; the returning session starts them again from the SessionStart hook's list.
# The telegram_sync_daemon (pythonw) and the MCP servers are NOT touched.
$arva = if ($KeepWatchers) { @() } else { Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(\.exe)?$' -and $_.CommandLine -match 'kontextus_mero|monitor_wake_v3_db|posta_figyelo|csoport_tars_figyelo|egyszer\.py' } }
if ($arva) { Log ("stopping orphaned watchers: {0}" -f @($arva).Count); $arva | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }
if (Test-Path $lnk) { Start-Process -FilePath $lnk; Log "started with the Startup shortcut" }
else { Start-Process "shell:AppsFolder\$AppId" -ErrorAction SilentlyContinue; Log "started via shell:AppsFolder (no shortcut found)" }
Start-Sleep -Seconds 10
$uj = Get-Process | Where-Object { $_.ProcessName -like 'claude*' }
Log ("after: {0} process(es) running" -f $uj.Count)

# --- WAKE-UP (only with the owner's consent): a line typed into the returned app window = a user turn -> the session wakes up,
#     the SessionStart hook's context comes with it, and the watchers can be restarted without a human. ---
if (-not $Ebreszto) { Log "wake-up: off (no -Ebreszto switch)"; exit 0 }
if (-not $SessionCim) { Log "wake-up: no -SessionCim given, not typing anywhere"; exit 0 }
$ablak = $null
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 2
  $ablak = Get-Process claude -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -eq 'Claude' } | Select-Object -First 1
  if ($ablak) { break }
}
if ($ablak) {
  Log ("window found (pid {0}), waiting {1} s for the session to load" -f $ablak.Id, $EbresztoVar)
  # Order: 15 s app load -> select my own session in the sidebar (UI Automation) -> the rest of the wait -> typing.
  $elso = [Math]::Min(15, $EbresztoVar); Start-Sleep -Seconds $elso
  try { Log ("session select: " + (Select-Session $ablak $SessionCim)) } catch { Log ("session select ERROR: " + $_.Exception.Message) }
  Start-Sleep -Seconds ([Math]::Max(0, $EbresztoVar - $elso))
  try {
    $ws = New-Object -ComObject WScript.Shell
    if (-not $ws.AppActivate($ablak.Id)) { Log "wake-up: the Claude window could not be focused - NOT typing"; exit 0 }
    Start-Sleep -Milliseconds 800
    # A restart also kills the session's own in-session scheduled jobs (crons); the wake-up line is where the returning
    # instance must hear about that. NOTE: SendKeys swallows brackets and the + ^ % ~ characters - so the text has none.
    $szoveg = "WAKE-UP onujraindito.ps1 automatic: you are back. 1. Start the watchers again per the SessionStart hook list. 2. IMPORTANT: the restart also killed the in-session scheduled jobs - check todays journal for any pending timed message or reminder and schedule it again. 3. Then carry on."
    if ($Uzenet) { $szoveg += " REASON: " + ($Uzenet -replace '[{}()\[\]+^%~]', ' ') }
    $ws.SendKeys($szoveg)
    Start-Sleep -Milliseconds 300
    $ws.SendKeys("{ENTER}")
    Log "wake-up typed into the window"
  } catch { Log ("wake-up ERROR: " + $_.Exception.Message) }
} else { Log "wake-up: no Claude window found within 120 s" }
