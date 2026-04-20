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
from typing import Literal, Optional

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
    size_category: Optional[int] = Field(default=None, ge=1, le=5)
    # Income band — Berlin's 100/140/160/180 multipliers of the baseline limit.
    income_category: Optional[IncomeCategory] = None


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


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: Literal["DE"] = "DE"
    city: str
    radius_km: int = Field(ge=0, le=500)
    district_allowlist: list[str] = Field(default_factory=list)
    home_lat: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    home_lng: Optional[float] = Field(default=None, ge=-180.0, le=180.0)

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
    min_contract_months: Optional[int] = Field(default=None, ge=0)

    wbs: WBS = Field(default_factory=WBS)
    notifications: Notifications = Field(default_factory=Notifications)

    @model_validator(mode="after")
    def _ranges_are_ordered(self) -> "Profile":
        if self.rent_max_warm < self.rent_min_warm:
            raise ValueError("rent_max_warm must be >= rent_min_warm")
        if self.rooms_max < self.rooms_min:
            raise ValueError("rooms_max must be >= rooms_min")
        return self

    @model_validator(mode="after")
    def _wbs_fields_required_when_yes(self) -> "Profile":
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
    def load_example(cls) -> "Profile":
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
