# BskyGuard — a Bluesky service running under a separate Windows user

This folder is the "strong" variant according to **SPEC 2**. The essence in one sentence:

> **The protection does not depend on Claude's good intentions, but on Windows file permissions.**

The earlier machine-approval chain, which ran under the same Windows user, failed
two adversarial reviews (the reports are not included in this package).
The lesson: every gate whose state lives in a file that is writable from the
untrusted side can be bypassed. That is why here the **trust anchor is physically elsewhere**.

---

## Actors

| Who | Where it runs | What it can do |
|---|---|---|
| **Claude** | under the `agent-user` Windows user | **UNTRUSTED side** — can be deceived. Writes into `kozos\ki\`, reads from `kozos\be\`. Nothing else. |
| **The guard** (this code) | under the `bsky-guard` Windows user, from Task Scheduler | **TRUSTED side.** Holds the Bluesky app password and the bot token. |
| **Owner** | phone, the NEW (second) Telegram bot | Says YES or NO. |

---

## Folders and the trust they carry

```
C:\BskyGuard\
  titok\      bsky_kulcs.env, or_bot.env       agent-user: NO rights
  program\    the code + tilto_mintak.json     agent-user: read only
              + szivesen_latnak.json
  allapot\    previews, log, offset            agent-user: read only
    elonezetek\     ← THIS IS THE ANCHOR
    archiv\         closed records older than 70 days
    varo\           the CANONICAL form of the dropped draft  agent-user: NO rights
    feldolgozott\   drafts already closed                    agent-user: NO rights
    zart\           the guard's fingerprint key              agent-user: NO rights
  kozos\
    be\       inbox.jsonl, allapot.json        the guard writes, agent-user reads
    ki\       drafts (*.json)                  agent-user writes, the guard reads
