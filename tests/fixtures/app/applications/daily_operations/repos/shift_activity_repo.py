from app.core.app_mode import sql_root_dir

def _load_sql_template(filename: str) -> str:
    return (sql_root_dir() / filename).read_text()

def fetch_workcell_shift_activity(conn, workcell: str, window_start, window_end) -> list[dict]:
    """Fetch normalized operation events for a Workcell and time window."""
    template = _load_sql_template('workcell_shift_activity.sql')
    return []
