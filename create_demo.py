"""Create demo data for dashboard testing."""
import json, time
from pathlib import Path

tour_id = "demo001"
base = Path("workspace") / tour_id
for d in ["apk", "static", "dynamic", "analysis", "output"]:
    (base / d).mkdir(parents=True, exist_ok=True)

# Pipeline state
(base / "pipeline_state.json").write_text(json.dumps({
    "tour_id": tour_id,
    "stage": "SCREENMAP_GENERATED",
    "apk_path": "demo.apk",
    "package_name": "com.example.app",
    "started_at": time.time(, encoding="utf-8"),
    "updated_at": time.time(),
    "error": None,
}, indent=2))

nodes = [
    {"screen_id": "page_splash", "activity": "SplashActivity", "label": "Splash",
     "functional_category": "navigation", "screen_purpose": "App launch screen",
     "params": {"inputs": [], "outputs": ["auth_token"], "displays": ["logo"]},
     "widgets": [], "screenshot_ref": "", "confidence": "high"},
    {"screen_id": "page_login", "activity": "LoginActivity", "label": "Login",
     "functional_category": "login", "screen_purpose": "User authentication",
     "params": {"inputs": [], "outputs": ["auth_token", "user_id"], "displays": ["email", "password"]},
     "widgets": [
         {"id": "et_email", "type": "input", "role": "Email input"},
         {"id": "et_password", "type": "input", "role": "Password input"},
         {"id": "btn_login", "type": "click", "role": "Submit login"},
     ], "screenshot_ref": "", "confidence": "high"},
    {"screen_id": "page_home", "activity": "HomeActivity", "label": "Home Feed",
     "functional_category": "home", "screen_purpose": "Main content hub with feed",
     "params": {"inputs": ["auth_token"], "outputs": ["selected_post_id"], "displays": ["feed", "badge"]},
     "widgets": [
         {"id": "btn_search", "type": "click", "role": "Open search"},
         {"id": "rv_feed", "type": "scroll", "role": "Scroll feed"},
         {"id": "tab_profile", "type": "click", "role": "Go to profile"},
         {"id": "fab_create", "type": "click", "role": "Create new post"},
     ], "screenshot_ref": "", "confidence": "high"},
    {"screen_id": "page_search", "activity": "SearchActivity", "label": "Search",
     "functional_category": "search", "screen_purpose": "Search content by keyword",
     "params": {"inputs": [], "outputs": ["query"], "displays": ["results"]},
     "widgets": [
         {"id": "et_query", "type": "input", "role": "Search input"},
         {"id": "rv_results", "type": "scroll", "role": "Result list"},
     ], "screenshot_ref": "", "confidence": "high"},
    {"screen_id": "page_detail", "activity": "DetailActivity", "label": "Post Detail",
     "functional_category": "content_detail", "screen_purpose": "View full post",
     "params": {"inputs": ["post_id"], "outputs": [], "displays": ["content", "comments"]},
     "widgets": [
         {"id": "btn_like", "type": "click", "role": "Like"},
         {"id": "btn_share", "type": "click", "role": "Share"},
     ], "screenshot_ref": "", "confidence": "medium"},
    {"screen_id": "page_profile", "activity": "ProfileActivity", "label": "Profile",
     "functional_category": "profile", "screen_purpose": "User profile and settings access",
     "params": {"inputs": ["user_id"], "outputs": [], "displays": ["username", "avatar"]},
     "widgets": [
         {"id": "btn_settings", "type": "click", "role": "Open settings"},
         {"id": "btn_edit", "type": "click", "role": "Edit profile"},
     ], "screenshot_ref": "", "confidence": "high"},
    {"screen_id": "page_settings", "activity": "SettingsActivity", "label": "Settings",
     "functional_category": "settings", "screen_purpose": "App preferences",
     "params": {"inputs": ["user_id"], "outputs": ["updated_preferences"], "displays": ["toggles"]},
     "widgets": [
         {"id": "sw_notifications", "type": "toggle", "role": "Toggle notifications"},
         {"id": "btn_logout", "type": "click", "role": "Logout"},
     ], "screenshot_ref": "", "confidence": "high"},
    {"screen_id": "page_create", "activity": "CreatePostActivity", "label": "Create Post",
     "functional_category": "form", "screen_purpose": "Compose and publish new post",
     "params": {"inputs": ["auth_token"], "outputs": ["post_id"], "displays": ["editor"]},
     "widgets": [
         {"id": "et_content", "type": "input", "role": "Post content"},
         {"id": "btn_publish", "type": "click", "role": "Publish"},
     ], "screenshot_ref": "", "confidence": "medium"},
]

