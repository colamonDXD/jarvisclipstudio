from datetime import datetime
from typing import List, Optional
from sqlalchemy import (
    Column,
    String,
    DateTime,
    ForeignKey,
    CheckConstraint,
    ARRAY,
    Boolean,
    Float,
    Integer,
    Text,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.sql import func
import uuid

from .database import Base


def generate_uuid_string():
    """Generate a UUID as a string for compatibility with Prisma"""
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid_string
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    emailVerified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    image: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        default=func.now(),
    )

    # Additional fields for backend compatibility
    first_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    default_clip_length: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=sql_text("'auto'")
    )

    # Billing fields
    notify_on_completion: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("'true'")
    )
    product_updates_unsubscribed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("'false'")
    )
    plan: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=sql_text("'free'")
    )
    subscription_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=sql_text("'inactive'")
    )
    subscription_provider: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, unique=True
    )
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, unique=True
    )
    billing_period_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    billing_period_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    subscription_cancel_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Pro subscribers from before the 720p change keep 1080p while subscribed.
    legacy_hd: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("'false'")
    )
    # Higher tier chosen in the billing portal, granted only after its invoice is paid.
    pending_plan: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    pending_plan_invoice_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    # Relationships
    tasks: Mapped[List["Task"]] = relationship(
        "Task", back_populates="user", cascade="all, delete-orphan"
    )


