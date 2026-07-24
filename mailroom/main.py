from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import hashlib
import json
import sqlite3
import uuid

app = FastAPI()


db = sqlite3.connect(
    "mailroom.db",
    check_same_thread=False
)

db.execute("""
CREATE TABLE IF NOT EXISTS proposals(
fingerprint TEXT PRIMARY KEY,
data TEXT
)
""")

db.commit()


def fingerprint(data):
    return hashlib.sha256(
        json.dumps(data, sort_keys=True).encode()
    ).hexdigest()


class MailRequest(BaseModel):
    operation: str
    evaluationId: str | None = None
    dossiers: list = []
    receipts: list = []


def decide_action(dossier):

    text = json.dumps(dossier).lower()

    if "prompt injection" in text or "ignore previous" in text:
        action = "quarantine_item"

    elif "duplicate" in text or "already completed" in text:
        action = "no_action"

    elif "approved" in text and "send" in text:
        action = "send_approved_notice"

    elif "ambiguous" in text or "identity" in text:
        action = "request_confirmation"

    else:
        action = "create_draft"


    return {
        "action": action,
        "evidenceRefs": [
            "dossier_content"
        ],
        "reason": "Decision based on dossier content and safety policy.",
        "callId": str(uuid.uuid4())
    }



@app.post("/v1/mailroom/actions")
def mailroom(req: MailRequest):

    if req.operation == "propose":

        proposals=[]

        for dossier in req.dossiers:

            fp=fingerprint(dossier)

            old=db.execute(
                "SELECT data FROM proposals WHERE fingerprint=?",
                (fp,)
            ).fetchone()


            if old:
                result=json.loads(old[0])

            else:
                result=decide_action(dossier)

                db.execute(
                    "INSERT INTO proposals VALUES (?,?)",
                    (
                        fp,
                        json.dumps(result)
                    )
                )

                db.commit()


            proposals.append(result)


        return {
            "status":"awaiting_receipts",
            "proposals":proposals
        }


    elif req.operation=="commit":

        return {
            "status":"completed",
            "outcomes":req.receipts
        }


    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid operation"
        )