"""InQsi production backend contracts.

This module defines the backend record shapes, storage contracts, and API route
contracts needed to move InQsi from frontend/admin scaffolds into real backend
wiring.

Locked rules:
- no saved slip can exceed three legs
- no fake/default market data
- no silent fallback when a table/provider is not wired
- public member score cards are opt-in
- connected social accounts are member-controlled and revocable
- subscriptions remain payment-provider neutral until the final provider is selected
- social posting is enabled by default after a member grants provider posting permissions
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

MAX_PARLAY_LEGS = 3
ISO8601 = str


def utc_now() -> ISO8601:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ContractError(RuntimeError):
    """Base error for backend contract violations."""


class BackendNotWiredError(ContractError):
    """Raised when a route/table is scaffolded but not connected to storage."""


class ValidationError(ContractError):
    """Raised when member input violates InQsi contract rules."""


class MemberRole(str, Enum):
    MEMBER = "MEMBER"
    ADMIN = "ADMIN"
    OWNER = "OWNER"


class MemberStatus(str, Enum):
    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELED = "CANCELED"
    SUSPENDED = "SUSPENDED"


class MemberPlan(str, Enum):
    MEMBER = "Member"
    FULL_ACCESS = "Full Access"
    MASTER = "Master"


class SlipVisibility(str, Enum):
    PRIVATE = "PRIVATE"
    PUBLIC_SCORE_ONLY = "PUBLIC_SCORE_ONLY"
    PUBLIC_FULL = "PUBLIC_FULL"


class SlipStatus(str, Enum):
    DRAFT = "DRAFT"
    SAVED = "SAVED"
    LOCKED = "LOCKED"
    GRADED = "GRADED"
    VOIDED = "VOIDED"


class LegResult(str, Enum):
    PENDING = "PENDING"
    WIN = "WIN"
    LOSS = "LOSS"
    PUSH = "PUSH"
    VOID = "VOID"


class ScoreWindow(str, Enum):
    SEVEN_DAY = "7D"
    THIRTY_DAY = "30D"
    LIFETIME = "LIFETIME"


class SubscriptionStatus(str, Enum):
    TRIALING = "TRIALING"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELED = "CANCELED"
    INCOMPLETE = "INCOMPLETE"


class PaymentProvider(str, Enum):
    NONE = "NONE"
    MANUAL = "MANUAL"
    PAYMENT_PROCESSOR = "PAYMENT_PROCESSOR"
    APP_STORE = "APP_STORE"
    GOOGLE_PLAY = "GOOGLE_PLAY"
    OTHER = "OTHER"


class AttributionTouch(str, Enum):
    FIRST = "FIRST"
    LAST = "LAST"


class SocialProvider(str, Enum):
    FACEBOOK = "FACEBOOK"
    INSTAGRAM = "INSTAGRAM"
    REDDIT = "REDDIT"
    X = "X"
    TIKTOK = "TIKTOK"
    YOUTUBE = "YOUTUBE"
    DISCORD = "DISCORD"
    LINKEDIN = "LINKEDIN"
    TWITCH = "TWITCH"
    SNAPCHAT = "SNAPCHAT"
    OTHER = "OTHER"


class SocialConnectionStatus(str, Enum):
    CONNECTED = "CONNECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


class AuditAction(str, Enum):
    MEMBER_CREATED = "MEMBER_CREATED"
    MEMBER_UPDATED = "MEMBER_UPDATED"
    SOCIAL_CONNECTED = "SOCIAL_CONNECTED"
    SOCIAL_REVOKED = "SOCIAL_REVOKED"
    SLIP_SAVED = "SLIP_SAVED"
    SLIP_GRADED = "SLIP_GRADED"
    PUBLIC_SCORE_UPDATED = "PUBLIC_SCORE_UPDATED"
    CREATOR_ATTRIBUTED = "CREATOR_ATTRIBUTED"
    SUBSCRIPTION_UPDATED = "SUBSCRIPTION_UPDATED"
    ADMIN_VIEWED = "ADMIN_VIEWED"
    ADMIN_UPDATED = "ADMIN_UPDATED"
    INDEXNOW_TRIGGERED = "INDEXNOW_TRIGGERED"
    SUPPORT_NOTE_CREATED = "SUPPORT_NOTE_CREATED"
    FEATURE_FLAG_UPDATED = "FEATURE_FLAG_UPDATED"


@dataclass(frozen=True)
class MemberRecord:
    member_id: str
    email: str
    created_at: ISO8601
    updated_at: ISO8601
    role: MemberRole = MemberRole.MEMBER
    status: MemberStatus = MemberStatus.TRIAL
    plan: MemberPlan = MemberPlan.FULL_ACCESS
    display_name: Optional[str] = None
    handle: Optional[str] = None
    state: Optional[str] = None
    primary_sport: Optional[str] = None
    public_profile_enabled: bool = False
    public_score_enabled: bool = False
    creator_ref: Optional[str] = None
    last_login_at: Optional[ISO8601] = None


@dataclass(frozen=True)
class SocialAccountRecord:
    connection_id: str
    member_id: str
    provider: SocialProvider
    provider_account_id: str
    provider_username: Optional[str]
    display_name: Optional[str]
    profile_url: Optional[str]
    status: SocialConnectionStatus
    connected_at: ISO8601
    updated_at: ISO8601
    revoked_at: Optional[ISO8601] = None
    scopes: List[str] = field(default_factory=list)
    access_token_secret_name: Optional[str] = None
    refresh_token_secret_name: Optional[str] = None
    token_expires_at: Optional[ISO8601] = None
    allow_public_badge: bool = False
    allow_creator_attribution: bool = False
    allow_social_posting: bool = True

    def validate(self) -> None:
        if not self.member_id:
            raise ValidationError("Social account must belong to a member.")
        if not self.provider_account_id:
            raise ValidationError("Social account must include provider_account_id.")
        if self.allow_social_posting and not self.scopes:
            raise ValidationError("Posting requires explicit provider scopes.")


@dataclass(frozen=True)
class SlipLegRecord:
    leg_id: str
    game_id: str
    sport: str
    market_type: Literal["moneyline", "spread", "total"]
    selection: str
    book: Optional[str] = None
    odds_american: Optional[int] = None
    line: Optional[float] = None
    result: LegResult = LegResult.PENDING
    graded_at: Optional[ISO8601] = None
    market_snapshot_id: Optional[str] = None
    risk_tags: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SavedSlipRecord:
    slip_id: str
    member_id: str
    created_at: ISO8601
    updated_at: ISO8601
    legs: List[SlipLegRecord]
    status: SlipStatus = SlipStatus.SAVED
    visibility: SlipVisibility = SlipVisibility.PRIVATE
    source: Literal["MANUAL", "AI_SLIP_SCANNER", "AI_SLIP_BUILDER"] = "MANUAL"
    score: Optional[float] = None
    post_game_review: Optional[str] = None

    def validate(self) -> None:
        if not self.member_id:
            raise ValidationError("Saved slip must belong to a member.")
        if len(self.legs) == 0:
            raise ValidationError("A saved slip must include at least one leg.")
        if len(self.legs) > MAX_PARLAY_LEGS:
            raise ValidationError(f"InQsi slips cannot exceed {MAX_PARLAY_LEGS} legs.")


@dataclass(frozen=True)
class ScoreHistoryRecord:
    score_id: str
    member_id: str
    window: ScoreWindow
    calculated_at: ISO8601
    total_slips: int
    graded_slips: int
    wins: int
    losses: int
    pushes: int
    accuracy_pct: float
    public_visible: bool = False


@dataclass(frozen=True)
class CreatorAttributionRecord:
    attribution_id: str
    member_id: Optional[str]
    anonymous_id: Optional[str]
    creator_ref: str
    touch: AttributionTouch
    first_seen_at: ISO8601
    last_seen_at: ISO8601
    landing_path: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    converted_at: Optional[ISO8601] = None
    paid_at: Optional[ISO8601] = None


@dataclass(frozen=True)
class SubscriptionRecord:
    subscription_id: str
    member_id: str
    provider: PaymentProvider
    status: SubscriptionStatus
    plan: MemberPlan
    created_at: ISO8601
    updated_at: ISO8601
    trial_ends_at: Optional[ISO8601] = None
    current_period_ends_at: Optional[ISO8601] = None
    provider_customer_id: Optional[str] = None
    provider_subscription_id: Optional[str] = None


@dataclass(frozen=True)
class AdminAuditLogRecord:
    audit_id: str
    actor_member_id: str
    action: AuditAction
    target_type: str
    target_id: str
    created_at: ISO8601
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SupportNoteRecord:
    support_id: str
    member_id: str
    created_at: ISO8601
    updated_at: ISO8601
    status: Literal["OPEN", "WATCHING", "CLOSED"]
    title: str
    note: str
    owner_note: Optional[str] = None


@dataclass(frozen=True)
class FeatureFlagRecord:
    flag_key: str
    enabled: bool
    updated_at: ISO8601
    updated_by: str
    description: str


@dataclass(frozen=True)
class ApiRouteContract:
    method: Literal["GET", "POST", "PATCH", "DELETE"]
    path: str
    auth_required: bool
    owner_only: bool
    description: str
    storage_required: List[str]
    live_data_required: bool = True


DYNAMODB_TABLE_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "members": {"env": "MEMBERS_TABLE", "pk": "member_id", "gsis": ["EmailIndex", "HandleIndex", "CreatorRefIndex"], "stores": "member identity, status, profile visibility, role, and source attribution"},
    "social_accounts": {"env": "SOCIAL_ACCOUNTS_TABLE", "pk": "member_id", "sk": "connection_id", "gsis": ["ProviderAccountIndex", "ProviderStatusIndex"], "stores": "member-owned OAuth/social connections and revocation status"},
    "saved_slips": {"env": "SAVED_SLIPS_TABLE", "pk": "member_id", "sk": "slip_id", "gsis": ["SlipIdIndex", "StatusIndex", "VisibilityIndex"], "stores": "member slips, max-three-leg compliance, visibility, scanner/builder source"},
    "score_history": {"env": "SCORE_HISTORY_TABLE", "pk": "member_id", "sk": "score_id", "gsis": ["WindowIndex", "PublicScoreIndex"], "stores": "rolling 7-day, 30-day, and lifetime member score cards"},
    "creator_attribution": {"env": "CREATOR_ATTRIBUTION_TABLE", "pk": "attribution_id", "gsis": ["CreatorRefIndex", "MemberAttributionIndex", "AnonymousAttributionIndex"], "stores": "first-touch and last-touch creator/referral attribution"},
    "subscriptions": {"env": "SUBSCRIPTIONS_TABLE", "pk": "member_id", "sk": "subscription_id", "gsis": ["ProviderCustomerIndex", "SubscriptionStatusIndex"], "stores": "trial, paid, past-due, cancellation, provider IDs"},
    "admin_audit_logs": {"env": "ADMIN_AUDIT_LOGS_TABLE", "pk": "target_id", "sk": "audit_id", "gsis": ["ActorIndex", "ActionIndex"], "stores": "owner/admin actions across members, slips, score cards, support, and flags"},
    "support_notes": {"env": "SUPPORT_NOTES_TABLE", "pk": "member_id", "sk": "support_id", "gsis": ["SupportStatusIndex"], "stores": "member support notes and owner/admin handling state"},
    "feature_flags": {"env": "FEATURE_FLAGS_TABLE", "pk": "flag_key", "stores": "launch controls for public score cards, challenges, social posting, and admin features"},
}


API_ROUTE_CONTRACTS: List[ApiRouteContract] = [
    ApiRouteContract("GET", "/v1/members/me", True, False, "Return authenticated member profile", ["members", "subscriptions", "social_accounts"]),
    ApiRouteContract("PATCH", "/v1/members/me", True, False, "Update member profile and privacy settings", ["members", "admin_audit_logs"]),
    ApiRouteContract("POST", "/v1/members/social/connect/start", True, False, "Begin provider OAuth connection", ["social_accounts", "admin_audit_logs"], live_data_required=False),
    ApiRouteContract("GET", "/v1/members/social/callback", False, False, "Complete provider OAuth callback", ["social_accounts", "admin_audit_logs"], live_data_required=False),
    ApiRouteContract("DELETE", "/v1/members/social/{connectionId}", True, False, "Revoke connected social account", ["social_accounts", "admin_audit_logs"]),
    ApiRouteContract("POST", "/v1/slips", True, False, "Save a member slip with max-three-leg enforcement", ["saved_slips", "members", "admin_audit_logs"]),
    ApiRouteContract("GET", "/v1/slips", True, False, "List member saved slips", ["saved_slips"]),
    ApiRouteContract("GET", "/v1/slips/{slipId}", True, False, "Read one saved slip", ["saved_slips", "score_history"]),
    ApiRouteContract("POST", "/v1/slips/{slipId}/grade", True, True, "Grade completed slip and write score history", ["saved_slips", "score_history", "admin_audit_logs"]),
    ApiRouteContract("GET", "/v1/scores/me", True, False, "Return private member score windows", ["score_history"]),
    ApiRouteContract("GET", "/v1/public/u/{handle}", False, False, "Return opt-in public member score card", ["members", "score_history"]),
    ApiRouteContract("POST", "/v1/attribution/visit", False, False, "Record anonymous creator/referral visit", ["creator_attribution"], live_data_required=False),
    ApiRouteContract("POST", "/v1/attribution/convert", True, False, "Attach attribution to member signup", ["creator_attribution", "members"]),
    ApiRouteContract("POST", "/v1/subscriptions/checkout", True, False, "Create payment-provider checkout/session", ["subscriptions", "members"], live_data_required=False),
    ApiRouteContract("POST", "/v1/subscriptions/webhook", False, False, "Process payment-provider subscription webhook", ["subscriptions", "members", "admin_audit_logs"], live_data_required=False),
    ApiRouteContract("GET", "/v1/admin/dashboard", True, True, "Owner/admin dashboard aggregate", ["members", "saved_slips", "score_history", "creator_attribution", "subscriptions", "support_notes", "feature_flags"]),
    ApiRouteContract("GET", "/v1/admin/members", True, True, "Owner/admin member management list", ["members", "subscriptions", "creator_attribution", "social_accounts"]),
    ApiRouteContract("GET", "/v1/admin/social-accounts", True, True, "Owner/admin social connection overview without exposing raw tokens", ["social_accounts", "members"]),
    ApiRouteContract("GET", "/v1/admin/audit", True, True, "Owner/admin audit log search", ["admin_audit_logs"]),
    ApiRouteContract("PATCH", "/v1/admin/feature-flags/{flagKey}", True, True, "Owner/admin feature flag update", ["feature_flags", "admin_audit_logs"]),
]


def record_to_dict(record: Any) -> Dict[str, Any]:
    return asdict(record)


def validate_saved_slip(record: SavedSlipRecord) -> None:
    record.validate()


def validate_social_account(record: SocialAccountRecord) -> None:
    record.validate()


def require_live_backend(feature: str) -> None:
    """Prevent frontend/admin scaffolds from pretending live wiring exists."""
    raise BackendNotWiredError(f"{feature} is not connected to live backend storage yet.")


def table_env_vars() -> List[str]:
    return [contract["env"] for contract in DYNAMODB_TABLE_CONTRACTS.values()]


def route_manifest() -> List[Dict[str, Any]]:
    return [record_to_dict(route) for route in API_ROUTE_CONTRACTS]
