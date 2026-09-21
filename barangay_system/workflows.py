"""Central, role-aware workflow transition rules."""
TRANSITIONS = {
    "permit": {
        "staff": {"Pending": {"Under Review"}, "Under Review": {"Endorsed to Admin"}},
        "admin": {"Pending": {"Under Review", "Cancelled"}, "Under Review": {"Endorsed to Admin", "Rejected"},
                  "Endorsed to Admin": {"Approved", "Rejected"},
                  # Release readiness is set only by the signed-permit/payment routes.
                  # Keeping it out of the generic status form prevents bypassing the
                  # required electronic-signature and manual-payment safeguards.
                  "Approved": set(),
                  "Ready for Pickup": {"Completed"}},
    },
    "event": {
        "staff": {"Pending": {"Under Review"}, "Under Review": {"Endorsed to Admin"}},
        "admin": {"Pending": {"Under Review", "Cancelled"}, "Under Review": {"Endorsed to Admin", "Rejected"},
                  "Endorsed to Admin": {"Approved", "Rejected"}, "Approved": {"Completed"}},
    },
    "blotter": {
        "staff": {"Filed": {"Under Review"}, "Under Review": {"For Investigation"},
                  "For Investigation": {"For Hearing / Scheduled"}},
        "admin": {"Filed": {"Under Review"}, "Under Review": {"For Investigation"},
                  "For Investigation": {"For Hearing / Scheduled", "Resolved"},
                  "For Hearing / Scheduled": {"Resolved"}, "Resolved": {"Closed"}},
    },
    "schedule": {
        "staff": {"Requested": {"Confirmed", "Rescheduled"}, "Confirmed": {"Rescheduled", "Completed"},
                  "Rescheduled": {"Confirmed", "Completed"}},
        "admin": {"Requested": {"Confirmed", "Cancelled"}, "Confirmed": {"Rescheduled", "Completed", "Cancelled"},
                  "Rescheduled": {"Confirmed", "Completed", "Cancelled"}},
    },
}

def allowed_transitions(resource, role, current):
    return sorted(TRANSITIONS.get(resource, {}).get(role, {}).get(current, set()))

def validate_transition(resource, role, current, target):
    return target in TRANSITIONS.get(resource, {}).get(role, {}).get(current, set())
