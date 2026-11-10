"""Settings and model metadata for Sentient Voice Recognition."""

DOMAIN = "sentient_voice_recognition"
TITLE = "Sentient Voice Recognition"
CONF_STT = "stt_provider"
CONF_AGENT = "conversation_agent"
CONF_THRESHOLD = "match_threshold"
CONF_SHARE_NAME = "share_name"
DEFAULT_THRESHOLD = 0.65
MIN_MARGIN = 0.10
EVENT_IDENTIFIED = f"{DOMAIN}_turn"
SAMPLE_RATE = 16_000
MAX_CAPTURE_BYTES = SAMPLE_RATE * 2 * 12
MAX_PROFILES = 16
RECORDER_PATH = "sentient-voice-enrollment"
RECORDER_SECONDS = 10
ENROLLMENT_PROMPTS = (
    "Could you turn on the living room lamp and tell me what the weather will be like tomorrow?",
    "I'd like to know how warm it is upstairs before I open the bedroom window for a while.",
    "Please check whether the back door is closed, then tell me if any lights are still on downstairs.",
)

CONF_TOKEN = "engine_token"
CONF_ENGINE_INSTALL = "engine_install"
ENGINE_INSTALL_LOCAL = "local"
ENGINE_INSTALL_REPOSITORY = "repository"
ENGINE_URL = "http://local-sentient-voice-engine:10487"
REPOSITORY_ENGINE_URL = "http://bdced7f5-sentient-voice-engine:10487"
ENGINE_DIMENSION = 192
MAX_WAV_BYTES = 4 * 1024 * 1024