```

At startup the guard **verifies** these with `icacls`, and if anything is off it
**does not start** — it writes the error into `kozos\be\allapot.json`.
The check is **allowlist-based**: it does not look at whether `agent-user` appears (that
could be renamed, and it could also come in through a group), but whether anyone
**outside** the `SYSTEM / Administrators / bsky-guard` trio gets any right.
An unknown permission token also stops startup — but separately, with its own
message, because it does not necessarily mean actual access.

The check also covers the **root** and the `kozos` folder (Modify on the root
also grants `FILE_DELETE_CHILD`, with which the `allapot` folder could be
renamed), the **subfolders** of `allapot`, and everywhere the
**OWNER** too: the owner has an implicit `WRITE_DAC` right, so it can
give itself access back at any time, however clean the list is.

### The three CLOSED folders

`allapot\varo`, `allapot\feldolgozott` and `allapot\zart` **do not
inherit** from the `allapot` folder: each gets its own, non-inheriting `icacls` line
in which the untrusted user **does not appear at all** — not even
as a reader. Everything derived from the contents of dropped files goes here.

This is the **third layer of defence against drop-side attacks, independent of the code's control flow**.
The other two: the dropped file is opened without following links, and its
contents are validated against the full schema before anything is written to disk. Chapter 3
of the installation guide creates these three folders separately; if they
are missing, the guard **does not start**, because if it created them itself they would inherit
the (readable) permissions of `allapot`.

---

## The outgoing flow

1. Claude writes a `kozos\ki\NEV.json` file. **Strict schema**, closed field set:
   `verzio` (1), `tipus` ("poszt" / "valasz"), `szoveg`, `valasz_cel_uri`,
   `langs`, `megjegyzes_tulajnak`. Unknown field, file larger than 8 KB, not
   UTF-8, not valid JSON → **rejection**, with the reason in the status file.
   The draft **must not contain** a `cid` — the guard takes that from the network.
   Example (`C:\BskyGuard\kozos\ki\2026-09-20_koszones.json`):

   ```json
   {
     "verzio": 1,
     "tipus": "valasz",
     "szoveg": "Köszönöm, hogy megkérdezted — ma a tanulásról írnék.",
     "valasz_cel_uri": "at://did:plc:pelda123/app.bsky.feed.post/abc123",
     "langs": ["hu"],
     "megjegyzes_tulajnak": "Ez válasz arra, aki reggel megszólított."
   }
   ```

2. The existing, tested rules (`szures.py`): text only, max 300 graphemes,
   block filter (base patterns live **in the code**, the JSON can only extend them), text
   imitating the approval formula is rejected.
3. For replies, a **LIVE** check (`getPosts`): the target post exists; it mentions
   us (mention facet) or is a direct reply to our own post (in that case
   we fetch **the parent too** — anyone can write the parent uri into their own post);
   or the author's DID is on the allowlist → **INITIATED**.
3/b. **THE THREAD ROOT AND THE FULL ANCESTOR CHAIN are verified from the network.** The root
   is **never taken from the draft or from anybody's claim**. The root is the
   `reply.root` of the **target post's own record, fetched from the network** (if the
   target post has no `reply`, then the target post itself), and this root is also
   fetched from the network: does it exist, does its cid match.
   In addition, starting from the target post we **walk up the actual ancestor chain**,
   fetching each step from the network, with an upper bound (`FELMENO_MAX_LEPES`).
   This is needed because the two attacks are mirror images of each other: in one the
   `reply.root` lies, in the other the `reply.parent` is TRUE (a victim's post),
   and the truth itself is the problem — the claimed root is then the attacker's harmless
   post, while our reply would land in the victim's conversation.
   The decision is based on the actors in the chain:
   * every author is **us / the one who addressed us / allowlisted** → normal reply;
   * there is an **unrelated third party** in it → not a rejection, but an
     **INITIATED** classification (falls under the daily cap of two), and the
     preview states in CAPITALS whose thread the reply goes into (handle + DID
     + sanitized text), and that a third party may also get a notification;
   * the chain **cannot be walked**, the limit runs out, or anything does not match →
     **rejection**.
   The preview always shows the chain length and which thread the post goes into.
4. **Caps derived from network truth** (`getAuthorFeed` on our own account,
   with `filter=posts_with_replies` and **paging** to the end of the 24-hour window; if the
   paging upper bound runs out, the guard FAIL-CLOSED **does not post**):
   5 posts / 24 hours, 2 initiated / 24 hours, 10 minutes between two posts,
   duplicates (same normalized text + same parent) forbidden. In addition:
   at most **3** previews awaiting approval at once.
   **What counts as initiated** (2026-09-20, J6): a reply is NOT
   initiated if the target post (a) mentions us or replies to us, OR
   (b) is the direct continuation of an **ongoing conversation**: we wrote, X
   replied to it, then X continued under their own reply (for example added an image
   below it) without tagging us. Rule (b) is narrow and fail-closed — it is decided
   from the chain walked on the network, the anchor (X's first reply to our post) is
   at most 24 hours old, and nobody other than us and X may appear in the chain;
   if any condition is uncertain, the reply counts as initiated.
   If the target post is neither, but its author is on the allowlist
   (`szivesen_latnak.json`), the reply is **initiated** and counts toward the daily
   two; if the author is not on the allowlist either, the draft is rejected
   (`halozati_nem_szolitottak_meg`). The daily-two COUNTER, however, counts from the feed
   by the old, strict rule (every "reply to someone else" counts),
   because determining an ongoing conversation retroactively would require a chain
   walk per post; the direction is safe: in the worst case a legitimate reply
   is postponed, an unwanted reply does not go out.
5. **Preview** to the owner on the new bot, **byte-exact**. Foreign fields placed in
   a frame are sanitized and frame-neutralized, the frame marker is
   **random per preview**, and the fixed approval line appears **once, at the very end**.
   The OUTGOING text, however, is never rewritten for display purposes: if it
   would contain a character or pattern that would need rewriting (`==`,
   the phrase "külső adat" ("external data"), control, zero-width or right-to-left characters), the
   draft is **rejected**, with a reason. Before sending, the engine checks once more
   that the canonical text is present byte-for-byte in the message; if not,
   the preview does not go out at all. We send to Telegram **without** `parse_mode`.
   Under `allapot\` the guard stores the `message_id` of the sent message, the
   **canonical record** and its sha256.
6. **Approval**: the guard reads the bot with its own `getUpdates` long poll.
   The YES is valid only if the sender is the owner's user_id, the chat is the owner's
   private chat, the message is a **reply to the stored preview's `message_id`**, not
   edited, not forwarded, not `via_bot`, and after whitespace trimming and
   case folding the text is **exactly** `IGEN` (or `NEM`). It expires after 48 hours.
7. **Posting happens exclusively from the stored canonical record** — the draft file is
   never re-read after the preview (by then the file has already been moved under
   `allapot\feldolgozott\`). Before sending, once more: caps + live
   target check + matching the **thread root**, the **ancestor chain**, the **foreign
   actors** and the **handle** against the approved record. The handle
   also because the owner recognises from it, on the phone, whom we are replying to
   (the anchor is the DID, but the handle can be changed); on a mismatch we stop and
   report on the bot. Write-ahead `kuldes_alatt` marker; if it gets stuck we **do not
   resend** — we notify the owner instead.
8. Any other message on the bot (stranger, command, other text) → ignored.

> **The path of a dropped draft.** The guard **takes over** the draft from the `kozos\ki\` folder
> into the protected `allapot\varo\` folder, and reads it in **only from there**.
> The takeover is **not a move but a byte copy**: the guard safely
> opens the file (`os.lstat` + `os.fstat` repeated on the opened descriptor:
> regular file, no hard link, not a giant file), reads the
> contents, **writes a new file** into the protected folder (`O_EXCL` + fsync), and only
> then deletes the original.
>
> Why not a move? Because within the same volume Windows `MoveFileEx`
> **preserves the file's access list and owner**, and does not re-inherit from the new parent.
> A moved draft would therefore have sat in the protected folder still owned by the
> untrusted user, with that user's rights — and the owner
> can give itself anything back with the implicit permission-setting right. The new
> file, by contrast, inherits from the **destination folder**, and its owner is the guard.
>
> If the deletion fails (the untrusted side keeps it open or revokes
> its own delete right), the guard stores the **sha256 of the content**, and
> never takes over the same content again — so the stuck file cannot
> generate a new preview every round. For a deferrable reason the draft stays in the
> waiting area, not on the untrusted side.
>
> **We also check the drop folder itself**: if it is a junction or a
> link, we take nothing from it — otherwise, by swapping the folder, the
> guard would "process" its OWN records awaiting approval.
>
> **Anything that is not a regular entry** (a dropped FOLDER, a locked or denied
> file) we try to remove (`os.remove`, then for a directory
> `os.rmdir`). If that still fails after three rounds, the name is put on a
> **skip list** (`allapot\kihagyando_bedobott.json`, with an upper bound),
> and from then on it does not even enter the per-round batch of fifty — otherwise, with fifty
> directories whose names sort early, the drop folder could be filled permanently. The matter shows up
> among the messages in `kozos\be\allapot.json`.
>
> **The draft file is moved under `allapot\feldolgozott\` BEFORE THE PREVIEW
> IS SENT.** So if the run is interrupted, the same draft will not
> produce a second preview.

## Rejection reasons — why a draft can bounce back

The draft is **written by Claude**, so Claude needs to know what bounces back and why.
The reasons appear in the `kozos\be\allapot.json` file, in the item's `ok` field.
The `optin:` prefix means it failed at the reply gate.

| Reason | What it means | What to do |
|---|---|---|
| `nem_szabalyos_json`, `nem_objektum`, `nem_utf8`, `tul_nagy_fajl`, `ures_fajl` | the file itself is bad | write it again, in UTF-8, under 8 KB |
| `ismeretlen_mezo`, `ismeretlen_mezo_darab:N`, `hianyzo_mezo:…` | the schema is closed: exactly six fields | add or remove the field. The **field name is intentionally not shown**: it would be raw text taken from the dropped file |
| **`hivatkozas`**, **`merev_hivatkozas`**, **`fajl_kicserelve`**, **`bedobon_kivuli_fajl`**, **`nem_regularis_fajl`** | the dropped file is not an ordinary file in the drop folder (link, junction, or swapped mid-run) | drop it again, as a normal file |
| **`tul_sok_zwj`**, **`tul_hosszu_zwj_lanc`** | at most 7 elements per emoji sequence, at most 10 joiners per post | shorten the emoji sequence |
| **`ismetelt_valtozat_valaszto`** | at most ONE U+FE0F after a base character | remove the second one |
| **`tul_sok_kombinalo_jel`** | at most 2 combining marks per base character, at most 20 per post (Zalgo limit) | write the text with normal accented letters |
| `rossz_verzio`, `rossz_tipus`, `rossz_langs`, `rossz_valasz_cel_uri` | a field value is invalid | see the example above |
| `tipus_cel_ellentmondas`, `valasz_cel_nelkul` | "poszt" has no target, "valasz" needs one | fix the `tipus`/`valasz_cel_uri` pair |
| `ures_szoveg`, `tul_hosszu_grafema`, `tul_hosszu_bajt` | length rule | max 300 graphemes / 3000 bytes |
| `tilto:…` | the block filter found something (the pattern's NAME, never the text) | rewrite the text |
| **`szoveg_atirasra_szorulna`** | the text contains a character that sanitization would rewrite: **any default-invisible code point**, control, right-to-left or to-be-normalized character, non-ASCII space | remove the invisible characters — see the "Invisible code points" section. **A legitimate emoji sequence passes** |
| **`megjelenites_atirna:vezerlo_vagy_normalizalando_karakter`** | the same, applied to the outgoing text | the same |
| **`megjelenites_atirna:keret_utanzat`** | the text contains `==` or the phrase "külső adat" | these would break the preview frame — rephrase |
| `jovahagyo_formula_utanzas` | the text imitates the approval line | rephrase |
| **`tobbsoros_vagy_atirando_megjegyzes`**, **`tul_hosszu_megjegyzes`** | `megjegyzes_tulajnak` is ONE line, at most 80 characters | shorten it, remove the line break |
| `optin:halozati_nem_szolitottak_meg` | we were not addressed, and the author is not on the allowlist | we do not reply here |
| `optin:halozati_uri_elter`, `optin:halozati_cid_elter`, `optin:nincs_halozati_ellenorzes` | the target post disappeared or changed | fetch it again and write a new draft |
| `optin:szulo_poszt_nem_letezik`, `optin:szulo_szerzoje_nem_mi` | the "replied to us" claim cannot be verified | we do not reply here |
| **`optin:gyoker_alakja_rossz`**, **`optin:gyoker_nem_igazolhato`**, **`optin:gyoker_uri_elter`**, **`optin:gyoker_cid_elter`**, **`optin:gyoker_szerzoje_ismeretlen`** | the thread root cannot be verified from the network | the thread cannot be walked — we do not reply here |
| **`optin:felmeno_nem_igazolhato`**, **`optin:felmeno_uri_elter`**, **`optin:felmeno_alakja_rossz`**, **`optin:felmeno_lanc_korben_jar`**, **`optin:felmeno_lanc_tul_hosszu`**, **`optin:felmeno_szerzoje_ismeretlen`** | the ancestor chain cannot be walked to the end | fail-closed: we do not reply into a thread we have not seen in full |
| `elonezet_nem_bajtpontos` | the preview would not contain exactly the outgoing text | suspected program bug — report it |
| `elonezet_nem_ment_ki` | Telegram did not accept the preview | try again later |
| `celpont_megvaltozott`, `gyoker_megvaltozott`, **`felmeno_lanc_megvaltozott`**, **`idegen_szereplok_megvaltoztak`**, `kezdemenyezett_elter` | something changed SINCE the approval | the approval is stale, write a new draft |
| **`handle_megvaltozott`**, **`gyoker_handle_megvaltozott`** | the person who addressed us changed their handle after the approval | The owner saw something other than what is there now — a new preview is needed |
| `napi_limit`, `perc_limit`, `kezdemenyezett_napi_limit`, `fuggo_limit` | **temporary** cap | wait, the draft stays in the waiting area |

For **final** rejections the status file also writes an `ok_magyarul` field:
the same in human language, together with what can be done. The machine reason does not disappear.

### Invisible code points — the rule INVERTED

We do not ban a few known tricks, but **every code point that is
invisible by default**. What is invisible, the owner cannot see in
the preview either — and the owner approves what they see; such a character can
carry several bits of hidden data per character.

**The list is not a manual enumeration** — this matters, because sooner or later something
gets left off a manual list (for example U+16FE4 KHITAN FILLER, added in Unicode 13).
The rule (`szures.lathatatlan_kodpont_e`) is assembled from two sources:

1. **A category rule from `unicodedata`**, so it is always up to date with Python's
   Unicode table: `Cf`, `Cc` (line break and tab are exceptions per the existing
   rule), `Cs`, `Co`, `Cn`, `Zl`, `Zp`, and every non-ASCII
   `Zs` (space-like).
2. **Hard-coded ranges**: the FULL `Default_Ignorable_Code_Point` set according to Unicode's
   `DerivedCoreProperties.txt` — U+00AD, U+034F, U+061C,
   U+115F–1160, U+17B4–17B5, U+180B–180F, U+200B–200F, U+202A–202E,
   U+2060–206F, U+3164, U+FE00–FE0F, U+FEFF, U+FFA0, U+FFF0–FFF8, **U+16FE4**,
   U+1BCA0–1BCA3, U+1D173–1D17A, U+E0000–E0FFF. Some of these are in category
   `Mn`, so the category rule would not catch them. Plus three
   "seemingly visible, actually blank" characters: U+2800 (Braille blank),
   U+FFFC (object replacement character), U+FFFD (replacement character).

A test **walks the entire code point space**, and fails if any
`Mn`/`Me`/`Cf` category `Default_Ignorable` code point would pass the outgoing
rules — so growth in the next Unicode version does not go unnoticed.

**There are exactly three exceptions**, each narrowly defined:

1. **ZWJ (U+200D) in an emoji context** — an emoji before AND after it. So
   family emoji and profession emoji pass; between two letters it does not.
2. **Variation selector (U+FE0F) after an emoji**, or in the **exact keycap-emoji
   triple**: `0`–`9` / `#` / `*`, then U+FE0F, then U+20E3. Any
   damaged form of the triple (without the terminator, with a letter base) is rejected.
