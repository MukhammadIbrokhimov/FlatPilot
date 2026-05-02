# Saved-Searches Power-User Design

**Beads:** FlatPilot-6o3 (per-saved-search notification routing), FlatPilot-d36 (wizard support for multiple saved searches + cap/cooldown tuning)

**Date:** 2026-05-02

**Status:** Design — implementation pending

---

## 1. Motivation

Two bundled bumps to the saved-searches feature that landed with auto-apply (FlatPilot-f2fa, PR #32):

1. **Per-saved-search notification routing.** Today the dispatcher routes every match to base profile channels, regardless of which saved search produced the match. Users running multiple named searches want to scope notifications: *"Telegram me Kreuzberg hits, email me Spandau hits, ping the shared roommate chat for any 3-bedroom."*

2. **Wizard support for multiple saved searches and cap/cooldown tuning.** `flatpilot init` today offers a single y/N that appends an `auto-default` search. Power users running 2–5 named searches need add/edit/delete from the wizard, plus interactive tuning of `daily_cap_per_platform` and `cooldown_seconds_per_platform`.

These ship in one PR because they share the touch-points: `SavedSearch` schema, `wizard/init.py`, `notifications/dispatcher.py`. Splitting them would mean writing the wizard's notifications-override prompts twice (once empty, once filled).

## 2. Scope

**In scope:**
- `SavedSearchNotifications` pydantic model with optional per-channel transport overrides.
- Dispatcher channel-selection logic that walks `matches.matched_saved_searches_json` to pick channels and resolve transports per match.
- Adapter signature extension so `telegram.send` / `email.send` accept transport overrides.
- Backwards-compatible `notified_channels_json` parsing (legacy bare channel names continue to dedup correctly).
- `flatpilot init` saved-searches menu loop (add / edit / delete / caps & cooldowns / done).
- Doctor row: validate that any saved-search-defined env vars resolve.
- Test coverage at the level the rest of the codebase carries (≥95% line coverage on changed modules).

**Out of scope:**
- DB schema changes (none needed — `profile_hash` already re-keys match rows on profile edits).
- `pacing_seconds_per_platform`, `max_failures_per_flat` wizard prompts (stay JSON-editable).
- Dashboard UI for per-search notifications.
- Per-saved-search `flatpilot notify --test` (kept on base profile only — separate bead if requested).

## 3. Schema

### 3.1 `src/flatpilot/profile.py` additions

```python
class TelegramNotificationOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    bot_token_env: str | None = None  # None = inherit base
    chat_id: str | None = None         # None = inherit base


class EmailNotificationOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    smtp_env: str | None = None        # None = inherit base


class SavedSearchNotifications(BaseModel):
    model_config = ConfigDict(extra="forbid")
    telegram: TelegramNotificationOverride | None = None
    email: EmailNotificationOverride | None = None


class SavedSearch(BaseModel):
    # ... existing fields unchanged ...
    notifications: SavedSearchNotifications | None = None
```

### 3.2 Why separate `*Override` models

Base `TelegramNotification` / `EmailNotification` require always-set string fields with sensible defaults. Override variants need `None`-as-fallthrough to be distinguishable from "explicitly set to empty string." A separate model keeps that semantic clean and `extra="forbid"` keeps typo protection.

**Forward-compat note:** `extra="forbid"` on `SavedSearchNotifications` will reject any future channel addition (e.g. a Discord channel) until that channel field is added to the model. That's intentional — typo protection beats silent acceptance — but warrants a code comment so the future-channel adder doesn't get confused.

### 3.3 Validators

No new validators. Existing `_saved_search_names_unique` and `_ranges_are_ordered` cover everything that needs invariants.

### 3.4 Migration

None. `notifications: None` is the default for every existing saved search; behavior under semantic A clause 1 (Section 4) is identical to current production.

## 4. Dispatcher behavior (semantic A″ — per-channel replace)

### 4.1 Per-match channel resolution

For each match row, given `matched_saved_searches_json` and the profile's saved-search definitions, resolve channels **per channel**, not per definer:

1. Collect the `notifications` blocks from every matched search that has one (the "definers"). Searches without a `notifications` block are silent and contribute nothing.

2. For each channel `ch` ∈ {`telegram`, `email`}:
   - Build `overrides_for_ch = [ss.notifications.<ch> for ss in definers if ss.notifications.<ch> is not None]`.
   - **Empty list** (no definer has an opinion on `ch`) → fire base `profile.notifications.<ch>` if it's enabled. **Today's behavior preserved exactly when no saved search overrides this channel.**
   - **Non-empty:** definers *replace* base for this channel.
     - For each override with `enabled=True`: fire the channel with the definer's resolved transport (Section 4.2).
     - Overrides with `enabled=False` contribute nothing to the fire set.
     - If every override has `enabled=False`: channel is suppressed for this match (no fires, base also does not fire — definers replaced base).

3. Multiple definers on the same channel each contribute their own (channel, resolved transport) tuple. Identical resolved transports collapse via signature dedup (Section 4.3); distinct resolved transports each fire separately.

**Why this composes well:**
- A non-defining matched search never silences anything (no surprise suppression — the I1 problem is gone).
- The bead's stated example *"Telegram me only for kreuzberg-2br matches"* is expressed as `notifications.email.enabled=False` on `kreuzberg-2br`, which actively suppresses email for that search's matches.
- The roommate routing case from Q2/B works: a saved search with `telegram.enabled=True, chat_id="roommate_chat"` replaces base's solo chat for matches against that search.

**Multi-definer caveat (read carefully).** "Definers replace base" applies *per channel*, not *per send*. If two distinct definers each have `enabled=True` overrides on the same channel that resolve to *different* transports, both fire. In particular, a definer with `enabled=True` and all transport fields `None` (i.e. inherit base) resolves to base values and produces signature `<channel>:base` — which fires alongside any sibling definer's transport-specific override. This is intentional: it's the only way for a saved search to express *"enable this channel even if base has it disabled"* without committing to an alternate transport. Users who want strict single-routing should ensure each search either fully overrides the transport or omits the channel entirely.

**Worked dual-match example A (no transport collision):** base = `{telegram=solo_chat, email=base}`. `kreuzberg-2br.notifications = {telegram: enabled=True chat=k_chat, email: None}`. `spandau-cheap.notifications = None`. A flat matches both.
- Telegram: overrides_for_ch = `[{enabled=True chat=k_chat}]`. Non-empty, definer replaces base → fire telegram@k_chat (NOT base's solo_chat).
- Email: overrides_for_ch = `[]`. Empty → fire base email.
- Result: telegram@k_chat + email base.

**Worked dual-match example B (multiple definers, distinct transports).** base = `{telegram=solo_chat}`. `kreuzberg-2br.notifications = {telegram: enabled=True, chat=k_chat}`. `roommate-3br.notifications = {telegram: enabled=True, all transport fields None}`. A flat matches both.
- Telegram: overrides_for_ch = `[{chat=k_chat}, {chat=None→solo_chat}]`. Two distinct resolved transports → both fire.
- Result: telegram@k_chat + telegram@solo_chat. The user gets pinged in **both** chats. This is the documented behavior of mixed-transport multi-definer matches.

### 4.2 Per-channel transport resolution

For each fired channel under the override path, resolve transport field-by-field against base:
- `bot_token_env`: definer's value if set, else base.
- `chat_id`: definer's value if set, else base.
- `smtp_env`: definer's value if set, else base.

**Multi-recipient case:** two matched searches with `enabled=True` on the same channel and different `chat_id`s each fire separately (one send per distinct resolved chat_id). This is the explicit point of overridable transports per Q2/B.

### 4.3 Dedup signature

Today `notified_channels_json` stores bare channel names. Under the new model it stores **canonicalized transport signatures**:
- `"telegram:base"` — channel fires with the same transport base would have used.
- `"telegram:bot=BOT_TOKEN_ROOMMATE,chat=98765"` — at least one transport field differs from base.
- `"email:base"`, `"email:smtp=SHARED_SMTP"`.

**Canonicalization rule:** after resolving the transport (Section 4.2), compare the resolved values to the base profile's values for that channel. **If the resolved transport equals base's transport for every field, signature is `"<channel>:base"` regardless of which code path produced it.** This is load-bearing — without it, an override of `bot_token_env="TELEGRAM_BOT_TOKEN"` (which happens to equal base's default) would produce a different signature than the no-override path and double-fire on legacy rows.

Per-canonical (`sent_canonicals` in `dispatcher.py:159`) and per-match dedup both key off the signature, **not** the bare channel name. The existing `sent_canonicals[canonical_id]: set[str]` becomes `set[signature]`.

**Loop-body restructure required.** The current dispatcher loop (`dispatcher.py:159-192`) iterates `enabled_channels(profile)` (a list of bare channel names computed once per dispatch) and checks each against `notified | already_for_canonical`. Under the new model, the list of channels-to-consider is *per-match-row*: it's the (channel, signature, transport-kwargs) tuples produced by Section 4.1's per-channel resolution. So the loop body shifts from "for each channel in the global list, decide if pending" to "for each per-match (channel, signature, transport) tuple from this row's resolution, decide if pending against `notified | already_for_canonical` (now signature sets)." This is a structural rewrite of the loop body, not just a key swap; Section 8's ~90 LOC for `dispatcher.py` accounts for it.

**Backwards-compat parse:** rows containing bare channel names (`["telegram", "email"]`) are interpreted as `["telegram:base", "email:base"]`. Combined with the canonicalization rule, this guarantees: any match that under the old code path would have fired `(channel, base transport)` carries signature `<channel>:base` under the new code path too. **Invariant:** matches with no overriding definers always produce signatures of the form `<channel>:base`. Existing pending notifications never re-fire.

**Forward-write upgrade.** When the dispatcher fires a remaining-pending channel on a row whose `notified_channels_json` originally held legacy bare names, the writeback uses the new signature format. Example: row had `["telegram"]` (legacy), email is still pending, base resolves to no override → after firing email, writeback is `["email:base", "telegram:base"]`. Subsequent reads parse the upgraded format directly. Mixed legacy/new content cannot occur within a single row after any post-rollout fire.

### 4.4 Adapter signature change

```python
# notifications/telegram.py — current signature uses `text: str`
def send(profile: Profile, text: str, parse_mode: str = "HTML",
         *, bot_token_env: str | None = None, chat_id: str | None = None) -> None: ...

# notifications/email.py
def send(recipient: str, subject: str, plain: str, html: str,
         *, smtp_env: str | None = None) -> None: ...
```

When a kwarg is `None` the adapter reads from the profile (today's behavior). When set, it overrides. Existing callers (`send_test`) pass nothing and behavior is unchanged. The dispatcher always pre-resolves and passes non-`None` values for overridden channels.

### 4.5 Misconfiguration handling

- Saved-search defines `notifications.email.enabled=True` but no override AND base has no usable `smtp_env` → log a warning, skip that channel for that match, dispatch continues. Consistent with today's `_email_recipient()` `None` handling.
- `bot_token_env` resolves to a missing env var → telegram_adapter raises `TelegramError`, dispatcher already catches and logs.
- A matched search references a saved-search **name not present in the current profile** (e.g. saved search was deleted between match-time and dispatch-time): treat as a non-definer (silent), log debug. The hash rotation that fires on profile edits would normally suppress these rows via `_mark_stale_matches_notified`, but a profile-edit-then-`flatpilot notify`-without-`flatpilot match` race could leak through.
- The same race exists for the auto-apply path (`auto_apply.py:158` reads `matched_saved_searches_json`). Verified handled at `auto_apply.py:180-182`: `saved_search_by_name.get(name)` returns `None`, the loop `continue`s. No spec change to `auto_apply.py` required.

### 4.6 `send_test`

Unchanged. Pings base profile channels only. Per-search test pings deferred.

## 5. Wizard menu loop

Replaces `wizard/init.py:179-186` (the legacy y/N + `_maybe_add_auto_apply` helper, both deleted). New section sits at the same point in the flow: after Notifications, before Review.

### 5.1 Top-level menu

```
Saved searches & auto-apply
───────────────────────────
  1. auto-default     auto-apply ✓  platforms: any  notifications: base
  2. kreuzberg-2br    auto-apply ✓  platforms: wg-gesucht, kleinanzeigen  notifications: telegram only

[a]dd  [e]dit N  [d]elete N  [c]aps & cooldowns  [done]
> _
```

When the list is empty, the menu shows `[a]dd  [c]aps & cooldowns  [done]` only.

The table is **reprinted from scratch** after every action. Indices are 1-based positional (`saved_searches[i-1]`) and renumber after deletions, so `[e]dit 2` always means "the row currently labeled 2." No persistent IDs.

Notifications summary values: `"base"`, `"telegram only"`, `"email only"`, `"telegram+email"`, `"none (silenced)"`, `"telegram (override) + email"`, etc. Format: per channel, append `" (override)"` if any transport field is non-`None`.

### 5.2 Add / edit sub-flow

Per Q4/C — **tiered**: 4 minimal prompts plus an optional filter-overrides branch.

1. **Name** — `Prompt.ask`, regex `^[a-z0-9_-]+$` enforced via re-prompt loop. Edit → existing name as default. Add → no default (required).
2. **Auto-apply** — `Confirm.ask`. Default: edit → current; add → False.
3. **Platforms** — `Prompt.ask` for comma-separated list, validated against `{wg-gesucht, kleinanzeigen, inberlinwohnen}`. Empty = all platforms.
4. **Notifications override** — `Confirm.ask "Override notifications for this search?"` (default: edit → `current is not None`; add → False).
   - If yes, show the explainer:
     > For each channel you set here, this search **replaces** base profile's setting. Channels you leave as "no opinion" still use base. Set `enabled=False` to suppress a channel for this search.
   - For each channel `ch` ∈ {telegram, email}:
     - `Confirm.ask "<ch>: have an opinion for this search?"` (default: edit → `notifications.<ch>` is non-`None`; add → False).
     - If yes:
       - `Confirm.ask "<ch> enabled for this search?"` (default: edit → current.enabled, else False). This is the only field that distinguishes "explicitly suppress" from "explicitly enable."
       - If enabled, prompt the override transport fields (blank input = inherit base for that field):
         - Telegram: `bot_token_env`, `chat_id`.
         - Email: `smtp_env`.
     - If no → corresponding `notifications.<ch>` is `None` (channel inherits base).
   - If both channels are "no opinion" → store `notifications=None` (avoid persisting an empty `SavedSearchNotifications` block).
   - If no → `notifications=None`.
5. **Filter overrides** — `Confirm.ask "Customize filter overrides for this search? [y/N]"` (default: edit → any overlay field is non-`None`; add → False).
   - If yes, walk the 8 overlay prompts. Two prompt patterns based on field type:
     - **Int fields** (`rent_min_warm`, `rent_max_warm`, `rooms_min`, `rooms_max`, `radius_km`, `min_contract_months`): single prompt; blank input → `None` (inherit base). Matches existing `_prompt_optional_int` (`wizard/init.py:352-367`).
     - **List/Literal fields** (`district_allowlist`, `furnished_pref`): two-step *override-then-value* pattern (matches the parent step 5 question's UX, applied per field):
       - `district_allowlist`: `Confirm.ask "Override district allowlist for this search? [y/N]"` (default: edit → `current is not None`; add → False). If no → `None`. If yes → `Prompt.ask "Districts (comma-separated; blank = any district)"`. Blank input → `[]` (override-to-empty); `"kreuzberg, mitte"` → `["kreuzberg", "mitte"]`.
       - `furnished_pref`: `Confirm.ask "Override furnished preference for this search? [y/N]"`. If no → `None`. If yes → `Prompt.ask` with `choices=["any", "furnished", "unfurnished"]`. No undiscoverable sentinel needed.

After save, return to top menu and reprint the table.

### 5.3 Delete sub-flow

`Prompt.ask "Delete '<name>'? Confirm name to delete:"` — typing the name deletes; anything else aborts. Tight guard against fat-fingered numbered deletion.

**No stale-match warning.** Profile-edit hash rotation (Section 6 risk 2) re-keys match rows; a deleted saved search's references in `matches.matched_saved_searches_json` are absorbed by the dispatcher's "name no longer in profile" handling (Section 4.5).

### 5.4 Caps & cooldowns sub-flow (per Q5/A — walk all platforms)

```
Caps & cooldowns
────────────────
  Platform: wg-gesucht
    Daily cap (default 20): _
    Cooldown seconds (default 120): _
  Platform: kleinanzeigen
    ...
  Platform: inberlinwohnen
    ...
```

Iterates `sorted(profile.auto_apply.daily_cap_per_platform.keys())` so adding a fourth platform later doesn't require wizard changes. Defaults are the user's current values. Returns to the top menu after the last platform.

### 5.5 Re-run handling

A profile with an existing `auto-default` (or any other saved searches) enters the menu showing them as rows. **The legacy "silently skip" path is removed.** `auto-default` has no special protection; the user can edit or delete it from the menu.

### 5.6 Validation

Pydantic re-validates the whole `Profile` at the existing point in the wizard (`Profile(**payload)`). Menu-level uniqueness check before save avoids the `_saved_search_names_unique` validator path.

## 6. Risks

### Risk 1 — Channel replacement, not addition

Under semantic A″, defining a channel for a saved search **replaces** base profile's setting for that channel on matching flats. A user setting `kreuzberg-2br.telegram.chat_id="k_chat"` will no longer get those matches in their base solo chat — pings route only to `k_chat`, *provided no other matched search also has a definer for telegram*. When two matched searches both have telegram definers, signature dedup applies: identical resolved transports collapse to one send, distinct transports each fire (Section 4.1's "Multi-definer caveat" and worked example B). This is the intended Q2/B behavior, but it's worth surfacing in the wizard so the user doesn't expect "additional" routing in the single-definer case or "single send" in the multi-definer case.

**Mitigation:** the wizard's notifications-override explainer (Section 5.2 step 4) frames it explicitly: *"For each channel you set here, this search **replaces** base profile's setting for matches against this search alone. If two of your saved searches both override the same channel and a flat matches both, you may be pinged on each distinct destination."* No "surprise silencing" failure mode like A/I1 had — non-defining searches contribute nothing, so they can never silence channels.

### Risk 2 — Profile-hash rotation drops queued notifications

Any saved-search edit (including just adding a `notifications` block) bumps `profile_hash` because it's computed from `model_dump_json()` (`profile.py:228-239`). On the next dispatcher run, `_mark_stale_matches_notified` (`dispatcher.py:93-116`) suppresses every pending match under the old hash. Pending notifications that hadn't fired yet are silently dropped.

**In practice:** the `flatpilot run` orchestrator re-matches before notifying, so it self-heals (matches are re-created under the new hash and dispatched). The failure mode only hits a user who edits the profile and then runs `flatpilot notify` standalone.

**Mitigation:** **(a) document as expected behavior.** Adding a "carve filter-only fields out of the hash" refactor would touch the entire matcher/dispatcher/auto-apply/doctor stack — out of scope for this PR. README + wizard exit message will note: *"After editing saved searches, run `flatpilot run` (not `flatpilot notify` alone) so pending matches are re-evaluated under the new profile."*

### Risk 3 — Backwards-compat parse correctness

If `_parse_channels` mishandles a legacy `notified_channels_json` row, every existing pending notification fires again on the next run.

**Mitigation:** the canonicalization invariant in Section 4.3 (matches with no overriding definers always produce `<channel>:base` signatures, which equals the legacy parse) plus an explicit dispatcher test for the legacy parse case (Section 7).

### Risk 4 — Adapter signature change

Extending `telegram_adapter.send` / `email_adapter.send` to accept overrides touches code outside the dispatcher. Change is additive — new optional kwargs with `None` defaults — so blast radius is contained to two function signatures.

### Risk 5 — Wizard menu UX divergence

This is the first menu-shaped section in `flatpilot init`. If users dislike it, the rest of the wizard still works. Rollback path: revert the wizard hunk only — schema and dispatcher additions are independent and backward-compatible.

### Risk 6 — `notified_channels_json` signature format

Once we write signatures like `"telegram:chat=123"`, downgrading to a previous FlatPilot version would see unrecognized strings in those rows. Acceptable for a personal-use project on `main`; at worst a few duplicate notifications on first post-revert run.

## 7. Tests

### Schema (`tests/test_profile.py`)

- `SavedSearchNotifications` with `notifications=None` round-trips identically.
- Override with `bot_token_env=None` keeps it `None` after JSON round-trip (not `""`).
- `extra="forbid"` rejects unknown fields on all three new models.
- A saved search with both channels' `enabled=False` (explicit silence) is structurally valid.

### Dispatcher (`tests/test_dispatcher.py`)

**Per-channel resolution under A″:**
- No saved searches matched (`matched_saved_searches_json='[]'`) → uses base profile channels exactly. Regression for current behavior.
- Matched search with `notifications=None` → uses base profile channels (silent matched search contributes nothing).
- Matched search defining only `telegram` (with `email=None`) → telegram from override, email **inherits base** (NOT silenced — codifies A″ vs the rejected I1 semantic).
- Matched search with `telegram.enabled=False` → telegram explicitly suppressed for that match, email inherits base.
- Two matched searches, one defines telegram override, the other has `notifications=None` → only one telegram override fires (the definer); email inherits base.
- Two matched searches both defining different `chat_id`s on telegram → telegram fires twice with two distinct resolved transports; `notified_channels_json` carries two distinct signatures.
- Two matched searches with identical `chat_id` override → fires once (dedup by resolved transport).
- Two definers, one `enabled=True` and one `enabled=False` on the same channel → channel fires once (`enabled=True` wins; `enabled=False` overrides contribute nothing to fire set, do not suppress siblings).
- All definers on a channel have `enabled=False` → channel suppressed (no fires, base does not fire either).

**Transport resolution:**
- Override with `bot_token_env=None`, `chat_id="123"` → `telegram_adapter.send` called with `bot_token_env=None`, `chat_id="123"` (kwargs); adapter resolves base bot token internally.
- Override declares email but neither override nor base sets `smtp_env` → channel skipped, warning logged, dispatch continues.

**Signature canonicalization:**
- Override resolves to base values for every transport field → signature is `"telegram:base"` (NOT `"telegram:bot=...,chat=..."`). Codifies the canonicalization invariant from Section 4.3.
- Override differs from base on at least one field → signature includes the differing values.
- `sent_canonicals` per-canonical dedup uses signatures (not bare channel names): two match rows in the same canonical cluster, one with override, one without — both fire (different signatures), but a third row matching the same override in the same cluster is deduped.
- **Cross-row dedup with mixed enable/disable:** two match rows in the same canonical cluster, one with `enabled=True` override and one with `enabled=False` override on the same channel. The disabled row contributes nothing to its own fire set; the enabled row fires once and is recorded in `sent_canonicals`. A third row in the cluster with no override would, under canonicalization, dedup against the enabled row's signature only if the resolved transports match — confirms `sent_canonicals` carries signatures across all rows in a canonical cluster.

**Multi-definer cases (Section 4.1 worked example B):**
- Definer X with `enabled=True, all transport fields None` plus definer Y with `enabled=True, chat_id="B"` on the same channel → two distinct signatures (`<channel>:base` and `<channel>:chat=B`) both fire. Codifies the multi-definer caveat in Section 4.1.

**Backwards-compat:**
- `notified_channels_json=["telegram", "email"]` (legacy) treated as `["telegram:base", "email:base"]`. A new pending dispatch that resolves to `<channel>:base` does not re-fire.
- `notified_channels_json='[]'` (empty array, the schema default) parses to empty set; pending dispatch fires normally and writes the new signature format. Regression test against accidental "missing column" handling regressions.
- **Forward-write upgrade:** row had `["telegram"]` (legacy, partial), email is still pending under no-override → after dispatch, row contains `["email:base", "telegram:base"]` (new format). Verify by reading the row post-dispatch and confirming both legacy and new entries coexist as upgraded signatures.
- Profile edit re-keys matches — adding a saved search bumps `profile_hash`, `_mark_stale_matches_notified` suppresses old rows. Regression test pinning Risk 2 behavior.

**Edge cases:**
- `matched_saved_searches_json` references a saved-search name no longer in the profile → treated as silent (non-definer), debug log emitted, dispatch continues.

### Wizard (`tests/test_wizard.py`)

- Pure helpers (regex name validation, platforms parser, transport-override builder) get unit tests.
- Menu loop integration test: patch `Prompt.ask`/`Confirm.ask` in sequence; drive add → edit → caps → done. Two start-states: empty profile and existing `auto-default`.
- Edit branch with "customize filter overrides? = no" leaves overlay fields `None`.
- Edit branch with "customize filter overrides? = yes":
  - district_allowlist: "Override district allowlist? = no" → `None`. "Override = yes" + blank list input → `[]`. "Override = yes" + `"kreuzberg, mitte"` → `["kreuzberg", "mitte"]`.
  - furnished_pref: "Override furnished preference? = no" → `None`. "Override = yes" + `"any"` → `"any"`.
- Delete sub-flow: typing wrong name aborts; typing right name removes; menu reprints with renumbered indices.
- Caps menu: iterates `daily_cap_per_platform.keys()` sorted; blank input keeps current value.
- Re-run with existing `auto-default` enters the menu (no longer silently skipped).
- Notifications override sub-flow: "no opinion" on both channels → `notifications=None` (not an empty `SavedSearchNotifications` block).

### Doctor (`tests/test_doctor.py`)

- Saved search with `notifications.telegram.bot_token_env="MISSING_VAR"` → doctor row reports failure.
- Saved search with `notifications=None` → doctor row passes.

## 8. Files touched (estimate)

| File | Change | LOC |
|---|---|---|
| `src/flatpilot/profile.py` | + `SavedSearchNotifications`, two override models, attach to `SavedSearch` | ~30 |
| `src/flatpilot/notifications/dispatcher.py` | per-match channel resolution, transport signatures + canonicalization, `sent_canonicals` keyed on signature, backwards-compat parse | ~90 |
| `src/flatpilot/notifications/telegram.py` | optional override kwargs in `send()` | ~10 |
| `src/flatpilot/notifications/email.py` | optional override kwargs in `send()` | ~10 |
| `src/flatpilot/wizard/init.py` | menu loop + add/edit/delete/caps sub-flows; non-int overlay grammars; remove `_maybe_add_auto_apply` | ~300 |
| `src/flatpilot/doctor.py` | one row for saved-search notification overrides | ~20 |
| `tests/` | coverage per Section 7 | ~200 |
| **Total** | | **~660** |

This is larger than 6o3's bead estimate (~+200 LOC) and d36's implied scope, but the canonicalization + `sent_canonicals` keying + non-int overlay input grammars surfaced during design review and are load-bearing for correctness.

## 9. Rollback

Single PR, single commit (or stack of small commits) on `feat/saved-searches-power-user`. Revert with `git revert`. No DB changes to undo. Existing match rows with new-format `notified_channels_json` would be re-parsed by old code as bare strings; at worst a small number of duplicate notifications on the first post-revert run. Acceptable.

## 10. Open ambiguities resolved in the design

- **Notification routing semantic — A″ (per-channel replace):** definers replace base for the channels they define; non-definers contribute nothing; `enabled=False` actively suppresses. (Reviewed away from earlier I1 / strict-replace semantic which had a silent-suppression footgun.)
- **Multi-search ping:** flat matching 2 searches with different `chat_id`s on the same channel pings *both* — overridable transport per Q2/B preserved.
- **Channels nobody overrides:** inherit base. Channels at least one definer overrides: replaced (no base fallback for that channel on that match).
- **Signature canonicalization:** if resolved transport equals base values for every field, signature is `<channel>:base` regardless of code path.
- **Stale name in `matched_saved_searches_json`:** treated as silent (non-definer) at dispatch time.
- **Wizard slot:** after Notifications, before Review.
- **`auto-default` status:** no special protection; deletable like any other.
- **Wizard re-prompt vs. validator-after-save:** invalid name pattern re-prompts inline; pydantic validator is the last-line check.
- **Non-int overlay grammars:** `district_allowlist` and `furnished_pref` use a two-step *override?-then-value* prompt pattern (no sentinels, no extra choice values to teach). Int fields keep the existing single-prompt blank-equals-inherit pattern from `_prompt_optional_int`.
- **Multi-definer transport collisions:** when two definers on the same channel resolve to distinct transports, both fire (signature dedup at `<channel>:resolved`). An all-`None` `enabled=True` override resolves to base values and produces signature `<channel>:base`, which can fire alongside a sibling's transport-specific override.
- **Profile-hash rotation:** documented as expected; users edit-then-`flatpilot run` (which re-matches) rather than edit-then-`flatpilot notify`.
