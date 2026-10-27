"""Remove only newly staged uploads when their database transaction rolls back."""
import logging
import os
from sqlalchemy import event
from sqlalchemy.orm import Session


@event.listens_for(Session, "after_commit")
def keep_committed_uploads(session):
    session.info.pop("staged_uploads", None)


@event.listens_for(Session, "after_transaction_end")
def discard_uncommitted_uploads(session, transaction):
    if transaction.parent is not None:
        return
    for path in session.info.pop("staged_uploads", []):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            logging.getLogger(__name__).warning("Could not remove an uncommitted upload; private storage needs review.")


def stage_upload(session, path):
    session.info.setdefault("staged_uploads", []).append(os.path.abspath(path))
