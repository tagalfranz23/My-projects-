"""Central role-based authorization for all seven system processes."""
from functools import wraps
from flask import abort, g

ROLE_PERMISSIONS = {
    "resident": {
        "profile": {"view", "edit"}, "permit": {"view_own", "create"},
        "event": {"view_own", "create"}, "blotter": {"view_own", "create"},
        "schedule": {"view_own", "create"}, "notification": {"view_own", "edit_own"},
        "announcement": {"view"}, "status": {"view_own"},
    },
    "staff": {
        "profile": {"view", "edit"}, "users": {"view"},
        # Staff members retain operational access and use the same legitimate
        # account for their own resident transactions. Route-level ownership
        # checks prevent them from processing a request they submitted.
        "permit": {"view", "view_own", "create", "review", "endorse"},
        "event": {"view", "view_own", "create", "review", "endorse"},
        "blotter": {"view", "view_own", "create", "review", "endorse", "edit"},
        "schedule": {"view", "create", "edit", "confirm"},
        "notification": {"view", "create"}, "announcement": {"view"},
        "status": {"view_own"}, "work_summary": {"view"},
        "front_desk": {"view", "manage"}, "concern": {"create", "view", "route"},
        "resident_update": {"create", "view"}, "household": {"view", "manage"},
        "referral": {"create", "view"},
    },
    "admin": {"*": {"*"}},
}

def can(role, resource, action):
    grants = ROLE_PERMISSIONS.get(role, {})
    return "*" in grants or action in grants.get(resource, set()) or "*" in grants.get(resource, set())

def permission_required(resource, action):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = getattr(g, "user", None)
            if not user:
                abort(401)
            if not can(user.role, resource, action):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator

def navigation_for(role):
    common = [{"label": "Dashboard", "endpoint": "dashboard", "icon": "⌂"}]
    items = {
        "resident": [
            ("Announcements", "announcements", "◉"),
            ("My Permits", "permits", "▣"), ("My Event Requests", "events", "☆"),
            ("My Blotter Reports", "blotters", "⚖"), ("My Schedules", "schedules", "◷"),
            ("View Status", "transaction_status", "◎"), ("Notifications", "notifications", "♢"),
        ],
        "staff": [
            ("Announcements", "announcements", "◉"),
            ("Resident Services", "front_desk", "☆"),
            ("Permit Processing", "permits", "▣"), ("Event Approval", "events", "☆"),
            ("Blotter Management", "blotters", "⚖"), ("Scheduling", "schedules", "◷"),
            ("Read-only Users", "users", "♙"),
        ],
        "admin": [
            ("Announcements", "announcements", "◉"),
            ("Resident Services", "front_desk", "☆"),
            ("1.0 User Management", "users", "♙"), ("2.0 Permit Processing", "permits", "▣"),
            ("3.0 Event Approval", "events", "☆"), ("4.0 Blotter Management", "blotters", "⚖"),
            ("5.0 Schedules", "schedules", "◷"), ("Notifications", "notifications", "♢"),
            ("6.0 Reports & Monitoring", "reports", "▤"), ("7.0 Activity Logging", "activity_logs", "≡"),
            ("Configuration", "configuration", "⚙"),
        ],
    }
    result=common + [{"label": a, "endpoint": b, "icon": c} for a, b, c in items.get(role, [])]
    result.append({"label":"Manage Profile","endpoint":"profile","icon":"♙"})
    return result
