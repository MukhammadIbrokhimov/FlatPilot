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

### 3.3 Validators

No new validators. Existing `_saved_search_names_unique` and `_ranges_are_ordered` cover everything that needs invariants.

### 3.4 Migration

None. `notifications: None` is the default for every existing saved search; behavior under semantic A clause 1 (Section 4) is identical to current production.

## 4. Dispatcher behavior (semantic A — strict opt-in)

### 4.1 Per-match channel resolution

For each match row, given `matched_saved_searches_json` and the profile's saved-search definitions:

1. **No defining searches** — if `matched_saved_searches_json` is empty OR none of its entries have a non-`None` `notifications` block, use base `profile.notifications` exactly as today. **Existing behavior preserved bit-for-bit.**

2. **At least one defining search** — collect the `notifications` blocks from every matched search that has one (the "definers"). For each channel (`telegram`, `email`):
   - The channel fires if any definer has `notifications.<channel>.enabled=True`.
   - Definers without an override for that channel are silent contributors — they do **not** add the channel and do **not** pull base profile's setting in.

   *Example:* flat matches search `kreuzberg-2br` (overrides telegram only, enabled=True) and `spandau-cheap` (no override). Base profile has email enabled. Result: telegram fires (with `kreuzberg-2br`'s override). Email does **not** fire — because `kreuzberg-2br` is a definer and stops the base-fallback for the entire match. This is the **I1 interpretation**, codified by an explicit test.

### 4.2 Per-channel transport resolution

For each fired channel, resolve the transport (telegram bot+chat, email smtp prefix) field-by-field:
- For each transport field (`bot_token_env`, `chat_id`, `smtp_env`): take the first matched-search override that sets it non-`None`. Else use base.
- **Multi-recipient case:** if two matched searches set *different* `chat_id`s for the same channel, the channel fires *twice* — once per distinct resolved transport. (This is the explicit point of overridable transports per Q2/B: roommate chat + solo chat both get pinged.)

### 4.3 Dedup signature

Today `notified_channels_json` stores bare channel names. Under the new model it stores **transport signatures**:
- `"telegram:base"` — no overrides resolved against this match.
- `"telegram:bot=BOT_TOKEN_ROOMMATE,chat=98765"` — overrides resolved.
- `"email:base"`, `"email:smtp=SHARED_SMTP"`.

Per-canonical and per-match dedup keys off the signature, so two searches with identical resolved transports collapse into one send; two searches with different transports each fire once.

**Backwards-compat parse:** rows containing bare channel names (`["telegram", "email"]`) are interpreted as `["telegram:base", "email:base"]`. Existing pending notifications do not refire.

### 4.4 Adapter signature change

```python
# notifications/telegram.py
def send(profile: Profile, body: str, parse_mode: str = "HTML",
         *, bot_token_env: str | None = None, chat_id: str | None = None) -> None: ...

# notifications/email.py
def send(recipient: str, subject: str, plain: str, html: str,
         *, smtp_env: str | None = None) -> None: ...
```

When a kwarg is `None` the adapter reads from the profile (today's behavior). When set, it overrides. Existing callers (`send_test`) pass nothing and behavior is unchanged.

### 4.5 Misconfiguration handling

- Saved-search defines `notifications.email.enabled=True` but no override AND base has no usable `smtp_env` → log a warning, skip that channel for that match, dispatch continues. Consistent with today's `_email_recipient()` `None` handling.
- `bot_token_env` resolves to a missing env var → telegram_adapter raises `TelegramError`, dispatcher already catches and logs.

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

Notifications summary values: `"base"`, `"telegram only"`, `"email only"`, `"telegram+email"`, `"none (silenced)"`.

### 5.2 Add / edit sub-flow

Per Q4/C — **tiered**: 4 minimal prompts plus an optional filter-overrides branch.

1. **Name** — `Prompt.ask`, regex `^[a-z0-9_-]+$` enforced via re-prompt loop. Edit → existing name as default. Add → no default (required).
2. **Auto-apply** — `Confirm.ask`. Default: edit → current; add → False.
3. **Platforms** — `Prompt.ask` for comma-separated list, validated against `{wg-gesucht, kleinanzeigen, inberlinwohnen}`. Empty = all platforms.
4. **Notifications override** — `Confirm.ask "Override notifications for this search?"` (default: edit → `current is not None`; add → False).
   - If yes, show the warning (Section 6 risk 1), then for each channel:
     - `Confirm.ask "<channel> for this search?"` (default: current.enabled if defined, else False).
     - If enabled, prompt the override fields (blank = inherit base):
       - Telegram: `bot_token_env`, `chat_id`.
       - Email: `smtp_env`.
   - If no → `notifications=None`.
5. **Filter overrides** — `Confirm.ask "Customize filter overrides for this search? [y/N]"` (default: edit → any overlay field is non-`None`; add → False).
   - If yes, walk 8 overlay prompts: `rent_min_warm`, `rent_max_warm`, `rooms_min`, `rooms_max`, `district_allowlist`, `radius_km`, `furnished_pref`, `min_contract_months`. Each blank = `None` (inherit base).

After save, return to top menu and reprint the table.

### 5.3 Delete sub-flow

`Prompt.ask "Delete '<name>'? Confirm name to delete:"` — typing the name deletes; anything else aborts. Tight guard against fat-fingered numbered deletion.

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

Defaults are the user's current values. Returns to the top menu after the third platform.

### 5.5 Re-run handling

A profile with an existing `auto-default` (or any other saved searches) enters the menu showing them as rows. **The legacy "silently skip" path is removed.** `auto-default` has no special protection; the user can edit or delete it from the menu.

### 5.6 Validation

Pydantic re-validates the whole `Profile` at the existing point in the wizard (`Profile(**payload)`). Menu-level uniqueness check before save avoids the `_saved_search_names_unique` validator path.

## 6. Risks

### Risk 1 — Surprise silencing (semantic A)

A user adds a saved search with `notifications` defining only Telegram, expecting "tell me about Kreuzberg on Telegram." If their base has email enabled, semantic A means email goes silent for any match against that search.

**Mitigation:** the wizard's notifications-override sub-flow shows a one-line warning before saving:

> ⚠ Defining notifications here means matches against THIS search will only fire on the channels you enable here, not the base profile's channels.

### Risk 2 — Backwards-compat parse correctness

If `_parse_channels` mishandles a legacy `notified_channels_json` row, every existing pending notification fires again on the next run.

**Mitigation:** explicit dispatcher test for the legacy parse case (Section 7).

### Risk 3 — Adapter signature change

Extending `telegram_adapter.send` / `email_adapter.send` to accept overrides touches code outside the dispatcher. Change is additive — new optional kwargs with `None` defaults — so blast radius is contained to two function signatures.

### Risk 4 — Wizard menu UX divergence

This is the first menu-shaped section in `flatpilot init`. If users dislike it, the rest of the wizard still works. Rollback path: revert the wizard hunk only — schema and dispatcher additions are independent and backward-compatible.

### Risk 5 — `notified_channels_json` signature format

Once we write signatures like `"telegram:chat=123"`, downgrading to a previous FlatPilot version would see unrecognized strings in those rows. Acceptable for a personal-use project on `main`; at worst a few duplicate notifications on first post-revert run.

## 7. Tests

### Schema (`tests/test_profile.py`)

- `SavedSearchNotifications` with `notifications=None` round-trips identically.
- Override with `bot_token_env=None` keeps it `None` after JSON round-trip (not `""`).
- `extra="forbid"` rejects unknown fields on all three new models.
- A saved search with both channels' `enabled=False` (explicit silence) is structurally valid.

### Dispatcher (`tests/test_dispatcher.py`)

- No saved searches matched (`matched_saved_searches_json='[]'`) → uses base profile channels exactly. Regression for current behavior.
- Matched search with `notifications=None` → uses base profile channels.
- Matched search with `notifications` defining only telegram=True → only telegram fires, even if base has email enabled.
- Two matched searches, one defines notifications, one does not → only the definer's channels fire (codifies I1).
- Two matched searches both defining different `chat_id`s → telegram fires twice with different transports; `notified_channels_json` carries two distinct signatures.
- Two matched searches with identical `chat_id` override → fires once (dedup by resolved transport).
- Override with `bot_token_env=None`, `chat_id="123"` → `telegram_adapter.send` called with base bot token + override chat_id.
- Override declares email but neither override nor base sets `smtp_env` → channel skipped, warning logged, dispatch continues.
- Backwards-compat parse — `notified_channels_json=["telegram", "email"]` (legacy) treated as `["telegram:base", "email:base"]` and not re-fired.
- Profile edit re-keys matches — adding a saved search bumps `profile_hash`, `_mark_stale_matches_notified` suppresses old rows.

### Wizard (`tests/test_wizard.py`)

- Pure helpers (regex name validation, platforms parser, transport-override builder) get unit tests.
- Menu loop integration test: patch `Prompt.ask`/`Confirm.ask` in sequence; drive add → edit → caps → done. Two start-states: empty profile and existing `auto-default`.
- Edit branch with "customize filter overrides? = no" leaves overlay fields `None`.
- Edit branch with "customize filter overrides? = yes" sets them and re-validates.
- Delete sub-flow: typing wrong name aborts; typing right name removes.
- Caps menu: walks all 3 platforms, blank input keeps current value.
- Re-run with existing `auto-default` enters the menu (no longer silently skipped).

### Doctor (`tests/test_doctor.py`)

- Saved search with `notifications.telegram.bot_token_env="MISSING_VAR"` → doctor row reports failure.
- Saved search with `notifications=None` → doctor row passes.

## 8. Files touched (estimate)

| File | Change | LOC |
|---|---|---|
| `src/flatpilot/profile.py` | + `SavedSearchNotifications`, two override models, attach to `SavedSearch` | ~30 |
| `src/flatpilot/notifications/dispatcher.py` | per-match channel resolution, transport signatures, backwards-compat parse | ~70 |
| `src/flatpilot/notifications/telegram.py` | optional override kwargs in `send()` | ~10 |
| `src/flatpilot/notifications/email.py` | optional override kwargs in `send()` | ~10 |
| `src/flatpilot/wizard/init.py` | menu loop + add/edit/delete/caps sub-flows; remove `_maybe_add_auto_apply` | ~200 |
| `src/flatpilot/doctor.py` | one row for saved-search notification overrides | ~20 |
| `tests/` | coverage per Section 7 | ~120 |
| **Total** | | **~460** |

## 9. Rollback

Single PR, single commit (or stack of small commits) on `feat/saved-searches-power-user`. Revert with `git revert`. No DB changes to undo. Existing match rows with new-format `notified_channels_json` would be re-parsed by old code as bare strings; at worst a small number of duplicate notifications on the first post-revert run. Acceptable.

## 10. Open ambiguities resolved in the design

- **Multi-search ping:** flat matching 2 searches with different `chat_id`s pings *both*.
- **Mixed override/no-override matches (I1):** a matched search without `notifications` is silent; it does not pull base profile in.
- **Wizard slot:** after Notifications, before Review.
- **`auto-default` status:** no special protection; deletable like any other.
- **Wizard re-prompt vs. validator-after-save:** invalid name pattern re-prompts inline; pydantic validator is the last-line check.