edges = [
    {"edge_id": "e_001", "from": "page_splash", "to": "page_login", "trigger_action": "auto", "trigger_widget": "", "condition": "not_logged_in", "passed_params": [], "returned_params": []},
    {"edge_id": "e_002", "from": "page_splash", "to": "page_home", "trigger_action": "auto", "trigger_widget": "", "condition": "already_logged_in", "passed_params": ["auth_token"], "returned_params": []},
    {"edge_id": "e_003", "from": "page_login", "to": "page_home", "trigger_action": "click", "trigger_widget": "btn_login", "condition": "credentials_valid", "passed_params": ["auth_token", "user_id"], "returned_params": []},
    {"edge_id": "e_004", "from": "page_home", "to": "page_search", "trigger_action": "click", "trigger_widget": "btn_search", "condition": None, "passed_params": [], "returned_params": []},
    {"edge_id": "e_005", "from": "page_home", "to": "page_detail", "trigger_action": "click", "trigger_widget": "post_item", "condition": None, "passed_params": ["post_id"], "returned_params": []},
    {"edge_id": "e_006", "from": "page_home", "to": "page_profile", "trigger_action": "click", "trigger_widget": "tab_profile", "condition": None, "passed_params": ["user_id"], "returned_params": []},
    {"edge_id": "e_007", "from": "page_home", "to": "page_create", "trigger_action": "click", "trigger_widget": "fab_create", "condition": None, "passed_params": ["auth_token"], "returned_params": []},
    {"edge_id": "e_008", "from": "page_search", "to": "page_detail", "trigger_action": "click", "trigger_widget": "result_item", "condition": None, "passed_params": ["post_id"], "returned_params": []},
    {"edge_id": "e_009", "from": "page_search", "to": "page_home", "trigger_action": "press_back", "trigger_widget": "", "condition": None, "passed_params": [], "returned_params": []},
    {"edge_id": "e_010", "from": "page_detail", "to": "page_home", "trigger_action": "press_back", "trigger_widget": "", "condition": None, "passed_params": [], "returned_params": []},
    {"edge_id": "e_011", "from": "page_profile", "to": "page_settings", "trigger_action": "click", "trigger_widget": "btn_settings", "condition": None, "passed_params": ["user_id"], "returned_params": []},
    {"edge_id": "e_012", "from": "page_profile", "to": "page_home", "trigger_action": "press_back", "trigger_widget": "", "condition": None, "passed_params": [], "returned_params": []},
    {"edge_id": "e_013", "from": "page_settings", "to": "page_profile", "trigger_action": "press_back", "trigger_widget": "", "condition": None, "passed_params": [], "returned_params": []},
    {"edge_id": "e_014", "from": "page_settings", "to": "page_login", "trigger_action": "click", "trigger_widget": "btn_logout", "condition": None, "passed_params": [], "returned_params": []},
    {"edge_id": "e_015", "from": "page_create", "to": "page_home", "trigger_action": "click", "trigger_widget": "btn_publish", "condition": None, "passed_params": ["post_id"], "returned_params": []},
    {"edge_id": "e_016", "from": "page_create", "to": "page_home", "trigger_action": "press_back", "trigger_widget": "", "condition": None, "passed_params": [], "returned_params": []},
]

screenmap = {
    "screen_map": {
        "app_name": "ExampleApp",
        "package_name": "com.example.app",
        "version": "1.0.0",
        "generated_at": "2026-04-16T09:00:00Z",
        "graph": {
            "entry_node": "page_splash",
            "nodes": nodes,
            "edges": edges,
            "edge_groups": [],
            "global_params": {
                "auth_token": {"type": "string", "description": "Login token", "source_node": "page_login", "scope": "session"},
                "user_id": {"type": "string", "description": "User ID", "source_node": "page_login", "scope": "session"},
            },
        },
        "metadata": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "coverage_ratio": 0.875,
            "orphan_nodes": 0,
            "dead_end_nodes": 0,
            "validation_issues": 0,
        },
    }
}

(base / "output" / "screen_map.json").write_text(
    json.dumps(screenmap, indent=2, ensure_ascii=False, encoding="utf-8"), encoding="utf-8"
)
print(f"Demo data created at workspace/{tour_id}/")
