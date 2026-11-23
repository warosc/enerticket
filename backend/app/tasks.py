import os
from celery import Celery
celery_app = Celery("servicedesk", broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
@celery_app.task
def ticket_created(ticket_id: int):
    return {"ticket_id": ticket_id, "event": "created"}
