"""User profile schema and load / save helpers.

The profile is the single source of truth for what the matcher considers a
valid flat for this user. It's stored as JSON at ``config.PROFILE_PATH`` and
validated through pydantic so a typo in a boolean or an out-of-range rent
fails at load time rather than producing silent mismatches downstream.

A shipped example lives at ``src/flatpilot/profile.example.json``.
"""

from __future__ import annotations

from datetime import date
from importlib import resources
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from flatpilot.config import PROFILE_PATH

WBSStatus = Literal["none", "yes"]
IncomeCategory = Literal[100, 140, 160, 180]
EmploymentStatus = Literal["student", "employed", "self_employed", "other"]
FurnishedPref = Literal["any", "furnished", "unfurnished"]


class WBS(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: WBSStatus = "none"
    # Household-size category. WBS in Germany runs 1–5, where 5 covers any
    # household of 5 or more people.
    size_category: int | None = Field(default=None, ge=1, le=5)
    # Income band — Berlin's 100/140/160/180 multipliers of the baseline limit.
    income_category: IncomeCategory | None = None


class TelegramNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id: str = ""


class EmailNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # Prefix used to look up SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    # SMTP_FROM in the environment.
    smtp_env: str = "SMTP"


class Notifications(BaseModel):
    model_config = ConfigDict(extra="forbid")

    telegram: TelegramNotification = Field(default_factory=TelegramNotification)
    email: EmailNotification = Field(default_factory=EmailNotification)


class Attachments(BaseModel):
    """Which files to attach per rental platform when submitting an apply.

    All filenames are relative to ``config.ATTACHMENTS_DIR``
    (``~/.flatpilot/attachments/``). ``default`` is the fallback when a
    platform has no explicit ``per_platform`` entry — useful because
    most German landlords want the same pack (SCHUFA + Gehaltsnachweise
    + ID) regardless of listing site.
    """

    model_config = ConfigDict(extra="forbid")

    default: list[str] = Field(default_factory=list)
    per_platform: dict[str, list[str]] = Field(default_factory=dict)


class AutoApplySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_cap_per_platform: dict[str, int] = Field(
        default_factory=lambda: {
            "wg-gesucht": 20,
            "kleinanzeigen": 20,
            "inberlinwohnen": 20,
        }
    )
    cooldown_seconds_per_platform: dict[str, int] = Field(
        default_factory=lambda: {
            "wg-gesucht": 120,
            "kleinanzeigen": 120,
            "inberlinwohnen": 120,
        }
    )
    max_failures_per_flat: int = Field(default=3, ge=1)
    pacing_seconds_per_platform: dict[str, int] = Field(
        default_factory=lambda: {
            "wg-gesucht": 0, "kleinanzeigen": 0, "inberlinwohnen": 0,
        }
    )


class TelegramNotificationOverride(BaseModel):
    """Saved-search-scoped override of base profile's telegram channel.

    Any transport field left as ``None`` falls through to the base profile's
    value at dispatch time. ``enabled=False`` actively suppresses the channel
    for matches against this saved search.
    """
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    bot_token_env: str | None = None
    chat_id: str | None = None


class EmailNotificationOverride(BaseModel):
    """Saved-search-scoped override of base profile's email channel."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    smtp_env: str | None = None


class SavedSearchNotifications(BaseModel):
    """Per-saved-search notification routing override.

    A non-None block on a saved search marks that search as a *definer* for
    every channel it specifies. The dispatcher resolves channels per-match:
    definers replace base for the channels they define; non-defining matched
    searches contribute nothing.

    NOTE: ``extra="forbid"`` will reject any future channel addition until
    that channel field is explicitly added below. Typo protection beats
    silent acceptance.
    """
    model_config = ConfigDict(extra="forbid")

    telegram: TelegramNotificationOverride | None = None
    email: EmailNotificationOverride | None = None


class SavedSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    auto_apply: bool = False

    rent_min_warm: int | None = Field(default=None, ge=0)
    rent_max_warm: int | None = Field(default=None, ge=0)
    rooms_min: int | None = Field(default=None, ge=1)
    rooms_max: int | None = Field(default=None, ge=1)
    district_allowlist: list[str] | None = None
    radius_km: int | None = Field(default=None, ge=0, le=500)
    furnished_pref: FurnishedPref | None = None
    min_contract_months: int | None = Field(default=None, ge=0)

    platforms: list[str] = Field(default_factory=list)
    notifications: SavedSearchNotifications | None = None

    @model_validator(mode="after")
    def _ranges_are_ordered(self) -> SavedSearch:
        if (
            self.rent_min_warm is not None
            and self.rent_max_warm is not None
            and self.rent_max_warm < self.rent_min_warm
        ):
            raise ValueError("rent_max_warm must be >= rent_min_warm")
        if (
            self.rooms_min is not None
            and self.rooms_max is not None
            and self.rooms_max < self.rooms_min
        ):
            raise ValueError("rooms_max must be >= rooms_min")
        return self


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: Literal["DE"] = "DE"
    city: str
    radius_km: int = Field(ge=0, le=500)
    district_allowlist: list[str] = Field(default_factory=list)
    home_lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    home_lng: float | None = Field(default=None, ge=-180.0, le=180.0)

    rent_min_warm: int = Field(ge=0)
    rent_max_warm: int = Field(ge=0)

    rooms_min: int = Field(ge=1)
    rooms_max: int = Field(ge=1)

    household_size: int = Field(ge=1)
    kids: int = Field(ge=0)
    pets: list[str] = Field(default_factory=list)

    status: EmploymentStatus
    net_income_eur: int = Field(ge=0)
    move_in_date: date

    smoker: bool = False
    furnished_pref: FurnishedPref = "any"
    min_contract_months: int | None = Field(default=None, ge=0)

    wbs: WBS = Field(default_factory=WBS)
    notifications: Notifications = Field(default_factory=Notifications)
    attachments: Attachments = Field(default_factory=Attachments)
    auto_apply: AutoApplySettings = Field(default_factory=AutoApplySettings)
    saved_searches: list[SavedSearch] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ranges_are_ordered(self) -> Profile:
        if self.rent_max_warm < self.rent_min_warm:
            raise ValueError("rent_max_warm must be >= rent_min_warm")
        if self.rooms_max < self.rooms_min:
            raise ValueError("rooms_max must be >= rooms_min")
        return self

    @model_validator(mode="after")
    def _saved_search_names_unique(self) -> Profile:
        names = [ss.name for ss in self.saved_searches]
        if len(names) != len(set(names)):
            raise ValueError(
                f"duplicate saved-search names: {names}"
            )
        return self

    @model_validator(mode="after")
    def _wbs_fields_required_when_yes(self) -> Profile:
        if self.wbs.status == "yes":
            missing = [
                name
                for name, value in (
                    ("size_category", self.wbs.size_category),
                    ("income_category", self.wbs.income_category),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"wbs.status='yes' requires {', '.join(missing)} to be set"
                )
        return self

    @classmethod
    def example_path(cls) -> Path:
        return Path(str(resources.files("flatpilot") / "profile.example.json"))

    @classmethod
    def load_example(cls) -> Profile:
        return cls.model_validate_json(cls.example_path().read_text())


def load_profile(path: Path | None = None) -> Profile | None:
    p = path or PROFILE_PATH
    if not p.exists():
        return None
    return Profile.model_validate_json(p.read_text())


def save_profile(profile: Profile, path: Path | None = None) -> None:
    p = path or PROFILE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(profile.model_dump_json(indent=2))


def profile_hash(profile: Profile) -> str:
    """Stable 16-char SHA-256 prefix of the profile's JSON serialization.

    Any substantive profile change (rent band, rooms, WBS, districts, …)
    produces a new hash. Used by the matcher to key match rows so a
    profile change causes re-evaluation, and by the dispatcher to scope
    pending notifications to the current profile — otherwise matches
    computed under an older profile would still fire on the next pass.
    """
    import hashlib

    return hashlib.sha256(profile.model_dump_json().encode("utf-8")).hexdigest()[:16]
