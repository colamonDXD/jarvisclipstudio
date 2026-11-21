from dotenv import load_dotenv
import math
import os
from pathlib import Path

from .runtime_settings import get_cached_setting, setting_prefers_admin

load_dotenv()

_config_override = None
LOCAL_OLLAMA_BASE_URL = "http://localhost:11434/v1"
DOCKER_OLLAMA_BASE_URL = "http://host.docker.internal:11434/v1"


class Config:
    def __init__(self):
        self.openai_api_key = self._get_runtime_setting("OPENAI_API_KEY")
        self.openrouter_api_key = self._get_runtime_setting("OPENROUTER_API_KEY")
        self.openrouter_fallback_model = self._get_optional_env("OPENROUTER_FALLBACK_MODEL") or "nvidia/nemotron-3-ultra-550b-a55b:free"
        self.anthropic_api_key = self._get_runtime_setting("ANTHROPIC_API_KEY")
        self.google_api_key = self._get_runtime_setting("GOOGLE_API_KEY")
        # Groq is this deployment's working provider (direct pydantic-ai
        # route in ai.py reads GROQ_API_KEY from the process env); it was
        # never a Config attribute, so _infer_default_llm could not see it.
        self.groq_api_key = self._get_runtime_setting("GROQ_API_KEY")
        self.youtube_data_api_key = self._get_runtime_setting("YOUTUBE_DATA_API_KEY")
        self.ollama_base_url = self._get_runtime_setting("OLLAMA_BASE_URL")
        self.ollama_api_key = self._get_runtime_setting("OLLAMA_API_KEY")

        self.whisper_model = os.getenv("WHISPER_MODEL") or os.getenv("WHISPER_MODEL_SIZE", "base")
        # Batched faster-whisper inference batch size. 0 (the default) keeps
        # the per-model defaults in media.transcription (16, or 8 for the
        # large-class models on CUDA); any positive value is clamped to
        # [1, 64] and used as-is.
        self.whisper_batch_size = max(
            0, min(64, self._get_int_env("WHISPER_BATCH_SIZE", 0))
        )
        self.transcription_provider = self._normalize_transcription_provider(
            os.getenv("TRANSCRIPTION_PROVIDER", "assemblyai")
        )
        # Empty means auto-detect the spoken language; otherwise an AssemblyAI
        # language code such as "es" or "en_us" forces it.
        self.transcription_language = self._normalize_transcription_language(
            os.getenv("TRANSCRIPTION_LANGUAGE")
        )
        self.llm = self._get_runtime_setting("LLM") or self._infer_default_llm()
        # Optional separate model for the cross-window candidate rerank pass.
        self.rerank_llm = self._get_optional_env("RERANK_LLM") or self.llm
        # Estimated tokens allowed in one transcript-analysis request. Low-TPM
        # providers (for example Groq's 8k free tier) need this below their cap.
        self.transcript_analysis_max_tokens = max(
            1, self._get_int_env("TRANSCRIPT_ANALYSIS_MAX_TOKENS", 7000)
        )
        # Optional comma-separated provider:model list. Analysis chunks rotate
        # across these models; empty keeps the single LLM behaviour.
        self.analysis_models = self._get_csv_env("ANALYSIS_MODELS", [])
        # Proactive spacing between calls to the same model. None when the env
        # var is unset: ai.py then auto-paces only when a multi-chunk burst
        # could exceed the per-minute token bucket. An explicit value is a
        # floor in seconds; an explicit 0 disables pacing entirely.
        raw_min_interval = os.getenv("ANALYSIS_MIN_INTERVAL_SEC")
        if raw_min_interval is None or raw_min_interval.strip() == "":
            self.analysis_min_interval_sec = None
        else:
            try:
                self.analysis_min_interval_sec = max(0.0, float(raw_min_interval))
            except (TypeError, ValueError):
                self.analysis_min_interval_sec = None
        # Per-model tokens-per-minute bucket used to derive a safe interval.
        self.analysis_tpm = max(1, self._get_int_env("ANALYSIS_TPM", 8000))
        # Floor for the auto-pacing interval when it engages on its own.
        self.analysis_pacing_floor_sec = max(
            0.0, self._get_float_env("ANALYSIS_PACING_FLOOR_SEC", 2.0)
        )
        # Cumulative seconds one chunk may spend waiting out provider rate
        # limits (HTTP 413/429) before the failure is reported as such.
        self.analysis_rate_limit_budget_sec = max(
            1.0, self._get_float_env("ANALYSIS_RATE_LIMIT_BUDGET_SEC", 900.0)
        )
        # Transcripts longer than this use the long-form analysis route:
        # bigger windows and token budget, all sent to one large-context
        # model instead of rotating across every configured model.
        self.analysis_longform_threshold_sec = max(
            0, self._get_int_env("ANALYSIS_LONGFORM_THRESHOLD_SEC", 1800)
        )
        self.analysis_longform_window_sec = max(
            60, self._get_int_env("ANALYSIS_LONGFORM_WINDOW_SEC", 1200)
        )
        self.analysis_longform_max_tokens = max(
            1, self._get_int_env("ANALYSIS_LONGFORM_MAX_TOKENS", 48000)
        )
        # Empty means: first entry of ANALYSIS_MODELS, else the main LLM.
        self.analysis_longform_model = (
            self._get_optional_env("ANALYSIS_LONGFORM_MODEL") or ""
        )
        # Token bucket assumed for the long-form model when pacing it; 0
        # falls back to ANALYSIS_TPM (the conservative default).
        self.analysis_longform_tpm = max(
            0, self._get_int_env("ANALYSIS_LONGFORM_TPM", 0)
        )
        # Last-resort model for chunks whose cloud models stay rate-limited.
        # The bundled LiteLLM gateway always serves this alias locally.
        # Set to an empty value to disable the fallback.
        raw_fallback_model = os.getenv("ANALYSIS_FALLBACK_MODEL")
        self.analysis_fallback_model = (
            "openai-chat:local-qwen"
            if raw_fallback_model is None
            else raw_fallback_model.strip()
        )
        # Last-resort model reached WITHOUT the gateway (a direct provider
        # route), for when the gateway itself is unreachable: every openai-chat:*
        # alias dies with it, this one does not. The default names the local
        # Ollama model the bundled gateway's local-qwen alias also serves.
        # Set to an empty value to disable.
        raw_direct_fallback = os.getenv("ANALYSIS_DIRECT_FALLBACK_MODEL")
        self.analysis_direct_fallback_model = (
            "ollama:qwen3:8b"
            if raw_direct_fallback is None
            else raw_direct_fallback.strip()
        )
        # Local-first provider manager (src/provider_manager.py): tracks
        # provider failures, cools exhausted/rate-limited aliases down, falls
        # back through the configured providers to the local model, and
        # accounts tokens per task. PROVIDER_MODE: auto (configured order,
        # local preferred for routine calls and kept as final fallback),
        # local-first (local for everything while it is healthy), local-only
        # (never call a cloud provider). LOCAL_LLM names the local route —
        # the LiteLLM gateway alias by default, or ollama:<model> for a
        # direct Ollama connection.
        self.provider_manager_enabled = self._get_bool_env(
            "PROVIDER_MANAGER_ENABLED", True
        )
        self.provider_mode = self._normalize_provider_mode(
            os.getenv("PROVIDER_MODE", "auto")
        )
        self.local_llm = (
            self._get_optional_env("LOCAL_LLM") or "openai-chat:local-qwen"
        )
        self.assembly_ai_api_key = self._get_runtime_setting("ASSEMBLY_AI_API_KEY")
        # An explicit deployment choice overrides the model for every processing mode.
        self.assembly_ai_speech_model = os.getenv("ASSEMBLY_AI_SPEECH_MODEL", "").strip()
        self.assembly_ai_http_timeout_seconds = int(
            os.getenv("ASSEMBLY_AI_HTTP_TIMEOUT_SECONDS", "900")
        )
        self.assembly_ai_request_timeout_seconds = max(5, min(300, int(
            os.getenv("ASSEMBLY_AI_REQUEST_TIMEOUT_SECONDS", "60")
        )))
        self.pexels_api_key = self._get_runtime_setting("PEXELS_API_KEY")
        self.pixabay_api_key = self._get_runtime_setting("PIXABAY_API_KEY")
        self.coverr_api_key = self._get_runtime_setting("COVERR_API_KEY")
        self.apify_api_token = self._get_runtime_setting("APIFY_API_TOKEN")
        self.rapidapi_youtube_api_key = self._get_optional_env("RAPIDAPI_YOUTUBE_API_KEY")
        self.rapidapi_youtube_timeout_seconds = int(
            os.getenv("RAPIDAPI_YOUTUBE_TIMEOUT_SECONDS", "300")
        )
        self.youtube_download_provider = self._normalize_youtube_download_provider(
            os.getenv("YOUTUBE_DOWNLOAD_PROVIDER", "yt_dlp")
        )
        self.youtube_metadata_provider = self._normalize_youtube_metadata_provider(
            os.getenv("YOUTUBE_METADATA_PROVIDER", "yt_dlp")
        )
        # Task creation waits this long for YouTube length metadata. 30s matches
        # the Twitch/Kick preflight deadline and covers yt-dlp's deno step when
        # the backend runs under amd64 emulation.
        self.youtube_preflight_timeout_seconds = self._get_positive_float_env(
            "YOUTUBE_PREFLIGHT_TIMEOUT_SECONDS",
            30.0,
            maximum=180.0,
        )
        # Empty disables the residential-proxy yt-dlp path. Nothing in the
        # download chain changes until this template is set.
        self.youtube_proxy_url = os.getenv("YOUTUBE_PROXY_URL", "").strip()
        self.youtube_proxy_timeout_seconds = self._get_int_env("YOUTUBE_PROXY_TIMEOUT_SECONDS", 300)
        self.youtube_proxy_country = os.getenv("YOUTUBE_PROXY_COUNTRY", "").strip()
        self.youtube_pot_provider_url = os.getenv("YOUTUBE_POT_PROVIDER_URL", "").strip()
        self.youtube_download_max_bytes = self._get_int_env("YOUTUBE_DOWNLOAD_MAX_BYTES", 2_000_000_000)
        self.youtube_section_padding_seconds = self._get_float_env("YOUTUBE_SECTION_PADDING_SECONDS", 10.0)
        self.youtube_max_height = self._get_int_env("YOUTUBE_MAX_HEIGHT", 2160)
        self.youtube_source_cache = self._get_bool_env("YOUTUBE_SOURCE_CACHE", True)
        self.youtube_source_cache_ttl_seconds = self._get_int_env(
            "YOUTUBE_SOURCE_CACHE_TTL_SECONDS", 604800
        )
        self.twitch_download_providers = os.getenv("TWITCH_DOWNLOAD_PROVIDERS", "proxy,direct,apify_twitch").strip()
        self.kick_download_providers = os.getenv("KICK_DOWNLOAD_PROVIDERS", "proxy,direct_retry,apify_kick_hls,apify_kick_mp4").strip()
        self.apify_twitch_actor = os.getenv("APIFY_TWITCH_ACTOR", "abotapi/twitch-scraper").strip()
        self.apify_kick_hls_actor = os.getenv("APIFY_KICK_HLS_ACTOR", "hipersoft/kick-downloader").strip()
        self.apify_kick_mp4_actor = os.getenv("APIFY_KICK_MP4_ACTOR", "parsebird/kick-video-downloader").strip()
        self.apify_stream_enabled = self._get_bool_env("APIFY_STREAM_PROVIDERS", True)
        self.apify_stream_timeout_seconds = self._get_int_env("APIFY_STREAM_TIMEOUT_SECONDS", 300)
        self.apify_kick_max_video_size_mb = self._get_int_env("APIFY_KICK_MAX_VIDEO_SIZE_MB", 2000)
        self.apify_youtube_default_quality = self._normalize_apify_quality(
            os.getenv("APIFY_YOUTUBE_DEFAULT_QUALITY", "2160")
        )
        self.apify_youtube_downloader_actor = os.getenv(
            "APIFY_YOUTUBE_DOWNLOADER_ACTOR", ""
        ).strip()
        self.apify_run_timeout_seconds = int(
            os.getenv("APIFY_RUN_TIMEOUT_SECONDS", "3600")
        )
        # Long videos at >1080p don't finish within the Apify run timeout.
        self.apify_long_video_seconds = int(
            os.getenv("APIFY_LONG_VIDEO_SECONDS", "1800")
        )

        self.max_video_duration = int(os.getenv("MAX_VIDEO_DURATION", "5400"))
        # YouTube sources can be longer on paid tiers. Uploads continue to use
        # max_video_duration regardless of plan.
        self.pro_youtube_max_video_duration = int(
            os.getenv("PRO_YOUTUBE_MAX_VIDEO_DURATION", str(self.max_video_duration))
        )
        self.scale_youtube_max_video_duration = int(
            os.getenv("SCALE_YOUTUBE_MAX_VIDEO_DURATION", "10800")
        )
        # 4 GB fits a full-length upload (the duration cap is 90 minutes) at a
        # normal 1080p bitrate. Larger files make transcription, storage, and
        # rendering slow and fragile.
        self.max_video_upload_bytes = int(
            os.getenv("MAX_VIDEO_UPLOAD_BYTES", "4000000000")
        )
        self.output_dir = os.getenv("OUTPUT_DIR", "outputs")

        self.max_clips = int(os.getenv("MAX_CLIPS", "10"))
        self.clip_duration = int(os.getenv("CLIP_DURATION", "30"))  # seconds

        self.temp_dir = os.getenv("TEMP_DIR", "temp")
        # User-uploaded background music. Defaults under the shared temp
        # tree (the Docker `uploads` volume) so backend, worker and
        # ai-worker all see uploads at the same path; USER_MUSIC_DIR
        # overrides it for non-Docker deployments.
        self.user_music_dir = os.getenv("USER_MUSIC_DIR") or str(
            Path(self.temp_dir) / "user_music"
        )
        # User-uploaded sound effects. Same reasoning as user music:
        # under the shared temp tree by default (the Docker `uploads`
        # volume) so backend, worker and ai-worker all see uploads at
        # the same path — the bundled assets/sfx folder is read-only in
        # Docker, so uploads can never land there. USER_SFX_DIR
        # overrides it for non-Docker deployments.
        self.user_sfx_dir = os.getenv("USER_SFX_DIR") or str(
            Path(self.temp_dir) / "user_sfx"
        )
        # User-created reference-style profiles (Lane H2). Same reasoning
        # as user music: under the shared temp tree by default so backend,
        # worker and ai-worker all see the same styles; USER_PROFILES_DIR
        # overrides it. The stock profiles tree is never written to.
        self.user_profiles_dir = os.getenv("USER_PROFILES_DIR") or str(
            Path(self.temp_dir) / "user_profiles"
        )
        # User-imported .cube colour LUTs (Studio Colour panel). Same
        # reasoning as user music: under the shared temp tree by default
        # so backend, worker and ai-worker all see the same files;
        # USER_LUTS_DIR overrides it.
        self.user_luts_dir = os.getenv("USER_LUTS_DIR") or str(
            Path(self.temp_dir) / "user_luts"
        )
        # Watch-folder autopilot. The default inbox sits under the shared
        # temp tree so the backend that scans it and the worker that
        # renders from it see the same files; WATCH_FOLDER_PATH points it
        # at a bind-mounted host folder instead (for example the user's
        # D:\Jarvis_Autopilot mounted into the containers). A per-user
        # settings row can override the folder again at runtime.
        self.watch_folder_path = os.getenv("WATCH_FOLDER_PATH") or str(
            Path(self.temp_dir) / "autopilot"
        )
        try:
            self.watch_folder_poll_seconds = max(
                5, int(os.getenv("WATCH_FOLDER_POLL_SECONDS", "15"))
            )
        except ValueError:
            self.watch_folder_poll_seconds = 15
        # YouTube channel monitoring. YouTube serves each channel's
        # uploads feed with a 15-minute cache header, so polling more
        # often than that gains nothing: 900s is both the default and
        # the polite floor.
        try:
            self.channel_monitor_poll_seconds = max(
                60, int(os.getenv("CHANNEL_MONITOR_POLL_SECONDS", "900"))
            )
        except ValueError:
            self.channel_monitor_poll_seconds = 900
        self.media_storage = os.getenv("MEDIA_STORAGE", "local").strip().lower()
        if self.media_storage not in {"local", "s3"}:
            raise ValueError("MEDIA_STORAGE must be local or s3")
        self.media_s3_bucket = self._get_optional_env("MEDIA_S3_BUCKET")
        self.media_s3_prefix = os.getenv("MEDIA_S3_PREFIX", "media").strip().strip("/") or "media"
        self.media_s3_region = os.getenv("MEDIA_S3_REGION") or os.getenv("AWS_REGION", "eu-north-1")
        self.media_s3_endpoint = self._get_optional_env("MEDIA_S3_ENDPOINT")
        self.media_s3_access_key = self._get_optional_env("MEDIA_S3_ACCESS_KEY_ID")
        self.media_s3_secret_key = self._get_optional_env("MEDIA_S3_SECRET_ACCESS_KEY")
        self.media_s3_session_token = self._get_optional_env("MEDIA_S3_SESSION_TOKEN")
        self.media_fallback_bucket = self._get_optional_env("MEDIA_FALLBACK_BUCKET")
        self.media_fallback_endpoint = self._get_optional_env("MEDIA_FALLBACK_ENDPOINT")
        self.media_fallback_access_key = self._get_optional_env("MEDIA_FALLBACK_ACCESS_KEY_ID")
        self.media_fallback_secret_key = self._get_optional_env("MEDIA_FALLBACK_SECRET_ACCESS_KEY")
        self.media_fallback_region = os.getenv("MEDIA_FALLBACK_REGION", "auto")
        fallback_values = (self.media_fallback_bucket, self.media_fallback_endpoint,
                           self.media_fallback_access_key, self.media_fallback_secret_key)
        if any(fallback_values) and not all(fallback_values):
            raise ValueError("All MEDIA_FALLBACK bucket, endpoint and credentials are required")
        self.media_fallback_enabled = all(fallback_values)
        self.media_failover_enabled = self._get_bool_env("MEDIA_FAILOVER_ENABLED", False)
        if self.media_failover_enabled and not self.media_fallback_enabled:
            raise ValueError("MEDIA_FAILOVER_ENABLED requires MEDIA_FALLBACK configuration")
        self.media_url_ttl_seconds = min(3600, max(60, int(os.getenv("MEDIA_URL_TTL_SECONDS", "900"))))
        self.media_cache_ttl_seconds = max(14400, int(os.getenv("MEDIA_CACHE_TTL_SECONDS", "86400")))
        self.media_orphan_grace_seconds = max(86400, int(os.getenv("MEDIA_ORPHAN_GRACE_SECONDS", "86400")))
        self.media_upload_concurrency = max(1, int(os.getenv("MEDIA_UPLOAD_CONCURRENCY", "3")))
        self.media_upload_daily_limit = max(1, int(os.getenv("MEDIA_UPLOAD_DAILY_LIMIT", "20")))
        if self.media_storage == "s3" and not self.media_s3_bucket:
            raise ValueError("MEDIA_S3_BUCKET is required with MEDIA_STORAGE=s3")

        # Redis configuration
        self.redis_host = os.getenv("REDIS_HOST", "localhost")
        self.redis_port = int(os.getenv("REDIS_PORT", "6379"))
        self.redis_password = self._get_optional_env("REDIS_PASSWORD")

        # Fail-safe: queued tasks should not stay queued forever
        self.queued_task_timeout_seconds = int(
            os.getenv("QUEUED_TASK_TIMEOUT_SECONDS", "180")
        )
        # Fail-safe: processing tasks whose worker died mid-run should not stay
        # "processing" forever. Default comfortably exceeds arq's job_timeout so
        # a legitimately long job is never falsely swept into "error".
        self.processing_task_timeout_seconds = int(
            os.getenv("PROCESSING_TASK_TIMEOUT_SECONDS", "14400")
        )

        self.self_host = self._get_bool_env("SELF_HOST", True)
        self.monetization_enabled = not self.self_host
        self.backend_auth_secret = self._get_optional_env("BACKEND_AUTH_SECRET")
        self.allow_unsigned_backend_auth = self._get_bool_env(
            "ALLOW_UNSIGNED_BACKEND_AUTH", False
        )
        self.auth_signature_ttl_seconds = int(
            os.getenv("AUTH_SIGNATURE_TTL_SECONDS", "300")
        )
        # Monthly allowance in minutes of source video; 0 or less means unlimited.
        self.pro_plan_minute_limit = int(os.getenv("PRO_PLAN_MINUTE_LIMIT", "600"))
        self.scale_plan_minute_limit = int(os.getenv("SCALE_PLAN_MINUTE_LIMIT", "1500"))
        self.cors_origins = self._get_csv_env(
            "CORS_ORIGINS",
            [
                "http://localhost:3107",
                "http://sp.localhost:3107",
            ],
        )
        self.aws_region = self._get_optional_env("AWS_REGION")
        self.aws_access_key_id = self._get_optional_env("AWS_ACCESS_KEY_ID")
        self.aws_secret_access_key = self._get_optional_env("AWS_SECRET_ACCESS_KEY")
        self.ses_from_email = os.getenv(
            "SES_FROM_EMAIL", "SupoClip <onboarding@example.com>"
        )
        self.app_base_url = (
            self._get_optional_env("NEXT_PUBLIC_APP_URL") or "http://localhost:3107"
        ).rstrip("/")
        self.discord_feedback_webhook_url = self._get_optional_env("DISCORD_FEEDBACK_WEBHOOK_URL")
        self.discord_sales_webhook_url = self._get_optional_env("DISCORD_SALES_WEBHOOK_URL")

        # Social publishing. Each provider needs an OAuth app registered by the
        # operator; users then connect their own accounts through that app.
        self.youtube_oauth_client_id = self._get_optional_env(
            "YOUTUBE_OAUTH_CLIENT_ID"
        ) or self._get_optional_env("GOOGLE_OAUTH_CLIENT_ID")
        self.youtube_oauth_client_secret = self._get_optional_env(
            "YOUTUBE_OAUTH_CLIENT_SECRET"
        ) or self._get_optional_env("GOOGLE_OAUTH_CLIENT_SECRET")
        self.tiktok_client_key = self._get_optional_env("TIKTOK_CLIENT_KEY")
        self.tiktok_client_secret = self._get_optional_env("TIKTOK_CLIENT_SECRET")
        self.instagram_app_id = self._get_optional_env(
            "INSTAGRAM_APP_ID"
        ) or self._get_optional_env("META_APP_ID")
        self.instagram_app_secret = self._get_optional_env(
            "INSTAGRAM_APP_SECRET"
        ) or self._get_optional_env("META_APP_SECRET")
        # OAuth callbacks and public media fetches (Instagram pulls the video
        # from a URL) both go through the frontend, so they default to the
        # public app URL.
        self.social_oauth_redirect_base_url = (
            self._get_optional_env("SOCIAL_OAUTH_REDIRECT_BASE_URL")
            or self.app_base_url
        ).rstrip("/")
        self.social_public_media_base_url = (
            self._get_optional_env("SOCIAL_PUBLIC_MEDIA_BASE_URL")
            or self.app_base_url
        ).rstrip("/")
        self.social_metrics_refresh_hours = float(
            os.getenv("SOCIAL_METRICS_REFRESH_HOURS", "6")
        )
        self.social_metrics_max_post_age_days = int(
            os.getenv("SOCIAL_METRICS_MAX_POST_AGE_DAYS", "60")
        )
        self.social_performance_min_posts = int(
            os.getenv("SOCIAL_PERFORMANCE_MIN_POSTS", "3")
        )
        self.social_publish_max_attempts = int(
            os.getenv("SOCIAL_PUBLISH_MAX_ATTEMPTS", "3")
        )
        # Who may use social publishing: "off" (default; nav and pages stay
        # hidden), "admins" (admins plus the allowlist, for a gated rollout)
        # or "all".
        self.social_publishing_access = (
            os.getenv("SOCIAL_PUBLISHING_ACCESS", "off").strip().lower() or "off"
        )
        if self.social_publishing_access not in {"all", "admins", "off"}:
            raise ValueError("SOCIAL_PUBLISHING_ACCESS must be all, admins or off")
        # Platform rollout is separate from credentials and user access. An
        # explicitly empty list disables every platform; existing self-hosted
        # installations retain all platforms unless they set this variable.
        self.social_publishing_providers = frozenset(
            provider.strip().lower()
            for provider in os.getenv(
                "SOCIAL_PUBLISHING_PROVIDERS", "youtube,tiktok,instagram"
            ).split(",")
            if provider.strip()
        )
        if self.social_publishing_providers - {"youtube", "tiktok", "instagram"}:
            raise ValueError(
                "SOCIAL_PUBLISHING_PROVIDERS may only include youtube, tiktok, instagram"
            )
        self.social_publishing_allowed_emails = frozenset(
            email.strip().lower()
            for email in os.getenv("SOCIAL_PUBLISHING_ALLOWED_EMAILS", "").split(",")
            if email.strip()
        )
        self.default_processing_mode = os.getenv("DEFAULT_PROCESSING_MODE", "fast")
        self.fast_mode_max_clips = int(os.getenv("FAST_MODE_MAX_CLIPS", "10"))
        self.fast_mode_transcript_model = os.getenv(
            "FAST_MODE_TRANSCRIPT_MODEL", "universal-3-5-pro"
        )
        # Fast Clips mode - optimized for long videos (5-10+ hours)
        # Uses YouTube captions for instant transcript, partial download, parallel rendering
        self.fast_clips_enabled = self._get_bool_env("FAST_CLIPS_ENABLED", True)
        self.fast_clips_max_clips = int(os.getenv("FAST_CLIPS_MAX_CLIPS", "15"))
        self.fast_clips_target_clips_per_hour = float(os.getenv("FAST_CLIPS_TARGET_CLIPS_PER_HOUR", "3"))
        self.fast_clips_max_duration_minutes = int(os.getenv("FAST_CLIPS_MAX_DURATION_MINUTES", "600"))
        self.fast_clips_section_padding = float(os.getenv("FAST_CLIPS_SECTION_PADDING", "15.0"))
        self.fast_clips_render_concurrency = int(os.getenv("FAST_CLIPS_RENDER_CONCURRENCY", "5"))
        self.fast_clips_llm = os.getenv("FAST_CLIPS_LLM", "groq:llama-3.3-70b-versatile")
        self.fast_clips_skip_static_check = self._get_bool_env("FAST_CLIPS_SKIP_STATIC_CHECK", True)
        self.fast_clips_skip_opening_refinement = self._get_bool_env("FAST_CLIPS_SKIP_OPENING_REFINEMENT", True)
        # Replay a later punchline before the clip. Off: the same line was
        # playing twice (teaser, then again inside the clip) and the opening
        # has to stand on its own. Set HOOK_FIRST_TEASER=true to opt back in.
        self.hook_first_teaser = self._get_bool_env("HOOK_FIRST_TEASER", False)
        # Cut between speakers on multi-person wide shots using diarization.
        self.speaker_framing = self._get_bool_env("SPEAKER_FRAMING", True)

    def max_youtube_video_duration_for_plan(
        self,
        plan: str | None,
        subscription_status: str | None,
        subscription_provider: str | None = None,
    ) -> int:
        """Return the YouTube duration cap granted by an active entitlement."""
        status = (subscription_status or "").lower()
        provider = (subscription_provider or "").lower()
        # past_due keeps the cap only while Stripe is still retrying the invoice.
        entitled = status in {"active", "trialing"} or (
            status == "past_due" and provider == "stripe"
        )
        if not entitled:
            return self.max_video_duration

        if (plan or "").lower() == "scale":
            return self.scale_youtube_max_video_duration
        if (plan or "").lower() == "pro":
            return self.pro_youtube_max_video_duration
        return self.max_video_duration

    @staticmethod
    def _get_optional_env(name: str):
        value = os.getenv(name)
        if value is None:
            return None

        normalized = value.strip()
        return normalized or None

    @classmethod
    def _get_runtime_setting(cls, name: str):
        env_value = cls._get_optional_env(name)
        admin_value = get_cached_setting(name)
        if admin_value and setting_prefers_admin(name):
            return admin_value
        return env_value or admin_value

    def as_runtime_settings(self) -> dict[str, str | None]:
        return {
            "ASSEMBLY_AI_API_KEY": self.assembly_ai_api_key,
            "LLM": self.llm,
            "OPENAI_API_KEY": self.openai_api_key,
            "OPENROUTER_API_KEY": self.openrouter_api_key,
            "GOOGLE_API_KEY": self.google_api_key,
            "ANTHROPIC_API_KEY": self.anthropic_api_key,
            "OLLAMA_BASE_URL": self.ollama_base_url,
            "OLLAMA_API_KEY": self.ollama_api_key,
            "YOUTUBE_DATA_API_KEY": self.youtube_data_api_key,
            "APIFY_API_TOKEN": self.apify_api_token,
            "PEXELS_API_KEY": self.pexels_api_key,
            "PIXABAY_API_KEY": self.pixabay_api_key,
            "COVERR_API_KEY": self.coverr_api_key,
        }

    @staticmethod
    def _get_int_env(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _get_float_env(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    @classmethod
    def _get_positive_float_env(cls, name: str, default: float, *, maximum: float) -> float:
        """Parse a positive float. Blank or invalid values use ``default``.

        Values above ``maximum`` are capped so a typo cannot hold a request open.
        """
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(value) or value <= 0:
            return default
        return min(value, maximum)

    @staticmethod
    def _get_bool_env(name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _get_csv_env(name: str, default: list[str]) -> list[str]:
        value = os.getenv(name)
        if not value:
            return default
        return [item.strip() for item in value.split(",") if item.strip()]

    @staticmethod
    def _normalize_apify_quality(value: str | None) -> str:
        normalized = (value or "").strip()
        if normalized in {"360", "480", "720", "1080", "1440", "2160"}:
            return normalized
        return "2160"

    @staticmethod
    def _normalize_provider_mode(value: str | None) -> str:
        normalized = (value or "").strip().lower()
        if normalized in ("auto", "local-first", "local-only"):
            return normalized
        return "auto"

    @staticmethod
    def _normalize_transcription_provider(value: str | None) -> str:
        normalized = (value or "").strip().lower().replace("-", "_")
        if normalized in ("whisper", "faster_whisper", "youtube_captions"):
            return normalized
        return "assemblyai"

    @staticmethod
    def _normalize_transcription_language(value: str | None) -> str:
        normalized = (value or "").strip().lower().replace("-", "_")
        return "" if normalized in ("", "auto") else normalized

    @staticmethod
    def _normalize_youtube_metadata_provider(value: str | None) -> str:
        normalized = (value or "").strip().lower()
        if normalized == "youtube_data_api":
            return "youtube_data_api"
        return "yt_dlp"

    @staticmethod
    def _normalize_youtube_download_provider(value: str | None) -> str:
        normalized = (value or "").strip().lower().replace("-", "_")
        if normalized in {"apify", "rapidapi"}:
            return normalized
        return "yt_dlp"

    def resolve_youtube_data_api_key(self) -> str | None:
        return self.youtube_data_api_key or self.google_api_key

    def resolve_ollama_base_url(self) -> str:
        return self.ollama_base_url or self._default_ollama_base_url()

    @staticmethod
    def _default_ollama_base_url() -> str:
        if os.path.exists("/.dockerenv"):
            return DOCKER_OLLAMA_BASE_URL
        return LOCAL_OLLAMA_BASE_URL

    def _infer_default_llm(self) -> str:
        """
        Infer a usable default model based on whichever API key is present.
        Groq first (this deployment's working provider, via the direct
        pydantic-ai route in ai.py); local Ollama last. Gemini is never a
        default: its API is unusable here (HTTP 411), so resolving to it
        could only fail selection/classification or silently degrade the
        denylist-guarded content-requirements passes.
        """
        if self.groq_api_key:
            return "groq:openai/gpt-oss-120b"
        if self.openai_api_key:
            return "openai-chat:gpt-5.2"
        if self.anthropic_api_key:
            return "anthropic:claude-4-sonnet"
        if self.openrouter_api_key:
            return "openrouter:anthropic/claude-sonnet-5.5"
        return "ollama:qwen3:8b"


def get_config() -> Config:
    override = _config_override
    if override is not None:
        return override
    return Config()


def set_config_override(config: Config | None) -> None:
    global _config_override
    _config_override = config
