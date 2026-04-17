import discord

GLOBAL_ADMIN_ID = 187959278949105664

inhouse_mode = {}
lobby_players = {}
lobby_channel_ids = {}
lobby_message = {}
roll_count = {}
team_rolls = {}
original_teams = {}
captain_draft_state = {}
live_channel_ids = {}
active_match_ids = {}
live_embed_messages = {}
polling_tasks = {}
match_tracking_start_times = {}
random_polling_flags = {}
valid_team_combos = {}
prefix_cache = {}
rocket_lock = {}
match_wait_tasks = {}
display_name = {}
_last_fetch_stats = {}
_last_active_match_id = {}
_last_selected_match_id = {}
immortal_draft_running: dict[int, bool] = {}

ALLOWED_CAPTAIN_POLICIES = {"min_diff", "top2_if_close", "simulate"}
captain_policy_by_guild: dict[int, str] = {}
captain_policy_threshold_by_guild: dict[int, int] = {}

MAX_ROLLS = 5
IMMORTAL_MAX_ROLLS = 3
MMR_ROLE_OVERRULE_THRESHOLD = 1500
ROLE_FIT_WEIGHT = 10
STORE_ITEM_DD_TOKENS = "dd_tokens"
STORE_ITEM_VIP_FEEDER = "role_vip_feeder"
STORE_ITEM_CUSTOM_ROLE = "role_custom_role"
STORE_ROLE_DURATION_DAYS = 7
VIP_FEEDER_ROLE_NAME = "VIP Feeder"
VIP_FEEDER_ROLE_COLOR = discord.Color.from_rgb(57, 255, 20)
CUSTOM_STORE_ROLE_COLOR = discord.Color.from_rgb(0, 191, 255)
STORE_ITEM_ALIASES = {
    STORE_ITEM_DD_TOKENS: {
        "dd_tokens", "ddtoken", "ddtokens", "double down token", "double down tokens"
    },
    STORE_ITEM_VIP_FEEDER: {
        "role: vip feeder", "vip feeder", "role_vip_feeder"
    },
    STORE_ITEM_CUSTOM_ROLE: {
        "role: custom role", "custom role", "role_custom_role"
    },
}