3. **Three specific flag tag sequences, listed literally**: `🏴` +
   `gbeng` / `gbsct` / `gbwls` tag sequence + U+E007F — the English, Scottish and
   Welsh flags. Previously there was a RANGE here (1–16 arbitrary
   tag characters), and the tag characters map one-to-one onto the
   ASCII 0x20–0x7E range: a single post could hold 256 characters of hidden
   text while the preview showed only a few black flags.
   If other country flags are needed later, the list can be extended — but **always
   by enumeration, never by a range**.

### QUANTITY limits on the remaining exceptions

ZWJ and the variation selector remain allowed in an emoji context, and between two identical emoji
the presence or absence of a ZWJ is **one bit** that cannot be told apart on screen.
It cannot be excluded completely without harming legitimate use,
so there is a quantity limit on it (`szures.rejtett_csatorna_ellenorzes`):

- at most **7 elements** joined with ZWJ in one emoji sequence;
- at most **10 ZWJ** per post;
- U+FE0F only after a base character that has an emoji variant, and **at most once**
  after a given base;
- at most **2 combining marks** (`Mn`/`Me`) after a base character, at most **20**
  per post — this is the Zalgo limit.

The block filter (address, e-mail, phone number) runs on **two forms**: the usual
NFKC + homoglyph-normalized form, **and** the same form stripped of
invisible characters. Without this, a single inserted invisible character would have split the
forbidden word in two, and the filter would have let it through.

