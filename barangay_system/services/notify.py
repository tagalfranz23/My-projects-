from datetime import datetime
from models import db, Notification, User

VALID_CHANNELS={"sms","email","in_app"}

def create_notification(user_id,title,message,channel="in_app",related_type=None,related_id=None,commit=True):
    if channel not in VALID_CHANNELS: raise ValueError("Unsupported notification channel")
    user=db.session.get(User,user_id)
    if not user: raise ValueError("Notification recipient not found")
    contact={"sms":user.contact_number,"email":user.email,"in_app":None}[channel]
    # SMS/email delivery is explicitly simulated until a provider is configured.
    status="sent" if channel=="in_app" or contact else "failed"
    item=Notification(user_id=user.id,recipient_user_id=user.id,recipient_contact=contact,title=title,
        message=message,channel=channel,status=status,related_type=related_type,related_id=related_id,
        sent_at=datetime.utcnow() if status=="sent" else None)
    db.session.add(item)
    if commit: db.session.commit()
    return item

def notify_status(user_id,title,message,related_type,related_id):
    results=[create_notification(user_id,title,message,c,related_type,related_id,False) for c in ("in_app","email","sms")]
    db.session.commit(); return results

def send_sms(contact,message,related_type=None,related_id=None,recipient_user_id=None):
    return create_notification(recipient_user_id,"SMS notification",message,"sms",related_type,related_id)