class EmailDelivery(Base):
    """SES acceptance metadata for correlating bounce and complaint reports."""

    __tablename__ = "email_deliveries"

    message_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    setting_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    prefer_admin_value: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("'false'")
    )
    updated_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RevenueCatWebhookEvent(Base):
    __tablename__ = "revenuecat_webhook_events"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    type: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid_string
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )
    generated_clips_ids: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String(36)), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), server_default=sql_text("'pending'"), nullable=False
    )
    progress: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, server_default=sql_text("'0'")
    )
    progress_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Font customization fields
    font_family: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, server_default=sql_text("'TikTokSans-Regular'")
    )
    font_size: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, server_default=sql_text("'24'")
    )
    font_color: Mapped[Optional[str]] = mapped_column(
        String(7), nullable=True, server_default=sql_text("'#FFFFFF'")
    )  # Hex color code

    # Caption template and B-roll options
    caption_template: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, server_default=sql_text("'default'")
    )
    include_broll: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True, server_default=sql_text("'false'")
    )
    processing_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=sql_text("'fast'")
    )
    # Explicit niche choice for auto-director; NULL means use the classifier.
    niche_override: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True
    )
    # Explicit background-music choice (a music-library filename); NULL
    # means the niche profile's mood picks the track.
    music_override: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    # Music mode from the create form: "auto" (profile bed, else the Auto
    # bed), "on" (a bed must play: the picked track, else the profile's
    # bed, else the Auto-bed selection), "off" (guaranteed silence). NULL
    # means the row predates the control and behaves as "auto".
    music_mode: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True
    )
    # Template mode from the create form: "auto" (the niche profile's
    # full template treatment) or "plain" (Plain — just cuts: no B-roll,
    # music, SFX, cards, transitions or effects, always-full-frame
    # reframing). NULL means the row predates the control and behaves
    # as "auto".
    template_mode: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True
    )
    # Framing mode from the create form: "auto" (the layout planner
    # decides per shot), "fit" (the whole source frame inside the
    # output, blurred bands filling the rest) or "crop" (a full-bleed
    # face-tracked crop for every shot). A framing choice only — it
    # never changes what the template does. NULL means the row
    # predates the control and the template default decides (Plain
    # crops, Auto lets the planner decide). See
    # auto_director.framing_mode.resolve_framing_mode.
    framing_mode: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True
    )
    # Streamer layout template from the create form ("face on top, game
    # below": facecam_red / facecam_gold, media.gaming_templates) and the
    # optional credit line it draws. NULL = the automatic layout.
    layout_template: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )
    layout_credit: Mapped[Optional[str]] = mapped_column(
        String(48), nullable=True
    )
    # Title on/off from the create form. NULL (every older task) = drawn.
    hook_title_enabled: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )
    # B-roll switch from the create form: the user's own On/Off for stock
    # cutaways, independent of the other template effects. NULL means the
    # row predates the control and the template default decides (Auto
    # template plans B-roll, Plain never does); an explicit value wins
    # in Auto, and Plain resolves Off regardless (see
    # auto_director.broll_setting.resolve_broll_enabled).
    broll_enabled: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )
    # Voice/music separation opt-in from the create form: when true (and
    # the deployment enables SEPARATION_ENABLED), the source's vocals
    # stem is separated before transcription so music-heavy sources do
    # not poison the transcript. False for every pre-feature row.
    # The server_default MUST match migration
    # 20261110_0002_task_separation_opt_in.sql: init_db() runs
    # create_all BEFORE the migrations, so on a pristine DB the
    # migration's ADD COLUMN IF NOT EXISTS is a no-op and the ORM
    # definition is the only source of the column default. Without
    # it the column is NOT NULL with no default and any raw INSERT
    # that omits it (e.g. the test factories) fails.
    separation_opt_in: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("'false'")
    )
    # Brand Kit opt-in from the create form; NULL means no kit. When set,
    # the kit's defaults fill whatever the task itself left unchosen
    # (caption template, font, music) and its colours/logo/title
    # treatment ride the render through the EDL brand blocks.
    brand_kit_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True
    )
    # Intro/outro asset names carried from the opted-in kit. Recorded
    # only: the render pipeline does not stitch bumpers yet.
    intro_asset: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    outro_asset: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    # Explicit clip-count target from the create form; NULL means Auto
    # (the duration-based formula in ai.max_final_clips_for_duration).
    target_clip_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    # Campaign brief for this task (JSON, brief schema v2) plus the
    # search-outcome report written when selection finishes; NULL brief
    # means Content Requirements is off for the task.
    content_brief: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    brief_report: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Publishing campaign this task was created from, when any. The brief
    # above is a snapshot; later campaign edits never mutate the task.
    campaign_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Explicit creation mode (tracker 1.9): one of the four named
    # workflows in auto_director.creation_modes; NULL means no mode was
    # chosen and the task follows the historical pipeline exactly.
    creation_mode: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )
    # Validated Manual Cutter ranges as a JSON array of start/end
    # seconds; only set for the manual_cutter_ai creation mode.
    manual_ranges_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cache_hit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("'false'")
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    stage_timings_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    # Source length charged against the monthly minute allowance.
    source_duration_seconds: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    completion_notification_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    share_token: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    share_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("'false'")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="tasks")
    source: Mapped[Optional["Source"]] = relationship("Source", back_populates="tasks")
    generated_clips: Mapped[List["GeneratedClip"]] = relationship(
        "GeneratedClip", back_populates="task", cascade="all, delete-orphan"
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid_string
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Add check constraint for type enum
    __table_args__ = (
        CheckConstraint(
            "type IN ('youtube', 'video_url', 'twitch', 'kick')",
            name="check_source_type",
        ),
    )

    # Relationships - Source can have multiple tasks
    tasks: Mapped[List["Task"]] = relationship("Task", back_populates="source")

    def decide_source_type(self, source_url: str) -> str:
        """Decide which type of source this is."""
        if "youtube" in source_url:
            return "youtube"
        else:
            return "video_url"


class GeneratedClip(Base):
    __tablename__ = "generated_clips"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid_string
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    start_time: Mapped[str] = mapped_column(String(20), nullable=False)  # MM:SS format
    end_time: Mapped[str] = mapped_column(String(20), nullable=False)  # MM:SS format
    duration: Mapped[float] = mapped_column(
        Float, nullable=False
    )  # Duration in seconds
    text: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # Transcript text for this clip
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # AI reasoning for selection
    clip_order: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # Order within the task

    # Virality score breakdown
    virality_score: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, server_default=sql_text("'0'")
    )
    hook_score: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, server_default=sql_text("'0'")
    )
    engagement_score: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, server_default=sql_text("'0'")
    )
    value_score: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, server_default=sql_text("'0'")
    )
    shareability_score: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, server_default=sql_text("'0'")
    )
    hook_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Per-clip auto-director override; NULL inherits the task override.
    niche_override: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True
    )
    # AI quality-control verdict for the rendered file (pass/warn/fail/error)
    # plus the JSON evidence report, written by the pipeline QC seam.
    qc_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    qc_report: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Per-clip Viral Intelligence Engine plan (JSON): hook call, opening
    # treatment, planned SFX accents and music decision with reasons,
    # written by the pipeline plan seam before rendering.
    clip_plan: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Campaign-brief compliance verdict for the rendered file plus the
    # JSON evidence report, written by the post-render compliance seam.
    # Separate from qc_*: a brief failure is not fixable by a re-render.
    compliance_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    compliance_report: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    task: Mapped["Task"] = relationship("Task", back_populates="generated_clips")


class ProcessingCache(Base):
    __tablename__ = "processing_cache"

    cache_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    video_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transcript_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    analysis_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