### The REMAINING channel — what this code CANNOT solve

Honestly: the rules above close the **invisible** channels. What remains is
the **visible** text itself, and that cannot be excluded in principle.

- **Word choice.** In a three-hundred-grapheme post, the choice of each word
  carries a few bits of freedom. A pre-agreed code ("if the post starts with the word
  `Ma`, that's a 1") gives an arbitrary but low-bandwidth channel.
- **Emoji choice.** The same, just more conspicuous: which emoji, how many,
  in what order.
- **Sentence length, punctuation, where line breaks fall.** All visible, all
  freely chosen.

These **cannot be caught by a filter**, because the defence would be the same as
forbidding posting. The only protection here is **the owner's eyes**. That is
why the preview is byte-exact, why it contains nothing that needs rewriting, and
why the full text sits in a frame with an unguessable marker. If the post is
oddly worded, if the emoji do not fit, if the text is
incomprehensible — the reason may be that it is not meant for the public. In that case the
answer is NO, and no amount of code could replace that.

The bandwidth is small (a few bits per post), the cap is 5 posts a day, and
every piece has to be approved separately — so leaking a longer secret would
take days, with many strange posts.

## Follow and like (2026-10-02)

Two new, **text-free** operations: FOLLOW (`app.bsky.graph.follow`) and
LIKE (`app.bsky.feed.like`). Bluesky has no emoji reactions, only likes.
They go through **the same gate** as posts — there is no back door:

    kozos\ki\NEV.json → takeover into the protected waiting area → strict schema →
    LIVE target check (getProfile / getPosts) → cap from the network
    (listRecords) → PREVIEW to the owner → IGEN → fresh re-check →
    write-ahead `kuldes_alatt` → ONE createRecord, without retry

**The JSON examples** (the field set is closed here too; unknown field → rejection):

```json
{
  "verzio": 1,
  "tipus": "kovetes",
  "cel_handle": "valaki.bsky.social",
  "megjegyzes_tulajnak": "Ő írta a tegnapi kertes szálat."
}
```

Instead of `cel_handle`, `cel_did` can also be given (`"cel_did": "did:plc:…"`) —
**exactly one** of them is required; a field with a `null` value counts as missing. The handle
is written WITHOUT `@`; the guard lowercases it.

```json
{
  "verzio": 1,
  "tipus": "kedveles",
  "cel_uri": "at://did:plc:pelda123/app.bsky.feed.post/3abcxyz",
  "megjegyzes_tulajnak": "Kedves válasz volt a posztunkra."
}
```

`cel_uri` can only be a **post** (`…/app.bsky.feed.post/…`), in DID form.
The draft **must not contain** a `cid` — the guard fetches it from the network,
and compares it again before execution.

**What applies to them, and what does not:**

* **There is no outgoing text**, so the content filter (`szures.py`: length,
  block filter, invisible code points) does not run on them. The rule for
  `megjegyzes_tulajnak` is the same (one line, ≤ 80 characters).
* **Target check from the network**: for a follow `getProfile` (the DID comes from the
  network; if the draft gave a handle or DID, it must match),
  for a like `getPosts` (the post exists, its author is the one whose repo the
  uri points to, the `cid` comes from the network). Non-existent target → **final** rejection;
  network error → **postponement** (the draft stays in the waiting area).
* **Already following / already liked** → **final rejection** (`mar_kovetve`,
  `mar_kedvelve`), from the `viewer.following` / `viewer.like` field of the
  authenticated response. Not a "silent success": in that case there is nothing to execute, so
  neither a preview nor an `elkuldve` event is created (the latter must always carry the uri
  of a created record); the decision is visible in the status file with a reason and an
  `ok_magyarul` explanation. If the target becomes
  followed/liked BETWEEN approval and execution, the re-check before execution catches it
  (`kuldes_elutasitva`), and no duplicate record is created.
* **Caps** (`beallitasok.py`), rolling 24 hours, from network truth (the
  `listRecords` response of our own repo; a record with an unparseable time
  counts): `KOVETES_NAPI_MAX = 5`, `KEDVELES_NAPI_MAX = 10`. The two caps are
  independent of each other and of the post cap. When a cap is hit → **postponement**. The
  `FUGGO_MAX = 3` (previews waiting at once) is **shared** with posts.
* Only **one** preview may wait for the same target at a time
  (`mar_jovahagyasra_var`).
* Following our own account / liking our own post is rejected; if blocked, we do not
  follow.
* **Before execution** everything again: target, `cid` (like), handle (if the target's
  handle has changed since approval → we stop and report on the bot),
  cap.

**Preview** (Telegram, the approval line unchanged):

    FOLLOW: @valaki.bsky.social
    … handle, did, name, followers/posts, the profile description in an EXTERNAL DATA frame
    LIKE: post by @author: "the first 80 characters…"
    … author, did, uri, the post text in an EXTERNAL DATA frame
    in the last 24 hours: N follows/likes (cap: M)

Claude's note sits in the usual labelled block — intentionally NOT in the
header line, because the note is Claude's text, while the header line states the
object of the operation.

**Log** (`allapot\naplo.jsonl`): `elonezet_kikuldve` → `elkuldve` (containing
`tipus` and `follow_uri` / `like_uri`) or `piszkozat_elutasitva` /
`kuldes_elutasitva` / `kuldes_hiba`. **Status file**: the elements of `tetelek` with the
existing fields, plus `cel_did`, `cel_handle`, `follow_uri` for a follow,
and `cel_uri`, `like_uri` for a like.

**New reasons**: `nincs_ilyen_profil`, `nincs_ilyen_poszt`, `mar_kovetve`,
`mar_kedvelve`, `mar_jovahagyasra_var`, `sajat_fiok`, `sajat_poszt`,
`letiltas_miatt_nem`, `profil_did_elter`, `profil_handle_elter`,
`profil_did_ervenytelen`, `poszt_uri_elter`, `poszt_cid_ervenytelen`,
`poszt_szerzo_elter`, `hianyzo_mezo:cel_handle_vagy_cel_did`,
`cel_handle_es_cel_did_egyszerre`, `rossz_cel_handle`, `rossz_cel_did`,
`rossz_cel_uri`, and the deferrable `kovetes_napi_limit`, `kedveles_napi_limit`.
Each has an `ok_magyarul` text (`allapot.OK_MAGYARAZAT`).

Tests: `tesztek/test_kovetes_kedveles.py` (53 tests: schema, rules, full
flow with mock adapters, and the record structure of `bsky_api` by replacing
`_keres` — no test calls a write endpoint).

## The incoming flow

Every 10 minutes `listNotifications` (+ optionally the timeline) → sanitization and
suspicion marking (`olvaso_mag.py`) → `kozos\be\inbox.jsonl` (append, dedup,
size rotation). Content NEVER goes to the console, only counts.
Claude reads it with the `mutat.py --inbox …` command, inside a hard frame.

---

## Files

| File | What it does |
|---|---|
| `or.py` | the entry point: `--gyoker`, `--ellenoriz`, `--ki-vagyok`, `--szaraz`, `--egyszer`, `--idovonal N` (in the incoming flow also the last N posts of followed accounts), or continuous running |
| `--szaraz` (2026-09-22) | the permission gate checks the LIVE tree, but after that all state goes into the `allapot_szaraz\` sandbox (with a snapshot of the live records), only a COPY is made from the drop folder, and the `kozos\be` output is also in the sandbox. Previously the dry run took the dropped draft and recorded it with a made-up Telegram id — `tesztek/test_szaraz_homokozo.py` guards against this. |
| `motor.py` | the full flow; every network/Telegram call goes through an **adapter** |
| `jogok.py` | the startup NTFS self-check (with an injectable query) |
| `piszkozat.py` | the strict schema of the drop file |
| `kimeno.py` | caps, reply gate, text rules, and (2026-10-02) the target and cap rules for follow/like (pure functions) |
| `elonezet.py` | text of the message sent to the owner |
| `telegram_or.py` | the guard's own bot + the approval decision (pure function) |
| `allapot.py` | the anchor: preview records, used approvals, offset |
| `bejovo.py`, `olvaso_mag.py` | the incoming side |
| `szures.py`, `bsky_api.py` | **copied unchanged** from the existing, tested code; since 2026-10-02 `bsky_api.py` has been extended with four calls for follow/like (`getProfile`, `listRecords`, two `createRecord`) |
| `mutat.py` | on Claude's side: reading the inbox inside a hard frame |
| `tarolas_or.py` | atomic I/O, lock file |
| `ujjlenyomat.py` | SHA-256 fingerprint of the `program\` folder for verifying updates |
| `tesztek\` | 400 tests (2026-10-02). Every network and Telegram call is a **mock**; for the external Windows commands (`icacls`, `Get-Acl`, `tasklist`), however, **real, read-only smoke tests** also run on a temporary folder (`test_jogok.ValodiFustTeszt`) |

Run from the `or\` folder:

```
python -m unittest discover -s tesztek -v
```

---

## Decisions and open questions

Wherever the specification had gaps, I chose the **stricter** interpretation.

1. **The draft MUST contain all six fields.** The spec says
   "accepts no other field"; it does not say how a missing field is handled.
   Stricter: a missing field is also a rejection (`hianyzo_mezo:…`).
   `megjegyzes_tulajnak` may be an empty string, but it must be present.
2. **"IGEN" literally, WITHOUT normalization.** Cyrillic "ИГЕН" and full-width
   "ＩＧＥＮ" are **not** approvals. The spec says "exactly IGEN";
   admitting look-alike forms would widen it.
3. **`verzio: true` is not a version number.** In Python `bool` is a subtype of
   `int` — this is excluded separately.
4. **A stuck `kuldes_alatt` row does NOT stop the whole guard.** The item
   goes to manual review, the rest are processed (finding Kö'1).
5. **Temporary vs. final rejection.** An item that hits the caps, the pending limit or a
   network error is **not closed permanently**: the draft stays in the
   protected `allapot\varo\` folder (before the preview), or the
   preview awaiting approval stays `var` (after the preview), and the owner can
   approve it again later. Every other reason — rule violation, vanished or changed
   target, unwalkable thread — is **final**. A draft stuck in the waiting area
   for more than seven days is closed.
5/b. **A foreign thread is NOT a rejection, but INITIATED.** Previously the guard
    rejected every reply whose thread also contained a third party.
    This was both too strict for the honest case (on Bluesky it is
    everyday for someone to address us in someone else's conversation) and — because of
    the mirror-image attack — too lenient for the dishonest one. The right answer is not
    prohibition, but **making it visible**: a capitalized preview warning, naming
    the thread owner, and the narrower daily cap of two. The decision
    is the owner's.
11. **The note field is ONE line, at most 80 characters.** The specification allows 200,
    but this field sits directly above the fixed approval line: with multiple lines
    it could draw a "paragraph" there. A note containing a line break or exceeding
    80 characters is therefore rejected, not truncated.
12. **The outgoing text is never rewritten for display purposes.** We would rather
    reject the draft — that way the preview is byte-exact.
6. **Before the preview is sent, we write the record** with `elokeszites` status.
   If the run is interrupted during sending, this record cannot be approved
   (it has no `message_id`), and it is closed after 5 minutes (finding M'5).
7. **The guard NEVER creates the `kozos\be` and `program`/`titok`/`allapot` folders.**
   If we created them, they would come into existence with inherited (possibly wrong)
   permissions — we would rather stop.
8. **The outgoing text does not go into the status file, only its sha256.** The spec
   says "without secrets"; the outgoing text is not a secret, but the contents of
   `kozos\be\` go back to Claude, and we have no reason to feed it back.
9. **`--ki-vagyok` is the ONLY place where an identifier goes to the console**, and
   even there nothing is written to a file.
10. **The block patterns and the allowlist are in the `program\` folder**, which
    `agent-user` cannot write — the spec asks for this, and it closes finding Kö'3.

### Open risks — the code does NOT solve these

1. **The `agent-user` account is currently a member of the Administrators group.** This is the
   most important remaining risk: the Administrators group has full rights on the `titok\` folder
   (without that the folder would be unmanageable), so
   `agent-user` can read the secret and rewrite the state **with elevation (UAC)**.
   Not without elevation — and the session running Claude runs
   without elevation. The decision is the owner's; **chapter 0** of `INSTALL.md`
   describes two variants (A: it stays an administrator, UAC "Always
   notify"; B: ENABLE and TEST a separate administrator account
   FIRST, and only then remove `agent-user`), together with the way back. Important: the
   spare administrator accounts on the machine **are disabled**, so
   removing `agent-user` by itself would leave the machine without a usable administrator.
   At startup the guard **warns** if `agent-user` is an administrator — and the
   warning does not go only to the console, but also into the `kozos\be\allapot.json`
   file **and** the startup Telegram message.
2. **The Python installation.** If the guard runs a Python that `agent-user` can
   write, then `agent-user` can smuggle code into the guard. On the machine the `C:\Python312`
   folder granted **Modify** to `Authenticated Users` — **so that
   Python MUST NOT be used**. `C:\Python314` is fine
   (`Users: read+execute`). See chapter 4 of the installation guide.
2/b. **The source of the code is the `agent-user` desktop.** Updates bring code from here
   onto the trusted side. `ujjlenyomat.py` produces a SHA-256 list of the folder, but this
   is only worth something if the owner records the combined fingerprint on a
   channel that the `agent-user` side cannot rewrite afterwards (per installation
   step 5/b: a photo with the phone). An update **never overwrites** the `*.json` rule files —
   that is a separate, explicit step.
2/c. **Entering the secrets.** If that happened on the `agent-user` desktop, a deceived
   agent could read the token and the app password from a screenshot; with the
   app password the guard can be bypassed completely. That is why chapter 4
   of the installation guide places this in the administrator account's OWN session, and the
   app password and the bot token must be created ON THE PHONE and
   typed in from there.
3. **The block filter does not understand Hungarian.** A net against accidental typos;
   personal data that is paraphrased passes through it.
4. **The account is public.** What we post can be saved; deletion is not
   retraction. That is another reason for the daily cap of 5.
5. **If the app password is stolen**, a stranger could post. Damage limit: it cannot delete the
   account, cannot change the main password, and can be revoked at any time.
6. **A faulty or malicious PDS can make the guard mute.** The upper bound for paging our own feed
   is `FEED_MAX_OLDAL = 10` pages, fifty each. If the
   24-hour window cannot be reached even with that many (because the service always returns a cursor
   full of fresh items), the guard **deliberately stays mute** with `UzemHiba`:
   the cap cannot be verified, so we do not post. This is fail-closed, so the right
   direction, but you need to know about it: in that case **nothing goes out**, and the
   message in `kozos\be\allapot.json` says why ("paging our own feed
   did not reach the end of the 24-hour window"). The untrusted user cannot
   trigger this, because it is not the one posting to our account.
7. **Silencing by handle switching.** If someone changes their handle after every approval,
   the last check before sending stops with the reason
   `handle_megvaltozott`, and this is **not a deferrable reason** — the item
   becomes finally rejected. A persistent party can thus **permanently
   prevent us from replying to them**. This is a deliberate trade-off: the protection
   ensures the owner never sees a recipient other than the one who actually
   receives the reply. The price is acceptable — but you need to know about it. Practical
   sign: a new preview is needed again and again for the same thread.
8. **We do not reply into very deep threads.** The ancestor chain is walked up to
   `FELMENO_MAX_LEPES = 25` steps; for a deeper conversation the
   item is **finally** rejected with the reason `felmeno_lanc_tul_hosszu`. The same applies to a
   **deleted intermediate post** (`felmeno_nem_igazolhato` /
   `gyoker_nem_igazolhato`): we do not reply into what we cannot walk through.
   Both happen on Bluesky, so this is not a bug, but a limitation. The
   `ok_magyarul` field of the status file then says what happened, and that
   it is worth replying directly to the post at the top of the thread. Side effect: a
   deep thread costs one `getPosts` call per step.

### If something goes wrong

1. **Revoke the Bluesky app password** (Settings → Privacy and security →
   App passwords → Revoke). This stops posting immediately.
2. **Stop the scheduled task** (see chapter 11 of the installation guide).
3. `C:\BskyGuard\allapot\naplo.jsonl` shows what happened and when.
4. `C:\BskyGuard\kozos\be\allapot.json` states in human language what is waiting, what went
   out, what was rejected and why.
